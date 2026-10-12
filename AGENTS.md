# Agent guidance (DOX)

This file is the canonical context for AI agents working on **invoice_processor**. Update it when architecture, conventions, or scope change.

## Product intent

Batch-process supplier invoices (PDF, often OCR’d) into structured line items. Formats cluster by **supplier**; the PoC assumes **one layout** and manual grid alignment. Metadata (supplier block, totals, tax) is deferred.

## PoC scope (current)

- PyQt6: file list | PDF preview + draggable grid | line-item table
- `.env` sets `CONFIG_DIRECTORY` (loaded via python-dotenv); each subdirectory is a **project** with a `project_config.json` (`invoice_app/projects.py`)
- **File → Project** — pick a project; creates `project_config.json` if missing, folder-picks and records `data-directory` if unset
- **File → Load batch** — list `*.pdf` from a subdirectory of the project's `data-directory` (a *batch*)
- Last project/batch/PDF persist per user via `platformdirs` (`AppState` in `projects.py`, `app_config.json` under the OS config dir — not in `CONFIG_DIRECTORY`); restored at startup, saved on every selection change
- **Layout → Table region…** — rubber-band rectangle (normalized coords on preview)
- **Layout → Draw grid** — cell grid inside region only; user drags internal lines. If the project has saved **`col-fracs`**, only vertical dividers are drawn (no rows yet).
- **Layout → Save column fracs** — writes overlay `col_fracs` to `project_config.json` (`col-fracs` key).
- **Read → Infer grid** — OCR/text boxes in the table region; with saved `col-fracs`, columns are fixed and only rows are inferred (`grid_infer.py`).
- Centre panel fits page to available area (`PdfPanel._apply_fit`)
- **Read → Line items** — map cells to PDF coordinates; fill table from `get_text("words")` via PyMuPDF
- **Read → OCR grid** — rasterise the table region and fill the same table with PaddleOCR (`pdf_ocr.py`). Use this when the page has no text layer. One OCR pass; line boxes are assigned to cells by centre, same idea as line items
- **Read → OCR batch** — PaddleOCR every page of every PDF in the loaded batch; writes `<stem>.ocr.json` beside each PDF (`ocr_cache.py`). Skips files whose sidecar matches PDF mtime/size. **Read → Show text rectangles** loads page 0 from the sidecar when present

Do **not** add other OCR engines, cloud APIs, or supplier templates unless the user asks. Grid OCR stays on PaddleOCR.

## Tech stack

| Layer | Choice |
|-------|--------|
| UI | PyQt6 |
| PDF render + text | PyMuPDF (`fitz`) |
| OCR | PaddleOCR 3.x (PP-OCRv6) on PaddlePaddle 3.2.2, CPU, oneDNN off |
| Packaging | `pyproject.toml`, Python ≥ 3.13 |

## Code conventions

- Keep changes minimal and match existing module boundaries under `invoice_app/`.
- PDF coordinates: use page user space; map from widget pixels in `PdfPanel.widget_to_pdf_rect`.
- Grid state: normalized fractions `0..1` in `GridOverlay` (`row_fracs`, `col_fracs`).
- No commits unless the user explicitly requests them.

## Extension points (future)

1. **`pdf_text.py` / `pdf_ocr.py`** — cell assignment heuristics (reading order, multi-line cells, header row skip). OCR runs off the UI thread in `_OcrGridWorker` so the Paddle engine stays on one thread.
2. **`grid_overlay.py`** — optional region-of-interest box limiting grid to line-item band only.
3. **Templates** — JSON per supplier: fractions, column names, page index.
4. **Persistence** — save grid + table edits per file.

## Feasibility notes for agents

- **Read from existing text layer**: **Read → Line items** (`page.get_text("words")`).
- **Image-only PDFs**: **Read → OCR grid**. First run downloads PP-OCRv6 models (network). PaddlePaddle stays pinned at 3.2.2; 3.3.x CPU oneDNN breaks repeated inference.
- **Multi-page**: extend `PdfPanel` page index and file list behaviour. Both reads currently use page 0.

## Commands

```bash
uv sync && python main.py
```

## User docs

Human-oriented setup and UI description: **README.md**.
