# Agent guidance (DOX)

This file is the canonical context for AI agents working on **invoice_processor**. Update it when architecture, conventions, or scope change.

## Product intent

Batch-process supplier invoices (PDF, often OCR’d) into structured line items. Formats cluster by **supplier**; the PoC assumes **one layout** and manual grid alignment. Metadata (supplier block, totals, tax) is deferred.

## PoC scope (current)

- PyQt6: file list | PDF preview + draggable grid | line-item table
- **File → Load folder** — list `*.pdf` in a directory
- **Layout → Table region…** — rubber-band rectangle (normalized coords on preview)
- **Layout → Draw grid** — cell grid inside region only; user drags internal lines
- Centre panel fits page to available area (`PdfPanel._apply_fit`)
- **Read → Line items** — map cells to PDF coordinates; fill table from `get_text("words")` via PyMuPDF

Do **not** add OCR engines, cloud APIs, or supplier templates unless the user asks.

## Tech stack

| Layer | Choice |
|-------|--------|
| UI | PyQt6 |
| PDF render + text | PyMuPDF (`fitz`) |
| Packaging | `pyproject.toml`, Python ≥ 3.13 |

## Code conventions

- Keep changes minimal and match existing module boundaries under `invoice_app/`.
- PDF coordinates: use page user space; map from widget pixels in `PdfPanel.widget_to_pdf_rect`.
- Grid state: normalized fractions `0..1` in `GridOverlay` (`row_fracs`, `col_fracs`).
- No commits unless the user explicitly requests them.

## Extension points (future)

1. **`pdf_text.py`** — cell assignment heuristics (reading order, multi-line cells, header row skip).
2. **`grid_overlay.py`** — optional region-of-interest box limiting grid to line-item band only.
3. **Templates** — JSON per supplier: fractions, column names, page index.
4. **Persistence** — save grid + table edits per file.

## Feasibility notes for agents

- **Read from existing OCR text layer**: standard PDF text extraction; implemented in PoC.
- **Image-only PDFs**: require Tesseract/cloud OCR; not in repo yet.
- **Multi-page**: extend `PdfPanel` page index and file list behaviour.

## Commands

```bash
uv sync && python main.py
```

## User docs

Human-oriented setup and UI description: **README.md**.
