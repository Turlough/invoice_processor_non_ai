"""Centre panel: PDF page image with row/col controls and grid overlay."""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

import pymupdf as fitz
from PyQt6.QtCore import QEvent, QPointF, Qt, pyqtSignal
from PyQt6.QtGui import QImage, QPainter, QPixmap, QTransform
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
from invoice_app.pdf_text import Word, words_in_rect

# Smaller than this, scan skew is noise and the preview stays upright.
_SKEW_EPSILON = 0.05


@dataclass(frozen=True)
class _ViewFrame:
    """How the current preview pixmap sits on the upright page."""

    skew_deg: float = 0.0
    # Qt rotation applied to the pixmap. Positive is clockwise in y-down space.
    angle_deg: float = 0.0
    unrotated: tuple[int, int] = (1, 1)
    rotated: tuple[int, int] = (1, 1)
    min_x: float = 0.0
    min_y: float = 0.0


def _rotation_bounds(
    width: float, height: float, angle_deg: float
) -> tuple[int, int, float, float]:
    """Size of a rotated image, and the shift that pins its bounding box at (0, 0).

    ``angle_deg`` uses Qt's rotation (positive is clockwise when y grows downward).
    Every source corner lands inside the returned box, so the page is not cropped.
    """
    if abs(angle_deg) < 1e-6:
        return max(1, int(round(width))), max(1, int(round(height))), 0.0, 0.0
    transform = QTransform()
    transform.rotate(angle_deg)
    corners = [
        transform.map(QPointF(x, y))
        for x, y in ((0.0, 0.0), (width, 0.0), (width, height), (0.0, height))
    ]
    min_x = min(point.x() for point in corners)
    min_y = min(point.y() for point in corners)
    max_x = max(point.x() for point in corners)
    max_y = max(point.y() for point in corners)
    # +1 keeps a corner that lands on the exclusive edge inside the pixmap.
    dest_w = max(1, math.ceil(max_x - min_x - 1e-9)) + 1
    dest_h = max(1, math.ceil(max_y - min_y - 1e-9)) + 1
    return dest_w, dest_h, min_x, min_y


def _rotate_pixmap(pixmap: QPixmap, angle_deg: float) -> tuple[QPixmap, float, float]:
    """Rotate ``pixmap`` and expand the canvas so no corner is clipped."""
    dest_w, dest_h, min_x, min_y = _rotation_bounds(
        pixmap.width(), pixmap.height(), angle_deg
    )
    if abs(angle_deg) < 1e-6:
        return pixmap, min_x, min_y
    rotated = QPixmap(dest_w, dest_h)
    rotated.fill(Qt.GlobalColor.white)
    painter = QPainter(rotated)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
    # Transforms are prepended, so the later translate runs after the rotation:
    # device = rotate(source) - (min_x, min_y).
    painter.rotate(angle_deg)
    painter.translate(-min_x, -min_y)
    painter.drawPixmap(0, 0, pixmap)
    painter.end()
    return rotated, min_x, min_y


class _FitArea(QWidget):
    """Expands to fill the panel and keeps the page scaled to fit."""

    resized = pyqtSignal()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self.resized.emit()


class PdfPanel(QWidget):
    pdf_loaded = pyqtSignal()
    page_changed = pyqtSignal()
    region_defined = pyqtSignal()
    dimensions_changed = pyqtSignal()
    # Page image size or position changed (zoom, resize, new render).
    preview_changed = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._doc: fitz.Document | None = None
        self._page_index = 0
        self._page_regions: dict[int, tuple[float, float, float, float]] = {}
        # Skew (degrees) of the coordinate space each stored region was authored in.
        # 0 is the upright page, which is also how regions are saved to disk.
        self._region_space: dict[int, float] = {}
        # OCR skew-deg per page. The preview rotates by the opposite angle.
        self._deskew_angles: dict[int, float] = {}
        self._frame = _ViewFrame()
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
        self._overlay.region_changed.connect(self._on_overlay_region_changed)

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
        self._remember_region_for_page(self._page_index)
        self._page_index = index
        self._render_page()
        self._apply_region_for_current_page(clear_grid=True)
        self._update_page_controls()
        self.page_changed.emit()

    def set_page_regions(
        self, regions: dict[int, tuple[float, float, float, float]]
    ) -> None:
        self._page_regions = dict(regions)
        self._region_space = {}

    def set_upright_page_region(
        self, page_index: int, region: tuple[float, float, float, float] | None
    ) -> None:
        """Set or clear the upright-page normalized table region for ``page_index``."""
        if region is None:
            self._page_regions.pop(page_index, None)
            self._region_space.pop(page_index, None)
        else:
            self._page_regions[page_index] = region
            self._region_space[page_index] = 0.0
        if page_index == self._page_index:
            self._apply_region_for_current_page(clear_grid=region is None)

    def set_deskew_angles(self, angles: dict[int, float]) -> None:
        """Rotate pages by the opposite of each OCR ``skew-deg``.

        An empty dict clears deskew. The preview canvas grows so the rotated
        page is not cropped; Auto zoom then fits that canvas in the panel.
        """
        self._deskew_angles = dict(angles)
        if self._doc is None:
            return
        self._remember_region_for_page(self._page_index)
        self._render_page()
        self._apply_region_for_current_page(clear_grid=False)

    def deskew_angles(self) -> dict[int, float]:
        """Page index → OCR ``skew-deg`` currently applied to the preview."""
        return dict(self._deskew_angles)

    def page_skew_deg(self) -> float | None:
        """OCR skew for the current page, or None when no angle was supplied."""
        if self._page_index not in self._deskew_angles:
            return None
        return self._deskew_angles[self._page_index]

    def is_deskewed(self) -> bool:
        return abs(self._frame.angle_deg) >= 1e-6

    def zoom_to_fit(self) -> None:
        """Fit the whole preview, including any expanded deskew margin, in the panel."""
        self._set_zoom_mode("auto")

    def upright_page_region(self) -> tuple[float, float, float, float] | None:
        """Table region in upright-page fractions, for the layout sidecar."""
        region = self._page_regions.get(self._page_index)
        if region is None:
            return None
        space = self._region_space.get(self._page_index, 0.0)
        if abs(space) < _SKEW_EPSILON:
            return region
        return self._norm_between(region, space, 0.0)

    def page_regions(self) -> dict[int, tuple[float, float, float, float]]:
        return dict(self._page_regions)

    def _remember_region_for_page(self, page_index: int) -> None:
        # While deskewed, the overlay shows a projection of the stored region.
        # Copying it back would replace the upright region with that projection.
        if abs(self._frame.angle_deg) >= 1e-6:
            return
        norm = self._overlay.region_norm()
        if norm is not None:
            self._page_regions[page_index] = norm
            self._region_space[page_index] = 0.0
        else:
            self._page_regions.pop(page_index, None)
            self._region_space.pop(page_index, None)

    def _apply_region_for_current_page(self, *, clear_grid: bool) -> None:
        if clear_grid:
            self._overlay.clear_text_rects()
            self._overlay.set_row_fracs([])
            self._overlay.set_col_fracs([])
            self._overlay.set_visible_grid(False)
        self._overlay.set_region_norm(self._view_region(self._page_index))
        self._sync_overlay_geometry()

    def _on_overlay_region_changed(self) -> None:
        norm = self._overlay.region_norm()
        if norm is not None:
            self._page_regions[self._page_index] = norm
            self._region_space[self._page_index] = self._frame.skew_deg
        self._sync_overlay_geometry()
        self.region_defined.emit()

    def _view_region(
        self, page_index: int
    ) -> tuple[float, float, float, float] | None:
        region = self._page_regions.get(page_index)
        if region is None:
            return None
        space = self._region_space.get(page_index, 0.0)
        return self._norm_between(region, space, self._frame.skew_deg)

    def _norm_between(
        self,
        region: tuple[float, float, float, float],
        from_skew: float,
        to_skew: float,
    ) -> tuple[float, float, float, float]:
        """Map an axis-aligned preview rect between two skew orientations."""
        if abs(from_skew - to_skew) < _SKEW_EPSILON:
            return region
        uw, uh = self._frame.unrotated
        if uw < 2 or uh < 2:
            return region
        xs: list[float] = []
        ys: list[float] = []
        x0, y0, x1, y1 = region
        for x, y in ((x0, y0), (x1, y0), (x1, y1), (x0, y1)):
            sx, sy = self._norm_to_source(x, y, from_skew)
            nx, ny = self._source_to_norm(sx, sy, to_skew)
            xs.append(nx)
            ys.append(ny)
        left = max(0.0, min(xs))
        right = min(1.0, max(xs))
        top = max(0.0, min(ys))
        bottom = min(1.0, max(ys))
        if right - left < 0.01 or bottom - top < 0.01:
            return region
        return (left, top, right, bottom)

    def _skew_angle(self, skew_deg: float) -> float:
        if abs(skew_deg) < _SKEW_EPSILON:
            return 0.0
        # Positive OCR skew slopes down to the right (clockwise). Rotate
        # the other way so baselines come out horizontal.
        return -skew_deg

    def _norm_to_source(self, nx: float, ny: float, skew_deg: float) -> tuple[float, float]:
        uw, uh = self._frame.unrotated
        angle = self._skew_angle(skew_deg)
        dest_w, dest_h, min_x, min_y = _rotation_bounds(uw, uh, angle)
        dx, dy = nx * dest_w, ny * dest_h
        if abs(angle) < 1e-6:
            return dx, dy
        transform = QTransform()
        transform.rotate(angle)
        inverse, invertible = transform.inverted()
        if not invertible:
            return dx, dy
        point = inverse.map(QPointF(dx + min_x, dy + min_y))
        return point.x(), point.y()

    def _source_to_norm(self, sx: float, sy: float, skew_deg: float) -> tuple[float, float]:
        uw, uh = self._frame.unrotated
        angle = self._skew_angle(skew_deg)
        dest_w, dest_h, min_x, min_y = _rotation_bounds(uw, uh, angle)
        if dest_w <= 0 or dest_h <= 0:
            return 0.0, 0.0
        if abs(angle) < 1e-6:
            dx, dy = sx, sy
        else:
            transform = QTransform()
            transform.rotate(angle)
            point = transform.map(QPointF(sx, sy))
            dx, dy = point.x() - min_x, point.y() - min_y
        return dx / dest_w, dy / dest_h

    def page_size_pdf(self) -> tuple[float, float]:
        return self._page_size

    def overlay(self) -> GridOverlay:
        return self._overlay

    def load_pdf(self, path: Path) -> None:
        if self._doc is not None:
            self._doc.close()
        self._doc = fitz.open(path)
        self._page_index = 0
        self._page_regions = {}
        self._region_space = {}
        self._deskew_angles = {}
        self._frame = _ViewFrame()
        self._overlay.reset()
        self._zoom_mode = "auto"
        self._update_zoom_buttons()
        self._render_page()
        self._apply_region_for_current_page(clear_grid=True)
        self._update_page_controls()
        self.pdf_loaded.emit()

    def clear(self) -> None:
        if self._doc is not None:
            self._doc.close()
            self._doc = None
        self._page_index = 0
        self._page_regions = {}
        self._region_space = {}
        self._deskew_angles = {}
        self._frame = _ViewFrame()
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
        base = QPixmap.fromImage(image)
        skew = self._deskew_angles.get(self._page_index, 0.0)
        angle = self._skew_angle(skew)
        if abs(angle) < 1e-6:
            rotated = base
            min_x, min_y = 0.0, 0.0
            skew = 0.0
        else:
            rotated, min_x, min_y = _rotate_pixmap(base, angle)
        self._frame = _ViewFrame(
            skew_deg=skew,
            angle_deg=angle,
            unrotated=(pix.width, pix.height),
            rotated=(rotated.width(), rotated.height()),
            min_x=min_x,
            min_y=min_y,
        )
        self._source_pixmap = rotated
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

    def _widget_to_display(self, wx: float, wy: float) -> tuple[float, float]:
        label_w = self._image_label.width()
        label_h = self._image_label.height()
        disp_w, disp_h = self._frame.rotated
        if label_w <= 0 or label_h <= 0 or disp_w <= 0 or disp_h <= 0:
            return wx, wy
        return wx / label_w * disp_w, wy / label_h * disp_h

    def _display_to_widget(self, dx: float, dy: float) -> tuple[float, float]:
        label_w = self._image_label.width()
        label_h = self._image_label.height()
        disp_w, disp_h = self._frame.rotated
        if disp_w <= 0 or disp_h <= 0:
            return dx, dy
        return dx / disp_w * label_w, dy / disp_h * label_h

    def _display_to_source(self, dx: float, dy: float) -> tuple[float, float]:
        frame = self._frame
        if abs(frame.angle_deg) < 1e-6:
            return dx, dy
        transform = QTransform()
        transform.rotate(frame.angle_deg)
        inverse, invertible = transform.inverted()
        if not invertible:
            return dx, dy
        point = inverse.map(QPointF(dx + frame.min_x, dy + frame.min_y))
        return point.x(), point.y()

    def _source_to_display(self, sx: float, sy: float) -> tuple[float, float]:
        frame = self._frame
        if abs(frame.angle_deg) < 1e-6:
            return sx, sy
        transform = QTransform()
        transform.rotate(frame.angle_deg)
        point = transform.map(QPointF(sx, sy))
        return point.x() - frame.min_x, point.y() - frame.min_y

    def widget_point_to_pdf(self, wx: float, wy: float) -> tuple[float, float]:
        dx, dy = self._widget_to_display(wx, wy)
        sx, sy = self._display_to_source(dx, dy)
        page_w, page_h = self._page_size
        src_w, src_h = self._frame.unrotated
        if src_w <= 0 or src_h <= 0:
            return 0.0, 0.0
        return sx / src_w * page_w, sy / src_h * page_h

    def pdf_point_to_widget(self, x: float, y: float) -> tuple[float, float]:
        page_w, page_h = self._page_size
        src_w, src_h = self._frame.unrotated
        if page_w <= 0 or page_h <= 0:
            return x, y
        dx, dy = self._source_to_display(x / page_w * src_w, y / page_h * src_h)
        return self._display_to_widget(dx, dy)

    def widget_to_pdf_rect(
        self, x0: float, y0: float, x1: float, y1: float
    ) -> tuple[float, float, float, float]:
        """Map a preview rectangle to the upright PDF rectangle that contains it."""
        points = [
            self.widget_point_to_pdf(x, y)
            for x, y in ((x0, y0), (x1, y0), (x1, y1), (x0, y1))
        ]
        xs = [point[0] for point in points]
        ys = [point[1] for point in points]
        return (min(xs), min(ys), max(xs), max(ys))

    def pdf_rect_to_widget(
        self, x0: float, y0: float, x1: float, y1: float
    ) -> tuple[float, float, float, float]:
        """Map an upright PDF rectangle to the preview rectangle that contains it."""
        points = [
            self.pdf_point_to_widget(x, y)
            for x, y in ((x0, y0), (x1, y0), (x1, y1), (x0, y1))
        ]
        xs = [point[0] for point in points]
        ys = [point[1] for point in points]
        return (min(xs), min(ys), max(xs), max(ys))

    def text_in_widget_rect(
        self, words: list[Word], x0: float, y0: float, x1: float, y1: float
    ) -> str:
        """Text whose centres fall in a preview rectangle, in deskewed reading order."""
        projected = [
            Word(*self.pdf_rect_to_widget(word.x0, word.y0, word.x1, word.y1), word.text)
            for word in words
        ]
        return words_in_rect(projected, x0, y0, x1, y1)

    def raster_widget_rect(
        self, x0: float, y0: float, x1: float, y1: float
    ) -> tuple[object, tuple[float, float], tuple[float, float]]:
        """Rasterise a preview rectangle at OCR resolution, deskew included.

        ``origin`` and ``size`` locate the tight rectangle inside a few points
        of padding, so grid fractions still refer to that rectangle.
        """
        if self._doc is None:
            raise ValueError("No PDF is open.")
        import numpy as np

        page = self._doc[self._page_index]
        long_side = max(page.rect.width, page.rect.height, 1.0)
        zoom = min(6.0, max(2.0, 2200.0 / long_side))
        if long_side * zoom > 4000.0:
            zoom = 4000.0 / long_side
        pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
        image = QImage(
            pix.samples, pix.width, pix.height, pix.stride, QImage.Format.Format_RGB888
        ).copy()
        pixmap = QPixmap.fromImage(image)
        angle = self._frame.angle_deg
        if abs(angle) >= 1e-6:
            pixmap, _, _ = _rotate_pixmap(pixmap, angle)
        rendered = pixmap.toImage().convertToFormat(QImage.Format.Format_RGB888)
        width, height = rendered.width(), rendered.height()
        label_w = max(self._image_label.width(), 1)
        label_h = max(self._image_label.height(), 1)
        nx0, nx1 = sorted((x0 / label_w, x1 / label_w))
        ny0, ny1 = sorted((y0 / label_h, y1 / label_h))
        nx0, ny0 = max(0.0, nx0), max(0.0, ny0)
        nx1, ny1 = min(1.0, nx1), min(1.0, ny1)
        ix0 = max(0, min(width - 1, math.floor(nx0 * width)))
        iy0 = max(0, min(height - 1, math.floor(ny0 * height)))
        ix1 = max(ix0 + 1, min(width, math.ceil(nx1 * width)))
        iy1 = max(iy0 + 1, min(height, math.ceil(ny1 * height)))
        pad = int(round(8.0 * zoom))
        left = max(0, ix0 - pad)
        top = max(0, iy0 - pad)
        right = min(width, ix1 + pad)
        bottom = min(height, iy1 + pad)
        crop = rendered.copy(left, top, right - left, bottom - top).convertToFormat(
            QImage.Format.Format_RGB888
        )
        pointer = crop.bits()
        pointer.setsize(crop.sizeInBytes())
        raw = np.frombuffer(pointer, dtype=np.uint8).reshape(crop.height(), crop.bytesPerLine())
        rgb = np.ascontiguousarray(
            raw[:, : crop.width() * 3].reshape(crop.height(), crop.width(), 3)
        )
        origin = (float(ix0 - left), float(iy0 - top))
        size = (float(ix1 - ix0), float(iy1 - iy0))
        return rgb, origin, size

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

    def refresh_page_layout(self) -> None:
        """Apply the stored table region for the current page (clears grid lines)."""
        self._apply_region_for_current_page(clear_grid=True)

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
