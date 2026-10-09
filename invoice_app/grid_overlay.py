"""Draggable row/column grid drawn over the PDF preview."""

from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal, QRectF
from PyQt6.QtGui import QBrush, QMouseEvent, QPainter, QPen
from PyQt6.QtWidgets import QWidget


class GridOverlay(QWidget):
    """Table region rectangle, then horizontal/vertical divider lines inside it."""

    grid_changed = pyqtSignal()
    region_changed = pyqtSignal()

    HIT_PX = 6

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)
        self.setMouseTracking(True)
        self._row_fracs: list[float] = []
        self._col_fracs: list[float] = []
        self._drag: tuple[str, int] | None = None
        self._grid_visible = False
        # Normalized region on the page preview (0..1).
        self._region: tuple[float, float, float, float] | None = None
        self._draw_region_active = False
        self._region_anchor: tuple[float, float] | None = None
        self._region_drag: tuple[float, float] | None = None

    def reset(self) -> None:
        self._row_fracs = []
        self._col_fracs = []
        self._grid_visible = False
        self._region = None
        self._draw_region_active = False
        self._region_anchor = None
        self._region_drag = None
        self._drag = None
        self.update()

    def begin_region_draw(self) -> None:
        self._draw_region_active = True
        self._region_anchor = None
        self._region_drag = None
        self.update()

    def has_region(self) -> bool:
        return self._region is not None

    def is_drawing_region(self) -> bool:
        return self._draw_region_active

    def region_norm(self) -> tuple[float, float, float, float] | None:
        return self._region

    def set_grid_dimensions(self, rows: int, cols: int) -> None:
        if rows < 1 or cols < 1:
            self._row_fracs = []
            self._col_fracs = []
            self.update()
            return
        self._row_fracs = [i / rows for i in range(rows + 1)]
        self._col_fracs = [i / cols for i in range(cols + 1)]
        self.update()
        self.grid_changed.emit()

    def set_visible_grid(self, visible: bool) -> None:
        self._grid_visible = visible
        self.update()

    def row_fracs(self) -> list[float]:
        return list(self._row_fracs)

    def col_fracs(self) -> list[float]:
        return list(self._col_fracs)

    def _region_rect_px(self) -> QRectF | None:
        if self._region is None:
            return None
        w, h = self.width(), self.height()
        x0, y0, x1, y1 = self._region
        return QRectF(x0 * w, y0 * h, (x1 - x0) * w, (y1 - y0) * h)

    def _rubber_rect_px(self) -> QRectF | None:
        if self._region_anchor is None or self._region_drag is None:
            return None
        w, h = max(self.width(), 1), max(self.height(), 1)
        x0 = self._region_anchor[0] * w
        y0 = self._region_anchor[1] * h
        x1 = self._region_drag[0] * w
        y1 = self._region_drag[1] * h
        return QRectF(min(x0, x1), min(y0, y1), abs(x1 - x0), abs(y1 - y0))

    def cell_widget_rect(self, row: int, col: int) -> tuple[float, float, float, float]:
        """Widget-space bounds for a grid cell (requires region + grid)."""
        region = self._region_rect_px()
        if region is None or not self._row_fracs or not self._col_fracs:
            return (0.0, 0.0, 0.0, 0.0)
        x0 = region.x() + self._col_fracs[col] * region.width()
        x1 = region.x() + self._col_fracs[col + 1] * region.width()
        y0 = region.y() + self._row_fracs[row] * region.height()
        y1 = region.y() + self._row_fracs[row + 1] * region.height()
        return (x0, y0, x1, y1)

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        region_pen = QPen(Qt.GlobalColor.blue, 2, Qt.PenStyle.SolidLine)
        painter.setPen(region_pen)
        painter.setBrush(QBrush(Qt.GlobalColor.transparent))

        rubber = self._rubber_rect_px()
        if rubber is not None and self._draw_region_active:
            painter.drawRect(rubber)
            return

        region = self._region_rect_px()
        if region is not None:
            painter.drawRect(region)

        if not self._grid_visible or region is None or not self._row_fracs or not self._col_fracs:
            return

        grid_pen = QPen(Qt.GlobalColor.red, 2, Qt.PenStyle.DashLine)
        painter.setPen(grid_pen)
        rx, ry, rw, rh = region.x(), region.y(), region.width(), region.height()
        for frac in self._row_fracs:
            y = ry + frac * rh
            painter.drawLine(int(rx), int(y), int(rx + rw), int(y))
        for frac in self._col_fracs:
            x = rx + frac * rw
            painter.drawLine(int(x), int(ry), int(x), int(ry + rh))

    def _hit_row(self, y: int, region: QRectF) -> int | None:
        rh = region.height()
        if rh <= 0:
            return None
        for i in range(1, len(self._row_fracs) - 1):
            line_y = region.y() + self._row_fracs[i] * rh
            if abs(y - line_y) <= self.HIT_PX:
                return i
        return None

    def _hit_col(self, x: int, region: QRectF) -> int | None:
        rw = region.width()
        if rw <= 0:
            return None
        for i in range(1, len(self._col_fracs) - 1):
            line_x = region.x() + self._col_fracs[i] * rw
            if abs(x - line_x) <= self.HIT_PX:
                return i
        return None

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            return
        pos = event.position()
        w, h = max(self.width(), 1), max(self.height(), 1)
        nx, ny = pos.x() / w, pos.y() / h

        if self._draw_region_active:
            self._region_anchor = (nx, ny)
            self._region_drag = (nx, ny)
            self.update()
            return

        if not self._grid_visible or self._region is None:
            return

        region = self._region_rect_px()
        assert region is not None
        col_i = self._hit_col(int(pos.x()), region)
        row_i = self._hit_row(int(pos.y()), region)
        if col_i is not None:
            self._drag = ("col", col_i)
        elif row_i is not None:
            self._drag = ("row", row_i)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        pos = event.position()
        w, h = max(self.width(), 1), max(self.height(), 1)
        nx, ny = pos.x() / w, pos.y() / h

        if self._draw_region_active and self._region_anchor is not None:
            self._region_drag = (nx, ny)
            self.update()
            return

        if self._drag is None or not self._grid_visible or self._region is None:
            return

        region = self._region_rect_px()
        assert region is not None
        kind, index = self._drag
        if kind == "row":
            rh = max(region.height(), 1)
            frac = (pos.y() - region.y()) / rh
            frac = max(0.0, min(1.0, frac))
            lo = self._row_fracs[index - 1] + 0.02
            hi = self._row_fracs[index + 1] - 0.02
            self._row_fracs[index] = max(lo, min(hi, frac))
        else:
            rw = max(region.width(), 1)
            frac = (pos.x() - region.x()) / rw
            frac = max(0.0, min(1.0, frac))
            lo = self._col_fracs[index - 1] + 0.02
            hi = self._col_fracs[index + 1] - 0.02
            self._col_fracs[index] = max(lo, min(hi, frac))
        self.update()
        self.grid_changed.emit()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if self._draw_region_active and self._region_anchor is not None and self._region_drag is not None:
            x0, y0 = self._region_anchor
            x1, y1 = self._region_drag
            left, right = sorted((x0, x1))
            top, bottom = sorted((y0, y1))
            if right - left > 0.01 and bottom - top > 0.01:
                self._region = (left, top, right, bottom)
                self._row_fracs = []
                self._col_fracs = []
                self._grid_visible = False
                self.region_changed.emit()
            self._draw_region_active = False
            self._region_anchor = None
            self._region_drag = None
            self.update()
            return
        self._drag = None
