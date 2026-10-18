"""Unit tests for PDF text extraction (`pdf_text.py`) and OCR coordinate helpers (`pdf_ocr.py`)."""

from __future__ import annotations

import unittest

import pymupdf as fitz

from invoice_app.pdf_ocr import (
    OcrLine,
    assign_to_grid,
    lines_raster_to_pdf,
    page_pdf_rect,
    region_image,
    words_raster_to_pdf,
)
from invoice_app.pdf_text import Word, words_in_pdf_rect, words_in_rect, words_on_page


class TestPdfText(unittest.TestCase):
    def test_words_in_pdf_rect_and_reading_order_text(self) -> None:
        words = [
            Word(50, 20, 90, 30, "World"),
            Word(10, 20, 45, 30, "Hello"),
            Word(10, 40, 60, 50, "Second"),
            Word(300, 300, 350, 320, "Outside"),
        ]
        kept = words_in_pdf_rect(words, 0, 0, 200, 100)
        self.assertEqual(len(kept), 3)
        self.assertEqual(words_in_rect(words, 0, 0, 200, 100), "Hello World Second")

    def test_words_on_page_extracts_from_in_memory_pdf(self) -> None:
        with fitz.open() as doc:
            page = doc.new_page(width=400, height=300)
            page.insert_text((50, 80), "Invoice 12345")
            extracted = words_on_page(doc, 0)
        self.assertEqual([w.text for w in extracted], ["Invoice", "12345"])


class TestPdfOcrHelpers(unittest.TestCase):
    def test_assign_to_grid_buckets_words_by_cell_bounds(self) -> None:
        words = [
            Word(20, 10, 80, 30, "ItemA"),
            Word(120, 10, 180, 30, "10.00"),
            Word(20, 60, 80, 80, "ItemB"),
            Word(120, 60, 180, 80, "20.00"),
        ]
        grid = assign_to_grid(
            words,
            row_fracs=[0.0, 0.5, 1.0],
            col_fracs=[0.0, 0.5, 1.0],
            origin=(0.0, 0.0),
            size=(200.0, 100.0),
        )
        self.assertEqual(grid, [["ItemA", "10.00"], ["ItemB", "20.00"]])

    def test_region_image_and_raster_to_pdf_coordinate_mapping(self) -> None:
        with fitz.open() as doc:
            page = doc.new_page(width=500, height=400)
            rect = page_pdf_rect(doc, 0)
            self.assertEqual(rect, (0.0, 0.0, 500.0, 400.0))

            image, origin, size = region_image(doc, 0, (50.0, 60.0, 250.0, 160.0))
            self.assertEqual( getattr(image, "ndim", None), 3)
            self.assertGreater(size[0], 0.0)
            self.assertGreater(size[1], 0.0)
            self.assertGreater(origin[0], 0.0)
            self.assertGreater(origin[1], 0.0)

            # A word at the raster origin should map back to (50.0, 60.0) in PDF space
            ox, oy = origin
            rw, rh = size
            mapped_words = words_raster_to_pdf(
                [Word(ox, oy, ox + rw, oy + rh, "box")],
                (50.0, 60.0, 250.0, 160.0),
                page,
            )
            self.assertAlmostEqual(mapped_words[0].x0, 50.0, places=3)
            self.assertAlmostEqual(mapped_words[0].y0, 60.0, places=3)
            self.assertAlmostEqual(mapped_words[0].x1, 250.0, places=3)
            self.assertAlmostEqual(mapped_words[0].y1, 160.0, places=3)

            mapped_lines = lines_raster_to_pdf(
                [
                    OcrLine(
                        ox,
                        oy,
                        ox + rw,
                        oy + rh,
                        "line",
                        0.95,
                        poly=((ox, oy), (ox + rw, oy), (ox + rw, oy + rh), (ox, oy + rh)),
                    )
                ],
                (50.0, 60.0, 250.0, 160.0),
                page,
            )
            self.assertAlmostEqual(mapped_lines[0].x0, 50.0, places=3)
            assert mapped_lines[0].poly is not None
            self.assertAlmostEqual(mapped_lines[0].poly[0][0], 50.0, places=3)
            self.assertAlmostEqual(mapped_lines[0].poly[0][1], 60.0, places=3)


if __name__ == "__main__":
    unittest.main()
