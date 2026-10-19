# Tests

## Purpose

Automated unit tests for validation rules, grid inference, project/sidecar persistence, PDF/OCR coordinate transforms, and overlay geometry in `invoice_app`.

## Ownership

- Test modules under `tests/` (`test_*.py`)

## Local Contracts

- Prefer fast, headless unit tests against pure functions, dataclasses, and offscreen geometry (`QT_QPA_PLATFORM=offscreen`) without requiring live PaddleOCR model downloads or interactive GUI sessions.
- `test_validations.py` — `hours-times-rate-equals-total`, `sum-should-equal-total`, `between-values`, `regex`, and `ProjectValidations` runner (deduplication, unknown strategy handling, empty config).
- `test_grid_infer.py` — `filter_words_in_region`, `filter_words_in_columns`, `infer_row_bands`, `infer_from_words` (saved `col_fracs`, cold-start numeric column inference, multi-line trailing and leading wrapped descriptions, single-row multi-line items, tax-code columns, subtotal exclusion), and `infer_table_region` (line-item band detection between header and subtotal, leading/trailing wraps, and `None` on non-table pages).
- `test_projects.py` — `config_root`, `list_projects`, `Project` load/save, `col-fracs`, `table-x-range`, `columns` (`name` and `anchor`), `list_batches`, and `AppState` persistence.
- `test_sidecars.py` — `ocr_cache.py` (`write_cache`, `load_cache`, `load_page_words`, `load_skew_by_page`, `cache_matches_pdf` DST/SMB mtime tolerance, `median_skew_deg`) and `document_layout.py` (`save_page_region`, `load_layout`).
- `test_pdf_text_and_ocr.py` — `pdf_text.py` (`words_in_pdf_rect`, `words_in_rect`, `words_on_page` via in-memory PyMuPDF document) and `pdf_ocr.py` (`assign_to_grid`, `page_pdf_rect`, `region_image`, `words_raster_to_pdf`, `lines_raster_to_pdf`).
- `test_overlay_and_deskew.py` — `_rotation_bounds` deskew canvas expansion (`pdf_panel.py`) and `GridOverlay` cell geometry and invalid-cell state reset (`grid_overlay.py`).

## Work Guidance

## Verification

- `uv run python -m unittest discover -s tests -v`

## Child DOX Index
