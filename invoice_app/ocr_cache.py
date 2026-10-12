"""Persist full-page PaddleOCR results beside each PDF as ``<stem>.ocr.json``."""

from __future__ import annotations

import json
import math
import statistics
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pymupdf as fitz

from invoice_app.pdf_ocr import OcrLine, lines_raster_to_pdf
from invoice_app.pdf_text import Word

SCHEMA_VERSION = 1


def cache_path_for_pdf(pdf_path: Path) -> Path:
    return pdf_path.with_name(f"{pdf_path.stem}.ocr.json")


def pdf_source_fingerprint(pdf_path: Path) -> tuple[float, int]:
    stat = pdf_path.stat()
    return (stat.st_mtime, stat.st_size)


def cache_matches_pdf(pdf_path: Path, payload: dict[str, Any]) -> bool:
    try:
        mtime, size = pdf_source_fingerprint(pdf_path)
    except OSError:
        return False
    return (
        payload.get("schema-version") == SCHEMA_VERSION
        and float(payload.get("source-mtime", -1)) == mtime
        and int(payload.get("source-size", -1)) == size
    )


def load_cache(pdf_path: Path) -> dict[str, Any] | None:
    path = cache_path_for_pdf(pdf_path)
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict) or not cache_matches_pdf(pdf_path, payload):
        return None
    return payload


def load_page_words(pdf_path: Path, page_index: int) -> list[Word] | None:
    payload = load_cache(pdf_path)
    if payload is None:
        return None
    pages = payload.get("pages")
    if not isinstance(pages, list):
        return None
    for entry in pages:
        if not isinstance(entry, dict):
            continue
        if int(entry.get("page", -1)) != page_index:
            continue
        words_raw = entry.get("words")
        if not isinstance(words_raw, list):
            return []
        words: list[Word] = []
        for row in words_raw:
            if not isinstance(row, dict):
                continue
            text = str(row.get("text", "")).strip()
            if not text:
                continue
            words.append(
                Word(
                    float(row["x0"]),
                    float(row["y0"]),
                    float(row["x1"]),
                    float(row["y1"]),
                    text,
                )
            )
        return words
    return None


def median_skew_deg(polys: list[tuple[tuple[float, float], ...]]) -> float | None:
    angles: list[float] = []
    for poly in polys:
        if len(poly) < 2:
            continue
        x0, y0 = poly[0]
        x1, y1 = poly[1]
        dx = x1 - x0
        dy = y1 - y0
        if dx * dx + dy * dy < 1.0:
            continue
        angles.append(math.degrees(math.atan2(dy, dx)))
    if not angles:
        return None
    if len(angles) == 1:
        return round(angles[0], 2)
    med = statistics.median(angles)
    tight = [a for a in angles if abs(a - med) <= 10.0]
    if not tight:
        tight = angles
    return round(float(statistics.median(tight)), 2)


def _round_pt(value: float) -> float:
    return round(value, 1)


def _poly_to_json(poly: tuple[tuple[float, float], ...] | None) -> list[list[float]] | None:
    if not poly:
        return None
    return [[_round_pt(x), _round_pt(y)] for x, y in poly]


@dataclass(frozen=True)
class PageOcrResult:
    page_index: int
    width: float
    height: float
    skew_deg: float | None
    lines: tuple[OcrLine, ...]


def build_page_payload(result: PageOcrResult) -> dict[str, Any]:
    words: list[dict[str, Any]] = []
    for line in result.lines:
        row: dict[str, Any] = {
            "x0": _round_pt(line.x0),
            "y0": _round_pt(line.y0),
            "x1": _round_pt(line.x1),
            "y1": _round_pt(line.y1),
            "text": line.text,
            "score": round(line.score, 4),
        }
        poly_json = _poly_to_json(line.poly)
        if poly_json is not None:
            row["poly"] = poly_json
        words.append(row)
    payload: dict[str, Any] = {
        "page": result.page_index,
        "width": _round_pt(result.width),
        "height": _round_pt(result.height),
        "words": words,
    }
    if result.skew_deg is not None:
        payload["skew-deg"] = result.skew_deg
    return payload


def write_cache(pdf_path: Path, pages: list[PageOcrResult]) -> Path:
    mtime, size = pdf_source_fingerprint(pdf_path)
    payload = {
        "schema-version": SCHEMA_VERSION,
        "source-mtime": mtime,
        "source-size": size,
        "pages": [build_page_payload(p) for p in pages],
    }
    out = cache_path_for_pdf(pdf_path)
    out.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return out


def ocr_document_pages(doc: fitz.Document) -> list[PageOcrResult]:
    """OCR every page; boxes and polys in PDF user space."""
    from invoice_app.pdf_ocr import ocr_image_detailed, page_pdf_rect, region_image

    results: list[PageOcrResult] = []
    for page_index in range(doc.page_count):
        pdf_rect = page_pdf_rect(doc, page_index)
        page = doc[page_index]
        r = page.rect
        image, _, _ = region_image(doc, page_index, pdf_rect)
        raster_lines = ocr_image_detailed(image)
        pdf_lines = lines_raster_to_pdf(raster_lines, pdf_rect, page)
        polys = [ln.poly for ln in pdf_lines if ln.poly]
        skew = median_skew_deg(list(polys)) if polys else None
        results.append(
            PageOcrResult(
                page_index=page_index,
                width=r.width,
                height=r.height,
                skew_deg=skew,
                lines=tuple(pdf_lines),
            )
        )
    return results
