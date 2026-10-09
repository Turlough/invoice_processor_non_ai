# Invoice Processor (PoC)

PyQt6 desktop app for extracting **line items** from supplier invoices stored as PDF. The first milestone ignores header metadata (supplier name, invoice number, etc.) and focuses on aligning a user-defined grid with the line-item table, then reading cell text from the PDF’s existing text layer (typical of OCR’d scans).

## Is “Read → Line items” feasible?

**Yes.** Most “OCR’d” invoice PDFs are not plain images: they contain a **hidden text layer** with Unicode strings and coordinates for each word or glyph. This PoC uses [PyMuPDF](https://pymupdf.readthedocs.io/) (`page.get_text("words")`) to collect positioned words and assign them to grid cells by bounding box. No live OCR is required for that path.

Limitations to be aware of:

| Situation | Behaviour |
|-----------|-----------|
| PDF has a text layer (OCR embedded) | Works; align the grid, then **Read → Line items** |
| PDF is image-only (no text objects) | No words returned; you would need an OCR step (out of scope for this PoC) |
| Skewed scans or inconsistent OCR | Cells may need manual cleanup in the grid; later we can add snap-to-text or template profiles per supplier |

## UI layout

Three panels (horizontal splitter):

1. **Left** — PDF filenames from the folder chosen via **File → Load folder…**
2. **Centre** — Current PDF page with **Rows** / **Cols** spin boxes; red dashed grid after **Layout → Draw grid**
3. **Right** — `QTableWidget` with the same row/column count as the grid

### Grid interaction

- The PDF page **scales to fit** the centre panel (resizes with the splitter).
- Set **Rows** and **Cols** to match the line-item table on the invoice.
- **Layout → Table region…** — click and drag on the page to outline the table.
- **Layout → Draw grid** creates evenly spaced dividers inside the region.
- **Drag** internal horizontal or vertical lines (not the outer border) so cell boundaries match the printed/OCR layout.
- **Read → Line items** fills the right-hand table from PDF text in each cell.

## Requirements

- Python ≥ 3.13
- Dependencies: `PyQt6`, `pymupdf` (see `pyproject.toml`)

## Run locally

```bash
cd invoice_processor
uv sync
uv run python main.py
```

Or after install:

```bash
invoice-processor
```

## Project layout

```
main.py                 # Application entry
invoice_app/
  main_window.py        # Menus, splitter, read pipeline
  pdf_panel.py          # PDF render + row/col controls
  grid_overlay.py       # Draggable grid lines
  pdf_text.py           # Word extraction and cell assignment
AGENTS.md               # Guidance for AI agents (DOX)
```

## Roadmap (not implemented yet)

- Multi-page invoices and page selector
- Per-supplier layout templates (save grid fractions and column semantics)
- Header/metadata extraction
- Export (CSV/Excel)
- Image-only PDF fallback OCR

## GitLab / workflow

Use conventional commits; reference GitLab issues as `#123` in commits and merge requests when applicable.
