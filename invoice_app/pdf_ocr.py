"""OCR the table region with PaddleOCR and assign lines to grid cells."""

from __future__ import annotations

import os
import threading
from dataclasses import dataclass

import pymupdf as fitz

from invoice_app.pdf_text import Word, words_in_rect

# Paddle 3.3 CPU inference can fail inside oneDNN when image sizes change.
# Disable it before PaddleX reads these flags at import time.
os.environ["PADDLE_PDX_ENABLE_MKLDNN_BYDEFAULT"] = "0"
os.environ["FLAGS_use_mkldnn"] = "0"

_MIN_SCORE = 0.3
_PAD_PT = 8.0
_thread_local = threading.local()


def _render_zoom(width_pt: float, height_pt: float) -> float:
    """Scale so the region long side is about 2200 px, capped for the detector."""
    long_side = max(width_pt, height_pt, 1.0)
    zoom = min(6.0, max(2.0, 2200.0 / long_side))
    if long_side * zoom > 4000.0:
        zoom = 4000.0 / long_side
    return zoom


def _pixmap_rgb(pix: fitz.Pixmap):
    import numpy as np

    channels = pix.n
    raw = np.frombuffer(pix.samples, dtype=np.uint8)
    if pix.stride == pix.width * channels:
        image = raw.reshape(pix.height, pix.width, channels)
    else:
        rows = raw.reshape(pix.height, pix.stride)
        image = rows[:, : pix.width * channels].reshape(pix.height, pix.width, channels)
    if channels > 3:
        image = image[:, :, :3]
    return np.ascontiguousarray(image)


def page_pdf_rect(doc: fitz.Document, page_index: int = 0) -> tuple[float, float, float, float]:
    page = doc[page_index]
    r = page.rect
    return (r.x0, r.y0, r.x1, r.y1)


def _clip_and_zoom(
    page: fitz.Page, pdf_rect: tuple[float, float, float, float]
) -> tuple[fitz.Rect, float]:
    x0, y0, x1, y1 = pdf_rect
    width_pt = x1 - x0
    height_pt = y1 - y0
    if width_pt < 1.0 or height_pt < 1.0:
        raise ValueError("The region is too small to rasterise.")
    zoom = _render_zoom(width_pt, height_pt)
    clip = fitz.Rect(
        max(page.rect.x0, x0 - _PAD_PT),
        max(page.rect.y0, y0 - _PAD_PT),
        min(page.rect.x1, x1 + _PAD_PT),
        min(page.rect.y1, y1 + _PAD_PT),
    )
    return clip, zoom


@dataclass(frozen=True)
class OcrLine:
    x0: float
    y0: float
    x1: float
    y1: float
    text: str
    score: float
    poly: tuple[tuple[float, float], ...] | None = None


def _point_raster_to_pdf(
    x: float, y: float, clip: fitz.Rect, zoom: float
) -> tuple[float, float]:
    return (clip.x0 + x / zoom, clip.y0 + y / zoom)


def words_raster_to_pdf(
    words: list[Word],
    pdf_rect: tuple[float, float, float, float],
    page: fitz.Page,
) -> list[Word]:
    """Map OCR boxes from full pixmap pixels into PDF user space."""
    clip, zoom = _clip_and_zoom(page, pdf_rect)
    converted: list[Word] = []
    for w in words:
        converted.append(
            Word(
                clip.x0 + w.x0 / zoom,
                clip.y0 + w.y0 / zoom,
                clip.x0 + w.x1 / zoom,
                clip.y0 + w.y1 / zoom,
                w.text,
            )
        )
    return converted


def lines_raster_to_pdf(
    lines: list[OcrLine],
    pdf_rect: tuple[float, float, float, float],
    page: fitz.Page,
) -> list[OcrLine]:
    """Map OCR lines (and optional quads) from pixmap pixels into PDF user space."""
    clip, zoom = _clip_and_zoom(page, pdf_rect)
    converted: list[OcrLine] = []
    for line in lines:
        poly_pdf: tuple[tuple[float, float], ...] | None = None
        if line.poly:
            poly_pdf = tuple(_point_raster_to_pdf(px, py, clip, zoom) for px, py in line.poly)
        converted.append(
            OcrLine(
                clip.x0 + line.x0 / zoom,
                clip.y0 + line.y0 / zoom,
                clip.x0 + line.x1 / zoom,
                clip.y0 + line.y1 / zoom,
                line.text,
                line.score,
                poly_pdf,
            )
        )
    return converted


def region_image(
    doc: fitz.Document,
    page_index: int,
    pdf_rect: tuple[float, float, float, float],
) -> tuple[object, tuple[float, float], tuple[float, float]]:
    """Rasterise a PDF rectangle.

    Returns the RGB image (with a few points of padding), the region origin
    inside that image in pixels, and the region size in pixels. Grid fractions
    are applied to origin/size, not to the padded image.
    """
    x0, y0, x1, y1 = pdf_rect
    width_pt = x1 - x0
    height_pt = y1 - y0
    if width_pt < 1.0 or height_pt < 1.0:
        raise ValueError("The table region is too small to OCR.")

    page = doc[page_index]
    clip, zoom = _clip_and_zoom(page, pdf_rect)
    pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), clip=clip, alpha=False)
    image = _pixmap_rgb(pix)
    origin = ((x0 - clip.x0) * zoom, (y0 - clip.y0) * zoom)
    size = (width_pt * zoom, height_pt * zoom)
    return image, origin, size


def _ocr_engine():
    engine = getattr(_thread_local, "engine", None)
    if engine is not None:
        return engine
    try:
        from paddleocr import PaddleOCR
    except ImportError as exc:
        raise RuntimeError(
            "PaddleOCR is not installed. In the project directory run: uv sync"
        ) from exc
    engine = PaddleOCR(
        use_doc_orientation_classify=False,
        use_doc_unwarping=False,
        use_textline_orientation=False,
        device="cpu",
        engine="paddle",
        enable_mkldnn=False,
    )
    _thread_local.engine = engine
    return engine


def _result_field(result, key: str):
    try:
        return result[key]
    except Exception:
        payload = getattr(result, "json", None)
        if isinstance(payload, dict):
            inner = payload.get("res", payload)
            if isinstance(inner, dict) and key in inner:
                return inner[key]
        raise


def _poly_from_box(box) -> tuple[tuple[float, float], ...] | None:
    if box is None:
        return None
    try:
        points = list(box)
    except TypeError:
        return None
    if len(points) >= 4 and not isinstance(points[0], (int, float)):
        out: list[tuple[float, float]] = []
        for pt in points[:4]:
            out.append((float(pt[0]), float(pt[1])))
        return tuple(out)
    if len(points) >= 4:
        x0, y0, x1, y1 = (float(v) for v in points[:4])
        return ((x0, y0), (x1, y0), (x1, y1), (x0, y1))
    return None


def _optional_polys(result):
    for key in ("rec_polys", "dt_polys"):
        try:
            value = _result_field(result, key)
        except Exception:
            continue
        if value is not None:
            return value
    return None


def _lines_from_result(result) -> list[OcrLine]:
    texts = _result_field(result, "rec_texts")
    scores = _result_field(result, "rec_scores")
    boxes = _result_field(result, "rec_boxes")
    polys = _optional_polys(result)
    lines: list[OcrLine] = []
    for index, (text, score, box) in enumerate(zip(texts, scores, boxes)):
        cleaned = str(text).strip()
        if not cleaned or float(score) < _MIN_SCORE:
            continue
        x0, y0, x1, y1 = (float(value) for value in box)
        poly = None
        if polys is not None and index < len(polys):
            poly = _poly_from_box(polys[index])
        if poly is None:
            poly = _poly_from_box(box)
        lines.append(OcrLine(x0, y0, x1, y1, cleaned, float(score), poly))
    return lines


def ocr_image_detailed(image) -> list[OcrLine]:
    """Run PaddleOCR on an RGB uint8 image. Boxes are in image pixels."""
    import numpy as np

    rgb = np.ascontiguousarray(image)
    if rgb.ndim != 3 or rgb.shape[2] != 3:
        raise ValueError(f"Expected an RGB image, got shape {getattr(rgb, 'shape', None)}.")
    bgr = np.ascontiguousarray(rgb[:, :, ::-1])
    results = _ocr_engine().predict(bgr)
    lines: list[OcrLine] = []
    for result in results:
        lines.extend(_lines_from_result(result))
    return lines


def ocr_image(image) -> list[Word]:
    """Run PaddleOCR on an RGB uint8 image. Boxes are in image pixels."""
    return [
        Word(line.x0, line.y0, line.x1, line.y1, line.text)
        for line in ocr_image_detailed(image)
    ]


def assign_to_grid(
    words: list[Word],
    row_fracs: list[float],
    col_fracs: list[float],
    origin: tuple[float, float],
    size: tuple[float, float],
) -> list[list[str]]:
    """Place OCR lines into cells. Fractions are relative to the table region."""
    ox, oy = origin
    rw, rh = size
    rows = max(len(row_fracs) - 1, 0)
    cols = max(len(col_fracs) - 1, 0)
    table: list[list[str]] = []
    for row in range(rows):
        y0 = oy + row_fracs[row] * rh
        y1 = oy + row_fracs[row + 1] * rh
        row_text: list[str] = []
        for col in range(cols):
            x0 = ox + col_fracs[col] * rw
            x1 = ox + col_fracs[col + 1] * rw
            row_text.append(words_in_rect(words, x0, y0, x1, y1))
        table.append(row_text)
    return table


def read_grid(
    image,
    row_fracs: list[float],
    col_fracs: list[float],
    origin: tuple[float, float],
    size: tuple[float, float],
) -> list[list[str]]:
    return assign_to_grid(ocr_image(image), row_fracs, col_fracs, origin, size)
