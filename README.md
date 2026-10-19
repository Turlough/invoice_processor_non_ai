# Invoice Processor (PoC)

PyQt6 desktop app for extracting **line items** from supplier invoices stored as PDF. The first milestone ignores header metadata (supplier name, invoice number, etc.) and focuses on aligning a user-defined grid with the line-item table, then reading each cell — from the PDF text layer, or with PaddleOCR when the page is image-only.

## Is “Read → Line items” feasible?

**Yes.** Most “OCR’d” invoice PDFs are not plain images: they contain a **hidden text layer** with Unicode strings and coordinates for each word or glyph. This PoC uses [PyMuPDF](https://pymupdf.readthedocs.io/) (`page.get_text("words")`) to collect positioned words and assign them to grid cells by bounding box. No live OCR is required for that path.

Limitations to be aware of:

| Situation | Behaviour |
|-----------|-----------|
| PDF has a text layer | Align the grid, then **Read → Line items** |
| PDF is image-only (no text objects) | Align the grid, then **Read → OCR grid** (PaddleOCR) |
| Skewed scans | Opening a page levels it from `skew-deg` in the OCR sidecar. The rotated page is not cropped, and it still fits the preview |

## UI layout

Three panels (horizontal splitter):

1. **Left** — PDF filenames from the batch chosen via **File → Project** (pick a project) then **File → Load batch** (pick a batch folder)
2. **Centre** — Current PDF page with page navigation (◀ ▶, page spin box), zoom controls, and **Rows** / **Cols** spin boxes; red dashed grid after **Layout → Draw grid**
3. **Right** — `QTableWidget` with the same row/column count as the grid

### Grid interaction

- The PDF page **scales to fit** the centre panel by default (**Auto**). Zoom controls — **Auto**, **Height**, **Width**, **−**, **+** — switch between fit modes and manual zoom; scroll bars appear when the page overflows. Page controls (◀ ▶ or the spin box) move through multi-page documents; the grid carries over between pages.
- Set **Rows** and **Cols** to match the line-item table on the invoice.
- Opening a PDF or changing page levels that page from `skew-deg` in the OCR sidecar, when the sidecar has one. The page is not cropped, and Auto zoom fits the expanded result in the centre panel.
- **Layout → Table region…** — click and drag on the page to outline the table.
- **Layout → Draw grid** creates evenly spaced dividers inside the region.
- **Drag** internal horizontal or vertical lines (not the outer border) so cell boundaries match the printed/OCR layout.
- **Read → Line items** fills the right-hand table from PDF text in each cell.
- **Read → OCR grid** renders the table region and fills the same table with [PaddleOCR](https://www.paddleocr.ai/). The first run downloads the PP-OCRv6 models and can take several minutes. Later runs reuse them. The window stays responsive while recognition runs.

## Requirements

- Python ≥ 3.13
- Dependencies: `PyQt6`, `pymupdf`, `paddleocr`, `paddlepaddle`, `python-dotenv` (see `pyproject.toml`)
- Network access the first time you use **Read → OCR grid** (model download)

## Configuration

Copy `env.example` to `.env` and point `CONFIG_DIRECTORY` at your config directory. Each subdirectory there is a **project** holding a `project_config.json`:

```
CONFIG_DIRECTORY/
  Emerald/
    project_config.json     # { "data-directory": "/path/to/invoice/root" }
```

- **File → Project** lists the project directories. Picking one creates `project_config.json` if missing, and asks for the project's **data directory** (a folder picker) if the config has no `data-directory` yet — the choice is written back to the JSON file.
- **File → Load batch** lists the subdirectories of the data directory. Each is a *batch*: a folder of similar invoice PDFs shown in the left panel.

So: many projects, each with many batches.

The last-used project, batch and PDF are stored per user in the OS config location (`%APPDATA%\invoice_processor\app_config.json` on Windows, `~/.config/invoice_processor/app_config.json` on Linux) and restored on the next launch.

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
  projects.py           # CONFIG_DIRECTORY, project_config.json, batches
  pdf_panel.py          # PDF render + row/col controls
  grid_overlay.py       # Draggable grid lines
  pdf_text.py           # Word extraction and cell assignment
  pdf_ocr.py            # PaddleOCR on the table region
AGENTS.md               # Guidance for AI agents (DOX)
```

## Roadmap (not implemented yet)

- Per-supplier layout templates (save grid fractions and column semantics)
- Header/metadata extraction
- Export (CSV/Excel)

## GitLab / workflow

Use conventional commits; reference GitLab issues as `#123` in commits and merge requests when applicable.
