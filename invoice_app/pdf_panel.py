"""Centre panel: PDF page image with row/col controls and grid overlay."""

from __future__ import annotations

from pathlib import Path

import pymupdf as fitz
from PyQt6.QtCore import Qt, pyqtSignal, QEvent
from PyQt6.QtGui import QImage, QPixmap
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from invoice_app.grid_overlay import GridOverlay
from invoice_app.pdf_text import Word


class _FitArea(QWidget):
    """Expands to fill the panel and keeps the page scaled to fit."""

    resized = pyqtSignal()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self.resized.emit()


class PdfPanel(QWidget):
    pdf_loaded = pyqtSignal()
    dimensions_changed = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._doc: fitz.Document | None = None
        self._page_index = 0
        self._page_size: tuple[float, float] = (1.0, 1.0)
        self._render_scale = 2.0
        self._source_pixmap: QPixmap | None = None

        self._rows_spin = QSpinBox()
        self._rows_spin.setRange(1, 100)
        self._rows_spin.setValue(5)
        self._rows_spin.setPrefix("Rows: ")

        self._cols_spin = QSpinBox()
        self._cols_spin.setRange(1, 50)
        self._cols_spin.setValue(4)
        self._cols_spin.setPrefix("Cols: ")

        controls = QHBoxLayout()
        controls.addWidget(self._rows_spin)
        controls.addWidget(self._cols_spin)
        controls.addStretch()

        self._fit_area = _FitArea()
        self._fit_area.setMinimumSize(200, 200)
        self._fit_area.resized.connect(self._apply_fit)

        self._image_label = QLabel(self._fit_area)
        self._image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._image_label.setScaledContents(False)

        self._overlay = GridOverlay(self._image_label)
        self._overlay.hide()

        layout = QVBoxLayout(self)
        layout.addLayout(controls)
        layout.addWidget(self._fit_area, stretch=1)

        self._rows_spin.valueChanged.connect(self._on_dimensions_changed)
        self._cols_spin.valueChanged.connect(self._on_dimensions_changed)

    @property
    def image_label(self) -> QLabel:
        return self._image_label

    def num_rows(self) -> int:
        return self._rows_spin.value()

    def num_cols(self) -> int:
        return self._cols_spin.value()

    def document(self) -> fitz.Document | None:
        return self._doc

    def page_index(self) -> int:
        return self._page_index

    def page_size_pdf(self) -> tuple[float, float]:
        return self._page_size

    def overlay(self) -> GridOverlay:
        return self._overlay

    def load_pdf(self, path: Path) -> None:
        if self._doc is not None:
            self._doc.close()
        self._doc = fitz.open(path)
        self._page_index = 0
        self._overlay.reset()
        self._render_page()
        self.pdf_loaded.emit()

    def clear(self) -> None:
        if self._doc is not None:
            self._doc.close()
            self._doc = None
        self._source_pixmap = None
        self._image_label.clear()
        self._overlay.reset()
        self._overlay.hide()

    def begin_table_region(self) -> None:
        if self._doc is None:
            return
        self._overlay.begin_region_draw()
        self._sync_overlay_geometry()
        self._overlay.show()
        self._overlay.raise_()

    def draw_grid(self) -> None:
        self._overlay.set_grid_dimensions(self.num_rows(), self.num_cols())
        self._overlay.set_visible_grid(True)
        self._sync_overlay_geometry()

    def draw_column_grid(self, col_fracs: list[float]) -> None:
        """Show vertical dividers only (project-saved column ratios)."""
        self._overlay.set_row_fracs([])
        self._overlay.set_col_fracs(col_fracs)
        self._overlay.set_visible_grid(True)
        cols = max(len(col_fracs) - 1, 1)
        self._cols_spin.blockSignals(True)
        self._cols_spin.setValue(cols)
        self._cols_spin.blockSignals(False)
        self._sync_overlay_geometry()
        self.dimensions_changed.emit()

    def _on_dimensions_changed(self) -> None:
        if self._overlay.row_fracs() and self._overlay.has_region():
            self._overlay.set_grid_dimensions(self.num_rows(), self.num_cols())
            self._sync_overlay_geometry()
        self.dimensions_changed.emit()

    def _render_page(self) -> None:
        if self._doc is None:
            return
        page = self._doc[self._page_index]
        self._page_size = (page.rect.width, page.rect.height)
        matrix = fitz.Matrix(self._render_scale, self._render_scale)
        pix = page.get_pixmap(matrix=matrix, alpha=False)
        fmt = QImage.Format.Format_RGB888
        image = QImage(pix.samples, pix.width, pix.height, pix.stride, fmt).copy()
        self._source_pixmap = QPixmap.fromImage(image)
        self._apply_fit()

    def _apply_fit(self) -> None:
        if self._source_pixmap is None or self._source_pixmap.isNull():
            return
        area_w = max(self._fit_area.width(), 1)
        area_h = max(self._fit_area.height(), 1)
        pw = self._source_pixmap.width()
        ph = self._source_pixmap.height()
        scale = min(area_w / pw, area_h / ph)
        dw = max(1, int(pw * scale))
        dh = max(1, int(ph * scale))
        scaled = self._source_pixmap.scaled(
            dw,
            dh,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self._image_label.setPixmap(scaled)
        x = (area_w - scaled.width()) // 2
        y = (area_h - scaled.height()) // 2
        self._image_label.setGeometry(x, y, scaled.width(), scaled.height())
        self._sync_overlay_geometry()

    def _sync_overlay_geometry(self) -> None:
        self._overlay.setGeometry(0, 0, self._image_label.width(), self._image_label.height())
        if (
            self._overlay.has_region()
            or self._overlay.row_fracs()
            or self._overlay.is_drawing_region()
            or self._overlay.has_text_rects()
        ):
            self._overlay.show()
        self._overlay.raise_()

    def widget_to_pdf_rect(
        self, x0: float, y0: float, x1: float, y1: float
    ) -> tuple[float, float, float, float]:
        """Map widget coordinates on the page preview to PDF user space."""
        pw, ph = self._page_size
        lw, lh = self._image_label.width(), self._image_label.height()
        if lw <= 0 or lh <= 0:
            return (0, 0, pw, ph)
        sx = pw / lw
        sy = ph / lh
        return (x0 * sx, y0 * sy, x1 * sx, y1 * sy)

    def pdf_rect_to_widget(
        self, x0: float, y0: float, x1: float, y1: float
    ) -> tuple[float, float, float, float]:
        """Map PDF user space to widget coordinates on the page preview."""
        pw, ph = self._page_size
        lw, lh = self._image_label.width(), self._image_label.height()
        if pw <= 0 or ph <= 0:
            return (x0, y0, x1, y1)
        sx = lw / pw
        sy = lh / ph
        return (x0 * sx, y0 * sy, x1 * sx, y1 * sy)

    def pdf_words_to_widget_rects(self, words: list[Word]) -> list[tuple[float, float, float, float]]:
        rects: list[tuple[float, float, float, float]] = []
        for w in words:
            rects.append(self.pdf_rect_to_widget(w.x0, w.y0, w.x1, w.y1))
        return rects

    def region_words_to_widget_rects(
        self,
        words: list[Word],
        origin: tuple[float, float],
        size: tuple[float, float],
    ) -> list[tuple[float, float, float, float]]:
        """Map words in table-region pixel space to widget rects."""
        bounds = self._overlay.region_widget_bounds()
        if bounds is None:
            return []
        rx0, ry0, rx1, ry1 = bounds
        rw_px = rx1 - rx0
        rh_px = ry1 - ry0
        ox, oy = origin
        rw, rh = size
        if rw <= 0 or rh <= 0 or rw_px <= 0 or rh_px <= 0:
            return []
        rects: list[tuple[float, float, float, float]] = []
        for w in words:
            wx0 = rx0 + ((w.x0 - ox) / rw) * rw_px
            wy0 = ry0 + ((w.y0 - oy) / rh) * rh_px
            wx1 = rx0 + ((w.x1 - ox) / rw) * rw_px
            wy1 = ry0 + ((w.y1 - oy) / rh) * rh_px
            rects.append((wx0, wy0, wx1, wy1))
        return rects

    def table_region_pdf_rect(self) -> tuple[float, float, float, float] | None:
        """PDF bounds of the normalised table region, or None."""
        norm = self._overlay.region_norm()
        if norm is None:
            return None
        x0n, y0n, x1n, y1n = norm
        pw, ph = self._page_size
        lw, lh = self._image_label.width(), self._image_label.height()
        if lw <= 0 or lh <= 0:
            return None
        wx0, wy0 = x0n * lw, y0n * lh
        wx1, wy1 = x1n * lw, y1n * lh
        return self.widget_to_pdf_rect(wx0, wy0, wx1, wy1)

    def refresh_overlay(self) -> None:
        self._sync_overlay_geometry()

    def set_inferred_grid(self, row_fracs: list[float], col_fracs: list[float]) -> None:
        self._overlay.set_row_fracs(row_fracs)
        self._overlay.set_col_fracs(col_fracs)
        self._overlay.set_visible_grid(True)
        self._sync_overlay_geometry()
        rows = max(len(row_fracs) - 1, 1)
        cols = max(len(col_fracs) - 1, 1)
        self._rows_spin.blockSignals(True)
        self._cols_spin.blockSignals(True)
        self._rows_spin.setValue(rows)
        self._cols_spin.setValue(cols)
        self._rows_spin.blockSignals(False)
        self._cols_spin.blockSignals(False)
        self.dimensions_changed.emit()

    def changeEvent(self, event) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.Type.ParentChange:
            self._apply_fit()
