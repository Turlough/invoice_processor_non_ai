# Invoice App package

## Purpose

PyQt6 application shell, PDF rendering and deskew preview, draggable table region and cell grid overlay, PDF text-layer and PaddleOCR extraction, OCR/layout sidecar persistence, and text-box grid inference.

## Ownership

- Application window and menu orchestration: `main_window.py` (entry point `main.py` at repo root)
- Centre PDF preview, zoom/page controls, and deskew coordinate transforms: `pdf_panel.py`
- Interactive table region and row/column divider overlay: `grid_overlay.py`
- Project discovery, `project_config.json`, batches, and per-user `AppState`: `projects.py`
- Per-PDF sidecar caches: `ocr_cache.py` (`<stem>.ocr.json`) and `document_layout.py` (`<stem>.json`)
- Text and OCR extraction: `pdf_text.py` (PyMuPDF `get_text("words")`) and `pdf_ocr.py` (PaddleOCR on CPU)
- Row/column inference from positioned words: `grid_infer.py`
- Line-item validation rules live in `invoice_app/validation/` (see child DOX)

## Local Contracts

- **Projects & state (`projects.py`)**: `.env` defines `CONFIG_DIRECTORY`; each subdirectory is a project with `project_config.json` (`data-directory`, `col-fracs`, optional `table-x-range`, `columns` list of `{name, anchor}`, `validations`). Last project, batch, and PDF persist per user via `platformdirs` (`app_config.json` under the OS user config dir, not in `CONFIG_DIRECTORY`) and restore on startup.
- **Sidecar fingerprinting (`ocr_cache.py`, `document_layout.py`)**: `<stem>.ocr.json` and `<stem>.json` live beside each PDF and validate against the PDF's `source-size` and `source-mtime` (tolerating ±2s plus 1- or 2-hour whole-hour DST/SMB shifts).
- **Coordinates & deskew (`pdf_panel.py`, `grid_overlay.py`)**:
  - Table regions in `<stem>.json` are stored per page in upright-page normalized fractions `0..1`.
  - `GridOverlay` stores `row_fracs` and `col_fracs` as normalized `0..1` fractions inside the table region.
  - **Layout → Auto deskew** rotates the preview by `-skew_deg` (from `<stem>.ocr.json`) on an expanded canvas so no corner is clipped; `PdfPanel.widget_to_pdf_rect` and `pdf_rect_to_widget` map between preview widget pixels and upright PDF user space.
- **Grid & column settings (`main_window.py`, `grid_overlay.py`)**:
  - **Layout → Auto-detect region** (and automatic detection on PDF load / after `OCR batch` for pages missing from `<stem>.json`) runs `infer_table_region` and saves the detected upright region to `<stem>.json`.
  - **Layout → Save column fracs** saves `col-fracs` (and `table-x-range` when a region is active) to `project_config.json`.
  - **Layout → Draw grid** draws only vertical dividers when the project has saved `col-fracs`, or an even row×col grid otherwise.
  - Right-clicking a column in `GridOverlay` emits `column_context_requested(col_index, global_pos)` to edit `name` and `anchor` in `project_config.json`, then re-runs inference (if words are loaded) or re-validates the table.
- **Reading & OCR (`main_window.py`, `pdf_ocr.py`, `pdf_text.py`)**:
  - PaddleOCR must stay on its worker thread (`_OcrGridWorker`, `_OcrWordsWorker`, `_OcrBatchWorker`) with `PADDLE_PDX_ENABLE_MKLDNN_BYDEFAULT=0` and `FLAGS_use_mkldnn=0` set before import.
  - When a page loads or changes, valid `<stem>.ocr.json` words are loaded automatically (`_apply_cached_ocr_for_current_page`); if the page also has a table region, project `col-fracs` and `_infer_grid(silent=True)` run automatically.
- **Grid & region inference (`grid_infer.py`)**:
  - `infer_table_region(words, origin, size, hints, x_bounds=...)` detects the normalized `(x0, y0, x1, y1)` line-item band on a page by classifying approval stamps, table headers, multi-column numeric data anchors, wrapped prefix/suffix lines, and summary/subtotal/footer stop bands, and fitting `(x0, x1)` so `col_fracs` dividers sit in the column whitespace gutters. Returns `None` on non-table pages.
  - `infer_from_words(words, origin, size, hints)` strips any skewed header words bleeding across the top boundary, clusters words into horizontal `RowBand`s, classifies spine/anchor bands vs wrapped/sparse bands using `hints.anchor_cols` and vertical regularity, merges wrapped continuation bands into logical data rows, and stops at summary/subtotal stop bands.
  - `header_count` is `0` because the drawn region contains line items only.
- **Validation display (`main_window.py`, `grid_overlay.py`)**:
  - `_validate_table()` runs `ProjectValidations` on every table population (`_infer_grid`, `_read_line_items`, `_ocr_grid`) and cell edit (`itemChanged`), colouring invalid cells red with a fault tooltip in both `QTableWidget` and `GridOverlay` (`set_invalid_cells`).

## Work Guidance

- Keep `grid_infer.py` pure Python (no Qt imports) so layout heuristics stay unit-testable.
- Run `uv sync && uv run python main.py` from repo root to launch the app locally.

## Verification

- `uv run python -m unittest discover -s tests`

## Child DOX Index

- `invoice_app/validation/AGENTS.md` — config-driven line-item validation runner and strategies
- `invoice_app/examples/AGENTS.md` — sample `project_config.json` and `document_config.json` files
