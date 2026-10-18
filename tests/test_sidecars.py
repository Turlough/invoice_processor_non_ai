"""Unit tests for OCR sidecar (`ocr_cache.py`) and layout sidecar (`document_layout.py`)."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from invoice_app.document_layout import load_layout, layout_path_for_pdf, save_page_region
from invoice_app.ocr_cache import (
    PageOcrResult,
    cache_matches_pdf,
    load_cache,
    load_page_words,
    load_skew_by_page,
    median_skew_deg,
    write_cache,
)
from invoice_app.pdf_ocr import OcrLine


class TestOcrCache(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.pdf = Path(self._tmp.name) / "sample.pdf"
        self.pdf.write_bytes(b"%PDF-1.4 dummy bytes")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_write_and_load_cache_words_and_skew(self) -> None:
        line = OcrLine(
            x0=10.04,
            y0=20.04,
            x1=100.0,
            y1=35.0,
            text="Invoice Line",
            score=0.98765,
            poly=((10.0, 20.0), (100.0, 22.0), (100.0, 35.0), (10.0, 33.0)),
        )
        page_res = PageOcrResult(
            page_index=0,
            width=595.0,
            height=842.0,
            skew_deg=1.25,
            lines=(line,),
        )
        write_cache(self.pdf, [page_res])

        self.assertIsNotNone(load_cache(self.pdf))
        words = load_page_words(self.pdf, 0)
        self.assertIsNotNone(words)
        assert words is not None
        self.assertEqual(len(words), 1)
        self.assertEqual(words[0].text, "Invoice Line")
        self.assertIsNone(load_page_words(self.pdf, 1))
        self.assertEqual(load_skew_by_page(self.pdf), {0: 1.25})

    def test_cache_matches_pdf_tolerates_dst_hour_shift_and_rejects_size_change(self) -> None:
        stat = self.pdf.stat()
        payload_exact = {
            "schema-version": 1,
            "source-mtime": stat.st_mtime,
            "source-size": stat.st_size,
        }
        self.assertTrue(cache_matches_pdf(self.pdf, payload_exact))

        payload_dst = {
            "schema-version": 1,
            "source-mtime": stat.st_mtime - 3600.5,
            "source-size": stat.st_size,
        }
        self.assertTrue(cache_matches_pdf(self.pdf, payload_dst))

        payload_bad_size = {
            "schema-version": 1,
            "source-mtime": stat.st_mtime,
            "source-size": stat.st_size + 1,
        }
        self.assertFalse(cache_matches_pdf(self.pdf, payload_bad_size))

    def test_median_skew_deg_computes_angle_and_filters_outliers(self) -> None:
        # Horizontal baselines -> 0.0 deg
        polys = [
            ((0.0, 0.0), (100.0, 0.0), (100.0, 10.0), (0.0, 10.0)),
            ((0.0, 20.0), (100.0, 20.0), (100.0, 30.0), (0.0, 30.0)),
            # One extreme outlier (45 deg) should be filtered by 10-deg window around median
            ((0.0, 0.0), (50.0, 50.0), (50.0, 60.0), (0.0, 10.0)),
        ]
        self.assertEqual(median_skew_deg(polys), 0.0)
        self.assertIsNone(median_skew_deg([]))


class TestDocumentLayout(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.pdf = Path(self._tmp.name) / "invoice.pdf"
        self.pdf.write_bytes(b"%PDF-1.4 layout test")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_save_and_load_page_regions_merges_and_deletes(self) -> None:
        save_page_region(self.pdf, 0, (0.8, 0.7, 0.1, 0.2))  # unsorted corners
        save_page_region(self.pdf, 2, (0.05, 0.15, 0.95, 0.85))
        self.assertTrue(layout_path_for_pdf(self.pdf).is_file())

        regions = load_layout(self.pdf)
        self.assertEqual(regions[0], (0.1, 0.2, 0.8, 0.7))
        self.assertEqual(regions[2], (0.05, 0.15, 0.95, 0.85))

        # Remove page 0 region
        save_page_region(self.pdf, 0, None)
        self.assertEqual(load_layout(self.pdf), {2: (0.05, 0.15, 0.95, 0.85)})

    def test_stale_pdf_invalidates_layout_sidecar(self) -> None:
        save_page_region(self.pdf, 0, (0.1, 0.2, 0.8, 0.7))
        self.pdf.write_bytes(b"%PDF-1.4 modified size bytes")
        self.assertEqual(load_layout(self.pdf), {})


if __name__ == "__main__":
    unittest.main()
