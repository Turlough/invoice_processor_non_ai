"""OCR the table region with PaddleOCR and assign lines to grid cells."""

from __future__ import annotations

import os
import threading

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
    zoom = _render_zoom(width_pt, height_pt)
    clip = fitz.Rect(
        max(page.rect.x0, x0 - _PAD_PT),
        max(page.rect.y0, y0 - _PAD_PT),
        min(page.rect.x1, x1 + _PAD_PT),
        min(page.rect.y1, y1 + _PAD_PT),
    )
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


def _lines_from_result(result) -> list[Word]:
    texts = _result_field(result, "rec_texts")
    scores = _result_field(result, "rec_scores")
    boxes = _result_field(result, "rec_boxes")
    words: list[Word] = []
    for text, score, box in zip(texts, scores, boxes):
        cleaned = str(text).strip()
        if not cleaned or float(score) < _MIN_SCORE:
            continue
        x0, y0, x1, y1 = (float(value) for value in box)
        words.append(Word(x0, y0, x1, y1, cleaned))
    return words


def ocr_image(image) -> list[Word]:
    """Run PaddleOCR on an RGB uint8 image. Boxes are in image pixels."""
    import numpy as np

    rgb = np.ascontiguousarray(image)
    if rgb.ndim != 3 or rgb.shape[2] != 3:
        raise ValueError(f"Expected an RGB image, got shape {getattr(rgb, 'shape', None)}.")
    # PaddleOCR numpy input follows OpenCV's BGR channel order.
    bgr = np.ascontiguousarray(rgb[:, :, ::-1])
    results = _ocr_engine().predict(bgr)
    words: list[Word] = []
    for result in results:
        words.extend(_lines_from_result(result))
    return words


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
