# Plan: OCR-based grid inference (prototype)

This document tracks the **auto-layout prototype**: infer table row/column structure and line items from **OCR (or text-layer) box positions only**, without OpenCV grid detection in v1. Use it across agent sessions; update checkboxes and notes as work completes.

Canonical product context remains in **AGENTS.md**. Example JSON shapes live under **`invoice_app/examples/`** (align samples with this plan when they drift).

---

## Goal

Reduce manual grid alignment for homogeneous supplier batches by:

1. Clustering positioned text boxes into **rows** and **columns**.
2. Merging **wrapped description** lines into single **data rows**.
3. Emitting the same geometry the UI already uses: **`row_fracs`**, **`col_fracs`** (0..1 inside the table region), plus **`line-items`** keyed by project column names.
4. Learning stable **column gutters** at project level; keeping **row count** per document.

Manual draw/drag in the UI remains the fallback and ground truth when inference is wrong.

---

## Config model (target)

| Layer | File | Location |
|-------|------|----------|
| Project | `project_config.json` | `CONFIG_DIRECTORY/<project>/` |
| Document | `<pdf-basename>.json` | Same directory as the PDF (e.g. `batch/INV-001.pdf` → `batch/INV-001.json`) |

**Project (shared layout / schema)**

- `schema-version`
- `data-directory` (existing)
- `header-row-count` — header bands inside the grid; **not** counted in document `row-count`
- `table-band` — `{ "left", "right", "top" }` in **normalised page coords** (0..1); bottom derived from rows per document
- `col-fracs` — length = `len(columns) + 1`, fractions **inside the table region** (same semantics as `GridOverlay.col_fracs`)
- `columns[]` — `name`, `datatype` (not `type`; avoid JSON Schema clashes)
- Optional default stride for first grid seed: `row-height-frac` or `row-height-pt` (treat as **hint only** when scans vary; document `row-fracs` overrides)

**Document (per PDF)**

- `schema-version`
- `page` — 0-based
- `table-region` — optional full `[x0, y0, x1, y1]` normalised override; `null` = use project band + computed bottom
- `row-fracs` — inside table region; length = `header-row-count + row-count + 1`
- `row-count` — **data rows only** (no header)
- `line-items[]` — one object per data row; keys = project `columns[].name`; cell values as strings until parsed by `datatype`

**Coordinates**

- Overlay / persisted fracs: **normalised 0..1** on page (region) or inside region (grid lines), matching `grid_overlay.py` and `assign_to_grid` in `pdf_ocr.py`.
- Inference prototype may compute in **table-region pixel space** (`origin`, `size` from `region_image`) then convert to fracs.

---

## Approach: OCR text-box clustering (not pixel grids)

Invoices rarely show visible rules. Detect **alignment** of OCR/text boxes:

- **Rows:** cluster by vertical centre (`cy`); split when gap > threshold × median line height.
- **Columns:** assign by horizontal centre (`cx`) using project `col-fracs` prior, or discover gutters from numeric column alignment; refine per document; **median across batch** for project defaults.
- **Wrapped descriptions:** merge consecutive OCR bands when only the left column has text and the previous band has numeric columns populated.

**Reuse existing code**

- `Word` dataclass and centre-in-rect assignment: `invoice_app/pdf_text.py`, `words_in_rect`.
- OCR lines → words: `invoice_app/pdf_ocr.py` (`ocr_image`, `_lines_from_result`).
- Cell fill from fracs: `assign_to_grid` / `read_grid`.

**Explicitly defer**

- OpenCV morphological / projection grid finding (optional fallback later).
- Full-page table detection without a coarse band (user draw or project `table-band` + generous bottom in v1).
- New OCR engines (AGENTS.md).

---

## Module sketch

Add **`invoice_app/grid_infer.py`** (pure functions, no Qt; unit-testable).

```text
InferHints
  n_cols, header_row_count, col_fracs | None, min_score

InferResult
  row_fracs, col_fracs, data_row_count, rows (list[dict[str, str]]), debug (optional)

infer_from_words(words, rw, rh, hints, column_names) -> InferResult
infer_table_region_image(image, origin, size, hints, column_names) -> InferResult
median_col_fracs(list of per-doc col_fracs) -> list[float]
```

Optional later: CLI `python -m invoice_app.infer_demo path/to.pdf` before wiring a menu action.

**UI integration (milestone 4+)**

- Prerequisite: table region exists (rubber band or loaded config).
- Flow: `region_image` → `ocr_image` → infer → apply `row_fracs`/`col_fracs` to `GridOverlay` + fill table / write sidecar JSON.
- Run heavy OCR off the UI thread (same pattern as `_OcrGridWorker` in `main_window.py`).

---

## Prototype milestones

Track progress by marking `[x]` when done.

### Step 0 — Row bands

- [ ] `infer_row_bands(words, rw, rh) -> list[RowBand]` (or equivalent)
- [ ] Sort by `cy`; split on gap > `MERGE_GAP_FACTOR * median_line_height`
- [ ] Filter boxes to table `size` (ignore padding noise from `region_image` `_PAD_PT`)
- [ ] Unit tests: synthetic `Word` lists in fixed `rw=1000`, `rh=800`; no PDF/Paddle

**Done when:** band count matches expected OCR lines on fixtures.

---

### Step 1 — Wrapped row merge

- [ ] Assign each band’s boxes to columns (needs tentative or prior `col_fracs`)
- [ ] Merge consecutive bands: continuation in col 0 only + anchor row with numeric cols + gap < `WRAP_GAP_FACTOR * h`
- [ ] Do not merge subtotal/tax lines (`SUBTOTAL_REGEX` on text)
- [ ] Tests: two-line description + qty/amount on first line → **one** data row; header excluded via `header_row_count`

**Done when:** `data_row_count` matches logical line items on fixtures.

---

### Step 2 — Column inference

- [ ] With project prior: assign by `nx`, refine interior boundaries (median gutter positions on data rows)
- [ ] Cold start (no prior): numeric `x1` clustering for right columns; description left
- [ ] Enforce `MIN_COL_WIDTH_FRAC`; output length `n_cols + 1`
- [ ] Tests: three-column fixture → stable `col_fracs`

**Done when:** inferred gutters match manual alignment on 2–3 real crops.

---

### Step 3 — Full `infer_grid`

- [ ] `infer_from_words` → `InferResult` (`row_fracs`, `col_fracs`, `data_row_count`, `rows`)
- [ ] Build `row_fracs` from band tops/bottoms; length = `header_row_count + data_row_count + 1`
- [ ] Validate: optional `assign_to_grid` matches cluster-first cell text (cross-check)
- [ ] Debug payload: raw bands, merges, assignments (JSON log for tuning)
- [ ] Tests: header + N data rows; subtotal row not in `line-items`

**Done when:** end-to-end on 2–3 real PDFs (OCR path) matches or is close to manual grid.

---

### Step 4 — App or CLI wiring

- [ ] CLI or **Read → Infer layout** (name TBD): load PDF + region → infer → overlay + table
- [ ] Optional: shrink region bottom to last inferred row + padding
- [ ] Document sidecar write: `row-count`, `row-fracs`, `line-items` (and `page`)

**Done when:** user can open PDF, define region, run infer, see grid without drawing row/col lines first.

---

### Step 5 — Batch column learning

- [ ] Script or tool: run infer on many PDFs in a batch; collect per-doc `col_fracs`
- [ ] `median_col_fracs` → update project `col-fracs` (manual review before save)
- [ ] Document when to keep per-doc override (large deviation from project median)

**Done when:** project config stabilises columns for a batch; new docs need mainly row adjustment.

---

## Tuning constants (initial)

| Constant | Suggested | Purpose |
|----------|-----------|---------|
| `MERGE_GAP_FACTOR` | 0.6 | New row band if vertical gap > factor × median line height |
| `WRAP_GAP_FACTOR` | 1.2 | Max gap to merge wrapped description |
| `MIN_COL_WIDTH_FRAC` | 0.05 | Minimum column width in normalised table width |
| `MIN_OCR_SCORE` | 0.3 | Already in `pdf_ocr._MIN_SCORE` |
| `SUBTOTAL_REGEX` | `(?i)subtotal\|total\|vat\|tax\|balance` | Stop merge / exclude from data rows (heuristic) |

Tune per supplier using debug JSON from step 3.

---

## Test fixtures (synthetic)

Create in tests (no real PDFs required for steps 0–2):

1. Three data rows, single OCR line each, three columns.
2. One data row: two description lines + qty/amount on first line only.
3. One header band + two data rows.
4. Subtotal line: must not merge into previous item row.

Text-layer PDFs: group `words_on_page` into synthetic lines (same `cy` within tolerance) before reusing the same pipeline.

---

## Pitfalls

- **Padding:** `region_image` clip pad can add boxes outside logical table; filter by coordinates relative to unpadded `size`.
- **Header in grid:** UI row count includes header; **`row-count` in JSON is data-only**; `row_fracs` includes header bands.
- **Scan variability:** project `row-height-pt` / `row-height-frac` is not reliable across operators; prefer per-doc `row-fracs` after first infer or manual drag.
- **Paddle thread:** keep OCR on one worker thread (existing constraint).
- **Page index:** reads today use page 0; sidecar `page` for future multi-page.

---

## Session notes

_Agents: append dated bullets here (decisions, branch names, what failed, supplier-specific tuning)._

- _(none yet)_
