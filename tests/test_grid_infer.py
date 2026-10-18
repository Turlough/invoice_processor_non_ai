"""Unit tests for OCR/text-box table grid inference (`invoice_app/grid_infer.py`)."""

from __future__ import annotations

import unittest

from invoice_app.grid_infer import (
    InferHints,
    filter_words_in_columns,
    filter_words_in_region,
    infer_from_words,
    infer_row_bands,
)
from invoice_app.pdf_text import Word


def _w(x0: float, y0: float, x1: float, y1: float, text: str) -> Word:
    return Word(x0, y0, x1, y1, text)


class TestFilterWords(unittest.TestCase):
    def test_filter_words_in_region_excludes_outside_centres(self) -> None:
        inside = _w(10, 20, 50, 40, "inside")
        outside_left = _w(-40, 20, -10, 40, "left")
        outside_below = _w(10, 810, 50, 830, "below")
        kept = filter_words_in_region(
            [inside, outside_left, outside_below],
            origin=(0.0, 0.0),
            size=(1000.0, 800.0),
        )
        self.assertEqual(kept, [inside])
        self.assertEqual(
            filter_words_in_region([inside], origin=(0.0, 0.0), size=(0.0, 800.0)),
            [],
        )

    def test_filter_words_in_columns_respects_col_bounds(self) -> None:
        w1 = _w(50, 10, 90, 30, "in_band")  # cx=70 -> nx=0.07 (outside 0.1..0.9)
        w2 = _w(200, 10, 260, 30, "kept")   # cx=230 -> nx=0.23 (inside 0.1..0.9)
        kept = filter_words_in_columns(
            [w1, w2],
            origin=(0.0, 0.0),
            size=(1000.0, 800.0),
            col_fracs=[0.1, 0.5, 0.9],
        )
        self.assertEqual(kept, [w2])


class TestInferRowBands(unittest.TestCase):
    def test_clusters_words_on_same_line_and_splits_distinct_lines(self) -> None:
        words = [
            _w(20, 100, 120, 120, "Nurse"),
            _w(450, 102, 490, 122, "8.0"),
            _w(850, 100, 920, 120, "200.00"),
            _w(20, 150, 140, 170, "Carer"),
            _w(450, 151, 490, 171, "12.0"),
            _w(850, 150, 920, 170, "240.00"),
        ]
        bands = infer_row_bands(words, (0.0, 0.0), (1000.0, 800.0))
        self.assertEqual(len(bands), 2)
        self.assertEqual({w.text for w in bands[0].words}, {"Nurse", "8.0", "200.00"})
        self.assertEqual({w.text for w in bands[1].words}, {"Carer", "12.0", "240.00"})


class TestInferFromWords(unittest.TestCase):
    def test_three_single_line_rows_with_saved_col_fracs(self) -> None:
        words = [
            # Row 1 (y: 40..60)
            _w(20, 40, 180, 60, "Day"),
            _w(190, 40, 260, 60, "Shift"),
            _w(450, 40, 490, 60, "10"),
            _w(650, 40, 710, 60, "25.00"),
            _w(850, 40, 920, 60, "250.00"),
            # Row 2 (y: 100..120)
            _w(20, 100, 200, 120, "Night Shift"),
            _w(450, 100, 490, 120, "8"),
            _w(650, 100, 710, 120, "30.00"),
            _w(850, 100, 920, 120, "240.00"),
            # Row 3 (y: 160..180)
            _w(20, 160, 210, 180, "Weekend"),
            _w(450, 160, 490, 180, "12"),
            _w(650, 160, 710, 180, "35.00"),
            _w(850, 160, 920, 180, "420.00"),
        ]
        hints = InferHints(
            n_cols=4,
            col_fracs=[0.0, 0.4, 0.6, 0.8, 1.0],
            column_names=["Description", "Hours", "Rate", "Total"],
            anchor_cols=[False, True, True, True],
        )
        res = infer_from_words(words, (0.0, 0.0), (1000.0, 300.0), hints)
        self.assertEqual(res.data_row_count, 3)
        self.assertEqual(len(res.row_fracs), 4)
        self.assertEqual(res.rows[0]["Description"], "Day Shift")
        self.assertEqual(res.rows[0]["Hours"], "10")
        self.assertEqual(res.rows[0]["Rate"], "25.00")
        self.assertEqual(res.rows[0]["Total"], "250.00")
        self.assertEqual(res.rows[2]["Description"], "Weekend")

    def test_merges_wrapped_non_anchor_description_lines_and_excludes_subtotal(self) -> None:
        words = [
            # Row 1: main line + wrapped continuation line in col 0 (y=40..60 and y=68..88)
            _w(20, 40, 200, 60, "Senior Healthcare"),
            _w(450, 40, 490, 60, "10"),
            _w(650, 40, 710, 60, "20.00"),
            _w(850, 40, 920, 60, "200.00"),
            _w(20, 68, 180, 88, "Assistant Cover"),
            # Row 2: single line (y=130..150)
            _w(20, 130, 180, 150, "Standard Shift"),
            _w(450, 130, 490, 150, "5"),
            _w(650, 130, 710, 150, "20.00"),
            _w(850, 130, 920, 150, "100.00"),
            # Subtotal line that must be excluded from data rows
            _w(20, 210, 150, 230, "Subtotal"),
            _w(850, 210, 920, 230, "300.00"),
        ]
        hints = InferHints(
            n_cols=4,
            col_fracs=[0.0, 0.4, 0.6, 0.8, 1.0],
            column_names=["Description", "Hours", "Rate", "Total"],
            anchor_cols=[False, True, True, True],
        )
        res = infer_from_words(words, (0.0, 0.0), (1000.0, 300.0), hints)
        self.assertEqual(res.data_row_count, 2)
        self.assertEqual(
            res.rows[0]["Description"],
            "Senior Healthcare Assistant Cover",
        )
        self.assertEqual(res.rows[1]["Description"], "Standard Shift")

    def test_cold_start_infers_col_fracs_when_none_saved(self) -> None:
        words = [
            _w(30, 40, 180, 60, "Item A"),
            _w(520, 40, 560, 60, "2"),
            _w(820, 40, 900, 60, "50.00"),
            _w(30, 100, 180, 120, "Item B"),
            _w(520, 100, 560, 120, "4"),
            _w(820, 100, 900, 120, "100.00"),
        ]
        hints = InferHints(n_cols=3, col_fracs=None, column_names=["Item", "Qty", "Amount"])
        res = infer_from_words(words, (0.0, 0.0), (1000.0, 200.0), hints)
        self.assertEqual(len(res.col_fracs), 4)
        self.assertEqual(res.col_fracs[0], 0.0)
        self.assertEqual(res.col_fracs[-1], 1.0)
        self.assertEqual(res.data_row_count, 2)

    def test_empty_words_returns_default_bounds(self) -> None:
        hints = InferHints(n_cols=3, col_fracs=[0.0, 0.5, 0.8, 1.0])
        res = infer_from_words([], (0.0, 0.0), (1000.0, 200.0), hints)
        self.assertEqual(res.data_row_count, 0)
        self.assertEqual(res.row_fracs, [0.0, 1.0])
        self.assertEqual(res.rows, [])

    def test_bottom_aligned_leading_wrap_attaches_to_following_anchor(self) -> None:
        words = [
            # Leading wrapped description line above Row 1's anchor band
            _w(20, 20, 180, 38, "Line 1 prefix"),
            # Row 1 anchor band (y=46..64)
            _w(20, 46, 180, 64, "Line 1 main"),
            _w(550, 46, 600, 64, "10"),
            _w(850, 46, 920, 64, "100.00"),
            # Leading wrapped description line above Row 2's anchor band
            _w(20, 110, 180, 128, "Line 2 prefix"),
            # Row 2 anchor band (y=136..154)
            _w(20, 136, 180, 154, "Line 2 main"),
            _w(550, 136, 600, 154, "20"),
            _w(850, 136, 920, 154, "200.00"),
        ]
        hints = InferHints(
            n_cols=3,
            col_fracs=[0.0, 0.5, 0.75, 1.0],
            column_names=["Description", "Qty", "Total"],
            anchor_cols=[False, True, True],
        )
        res = infer_from_words(words, (0.0, 0.0), (1000.0, 250.0), hints)
        self.assertEqual(res.data_row_count, 2)
        self.assertEqual(res.rows[0]["Description"], "Line 1 prefix Line 1 main")
        self.assertEqual(res.rows[1]["Description"], "Line 2 prefix Line 2 main")



if __name__ == "__main__":
    unittest.main()
