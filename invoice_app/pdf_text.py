"""Extract positioned text from PDF pages (works with OCR text layers)."""

from __future__ import annotations

from dataclasses import dataclass

import pymupdf as fitz


@dataclass(frozen=True)
class Word:
    x0: float
    y0: float
    x1: float
    y1: float
    text: str


def words_on_page(doc: fitz.Document, page_index: int) -> list[Word]:
    page = doc[page_index]
    raw = page.get_text("words")
    return [
        Word(w[0], w[1], w[2], w[3], w[4])
        for w in raw
        if w[4].strip()
    ]


def words_in_pdf_rect(
    words: list[Word], x0: float, y0: float, x1: float, y1: float
) -> list[Word]:
    """Words whose centre lies inside the PDF rectangle."""
    kept: list[Word] = []
    for w in words:
        cx = (w.x0 + w.x1) / 2
        cy = (w.y0 + w.y1) / 2
        if x0 <= cx <= x1 and y0 <= cy <= y1:
            kept.append(w)
    return kept


def words_in_rect(words: list[Word], x0: float, y0: float, x1: float, y1: float) -> str:
    """Return text for words whose centre lies inside the rectangle (PDF coords)."""
    picked: list[tuple[float, float, str]] = []
    for w in words:
        cx = (w.x0 + w.x1) / 2
        cy = (w.y0 + w.y1) / 2
        if x0 <= cx <= x1 and y0 <= cy <= y1:
            picked.append((w.y0, w.x0, w.text))
    picked.sort()
    return " ".join(t for _, _, t in picked)
