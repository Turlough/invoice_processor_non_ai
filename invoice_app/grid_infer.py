"""Infer table grid lines from positioned text boxes (OCR or PDF text layer)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from invoice_app.pdf_text import Word

MERGE_GAP_FACTOR = 0.6
WRAP_GAP_FACTOR = 1.2
MIN_COL_WIDTH_FRAC = 0.05
SUBTOTAL_PATTERN = re.compile(r"(?i)\b(subtotal|total|vat|tax|balance)\b")


@dataclass(frozen=True)
class RowBand:
    """Horizontal band of OCR/text lines (before merge into logical rows)."""

    index: int
    y0: float
    y1: float
    words: tuple[Word, ...]


@dataclass
class InferHints:
    n_cols: int
    header_row_count: int = 1
    col_fracs: list[float] | None = None
    column_names: list[str] = field(default_factory=list)


@dataclass
class InferResult:
    row_fracs: list[float]
    col_fracs: list[float]
    data_row_count: int
    rows: list[dict[str, str]]
    header_band_count: int


def _median(values: list[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2


def _word_centre(w: Word) -> tuple[float, float]:
    return ((w.x0 + w.x1) / 2, (w.y0 + w.y1) / 2)


def _word_height(w: Word) -> float:
    return max(w.y1 - w.y0, 1.0)


def filter_words_in_columns(
    words: list[Word],
    origin: tuple[float, float],
    size: tuple[float, float],
    col_fracs: list[float],
) -> list[Word]:
    """Keep words whose horizontal centre lies inside a column band."""
    if len(col_fracs) < 2:
        return list(words)
    kept: list[Word] = []
    for w in words:
        nx, _ = _to_region_norm(w, origin, size)
        for col in range(len(col_fracs) - 1):
            if col_fracs[col] <= nx <= col_fracs[col + 1]:
                kept.append(w)
                break
    return kept


def filter_words_in_region(
    words: list[Word],
    origin: tuple[float, float],
    size: tuple[float, float],
) -> list[Word]:
    """Keep words whose centre lies in the table region (pixel coords)."""
    ox, oy = origin
    rw, rh = size
    if rw <= 0 or rh <= 0:
        return []
    x1, y1 = ox + rw, oy + rh
    kept: list[Word] = []
    for w in words:
        cx, cy = _word_centre(w)
        if ox <= cx <= x1 and oy <= cy <= y1:
            kept.append(w)
    return kept


def infer_row_bands(
    words: list[Word],
    origin: tuple[float, float],
    size: tuple[float, float],
) -> list[RowBand]:
    """Cluster words into horizontal bands by vertical centre."""
    ox, oy = origin
    rw, rh = size
    if not words:
        return []
    heights = [_word_height(w) for w in words]
    line_h = max(_median(heights), 1.0)
    threshold = MERGE_GAP_FACTOR * line_h

    sorted_words = sorted(words, key=lambda w: _word_centre(w)[1])
    bands: list[list[Word]] = [[sorted_words[0]]]
    for w in sorted_words[1:]:
        cy = _word_centre(w)[1]
        prev_cy = _median([_word_centre(p)[1] for p in bands[-1]])
        if cy - prev_cy > threshold:
            bands.append([w])
        else:
            bands[-1].append(w)

    result: list[RowBand] = []
    for i, group in enumerate(bands):
        y0 = min(w.y0 for w in group)
        y1 = max(w.y1 for w in group)
        result.append(RowBand(index=i, y0=y0, y1=y1, words=tuple(group)))
    return result


def _to_region_norm(w: Word, origin: tuple[float, float], size: tuple[float, float]) -> tuple[float, float]:
    ox, oy = origin
    rw, rh = size
    cx, cy = _word_centre(w)
    return ((cx - ox) / rw, (cy - oy) / rh)


def _assign_col(nx: float, col_fracs: list[float]) -> int:
    for col in range(len(col_fracs) - 1):
        if col_fracs[col] <= nx <= col_fracs[col + 1]:
            return col
    if nx < col_fracs[0]:
        return 0
    return len(col_fracs) - 2


def _default_col_fracs(n_cols: int) -> list[float]:
    return [i / n_cols for i in range(n_cols + 1)]


def _infer_col_fracs_from_numbers(
    words: list[Word],
    origin: tuple[float, float],
    size: tuple[float, float],
    n_cols: int,
) -> list[float]:
    if n_cols < 1:
        return [0.0, 1.0]
    numeric_re = re.compile(r"^[\d.,]+$")
    right_edges: list[float] = []
    for w in words:
        if numeric_re.match(w.text.strip()):
            nx, _ = _to_region_norm(w, origin, size)
            right_edges.append(nx)
    if len(right_edges) >= n_cols - 1 and n_cols > 1:
        right_edges.sort()
        # Rightmost clusters for amount columns.
        slots = n_cols - 1
        step = max(len(right_edges) // slots, 1)
        gutters = [0.0]
        for i in range(1, slots):
            gutters.append(right_edges[min(i * step, len(right_edges) - 1)])
        gutters.append(1.0)
        # Ensure strictly increasing with minimum width.
        for i in range(1, len(gutters)):
            gutters[i] = max(gutters[i], gutters[i - 1] + MIN_COL_WIDTH_FRAC)
        gutters[-1] = 1.0
        return gutters
    return _default_col_fracs(n_cols)


def _refine_col_fracs(
    words: list[Word],
    origin: tuple[float, float],
    size: tuple[float, float],
    col_fracs: list[float],
) -> list[float]:
    if len(col_fracs) < 2:
        return col_fracs
    refined = list(col_fracs)
    for boundary in range(1, len(refined) - 1):
        samples: list[float] = []
        for w in words:
            nx, _ = _to_region_norm(w, origin, size)
            left = refined[boundary - 1]
            right = refined[boundary + 1]
            if left + MIN_COL_WIDTH_FRAC < nx < right - MIN_COL_WIDTH_FRAC:
                if abs(nx - refined[boundary]) < 0.08:
                    samples.append(nx)
        if samples:
            refined[boundary] = _median(samples)
    for i in range(1, len(refined)):
        refined[i] = max(refined[i], refined[i - 1] + MIN_COL_WIDTH_FRAC)
    refined[-1] = 1.0
    return refined


def _band_text(band: RowBand) -> str:
    parts = sorted(band.words, key=lambda w: (w.y0, w.x0))
    return " ".join(w.text for w in parts)


def _band_matches_column_headings(band: RowBand, column_names: list[str]) -> bool:
    if not column_names:
        return False
    text = _band_text(band).lower()
    hits = sum(1 for name in column_names if name.strip() and name.lower() in text)
    return hits >= max(1, len(column_names) // 2)


def _occupied_columns(
    band: RowBand,
    col_fracs: list[float],
    origin: tuple[float, float],
    size: tuple[float, float],
) -> set[int]:
    cols: set[int] = set()
    for w in band.words:
        nx, _ = _to_region_norm(w, origin, size)
        cols.add(_assign_col(nx, col_fracs))
    return cols


def _merge_bands_into_logical_rows(
    bands: list[RowBand],
    col_fracs: list[float],
    origin: tuple[float, float],
    size: tuple[float, float],
    header_count: int,
) -> list[list[RowBand]]:
    if header_count >= len(bands):
        return []
    data_bands = bands[header_count:]
    if not data_bands:
        return []

    heights = [b.y1 - b.y0 for b in data_bands]
    line_h = max(_median(heights), 1.0)
    logical: list[list[RowBand]] = []
    i = 0
    while i < len(data_bands):
        band = data_bands[i]
        if SUBTOTAL_PATTERN.search(_band_text(band)):
            break
        group = [band]
        j = i + 1
        while j < len(data_bands):
            nxt = data_bands[j]
            if SUBTOTAL_PATTERN.search(_band_text(nxt)):
                break
            gap = nxt.y0 - group[-1].y1
            occ = _occupied_columns(nxt, col_fracs, origin, size)
            anchor = _occupied_columns(group[0], col_fracs, origin, size)
            continuation = occ <= {0} or (len(occ) == 1 and 0 in occ)
            has_numeric = any(c > 0 for c in anchor)
            if continuation and has_numeric and gap < WRAP_GAP_FACTOR * line_h:
                group.append(nxt)
                j += 1
            else:
                break
        logical.append(group)
        i = j if j > i + 1 else i + 1
    return logical


def _bands_to_row_fracs(
    bands: list[RowBand],
    origin: tuple[float, float],
    size: tuple[float, float],
) -> list[float]:
    _, oy = origin
    _, rh = size
    if not bands or rh <= 0:
        return [0.0, 1.0]
    fracs = [0.0]
    for i in range(len(bands) - 1):
        bottom = bands[i].y1
        top_next = bands[i + 1].y0
        mid = ((bottom - oy) + (top_next - oy)) / (2 * rh)
        fracs.append(max(0.0, min(1.0, mid)))
    fracs.append(1.0)
    return fracs


def _logical_rows_to_fracs(
    header_bands: list[RowBand],
    logical_rows: list[list[RowBand]],
    origin: tuple[float, float],
    size: tuple[float, float],
) -> list[float]:
    all_bands = list(header_bands)
    for group in logical_rows:
        merged_y0 = min(b.y0 for b in group)
        merged_y1 = max(b.y1 for b in group)
        words = tuple(w for b in group for w in b.words)
        all_bands.append(
            RowBand(index=len(all_bands), y0=merged_y0, y1=merged_y1, words=words)
        )
    return _bands_to_row_fracs(all_bands, origin, size)


def _row_dict(
    group: list[RowBand],
    col_fracs: list[float],
    origin: tuple[float, float],
    size: tuple[float, float],
    column_names: list[str],
) -> dict[str, str]:
    words = [w for b in group for w in b.words]
    by_col: dict[int, list[Word]] = {}
    for w in words:
        nx, _ = _to_region_norm(w, origin, size)
        col = _assign_col(nx, col_fracs)
        by_col.setdefault(col, []).append(w)
    row: dict[str, str] = {}
    n_cols = len(col_fracs) - 1
    for c in range(n_cols):
        key = column_names[c] if c < len(column_names) else f"col_{c + 1}"
        cell_words = sorted(by_col.get(c, []), key=lambda w: (w.y0, w.x0))
        row[key] = " ".join(w.text for w in cell_words)
    return row


def infer_from_words(
    words: list[Word],
    origin: tuple[float, float],
    size: tuple[float, float],
    hints: InferHints,
) -> InferResult:
    """Infer grid fractions and line-item rows from words in table-region pixel space."""
    saved_cols = hints.col_fracs if hints.col_fracs and len(hints.col_fracs) >= 2 else None
    n_cols = len(saved_cols) - 1 if saved_cols else max(hints.n_cols, 1)
    filtered = filter_words_in_region(words, origin, size)

    if saved_cols:
        col_fracs = list(saved_cols)
        row_words = filter_words_in_columns(filtered, origin, size, col_fracs)
    else:
        row_words = filtered
        col_fracs = _default_col_fracs(n_cols)

    bands = infer_row_bands(row_words, origin, size)
    if not bands:
        return InferResult(
            row_fracs=[0.0, 1.0],
            col_fracs=col_fracs,
            data_row_count=0,
            rows=[],
            header_band_count=0,
        )

    if not saved_cols:
        col_fracs = _infer_col_fracs_from_numbers(filtered, origin, size, n_cols)
        col_fracs = _refine_col_fracs(filtered, origin, size, col_fracs)

    header_count = hints.header_row_count
    if hints.column_names:
        for i, band in enumerate(bands[: min(4, len(bands))]):
            if _band_matches_column_headings(band, hints.column_names):
                header_count = max(header_count, i + 1)
                break

    header_bands = bands[:header_count]
    logical = _merge_bands_into_logical_rows(
        bands, col_fracs, origin, size, header_count
    )
    row_fracs = _logical_rows_to_fracs(header_bands, logical, origin, size)
    rows = [
        _row_dict(group, col_fracs, origin, size, hints.column_names)
        for group in logical
    ]
    return InferResult(
        row_fracs=row_fracs,
        col_fracs=col_fracs,
        data_row_count=len(logical),
        rows=rows,
        header_band_count=header_count,
    )
