"""Centre panel: PDF page image with row/col controls and grid overlay."""

from __future__ import annotations

from pathlib import Path

import pymupdf as fitz
from PyQt6.QtCore import Qt, pyqtSignal, QEvent
from PyQt6.QtGui import QImage, QPixmap
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QSpinBox,
    QToolButton,
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
    page_changed = pyqtSignal()
    dimensions_changed = pyqtSignal()
    # Page image size or position changed (zoom, resize, new render).
    preview_changed = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._doc: fitz.Document | None = None
        self._page_index = 0
        self._page_size: tuple[float, float] = (1.0, 1.0)
        self._render_scale = 2.0
        self._source_pixmap: QPixmap | None = None
        self._zoom_mode = "auto"  # "auto" | "height" | "width" | "manual"
        self._zoom_factor = 1.0  # pixmap-pixel scale, used in "manual" mode

        self._page_prev = QToolButton()
        self._page_prev.setArrowType(Qt.ArrowType.LeftArrow)
        self._page_prev.setToolTip("Previous page")
        self._page_next = QToolButton()
        self._page_next.setArrowType(Qt.ArrowType.RightArrow)
        self._page_next.setToolTip("Next page")
        self._page_spin = QSpinBox()
        self._page_spin.setPrefix("Page ")
        self._page_spin.setRange(0, 0)

        self._zoom_auto = self._zoom_button("Auto", "Fit the whole page")
        self._zoom_height = self._zoom_button("Height", "Fit the page height")
        self._zoom_width = self._zoom_button("Width", "Fit the page width")
        self._zoom_out = QToolButton()
        self._zoom_out.setText("−")
        self._zoom_out.setToolTip("Zoom out")
        self._zoom_in = QToolButton()
        self._zoom_in.setText("+")
        self._zoom_in.setToolTip("Zoom in")
        self._update_zoom_buttons()

        nav = QHBoxLayout()
        nav.addWidget(self._page_prev)
        nav.addWidget(self._page_spin)
        nav.addWidget(self._page_next)
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.VLine)
        sep.setFrameShadow(QFrame.Shadow.Sunken)
        nav.addWidget(sep)
        nav.addWidget(self._zoom_auto)
        nav.addWidget(self._zoom_height)
        nav.addWidget(self._zoom_width)
        nav.addWidget(self._zoom_out)
        nav.addWidget(self._zoom_in)
        nav.addStretch()

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

        self._scroll = QScrollArea(self)
        self._scroll.setWidget(self._fit_area)
        self._scroll.setWidgetResizable(False)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.viewport().installEventFilter(self)

        self._image_label = QLabel(self._fit_area)
        self._image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._image_label.setScaledContents(False)

        self._overlay = GridOverlay(self._image_label)
        self._overlay.hide()

        layout = QVBoxLayout(self)
        layout.addLayout(nav)
        layout.addLayout(controls)
        layout.addWidget(self._scroll, stretch=1)

        self._rows_spin.valueChanged.connect(self._on_dimensions_changed)
        self._cols_spin.valueChanged.connect(self._on_dimensions_changed)
        self._page_prev.clicked.connect(lambda: self.set_page(self._page_index - 1))
        self._page_next.clicked.connect(lambda: self.set_page(self._page_index + 1))
        self._page_spin.valueChanged.connect(lambda value: self.set_page(value - 1))
        self._zoom_auto.clicked.connect(lambda: self._set_zoom_mode("auto"))
        self._zoom_height.clicked.connect(lambda: self._set_zoom_mode("height"))
        self._zoom_width.clicked.connect(lambda: self._set_zoom_mode("width"))
        self._zoom_out.clicked.connect(lambda: self._zoom_step(1 / 1.25))
        self._zoom_in.clicked.connect(lambda: self._zoom_step(1.25))
        self._update_page_controls()

    @staticmethod
    def _zoom_button(text: str, tooltip: str) -> QToolButton:
        button = QToolButton()
        button.setText(text)
        button.setCheckable(True)
        button.setToolTip(tooltip)
        return button

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

    def page_count(self) -> int:
        return self._doc.page_count if self._doc is not None else 0

    def set_page(self, index: int) -> None:
        """Show the 0-based page, clamped to the document."""
        if self._doc is None:
            return
        index = max(0, min(index, self._doc.page_count - 1))
        if index == self._page_index:
            return
        self._page_index = index
        self._render_page()
        self._update_page_controls()
        self.page_changed.emit()

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
        self._zoom_mode = "auto"
        self._update_zoom_buttons()
        self._render_page()
        self._update_page_controls()
        self.pdf_loaded.emit()

    def clear(self) -> None:
        if self._doc is not None:
            self._doc.close()
            self._doc = None
        self._page_index = 0
        self._source_pixmap = None
        self._image_label.clear()
        self._overlay.reset()
        self._overlay.hide()
        self._update_page_controls()

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

    def _update_page_controls(self) -> None:
        count = self.page_count()
        self._page_spin.blockSignals(True)
        if count:
            self._page_spin.setRange(1, count)
            self._page_spin.setValue(self._page_index + 1)
            self._page_spin.setSuffix(f" / {count}")
        else:
            self._page_spin.setRange(0, 0)
            self._page_spin.setSuffix("")
        self._page_spin.blockSignals(False)
        self._page_spin.setEnabled(count > 1)
        self._page_prev.setEnabled(count > 1 and self._page_index > 0)
        self._page_next.setEnabled(count > 1 and self._page_index < count - 1)

    def _set_zoom_mode(self, mode: str) -> None:
        self._zoom_mode = mode
        self._update_zoom_buttons()
        self._apply_fit()

    def _update_zoom_buttons(self) -> None:
        for button, mode in (
            (self._zoom_auto, "auto"),
            (self._zoom_height, "height"),
            (self._zoom_width, "width"),
        ):
            button.blockSignals(True)
            button.setChecked(self._zoom_mode == mode)
            button.blockSignals(False)

    def _zoom_step(self, factor: float) -> None:
        if self._source_pixmap is None or self._source_pixmap.isNull():
            return
        if self._zoom_mode != "manual":
            # Continue from the scale the page is currently shown at.
            vw = max(self._scroll.viewport().width(), 1)
            vh = max(self._scroll.viewport().height(), 1)
            self._zoom_factor = self._current_scale(
                vw,
                vh,
                self._source_pixmap.width(),
                self._source_pixmap.height(),
            )
            self._zoom_mode = "manual"
        self._zoom_factor = min(8.0, max(0.05, self._zoom_factor * factor))
        self._update_zoom_buttons()
        self._apply_fit()

    def _current_scale(self, vw: int, vh: int, pw: int, ph: int) -> float:
        if self._zoom_mode == "width":
            return vw / pw
        if self._zoom_mode == "height":
            return vh / ph
        if self._zoom_mode == "manual":
            return self._zoom_factor
        return min(vw / pw, vh / ph)

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
        vw = max(self._scroll.viewport().width(), 1)
        vh = max(self._scroll.viewport().height(), 1)
        pw = self._source_pixmap.width()
        ph = self._source_pixmap.height()
        scale = self._current_scale(vw, vh, pw, ph)
        dw = max(1, int(pw * scale))
        dh = max(1, int(ph * scale))
        scaled = self._source_pixmap.scaled(
            dw,
            dh,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self._image_label.setPixmap(scaled)
        # Grow the scroll widget past the viewport so scroll bars appear when
        # the zoomed page overflows; centre the label inside it otherwise.
        fw = max(dw, vw)
        fh = max(dh, vh)
        self._fit_area.setFixedSize(fw, fh)
        x = (fw - scaled.width()) // 2
        y = (fh - scaled.height()) // 2
        self._image_label.setGeometry(x, y, scaled.width(), scaled.height())
        self._sync_overlay_geometry()
        self.preview_changed.emit()

    def _sync_overlay_geometry(self) -> None:
        self._overlay.setGeometry(0, 0, self._image_label.width(), self._image_label.height())
        visible = (
            self._overlay.has_region()
            or self._overlay.row_fracs()
            or self._overlay.is_drawing_region()
            or self._overlay.has_text_rects()
        )
        self._overlay.setVisible(visible)
        if visible:
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

    def eventFilter(self, watched, event) -> bool:
        if watched is self._scroll.viewport() and event.type() == QEvent.Type.Resize:
            self._apply_fit()
        return super().eventFilter(watched, event)

    def changeEvent(self, event) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.Type.ParentChange:
            self._apply_fit()
