"""Main window: file list, PDF preview, line-item table."""

from __future__ import annotations

import traceback
from pathlib import Path

import pymupdf as fitz
from PyQt6.QtCore import QMutex, QPoint, Qt, QThread, QWaitCondition, pyqtSignal
from PyQt6.QtGui import QBrush, QColor
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLineEdit,
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

from invoice_app.document_layout import load_layout, save_page_region
from invoice_app.grid_infer import InferHints, infer_from_words, infer_table_region
from invoice_app.ocr_cache import (
    load_cache,
    load_page_words,
    load_skew_by_page,
    ocr_document_pages,
    write_cache,
)
from invoice_app.pdf_ocr import (
    ocr_image,
    page_pdf_rect,
    read_grid,
    region_image,
    words_raster_to_pdf,
)
from invoice_app.pdf_panel import PdfPanel
from invoice_app.pdf_text import Word, words_on_page
from invoice_app.suppliers import AppState, ConfigError, Supplier, list_suppliers
from invoice_app.validation import SupplierValidations

_INVALID_CELL_BG = QColor(255, 210, 210)
_INVALID_CELL_FG = QColor(140, 0, 0)


class _OcrWordsWorker(QThread):
    """Run PaddleOCR on a rasterised region; emit word boxes in image pixels."""

    succeeded = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._mutex = QMutex()
        self._cond = QWaitCondition()
        self._image = None
        self._stop = False

    def submit(self, image) -> None:
        self._mutex.lock()
        self._image = image
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
            while self._image is None and not self._stop:
                self._cond.wait(self._mutex)
            if self._image is None and self._stop:
                self._mutex.unlock()
                return
            image = self._image
            self._image = None
            self._mutex.unlock()
            try:
                words = ocr_image(image)
                self.succeeded.emit(words)
            except Exception as exc:
                traceback.print_exc()
                self.failed.emit(str(exc))
            self._mutex.lock()
            stop = self._stop and self._image is None
            self._mutex.unlock()
            if stop:
                return


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


class _OcrBatchWorker(QThread):
    """OCR every page of each PDF in a batch; write ``<stem>.ocr.json`` beside each file."""

    progress = pyqtSignal(int, int, str)
    document_done = pyqtSignal(str, bool, str)
    finished_batch = pyqtSignal(int, int, int)
    failed = pyqtSignal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._mutex = QMutex()
        self._pdfs: list[Path] = []
        self._stop = False

    def submit(self, pdfs: list[Path]) -> None:
        self._mutex.lock()
        self._pdfs = list(pdfs)
        self._stop = False
        self._mutex.unlock()
        if not self.isRunning():
            self.start()

    def cancel(self) -> None:
        self._mutex.lock()
        self._stop = True
        self._mutex.unlock()

    def run(self) -> None:
        self._mutex.lock()
        pdfs = self._pdfs
        self._pdfs = []
        self._mutex.unlock()
        if not pdfs:
            return
        ocr_count = 0
        skip_count = 0
        fail_count = 0
        total = len(pdfs)
        try:
            for index, pdf_path in enumerate(pdfs):
                self._mutex.lock()
                stop = self._stop
                self._mutex.unlock()
                if stop:
                    break
                label = pdf_path.name
                self.progress.emit(index, total, label)
                if load_cache(pdf_path) is not None:
                    skip_count += 1
                    self.document_done.emit(label, True, "skipped (cache up to date)")
                    continue
                try:
                    with fitz.open(pdf_path) as doc:
                        pages = ocr_document_pages(doc)
                    write_cache(pdf_path, pages)
                    ocr_count += 1
                    self.document_done.emit(label, True, f"{len(pages)} page(s)")
                except Exception as exc:
                    traceback.print_exc()
                    fail_count += 1
                    self.document_done.emit(label, False, str(exc))
            self.finished_batch.emit(ocr_count, skip_count, fail_count)
        except Exception as exc:
            traceback.print_exc()
            self.failed.emit(str(exc))


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Invoice Processor (PoC)")
        self.resize(1280, 800)

        self._folder: Path | None = None
        self._supplier: Supplier | None = None
        self._state = AppState.load()
        self._ocr_busy = False
        self._ocr_progress: QProgressDialog | None = None
        self._ocr_worker = _OcrGridWorker(self)
        self._ocr_worker.succeeded.connect(self._on_ocr_succeeded)
        self._ocr_worker.failed.connect(self._on_ocr_failed)
        self._ocr_words_worker = _OcrWordsWorker(self)
        self._ocr_words_worker.succeeded.connect(self._on_ocr_words_succeeded)
        self._ocr_words_worker.failed.connect(self._on_ocr_words_failed)
        self._ocr_batch_worker = _OcrBatchWorker(self)
        self._ocr_batch_worker.progress.connect(self._on_ocr_batch_progress)
        self._ocr_batch_worker.finished_batch.connect(self._on_ocr_batch_finished)
        self._ocr_batch_worker.failed.connect(self._on_ocr_batch_failed)
        self._ocr_batch_mode = False
        self._ocr_words_pending: str | None = None
        self._ocr_words_page = 0
        self._ocr_words_pdf_rect: tuple[float, float, float, float] | None = None
        self._ocr_words_origin: tuple[float, float] = (0.0, 0.0)
        self._ocr_words_size: tuple[float, float] = (1.0, 1.0)
        self._table_words: list[Word] | None = None
        self._table_words_origin: tuple[float, float] = (0.0, 0.0)
        self._table_words_size: tuple[float, float] = (1.0, 1.0)
        self._table_words_space: str = "pdf"

        self._file_list = QListWidget()
        self._file_list.currentItemChanged.connect(self._on_file_selected)

        self._pdf_panel = PdfPanel()
        self._pdf_panel.pdf_loaded.connect(self._on_pdf_loaded)
        self._pdf_panel.page_changed.connect(self._on_pdf_page_changed)
        self._pdf_panel.region_defined.connect(self._on_table_region_defined)
        self._pdf_panel.preview_changed.connect(self._refresh_text_rects)
        self._pdf_panel.overlay().grid_changed.connect(self._resize_table)
        self._pdf_panel.overlay().column_context_requested.connect(self._on_column_context)
        self._pdf_panel.dimensions_changed.connect(self._resize_table)

        self._table = QTableWidget()
        self._table.setAlternatingRowColors(True)
        self._table.itemChanged.connect(self._on_table_item_changed)

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
        self._supplier_menu = file_menu.addMenu("&Supplier")
        self._supplier_menu.aboutToShow.connect(self._populate_supplier_menu)
        self._batch_menu = file_menu.addMenu("Load &batch")
        self._batch_menu.aboutToShow.connect(self._populate_batch_menu)
        file_menu.addSeparator()
        quit_action = file_menu.addAction("E&xit")
        quit_action.triggered.connect(QApplication.instance().quit)

        layout_menu = self.menuBar().addMenu("&Layout")
        table_region = layout_menu.addAction("&Table region…")
        table_region.triggered.connect(self._table_region)
        auto_region = layout_menu.addAction("Auto-&detect region")
        auto_region.triggered.connect(self._auto_detect_table_region)
        draw_grid = layout_menu.addAction("&Draw grid")
        draw_grid.triggered.connect(self._draw_grid)
        save_col_fracs = layout_menu.addAction("Save column &fracs")
        save_col_fracs.triggered.connect(self._save_column_fracs)

        read_menu = self.menuBar().addMenu("&Read")
        line_items = read_menu.addAction("&Line items")
        line_items.triggered.connect(self._read_line_items)
        ocr_grid = read_menu.addAction("OCR &grid")
        ocr_grid.triggered.connect(self._ocr_grid)
        ocr_batch = read_menu.addAction("OCR &batch")
        ocr_batch.triggered.connect(self._ocr_batch)
        read_menu.addSeparator()
        show_rects = read_menu.addAction("Show text &rectangles")
        show_rects.triggered.connect(self._show_text_rectangles)
        infer_grid = read_menu.addAction("&Infer grid")
        infer_grid.triggered.connect(lambda: self._infer_grid())

    def _populate_supplier_menu(self) -> None:
        self._supplier_menu.clear()
        try:
            names = list_suppliers()
        except ConfigError as exc:
            action = self._supplier_menu.addAction(str(exc))
            action.setEnabled(False)
            return
        if not names:
            action = self._supplier_menu.addAction("(no suppliers)")
            action.setEnabled(False)
            return
        for name in names:
            action = self._supplier_menu.addAction(name)
            action.setCheckable(True)
            action.setChecked(
                self._supplier is not None and self._supplier.name == name
            )
            action.triggered.connect(
                lambda _checked=False, n=name: self._select_supplier(n)
            )

    def _select_supplier(self, name: str) -> None:
        try:
            supplier = Supplier.load(name)
        except (ConfigError, OSError) as exc:
            QMessageBox.warning(self, "Supplier", str(exc))
            return
        if supplier.data_directory is None:
            directory = QFileDialog.getExistingDirectory(
                self,
                f"Choose the data directory for supplier “{name}”",
            )
            if not directory:
                return
            supplier.set_data_directory(Path(directory))
        changing = self._supplier is None or self._supplier.name != supplier.name
        self._supplier = supplier
        self._state.supplier = supplier.name
        if changing:
            self._close_batch()
        else:
            self._save_state()
        self.statusBar().showMessage(
            f"Supplier: {name} — data directory: {supplier.data_directory}", 8000
        )

    def _close_batch(self) -> None:
        """Drop the open batch and clear the file list, preview, and table."""
        self._folder = None
        self._table_words = None
        self._file_list.blockSignals(True)
        self._file_list.clear()
        self._file_list.blockSignals(False)
        self._pdf_panel.clear()
        self._table.clear()
        self._table.setRowCount(0)
        self._table.setColumnCount(0)
        self._state.batch = None
        self._state.pdf = None
        self._save_state()

    def _populate_batch_menu(self) -> None:
        self._batch_menu.clear()
        supplier = self._supplier
        if supplier is None:
            action = self._batch_menu.addAction("(choose a supplier first)")
            action.setEnabled(False)
            return
        root = supplier.data_directory
        if root is None or not root.is_dir():
            action = self._batch_menu.addAction(
                f"(data directory not found: {root})"
            )
            action.setEnabled(False)
            return
        batches = supplier.list_batches()
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
        """Reopen the last supplier, batch and PDF. Never prompts or raises."""
        state = self._state
        if not state.supplier:
            return
        try:
            if state.supplier not in list_suppliers():
                return
            supplier = Supplier.load(state.supplier)
        except (ConfigError, OSError):
            return
        self._supplier = supplier
        root = supplier.data_directory
        if root is None or not root.is_dir():
            self.statusBar().showMessage(f"Supplier: {supplier.name} (restored)", 8000)
            return
        batches = {p.name: p for p in supplier.list_batches()}
        folder = batches.get(state.batch or "")
        if folder is None:
            self.statusBar().showMessage(f"Supplier: {supplier.name} (restored)", 8000)
            return
        self._load_batch(folder, select_name=state.pdf)

    def _on_pdf_loaded(self) -> None:
        pdf_path = self._current_pdf_path()
        if pdf_path is not None:
            regions = load_layout(pdf_path)
            regions = self._auto_detect_missing_regions(pdf_path, regions)
            self._pdf_panel.set_page_regions(regions)
            self._pdf_panel.refresh_page_layout()
        self._apply_page_deskew()
        self._apply_cached_ocr_for_current_page()
        self._auto_apply_layout_after_region()
        self._resize_table()

    def _auto_detect_missing_regions(
        self,
        pdf_path: Path,
        regions: dict[int, tuple[float, float, float, float]],
    ) -> dict[int, tuple[float, float, float, float]]:
        """Infer and persist table regions for any page not yet in ``regions``."""
        doc = self._pdf_panel.document()
        if doc is None:
            return regions
        hints = self._infer_hints()
        x_bounds = self._supplier.table_x_range() if self._supplier is not None else None
        updated = dict(regions)
        for page_idx in range(len(doc)):
            if page_idx in updated:
                continue
            page_words = load_page_words(pdf_path, page_idx)
            if not page_words:
                continue
            rect = doc[page_idx].rect
            pw, ph = float(rect.width), float(rect.height)
            detected = infer_table_region(
                page_words, (0.0, 0.0), (pw, ph), hints, x_bounds=x_bounds
            )
            if detected is not None:
                updated[page_idx] = detected
                try:
                    save_page_region(pdf_path, page_idx, detected)
                except OSError:
                    pass
        return updated

    def _on_pdf_page_changed(self) -> None:
        self._apply_cached_ocr_for_current_page()
        if not self._pdf_panel.overlay().has_region():
            self._table.clearContents()
            self._pdf_panel.overlay().clear_invalid_cells()
        else:
            self._auto_apply_layout_after_region()
        self._resize_table()

    def _on_table_region_defined(self) -> None:
        pdf_path = self._current_pdf_path()
        norm = self._pdf_panel.upright_page_region()
        if pdf_path is not None and norm is not None:
            try:
                save_page_region(pdf_path, self._pdf_panel.page_index(), norm)
            except OSError as exc:
                self.statusBar().showMessage(f"Could not save table region: {exc}", 8000)
        self._auto_apply_layout_after_region()

    def _auto_apply_layout_after_region(self) -> None:
        """When a page has a table region, apply supplier col-fracs and infer if OCR exists."""
        overlay = self._pdf_panel.overlay()
        if not overlay.has_region():
            return
        saved = self._supplier.col_fracs() if self._supplier is not None else None
        if saved:
            self._pdf_panel.draw_column_grid(saved)
        if self._table_words is not None:
            if not self._infer_grid(silent=True):
                self._table.clearContents()
                overlay.clear_invalid_cells()
        else:
            self._table.clearContents()
            overlay.clear_invalid_cells()

    def _refresh_text_rects(self) -> None:
        """Remap cached word boxes after the preview is scaled or resized."""
        if not self._table_words:
            return
        self._apply_table_word_session(
            self._table_words,
            origin=self._table_words_origin,
            size=self._table_words_size,
            space=self._table_words_space,
        )

    def _on_file_selected(self, current: QListWidgetItem | None, _previous) -> None:
        if current is None:
            self._pdf_panel.clear()
            if self._state.pdf is not None:
                self._state.pdf = None
                self._save_state()
            return
        path = Path(current.data(Qt.ItemDataRole.UserRole))
        self._table_words = None
        try:
            self._pdf_panel.load_pdf(path)
        except Exception as exc:
            QMessageBox.warning(self, "Open PDF", f"Could not open {path.name}:\n{exc}")
            return
        self._state.pdf = path.name
        self._save_state()

    def _apply_page_deskew(self) -> None:
        """Level the open PDF from each page's sidecar ``skew-deg``.

        Pages with no OCR skew stay upright. Changing pages uses the angles
        loaded here; ``PdfPanel`` rotates that page when it renders.
        """
        pdf_path = self._current_pdf_path()
        loaded = load_skew_by_page(pdf_path) if pdf_path is not None else None
        angles = loaded or {}
        if angles == self._pdf_panel.deskew_angles():
            return
        self._pdf_panel.set_deskew_angles(angles)

    def _table_region(self) -> None:
        if self._pdf_panel.document() is None:
            QMessageBox.information(self, "Table region", "Load a PDF first.")
            return
        self._pdf_panel.begin_table_region()

    def _auto_detect_table_region(self) -> None:
        doc = self._pdf_panel.document()
        if doc is None:
            QMessageBox.information(self, "Auto-detect region", "Load a PDF first.")
            return
        page_index = self._pdf_panel.page_index()
        pdf_path = self._current_pdf_path()
        words = self._table_words if self._table_words_space == "pdf" else None
        if not words and pdf_path is not None:
            words = load_page_words(pdf_path, page_index)
        if not words:
            QMessageBox.information(
                self,
                "Auto-detect region",
                "Run Read → Show text rectangles or Read → OCR batch first.",
            )
            return
        pw, ph = self._pdf_panel.page_size_pdf()
        hints = self._infer_hints()
        x_bounds = self._supplier.table_x_range() if self._supplier is not None else None
        detected = infer_table_region(
            words, (0.0, 0.0), (pw, ph), hints, x_bounds=x_bounds
        )
        if detected is None:
            self._pdf_panel.set_upright_page_region(page_index, None)
            self._table.clearContents()
            self._pdf_panel.overlay().clear_invalid_cells()
            self._resize_table()
            self.statusBar().showMessage(
                "Auto-detect region: no line-item table found on this page.", 8000
            )
            return
        self._pdf_panel.set_upright_page_region(page_index, detected)
        if pdf_path is not None:
            try:
                save_page_region(pdf_path, page_index, detected)
            except OSError as exc:
                self.statusBar().showMessage(f"Could not save table region: {exc}", 8000)
        self._auto_apply_layout_after_region()
        self._resize_table()
        self.statusBar().showMessage("Auto-detected line-item table region.", 8000)

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
        saved = self._supplier.col_fracs() if self._supplier is not None else None
        if saved:
            self._pdf_panel.draw_column_grid(saved)
        else:
            self._pdf_panel.draw_grid()
        self._resize_table()

    def _save_column_fracs(self) -> None:
        if self._supplier is None:
            QMessageBox.information(
                self,
                "Save column fracs",
                "Choose a supplier first (File → Supplier).",
            )
            return
        overlay = self._pdf_panel.overlay()
        fracs = overlay.col_fracs()
        if len(fracs) < 2:
            QMessageBox.information(
                self,
                "Save column fracs",
                "Draw a grid first (Layout → Draw grid) and align the column lines.",
            )
            return
        self._supplier.set_col_fracs(fracs)
        norm = self._pdf_panel.upright_page_region()
        if norm is not None:
            self._supplier.set_table_x_range(norm[0], norm[2])
        self.statusBar().showMessage(
            f"Saved {len(fracs) - 1} column(s) to supplier “{self._supplier.name}”.",
            8000,
        )

    def _resize_table(self) -> None:
        overlay = self._pdf_panel.overlay()
        row_fracs = overlay.row_fracs()
        col_fracs = overlay.col_fracs()
        if len(row_fracs) >= 2:
            rows = len(row_fracs) - 1
        elif overlay.has_region() and len(col_fracs) >= 2:
            rows = 0
        else:
            rows = self._pdf_panel.num_rows()
        cols = len(col_fracs) - 1 if len(col_fracs) >= 2 else self._pdf_panel.num_cols()
        self._table.setRowCount(rows)
        self._table.setColumnCount(cols)
        names = self._supplier.column_names() if self._supplier is not None else []
        headers = [
            names[c] if c < len(names) else f"Col {c + 1}"
            for c in range(cols)
        ]
        self._table.setHorizontalHeaderLabels(headers)
        for r in range(rows):
            self._table.setVerticalHeaderItem(r, QTableWidgetItem(str(r + 1)))

    def _on_column_context(self, col_index: int, global_pos: QPoint) -> None:
        if self._supplier is None:
            QMessageBox.information(
                self,
                "Column settings",
                "Choose a supplier first (File → Supplier) to configure columns.",
            )
            return
        overlay = self._pdf_panel.overlay()
        col_fracs = overlay.col_fracs()
        n_cols = len(col_fracs) - 1 if len(col_fracs) >= 2 else self._pdf_panel.num_cols()
        if col_index < 0 or col_index >= n_cols:
            return

        current_name, current_anchor = self._supplier.column_entry(col_index, n_cols)

        dialog = QDialog(self)
        dialog.setWindowTitle(f"Column {col_index + 1} settings")
        form = QFormLayout(dialog)

        name_edit = QLineEdit(current_name, dialog)
        name_edit.setPlaceholderText(f"Col {col_index + 1}")
        form.addRow("Name:", name_edit)

        anchor_check = QCheckBox("Anchor (single-line, present on every row)", dialog)
        anchor_check.setChecked(current_anchor)
        form.addRow("", anchor_check)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel,
            parent=dialog,
        )
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        form.addRow(buttons)

        dialog.adjustSize()
        dialog.move(global_pos)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        try:
            self._supplier.set_column_entry(
                col_index,
                name=name_edit.text(),
                anchor=anchor_check.isChecked(),
                n_cols=n_cols,
            )
        except OSError as exc:
            QMessageBox.warning(self, "Column settings", f"Could not save supplier config:\n{exc}")
            return

        self._resize_table()
        if self._table_words is not None and overlay.has_region():
            self._infer_grid(silent=True)
        else:
            self._validate_table()
        state_str = "anchor" if anchor_check.isChecked() else "non-anchor"
        label = name_edit.text().strip() or f"Col {col_index + 1}"
        self.statusBar().showMessage(
            f"Saved column {col_index + 1} (“{label}”, {state_str}) to supplier “{self._supplier.name}”.",
            8000,
        )

    def _infer_hints(self) -> InferHints:
        supplier = self._supplier
        names: list[str] = []
        col_fracs: list[float] | None = None
        anchor_cols: list[bool] | None = None
        overlay_cols = self._pdf_panel.overlay().col_fracs()
        if supplier is not None:
            names = supplier.column_names()
            col_fracs = supplier.col_fracs()
        if not col_fracs and len(overlay_cols) >= 2:
            col_fracs = overlay_cols
        if col_fracs and len(col_fracs) >= 2:
            n_cols = len(col_fracs) - 1
        elif names:
            n_cols = len(names)
        else:
            n_cols = self._pdf_panel.num_cols()
        if supplier is not None:
            anchor_cols = supplier.anchor_cols(n_cols)
        return InferHints(
            n_cols=n_cols,
            header_row_count=0,
            col_fracs=col_fracs,
            column_names=names,
            anchor_cols=anchor_cols,
        )

    def _current_pdf_path(self) -> Path | None:
        item = self._file_list.currentItem()
        if item is None:
            return None
        return Path(item.data(Qt.ItemDataRole.UserRole))

    def _batch_pdf_paths(self) -> list[Path]:
        paths: list[Path] = []
        for i in range(self._file_list.count()):
            item = self._file_list.item(i)
            if item is None:
                continue
            paths.append(Path(item.data(Qt.ItemDataRole.UserRole)))
        return paths

    def _apply_cached_ocr_for_current_page(self) -> bool:
        """Draw OCR boxes from ``<stem>.ocr.json`` when the sidecar is valid."""
        pdf_path = self._current_pdf_path()
        if pdf_path is None or self._pdf_panel.document() is None:
            self._table_words = None
            return False
        page_index = self._pdf_panel.page_index()
        cached = load_page_words(pdf_path, page_index)
        if cached is None:
            self._table_words = None
            self._pdf_panel.overlay().clear_text_rects()
            self._pdf_panel.refresh_overlay()
            return False
        pw, ph = self._pdf_panel.page_size_pdf()
        self._apply_table_word_session(
            cached,
            origin=(0.0, 0.0),
            size=(pw, ph),
            space="pdf",
        )
        return True

    def _apply_table_word_session(
        self,
        words: list[Word],
        *,
        origin: tuple[float, float],
        size: tuple[float, float],
        space: str,
    ) -> None:
        self._table_words = words
        self._table_words_origin = origin
        self._table_words_size = size
        self._table_words_space = space
        overlay = self._pdf_panel.overlay()
        if space == "pdf":
            rects = self._pdf_panel.pdf_words_to_widget_rects(words)
        else:
            rects = self._pdf_panel.region_words_to_widget_rects(words, origin, size)
        overlay.set_text_rects(rects)
        overlay.show()
        overlay.raise_()
        self._pdf_panel.refresh_overlay()

    def _begin_ocr_words(self, purpose: str) -> None:
        doc = self._pdf_panel.document()
        if doc is None:
            return
        page_index = self._pdf_panel.page_index()
        pdf_rect = page_pdf_rect(doc, page_index)
        try:
            image, origin, size = region_image(doc, page_index, pdf_rect)
        except Exception as exc:
            QMessageBox.warning(self, purpose, f"Could not render the page:\n{exc}")
            return
        if self._ocr_busy or self._ocr_batch_mode:
            QMessageBox.information(self, purpose, "OCR is already running.")
            return
        self._ocr_busy = True
        self._ocr_words_pending = purpose
        self._ocr_words_page = page_index
        self._ocr_words_pdf_rect = pdf_rect
        dialog = QProgressDialog(
            "Running PaddleOCR on the page.\n"
            "The first run downloads models and can take several minutes.",
            None,
            0,
            0,
            self,
        )
        dialog.setWindowTitle(purpose)
        dialog.setWindowModality(Qt.WindowModality.WindowModal)
        dialog.setCancelButton(None)
        dialog.setMinimumDuration(0)
        dialog.setMinimumWidth(420)
        dialog.show()
        self._ocr_progress = dialog
        self.statusBar().showMessage("Running PaddleOCR…")
        self._ocr_words_origin = origin
        self._ocr_words_size = size
        self._ocr_words_worker.submit(image)

    def _show_text_rectangles(self) -> None:
        doc = self._pdf_panel.document()
        if doc is None:
            QMessageBox.information(self, "Show text rectangles", "Load a PDF first.")
            return
        pdf_path = self._current_pdf_path()
        if pdf_path is not None and self._apply_cached_ocr_for_current_page():
            count = len(self._table_words or [])
            self.statusBar().showMessage(
                f"Show text rectangles: {count} boxes (from {pdf_path.stem}.ocr.json).",
                8000,
            )
            return
        # Always OCR the full page: many scans ship a partial text layer (header
        # only) which would hide the rest of the page if we used words_on_page.
        self._begin_ocr_words("Show text rectangles")

    def _ocr_batch(self) -> None:
        if self._folder is None or self._file_list.count() == 0:
            QMessageBox.information(
                self,
                "OCR batch",
                "Load a batch first (File → Load batch).",
            )
            return
        if self._ocr_busy or self._ocr_batch_mode:
            QMessageBox.information(self, "OCR batch", "OCR is already running.")
            return
        pdfs = self._batch_pdf_paths()
        self._ocr_batch_mode = True
        dialog = QProgressDialog(
            "Running PaddleOCR on every page of every PDF in the batch.\n"
            "The first run downloads models and can take a long time.\n"
            "Up-to-date sidecars are skipped.",
            "Cancel",
            0,
            len(pdfs),
            self,
        )
        dialog.setWindowTitle("OCR batch")
        dialog.setWindowModality(Qt.WindowModality.WindowModal)
        dialog.setMinimumDuration(0)
        dialog.setMinimumWidth(480)
        dialog.canceled.connect(self._ocr_batch_worker.cancel)
        dialog.show()
        self._ocr_progress = dialog
        self.statusBar().showMessage("OCR batch: starting…")
        self._ocr_batch_worker.submit(pdfs)

    def _on_ocr_batch_progress(self, index: int, total: int, name: str) -> None:
        dialog = self._ocr_progress
        if dialog is not None:
            dialog.setValue(index)
            dialog.setLabelText(
                f"Processing ({index + 1} of {total}):\n{name}\n\n"
                "Up-to-date .ocr.json files are skipped."
            )
        self.statusBar().showMessage(f"OCR batch: {name} ({index + 1}/{total})")

    def _on_ocr_batch_finished(self, ocr_count: int, skip_count: int, fail_count: int) -> None:
        self._ocr_batch_mode = False
        dialog = self._ocr_progress
        self._ocr_progress = None
        if dialog is not None:
            dialog.setValue(dialog.maximum())
            dialog.close()
        summary = (
            f"OCR batch finished: {ocr_count} OCR'd, {skip_count} skipped, {fail_count} failed."
        )
        self.statusBar().showMessage(summary, 12000)
        if fail_count:
            QMessageBox.warning(self, "OCR batch", summary)
        else:
            QMessageBox.information(self, "OCR batch", summary)
        pdf_path = self._current_pdf_path()
        if pdf_path is not None:
            regions = load_layout(pdf_path)
            regions = self._auto_detect_missing_regions(pdf_path, regions)
            self._pdf_panel.set_page_regions(regions)
            self._pdf_panel.refresh_page_layout()
        self._apply_page_deskew()
        self._apply_cached_ocr_for_current_page()
        self._auto_apply_layout_after_region()
        self._resize_table()

    def _on_ocr_batch_failed(self, message: str) -> None:
        self._ocr_batch_mode = False
        self._close_ocr_progress()
        self.statusBar().showMessage("OCR batch failed.", 8000)
        QMessageBox.warning(self, "OCR batch", message or "OCR batch failed.")

    def _infer_grid(self, *, silent: bool = False) -> bool:
        if self._pdf_panel.document() is None:
            if not silent:
                QMessageBox.information(self, "Infer grid", "Load a PDF first.")
            return False
        if not self._pdf_panel.overlay().has_region():
            if not silent:
                QMessageBox.information(
                    self,
                    "Infer grid",
                    "Draw the line-item table area first (Layout → Table region…).",
                )
            return False
        if self._table_words is None:
            if not silent:
                QMessageBox.information(
                    self,
                    "Infer grid",
                    "Run Read → Show text rectangles first (or wait for OCR to finish).",
                )
            return False
        if self._table_words_space == "pdf":
            bounds = self._pdf_panel.overlay().region_widget_bounds()
            if bounds is None:
                return False
            rx0, ry0, rx1, ry1 = bounds
            rw_px, rh_px = rx1 - rx0, ry1 - ry0
            if rw_px <= 0 or rh_px <= 0:
                return False
            region_words: list[Word] = []
            for word in self._table_words:
                wx0, wy0, wx1, wy1 = self._pdf_panel.pdf_rect_to_widget(
                    word.x0, word.y0, word.x1, word.y1
                )
                cx = (wx0 + wx1) / 2
                cy = (wy0 + wy1) / 2
                if rx0 <= cx <= rx1 and ry0 <= cy <= ry1:
                    region_words.append(
                        Word(wx0 - rx0, wy0 - ry0, wx1 - rx0, wy1 - ry0, word.text)
                    )
            origin = (0.0, 0.0)
            size = (rw_px, rh_px)
            words_for_infer = region_words
        else:
            origin = self._table_words_origin
            size = self._table_words_size
            words_for_infer = self._table_words

        hints = self._infer_hints()
        result = infer_from_words(words_for_infer, origin, size, hints)
        if len(result.row_fracs) < 2 or len(result.col_fracs) < 2:
            if not silent:
                QMessageBox.warning(
                    self,
                    "Infer grid",
                    "Could not infer a grid from the text boxes in the table region.",
                )
            return False
        self._pdf_panel.set_inferred_grid(result.row_fracs, result.col_fracs)
        self._resize_table()
        names = hints.column_names
        if names:
            self._table.setHorizontalHeaderLabels(
                names[: self._table.columnCount()]
                + [f"Col {i + 1}" for i in range(len(names), self._table.columnCount())]
            )
        rows = len(result.row_fracs) - 1
        cols = len(result.col_fracs) - 1
        self._table.blockSignals(True)
        try:
            self._table.clearContents()
            for r, row_data in enumerate(result.rows):
                if r >= rows:
                    break
                for c in range(cols):
                    key = names[c] if c < len(names) else f"col_{c + 1}"
                    text = row_data.get(key, "")
                    self._table.setItem(r, c, QTableWidgetItem(text))
        finally:
            self._table.blockSignals(False)
        self._validate_table()
        if not silent:
            self.statusBar().showMessage(
                f"Infer grid: {result.data_row_count} data rows.",
                8000,
            )
        return True

    def _on_ocr_words_succeeded(self, words: list) -> None:
        purpose = self._ocr_words_pending or "OCR"
        self._ocr_words_pending = None
        self._close_ocr_progress()
        doc = self._pdf_panel.document()
        pdf_rect = self._ocr_words_pdf_rect
        self._ocr_words_pdf_rect = None
        if doc is not None and pdf_rect is not None:
            words = words_raster_to_pdf(words, pdf_rect, doc[self._ocr_words_page])
        pw, ph = self._pdf_panel.page_size_pdf()
        self._apply_table_word_session(
            words,
            origin=(0.0, 0.0),
            size=(pw, ph),
            space="pdf",
        )
        self.statusBar().showMessage(f"{purpose}: {len(words)} OCR boxes.", 8000)
        if not words:
            QMessageBox.information(
                self,
                purpose,
                "OCR finished, but no text was found in the table region.",
            )

    def _on_ocr_words_failed(self, message: str) -> None:
        purpose = self._ocr_words_pending or "OCR"
        self._ocr_words_pending = None
        self._close_ocr_progress()
        self.statusBar().showMessage(f"{purpose} failed.", 8000)
        QMessageBox.warning(self, purpose, message or "PaddleOCR failed.")

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

        words = words_on_page(doc, self._pdf_panel.page_index())
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

        self._table.blockSignals(True)
        try:
            for r in range(rows):
                for c in range(cols):
                    x0, y0, x1, y1 = overlay.cell_widget_rect(r, c)
                    text = self._pdf_panel.text_in_widget_rect(words, x0, y0, x1, y1)
                    self._table.setItem(r, c, QTableWidgetItem(text))
        finally:
            self._table.blockSignals(False)
        self._validate_table()

    def _ocr_grid(self) -> None:
        if self._ocr_busy or self._ocr_batch_mode:
            QMessageBox.information(self, "OCR grid", "OCR is already running.")
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
        try:
            if self._pdf_panel.is_deskewed():
                image, origin, size = self._pdf_panel.raster_widget_rect(x0, y0, x1, y1)
            else:
                pdf_rect = self._pdf_panel.widget_to_pdf_rect(x0, y0, x1, y1)
                image, origin, size = region_image(
                    doc, self._pdf_panel.page_index(), pdf_rect
                )
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
        self._table.blockSignals(True)
        try:
            for r, row in enumerate(cells):
                for c, text in enumerate(row):
                    self._table.setItem(r, c, QTableWidgetItem(text))
                    if text:
                        filled += 1
        finally:
            self._table.blockSignals(False)
        self._validate_table()
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

    def _on_table_item_changed(self, _item: QTableWidgetItem) -> None:
        self._validate_table()

    def _validate_table(self) -> None:
        """Run configured supplier validations on every row and highlight failing cells."""
        overlay = self._pdf_panel.overlay()
        rows = self._table.rowCount()
        cols = self._table.columnCount()
        if rows == 0 or cols == 0:
            overlay.clear_invalid_cells()
            return

        supplier_config = self._supplier.config if self._supplier is not None else {}
        sv = SupplierValidations(supplier_config)

        col_headers: list[str] = []
        name_to_col: dict[str, int] = {}
        for c in range(cols):
            header_item = self._table.horizontalHeaderItem(c)
            header = header_item.text().strip() if header_item is not None else f"Col {c + 1}"
            col_headers.append(header)
            name_to_col[header] = c
            name_to_col[f"Col {c + 1}"] = c
            name_to_col[f"col_{c + 1}"] = c

        page_num = self._pdf_panel.page_index() + 1
        field_to_page = {name: page_num for name in name_to_col}
        invalid_cells: dict[tuple[int, int], str] = {}

        self._table.blockSignals(True)
        try:
            default_brush = QBrush()
            for r in range(rows):
                field_values: dict[str, str] = {}
                for c in range(cols):
                    item = self._table.item(r, c)
                    text = item.text() if item is not None else ""
                    if item is not None:
                        item.setBackground(default_brush)
                        item.setForeground(default_brush)
                        item.setToolTip("")
                    field_values[col_headers[c]] = text
                    field_values[f"Col {c + 1}"] = text
                    field_values[f"col_{c + 1}"] = text

                if not sv.has_rules():
                    continue

                failures = sv.run_validations(
                    row_index=r,
                    field_values=field_values,
                    field_to_page=field_to_page,
                )
                for _page, field_name, message in failures:
                    c = name_to_col.get(field_name)
                    if c is None:
                        continue
                    item = self._table.item(r, c)
                    if item is None:
                        item = QTableWidgetItem("")
                        self._table.setItem(r, c, item)
                    item.setBackground(QBrush(_INVALID_CELL_BG))
                    item.setForeground(QBrush(_INVALID_CELL_FG))
                    item.setToolTip(message)
                    invalid_cells[(r, c)] = message
        finally:
            self._table.blockSignals(False)

        overlay.set_invalid_cells(invalid_cells)

    def closeEvent(self, event) -> None:
        self._ocr_batch_worker.cancel()
        self._ocr_worker.shutdown()
        self._ocr_words_worker.shutdown()
        self._ocr_batch_worker.wait(5000)
        self._ocr_worker.wait()
        self._ocr_words_worker.wait()
        super().closeEvent(event)
