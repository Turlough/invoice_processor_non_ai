"""Main window: file list, PDF preview, line-item table."""

from __future__ import annotations

import traceback
from pathlib import Path

from PyQt6.QtCore import QMutex, Qt, QThread, QWaitCondition, pyqtSignal
from PyQt6.QtWidgets import (
    QApplication,
    QFileDialog,
    QHBoxLayout,
    QMainWindow,
    QMessageBox,
    QProgressDialog,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
    QListWidget,
    QListWidgetItem,
)

from invoice_app.pdf_ocr import read_grid, region_image
from invoice_app.pdf_panel import PdfPanel
from invoice_app.pdf_text import words_in_rect, words_on_page
from invoice_app.projects import AppState, ConfigError, Project, list_projects


class _OcrGridWorker(QThread):
    """One long-lived thread. PaddleOCR stays on the thread that created it."""

    succeeded = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._mutex = QMutex()
        self._cond = QWaitCondition()
        self._job: tuple | None = None
        self._stop = False

    def submit(
        self,
        image,
        row_fracs: list[float],
        col_fracs: list[float],
        origin: tuple[float, float],
        size: tuple[float, float],
    ) -> None:
        self._mutex.lock()
        self._job = (image, row_fracs, col_fracs, origin, size)
        self._cond.wakeOne()
        self._mutex.unlock()
        if not self.isRunning():
            self.start()

    def shutdown(self) -> None:
        self._mutex.lock()
        self._stop = True
        self._cond.wakeOne()
        self._mutex.unlock()

    def run(self) -> None:
        while True:
            self._mutex.lock()
            while self._job is None and not self._stop:
                self._cond.wait(self._mutex)
            if self._job is None and self._stop:
                self._mutex.unlock()
                return
            job = self._job
            self._job = None
            self._mutex.unlock()
            try:
                image, row_fracs, col_fracs, origin, size = job
                cells = read_grid(image, row_fracs, col_fracs, origin, size)
                self.succeeded.emit(cells)
            except Exception as exc:
                traceback.print_exc()
                self.failed.emit(str(exc))
            self._mutex.lock()
            stop = self._stop and self._job is None
            self._mutex.unlock()
            if stop:
                return


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Invoice Processor (PoC)")
        self.resize(1280, 800)

        self._folder: Path | None = None
        self._project: Project | None = None
        self._state = AppState.load()
        self._ocr_busy = False
        self._ocr_progress: QProgressDialog | None = None
        self._ocr_worker = _OcrGridWorker(self)
        self._ocr_worker.succeeded.connect(self._on_ocr_succeeded)
        self._ocr_worker.failed.connect(self._on_ocr_failed)

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
        self._restore_state()

    def _build_menus(self) -> None:
        file_menu = self.menuBar().addMenu("&File")
        self._project_menu = file_menu.addMenu("&Project")
        self._project_menu.aboutToShow.connect(self._populate_project_menu)
        self._batch_menu = file_menu.addMenu("Load &batch")
        self._batch_menu.aboutToShow.connect(self._populate_batch_menu)
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
        ocr_grid = read_menu.addAction("OCR &grid")
        ocr_grid.triggered.connect(self._ocr_grid)

    def _populate_project_menu(self) -> None:
        self._project_menu.clear()
        try:
            names = list_projects()
        except ConfigError as exc:
            action = self._project_menu.addAction(str(exc))
            action.setEnabled(False)
            return
        if not names:
            action = self._project_menu.addAction("(no projects)")
            action.setEnabled(False)
            return
        for name in names:
            action = self._project_menu.addAction(name)
            action.setCheckable(True)
            action.setChecked(
                self._project is not None and self._project.name == name
            )
            action.triggered.connect(
                lambda _checked=False, n=name: self._select_project(n)
            )

    def _select_project(self, name: str) -> None:
        try:
            project = Project.load(name)
        except (ConfigError, OSError) as exc:
            QMessageBox.warning(self, "Project", str(exc))
            return
        if project.data_directory is None:
            directory = QFileDialog.getExistingDirectory(
                self,
                f"Choose the data directory for project “{name}”",
            )
            if not directory:
                return
            project.set_data_directory(Path(directory))
        self._project = project
        self._state.project = project.name
        self._state.batch = None
        self._state.pdf = None
        self._save_state()
        self.statusBar().showMessage(
            f"Project: {name} — data directory: {project.data_directory}", 8000
        )

    def _populate_batch_menu(self) -> None:
        self._batch_menu.clear()
        project = self._project
        if project is None:
            action = self._batch_menu.addAction("(choose a project first)")
            action.setEnabled(False)
            return
        root = project.data_directory
        if root is None or not root.is_dir():
            action = self._batch_menu.addAction(
                f"(data directory not found: {root})"
            )
            action.setEnabled(False)
            return
        batches = project.list_batches()
        if not batches:
            action = self._batch_menu.addAction("(no batches)")
            action.setEnabled(False)
            return
        for path in batches:
            action = self._batch_menu.addAction(path.name)
            action.setCheckable(True)
            action.setChecked(self._folder is not None and self._folder == path)
            action.triggered.connect(
                lambda _checked=False, p=path: self._load_batch(p)
            )

    def _load_batch(self, folder: Path, select_name: str | None = None) -> None:
        self._folder = folder
        self._file_list.clear()
        pdfs = sorted(folder.glob("*.pdf")) + sorted(folder.glob("*.PDF"))
        seen: set[str] = set()
        for path in pdfs:
            key = str(path.resolve()).lower()
            if key in seen:
                continue
            seen.add(key)
            item = QListWidgetItem(path.name)
            item.setData(Qt.ItemDataRole.UserRole, str(path))
            self._file_list.addItem(item)
        self._state.batch = folder.name
        self._save_state()
        self.statusBar().showMessage(
            f"Batch: {folder.name} — {self._file_list.count()} PDFs", 8000
        )
        row = 0
        if select_name is not None:
            for i in range(self._file_list.count()):
                if self._file_list.item(i).text() == select_name:
                    row = i
                    break
        if self._file_list.count():
            self._file_list.setCurrentRow(row)

    def _save_state(self) -> None:
        try:
            self._state.save()
        except OSError as exc:
            self.statusBar().showMessage(f"Could not save app state: {exc}", 8000)

    def _restore_state(self) -> None:
        """Reopen the last project, batch and PDF. Never prompts or raises."""
        state = self._state
        if not state.project:
            return
        try:
            if state.project not in list_projects():
                return
            project = Project.load(state.project)
        except (ConfigError, OSError):
            return
        self._project = project
        root = project.data_directory
        if root is None or not root.is_dir():
            self.statusBar().showMessage(f"Project: {project.name} (restored)", 8000)
            return
        batches = {p.name: p for p in project.list_batches()}
        folder = batches.get(state.batch or "")
        if folder is None:
            self.statusBar().showMessage(f"Project: {project.name} (restored)", 8000)
            return
        self._load_batch(folder, select_name=state.pdf)

    def _on_file_selected(self, current: QListWidgetItem | None, _previous) -> None:
        if current is None:
            self._pdf_panel.clear()
            if self._state.pdf is not None:
                self._state.pdf = None
                self._save_state()
            return
        path = Path(current.data(Qt.ItemDataRole.UserRole))
        try:
            self._pdf_panel.load_pdf(path)
        except Exception as exc:
            QMessageBox.warning(self, "Open PDF", f"Could not open {path.name}:\n{exc}")
            return
        self._state.pdf = path.name
        self._save_state()

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
                "No text found on this page. Use Read → OCR grid to read the cells from the page image.",
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

    def _ocr_grid(self) -> None:
        if self._ocr_busy:
            return
        doc = self._pdf_panel.document()
        if doc is None:
            QMessageBox.information(self, "OCR grid", "Load a PDF first.")
            return
        overlay = self._pdf_panel.overlay()
        if not overlay.row_fracs():
            QMessageBox.information(
                self,
                "OCR grid",
                "Draw a grid first (Layout → Draw grid) and align it with the line items.",
            )
            return

        rows = len(overlay.row_fracs()) - 1
        cols = len(overlay.col_fracs()) - 1
        x0, y0, _, _ = overlay.cell_widget_rect(0, 0)
        _, _, x1, y1 = overlay.cell_widget_rect(rows - 1, cols - 1)
        pdf_rect = self._pdf_panel.widget_to_pdf_rect(x0, y0, x1, y1)
        try:
            image, origin, size = region_image(doc, 0, pdf_rect)
        except Exception as exc:
            QMessageBox.warning(
                self,
                "OCR grid",
                f"Could not render the table region:\n{exc}",
            )
            return

        self._ocr_busy = True
        dialog = QProgressDialog(
            "Reading the grid with PaddleOCR.\n"
            "The first run downloads models and can take several minutes.",
            None,
            0,
            0,
            self,
        )
        dialog.setWindowTitle("OCR grid")
        dialog.setWindowModality(Qt.WindowModality.WindowModal)
        dialog.setCancelButton(None)
        dialog.setMinimumDuration(0)
        dialog.setMinimumWidth(420)
        dialog.show()
        self._ocr_progress = dialog
        self.statusBar().showMessage("Running PaddleOCR…")
        self._ocr_worker.submit(
            image,
            overlay.row_fracs(),
            overlay.col_fracs(),
            origin,
            size,
        )

    def _close_ocr_progress(self) -> None:
        self._ocr_busy = False
        dialog = self._ocr_progress
        self._ocr_progress = None
        if dialog is not None:
            dialog.close()

    def _on_ocr_succeeded(self, cells: list) -> None:
        self._close_ocr_progress()
        if not cells or self._table.rowCount() != len(cells):
            QMessageBox.information(
                self,
                "OCR grid",
                "The grid changed while OCR was running. Align it and run OCR grid again.",
            )
            return
        if any(self._table.columnCount() != len(row) for row in cells):
            QMessageBox.information(
                self,
                "OCR grid",
                "The grid changed while OCR was running. Align it and run OCR grid again.",
            )
            return

        filled = 0
        for r, row in enumerate(cells):
            for c, text in enumerate(row):
                self._table.setItem(r, c, QTableWidgetItem(text))
                if text:
                    filled += 1
        self.statusBar().showMessage(f"OCR grid: {filled} cells with text.", 8000)
        if filled == 0:
            QMessageBox.information(
                self,
                "OCR grid",
                "OCR finished, but no text was found inside the grid.",
            )

    def _on_ocr_failed(self, message: str) -> None:
        self._close_ocr_progress()
        self.statusBar().showMessage("OCR grid failed.", 8000)
        QMessageBox.warning(self, "OCR grid", message or "PaddleOCR failed.")

    def closeEvent(self, event) -> None:
        self._ocr_worker.shutdown()
        self._ocr_worker.wait()
        super().closeEvent(event)
