"""Unit tests for deskew rotation bounds (`pdf_panel.py`) and `GridOverlay` geometry."""

from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

from invoice_app.grid_overlay import GridOverlay
from invoice_app.pdf_panel import _rotation_bounds


def _ensure_qapp() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app  # type: ignore[return-value]


class TestRotationBounds(unittest.TestCase):
    def test_zero_angle_preserves_dimensions(self) -> None:
        w, h, min_x, min_y = _rotation_bounds(800, 1000, 0.0)
        self.assertEqual((w, h, min_x, min_y), (800, 1000, 0.0, 0.0))

    def test_nonzero_angle_expands_canvas_without_clipping(self) -> None:
        w, h, min_x, min_y = _rotation_bounds(800, 1000, 2.5)
        self.assertGreater(w, 800)
        self.assertGreater(h, 1000)
        self.assertLess(min_x, 0.0)
        self.assertLessEqual(min_y, 0.0)


class TestGridOverlay(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._app = _ensure_qapp()

    def test_cell_widget_rect_and_invalid_cells_reset_on_grid_change(self) -> None:
        overlay = GridOverlay()
        overlay.resize(1000, 800)
        overlay.set_region_norm((0.1, 0.2, 0.9, 0.7))
        overlay.set_row_fracs([0.0, 0.5, 1.0])
        overlay.set_col_fracs([0.0, 0.25, 1.0])

        # Region is x: 100..900 (w=800), y: 160..560 (h=400)
        # Cell (0, 0) is x: 100..300, y: 160..360
        x0, y0, x1, y1 = overlay.cell_widget_rect(0, 0)
        self.assertAlmostEqual(x0, 100.0)
        self.assertAlmostEqual(y0, 160.0)
        self.assertAlmostEqual(x1, 300.0)
        self.assertAlmostEqual(y1, 360.0)

        overlay.set_invalid_cells({(0, 1): "Total mismatch"})
        overlay.clear_invalid_cells()

        overlay.set_invalid_cells({(0, 0): "Fault"})
        overlay.set_row_fracs([0.0, 1.0])
        # Setting new row fracs clears invalid cell state
        self.assertEqual(overlay.toolTip(), "")


if __name__ == "__main__":
    unittest.main()
