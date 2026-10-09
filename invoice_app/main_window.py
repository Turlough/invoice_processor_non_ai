"""Main window: file list, PDF preview, line-item table."""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QApplication,
    QFileDialog,
    QHBoxLayout,
    QMainWindow,
    QMessageBox,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
    QListWidget,
    QListWidgetItem,
)

from invoice_app.pdf_panel import PdfPanel
from invoice_app.pdf_text import words_in_rect, words_on_page


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Invoice Processor (PoC)")
        self.resize(1280, 800)

        self._folder: Path | None = None

        self._file_list = QListWidget()
        self._file_list.currentItemChanged.connect(self._on_file_selected)

        self._pdf_panel = PdfPanel()
        self._pdf_panel.pdf_loaded.connect(self._resize_table)
        self._pdf_panel.overlay().grid_changed.connect(self._resize_table)
        self._pdf_panel.dimensions_changed.connect(self._resize_table)

        self._table = QTableWidget()
        self._table.setAlternatingRowColors(True)

        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.addWidget(self._file_list)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(left)
        splitter.addWidget(self._pdf_panel)
        splitter.addWidget(self._table)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 3)
        splitter.setStretchFactor(2, 2)

        container = QWidget()
        layout = QHBoxLayout(container)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.addWidget(splitter)
        self.setCentralWidget(container)

        self._build_menus()
        self._resize_table()

    def _build_menus(self) -> None:
        file_menu = self.menuBar().addMenu("&File")
        load_action = file_menu.addAction("&Load folder…")
        load_action.triggered.connect(self._load_folder)
        file_menu.addSeparator()
        quit_action = file_menu.addAction("E&xit")
        quit_action.triggered.connect(QApplication.instance().quit)

        layout_menu = self.menuBar().addMenu("&Layout")
        table_region = layout_menu.addAction("&Table region…")
        table_region.triggered.connect(self._table_region)
        draw_grid = layout_menu.addAction("&Draw grid")
        draw_grid.triggered.connect(self._draw_grid)

        read_menu = self.menuBar().addMenu("&Read")
        line_items = read_menu.addAction("&Line items")
        line_items.triggered.connect(self._read_line_items)

    def _load_folder(self) -> None:
        directory = QFileDialog.getExistingDirectory(self, "Select invoice folder")
        if not directory:
            return
        self._folder = Path(directory)
        self._file_list.clear()
        pdfs = sorted(self._folder.glob("*.pdf")) + sorted(self._folder.glob("*.PDF"))
        seen: set[str] = set()
        for path in pdfs:
            key = str(path.resolve()).lower()
            if key in seen:
                continue
            seen.add(key)
            item = QListWidgetItem(path.name)
            item.setData(Qt.ItemDataRole.UserRole, str(path))
            self._file_list.addItem(item)
        if self._file_list.count():
            self._file_list.setCurrentRow(0)

    def _on_file_selected(self, current: QListWidgetItem | None, _previous) -> None:
        if current is None:
            self._pdf_panel.clear()
            return
        path = Path(current.data(Qt.ItemDataRole.UserRole))
        try:
            self._pdf_panel.load_pdf(path)
        except Exception as exc:
            QMessageBox.warning(self, "Open PDF", f"Could not open {path.name}:\n{exc}")

    def _table_region(self) -> None:
        if self._pdf_panel.document() is None:
            QMessageBox.information(self, "Table region", "Load a PDF first.")
            return
        self._pdf_panel.begin_table_region()

    def _draw_grid(self) -> None:
        if self._pdf_panel.document() is None:
            QMessageBox.information(self, "Draw grid", "Load a PDF first.")
            return
        if not self._pdf_panel.overlay().has_region():
            QMessageBox.information(
                self,
                "Draw grid",
                "Draw the line-item table area first (Layout → Table region…).",
            )
            return
        self._pdf_panel.draw_grid()
        self._resize_table()

    def _resize_table(self) -> None:
        rows = self._pdf_panel.num_rows()
        cols = self._pdf_panel.num_cols()
        self._table.setRowCount(rows)
        self._table.setColumnCount(cols)
        headers = [f"Col {c + 1}" for c in range(cols)]
        self._table.setHorizontalHeaderLabels(headers)
        for r in range(rows):
            self._table.setVerticalHeaderItem(r, QTableWidgetItem(str(r + 1)))

    def _read_line_items(self) -> None:
        doc = self._pdf_panel.document()
        if doc is None:
            QMessageBox.information(self, "Read line items", "Load a PDF first.")
            return
        overlay = self._pdf_panel.overlay()
        if not overlay.row_fracs():
            QMessageBox.information(
                self,
                "Read line items",
                "Draw a grid first (Layout → Draw grid) and align it with the line items.",
            )
            return

        words = words_on_page(doc, 0)
        if not words:
            QMessageBox.warning(
                self,
                "Read line items",
                "No text found on this page. The PDF may be image-only without an OCR text layer.",
            )
            return

        row_fracs = overlay.row_fracs()
        col_fracs = overlay.col_fracs()
        rows = len(row_fracs) - 1
        cols = len(col_fracs) - 1

        for r in range(rows):
            for c in range(cols):
                x0, y0, x1, y1 = overlay.cell_widget_rect(r, c)
                px0, py0, px1, py1 = self._pdf_panel.widget_to_pdf_rect(x0, y0, x1, y1)
                text = words_in_rect(words, px0, py0, px1, py1)
                self._table.setItem(r, c, QTableWidgetItem(text))
