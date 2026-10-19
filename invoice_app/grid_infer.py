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
    header_row_count: int = 0
    col_fracs: list[float] | None = None
    column_names: list[str] = field(default_factory=list)
    anchor_cols: list[bool] | None = None


@dataclass
class InferResult:
    row_fracs: list[float]
    col_fracs: list[float]
    data_row_count: int
    rows: list[dict[str, str]]
    header_band_count: int
    # One dict per leading grid row (same column keys as ``rows``).
    header_rows: list[dict[str, str]] = field(default_factory=list)


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
        for i in range(slots):
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


def _sort_cell_words(words: list[Word], line_h: float) -> list[Word]:
    """Order words in a cell top-to-bottom by line band, then left-to-right."""
    if len(words) <= 1:
        return list(words)
    threshold = MERGE_GAP_FACTOR * max(line_h, 1.0)
    by_cy = sorted(words, key=lambda w: _word_centre(w)[1])
    lines: list[list[Word]] = [[by_cy[0]]]
    for w in by_cy[1:]:
        cy = _word_centre(w)[1]
        prev_cy = _median([_word_centre(p)[1] for p in lines[-1]])
        if cy - prev_cy > threshold:
            lines.append([w])
        else:
            lines[-1].append(w)
    ordered: list[Word] = []
    for line in lines:
        ordered.extend(sorted(line, key=lambda w: w.x0))
    return ordered


def _classify_anchor_bands(
    data_bands: list[RowBand],
    occ_list: list[set[int]],
    n_cols: int,
    allowed_anchors: set[int] | None = None,
) -> tuple[list[bool], set[int]]:
    """Identify anchor bands (one per logical row) and wrapping/non-anchor column indices."""
    n = len(data_bands)
    if n <= 1 or n_cols <= 1:
        return [True] * n, set()

    if allowed_anchors is not None:
        candidate_cols = {c for c in allowed_anchors if 0 <= c < n_cols}
    else:
        candidate_cols = set(range(n_cols))

    if not candidate_cols:
        candidate_cols = set(range(n_cols))

    # User explicitly narrowed down anchor columns: any band occupying at least one
    # user-selected anchor column is an anchor candidate, and disabled columns are non-anchor.
    user_narrowed = len(candidate_cols) < n_cols

    col_bands: dict[int, list[int]] = {}
    for i, occ in enumerate(occ_list):
        for c in occ:
            col_bands.setdefault(c, []).append(i)
    if not col_bands:
        return [True] * n, set()

    cand_with_data = [c for c in candidate_cols if c in col_bands]
    if not cand_with_data:
        cand_with_data = list(col_bands.keys())

    # Check vertical regularity of each candidate column to distinguish fully-populated
    # single-line spine columns from sparse columns (which have missing-row holes).
    table_y0 = data_bands[0].y0
    table_y1 = data_bands[-1].y1
    table_span = max(table_y1 - table_y0, 1.0)

    regular_counts: list[int] = []
    for c in cand_with_data:
        idxs = col_bands[c]
        if len(idxs) < 2:
            continue
        cys = [(data_bands[i].y0 + data_bands[i].y1) / 2 for i in idxs]
        steps = [cys[k + 1] - cys[k] for k in range(len(cys) - 1)]
        med_step = max(_median(steps), 1.0)
        max_step = max(steps)
        top_margin = cys[0] - table_y0
        bot_margin = table_y1 - cys[-1]
        # No missing-row holes inside the column, and no missing first/last row.
        if (
            max_step < 1.65 * med_step
            and top_margin < 1.6 * med_step
            and bot_margin < 1.6 * med_step
            and len(idxs) * med_step >= 0.7 * table_span
        ):
            regular_counts.append(len(idxs))

    if len(regular_counts) >= 2:
        # Take the mode (most common count) among regular full-span columns,
        # breaking ties toward the smaller count (single-line over multi-line).
        freq_map: dict[int, int] = {}
        for cnt in regular_counts:
            freq_map[cnt] = freq_map.get(cnt, 0) + 1
        spine_freq = min(freq_map.keys(), key=lambda cnt: (-freq_map[cnt], cnt))
    else:
        # Fallback when rows have uneven heights or very few rows:
        counts = sorted(len(col_bands[c]) for c in cand_with_data)
        spine_freq = int(round(_median([float(x) for x in counts])))

    non_anchor_cols = {c for c in range(n_cols) if c not in candidate_cols}
    wrap_cols = set(non_anchor_cols)
    for c, idxs in col_bands.items():
        if len(idxs) > spine_freq:
            wrap_cols.add(c)

    spine_cols = {
        c
        for c in cand_with_data
        if c not in wrap_cols and abs(len(col_bands[c]) - spine_freq) <= max(1, int(0.15 * spine_freq))
    }

    is_anchor: list[bool] = []
    for occ in occ_list:
        cand_occ = occ & candidate_cols
        if not cand_occ:
            is_anchor.append(False)
        elif spine_cols and (cand_occ & spine_cols):
            is_anchor.append(True)
        elif user_narrowed and not (cand_occ <= wrap_cols):
            is_anchor.append(True)
        elif len(cand_occ) == 1 and next(iter(cand_occ)) in wrap_cols:
            is_anchor.append(False)
        elif wrap_cols and cand_occ <= wrap_cols:
            is_anchor.append(False)
        elif len(occ) == 1 and not user_narrowed and spine_freq > 1 and len(col_bands[next(iter(occ))]) > spine_freq:
            is_anchor.append(False)
        else:
            is_anchor.append(True)

    if not any(is_anchor):
        return [True] * n, set()
    return is_anchor, wrap_cols


def _anchor_wrap_offset(
    band: RowBand,
    occ: set[int],
    wrap_cols: set[int],
    col_fracs: list[float],
    origin: tuple[float, float],
    size: tuple[float, float],
) -> float:
    """Signed vertical offset (cy_wrap - cy_spine) inside an anchor band.

    Positive means the wrapped column's word sits lower than the spine columns
    (typical of bottom-aligned rows where the first wrapped line is above).
    Negative means it sits higher (typical of top-aligned rows).
    """
    if not wrap_cols or not (occ & wrap_cols) or not (occ - wrap_cols):
        return 0.0
    wrap_cys: list[float] = []
    spine_cys: list[float] = []
    for w in band.words:
        nx, _ = _to_region_norm(w, origin, size)
        c = _assign_col(nx, col_fracs)
        cy = _word_centre(w)[1]
        if c in wrap_cols:
            wrap_cys.append(cy)
        else:
            spine_cys.append(cy)
    if not wrap_cys or not spine_cys:
        return 0.0
    return _median(wrap_cys) - _median(spine_cys)


def _merge_bands_into_logical_rows(
    bands: list[RowBand],
    col_fracs: list[float],
    origin: tuple[float, float],
    size: tuple[float, float],
    header_count: int,
    anchor_cols: list[bool] | None = None,
) -> list[list[RowBand]]:
    if header_count >= len(bands):
        return []
    raw_bands = bands[header_count:]
    ox, _ = origin
    rw, _ = size
    n_cols = max(len(col_fracs) - 1, 1)
    data_bands: list[RowBand] = []
    for band in raw_bands:
        if not data_bands:
            # Strip column-header words that bled across the top edge of the
            # region on a skewed scan (e.g. "HOURS | RATE | TOTAL" on the right
            # while Row 1's date already started on the left).
            kept_words = tuple(
                w
                for w in band.words
                if w.text.strip().lower() not in _BLED_HEADER_WORDS
            )
            if not kept_words:
                continue
            if len(kept_words) != len(band.words):
                band = RowBand(
                    index=band.index,
                    y0=min(w.y0 for w in kept_words),
                    y1=max(w.y1 for w in kept_words),
                    words=kept_words,
                )
        occ = _occupied_columns(band, col_fracs, origin, size)
        if len(occ) < max(4, n_cols - 1) and _is_stop_band(band, ox, rw):
            break
        data_bands.append(band)
    if not data_bands:
        return []

    word_heights = [_word_height(w) for b in data_bands for w in b.words]
    line_h = max(_median(word_heights), 1.0)
    max_gap = WRAP_GAP_FACTOR * line_h
    occ_list = [_occupied_columns(b, col_fracs, origin, size) for b in data_bands]

    allowed_anchors: set[int] | None = None
    if anchor_cols is not None:
        allowed_anchors = {
            c for c in range(n_cols) if (anchor_cols[c] if c < len(anchor_cols) else True)
        }

    is_anchor, wrap_cols = _classify_anchor_bands(
        data_bands, occ_list, n_cols, allowed_anchors=allowed_anchors
    )
    anchor_indices = [i for i, flag in enumerate(is_anchor) if flag]
    if not anchor_indices:
        return [[b] for b in data_bands]

    sparse_cols = set(wrap_cols)
    for i, flag in enumerate(is_anchor):
        if not flag:
            sparse_cols |= occ_list[i]

    groups: list[list[RowBand]] = [[data_bands[idx]] for idx in anchor_indices]
    leading_orphans: list[list[RowBand]] = []
    trailing_orphans: list[list[RowBand]] = []

    # 1. Leading sparse bands before the first anchor can only attach downward.
    first_anchor = anchor_indices[0]
    has_leading_prefix = False
    for idx in range(first_anchor - 1, -1, -1):
        cand = data_bands[idx]
        if groups[0][0].y0 - cand.y1 < max_gap:
            groups[0].insert(0, cand)
            has_leading_prefix = True
        else:
            leading_orphans = [[data_bands[j]] for j in range(idx + 1)]
            break

    # 2. Trailing sparse bands after the last anchor can only attach upward.
    last_anchor = anchor_indices[-1]
    has_trailing_suffix = False
    for idx in range(last_anchor + 1, len(data_bands)):
        cand = data_bands[idx]
        if cand.y0 - groups[-1][-1].y1 < max_gap:
            groups[-1].append(cand)
            has_trailing_suffix = True
        else:
            trailing_orphans = [[data_bands[j]] for j in range(idx, len(data_bands))]
            break

    # 3. Estimate table-wide alignment bias (leading/bottom vs trailing/top vs centered).
    center_aligned = has_leading_prefix and has_trailing_suffix
    if has_leading_prefix and not has_trailing_suffix:
        boundary_bias = -1.0  # Sparse bands lead their anchor (bottom-aligned rows).
    elif has_trailing_suffix and not has_leading_prefix:
        boundary_bias = 1.0  # Sparse bands trail their anchor (top-aligned rows).
    else:
        boundary_bias = 0.0

    offsets = [
        _anchor_wrap_offset(
            data_bands[idx], occ_list[idx], sparse_cols, col_fracs, origin, size
        )
        for idx in anchor_indices
    ]
    nonzero_offsets = [dy for dy in offsets if abs(dy) > 1e-3]
    median_offset = _median(nonzero_offsets) if nonzero_offsets else 0.0

    # 4. Partition each interior run of sparse bands between anchor k and anchor k+1.
    interior_orphans: dict[int, list[list[RowBand]]] = {}
    for k in range(len(anchor_indices) - 1):
        a_prev_idx = anchor_indices[k]
        a_next_idx = anchor_indices[k + 1]
        run = data_bands[a_prev_idx + 1 : a_next_idx]
        if not run:
            continue

        m = len(run)
        chain = [data_bands[a_prev_idx], *run, data_bands[a_next_idx]]
        # gaps[s] is the vertical gap if the row boundary is placed at cut s (0..m).
        gaps = [chain[s + 1].y0 - chain[s].y1 for s in range(m + 1)]

        max_up = 0
        while max_up < m and gaps[max_up] < max_gap:
            max_up += 1
        min_down = m
        while min_down > 0 and gaps[min_down] < max_gap:
            min_down -= 1

        if min_down <= max_up:
            best_s = max_up
            best_score = float("-inf")
            prev_occ = occ_list[a_prev_idx]
            next_occ = occ_list[a_next_idx]
            run_cols = set().union(*(occ_list[a_prev_idx + 1 + j] for j in range(m)))
            both_missing_wrap = bool(run_cols) and run_cols.isdisjoint(prev_occ) and run_cols.isdisjoint(next_occ)

            for s in range(min_down, max_up + 1):
                score = gaps[s]
                if m >= 2 and (center_aligned or both_missing_wrap) and s == m // 2:
                    score += 0.35 * line_h
                if s == 0:
                    if boundary_bias < 0:
                        score += 0.35 * line_h
                    score += 0.5 * median_offset
                    # If only the lower anchor is missing the sparse column, prefer attaching down.
                    if run_cols and run_cols.isdisjoint(next_occ) and not run_cols.isdisjoint(prev_occ):
                        score += 0.25 * line_h
                if s == m:
                    if boundary_bias > 0:
                        score += 0.35 * line_h
                    score -= 0.5 * median_offset
                    if run_cols and run_cols.isdisjoint(prev_occ) and not run_cols.isdisjoint(next_occ):
                        score += 0.25 * line_h
                    # Tiny tie-breaker favoring trailing continuation when all signals are equal.
                    score += 1e-4 * line_h

                if score > best_score:
                    best_score = score
                    best_s = s

            groups[k].extend(run[:best_s])
            groups[k + 1] = run[best_s:] + groups[k + 1]
        else:
            groups[k].extend(run[:max_up])
            groups[k + 1] = run[min_down:] + groups[k + 1]
            interior_orphans[k] = [[b] for b in run[max_up:min_down]]

    logical: list[list[RowBand]] = []
    logical.extend(leading_orphans)
    for k, grp in enumerate(groups):
        logical.append(grp)
        if k in interior_orphans:
            logical.extend(interior_orphans[k])
    logical.extend(trailing_orphans)
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
    line_h = max(_median([_word_height(w) for w in words]), 1.0) if words else 1.0
    by_col: dict[int, list[Word]] = {}
    for w in words:
        nx, _ = _to_region_norm(w, origin, size)
        col = _assign_col(nx, col_fracs)
        by_col.setdefault(col, []).append(w)
    row: dict[str, str] = {}
    n_cols = len(col_fracs) - 1
    for c in range(n_cols):
        key = column_names[c] if c < len(column_names) else f"col_{c + 1}"
        cell_words = _sort_cell_words(by_col.get(c, []), line_h)
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

    if row_words:
        min_wy0 = min(w.y0 for w in row_words)
        med_wh = max(_median([_word_height(w) for w in row_words]), 1.0)
        row_words = [
            w
            for w in row_words
            if not (
                (w.y0 - min_wy0) <= 1.2 * med_wh
                and w.text.strip().lower() in _BLED_HEADER_WORDS
            )
        ]

    bands = infer_row_bands(row_words, origin, size)
    if not bands:
        return InferResult(
            row_fracs=[0.0, 1.0],
            col_fracs=col_fracs,
            data_row_count=0,
            rows=[],
            header_band_count=0,
            header_rows=[],
        )

    if not saved_cols:
        col_fracs = _infer_col_fracs_from_numbers(filtered, origin, size, n_cols)
        col_fracs = _refine_col_fracs(filtered, origin, size, col_fracs)

    # The drawn region is the line-item band only. Column headings sit above
    # it and are often absent on later pages, so do not peel bands off as headers.
    header_count = 0
    header_bands = bands[:header_count]
    logical = _merge_bands_into_logical_rows(
        bands, col_fracs, origin, size, header_count, anchor_cols=hints.anchor_cols
    )
    row_fracs = _logical_rows_to_fracs(header_bands, logical, origin, size)
    header_rows = [
        _row_dict([band], col_fracs, origin, size, hints.column_names)
        for band in header_bands
    ]
    rows = [
        _row_dict(group, col_fracs, origin, size, hints.column_names)
        for group in logical
    ]
    return InferResult(
        row_fracs=row_fracs,
        col_fracs=col_fracs,
        data_row_count=len(logical),
        rows=rows,
        header_band_count=len(header_rows),
        header_rows=header_rows,
    )


_APPROVAL_STAMP_RE = re.compile(
    r"(?i)\bstatus\s*:\s*approved\b|\buser\s*:|\bstatus\b.*\bapproved\b"
)

_TABLE_HEADER_TOKENS = frozenset(
    {
        "date",
        "dates",
        "day",
        "description",
        "details",
        "activity",
        "employee",
        "staff",
        "name",
        "candidate",
        "worker",
        "person",
        "position",
        "grade",
        "role",
        "type",
        "shift",
        "shifts",
        "time",
        "start",
        "end",
        "break",
        "week",
        "period",
        "hours",
        "hrs",
        "units",
        "unit",
        "qty",
        "quantity",
        "rate",
        "rates",
        "price",
        "charge",
        "net",
        "tax",
        "vat",
        "amount",
        "total",
        "eur",
        "authorisor",
        "half",
        "pay",
    }
)

_TABLE_HEADER_METRIC_TOKENS = frozenset(
    {
        "hours",
        "hrs",
        "units",
        "unit",
        "qty",
        "quantity",
        "rate",
        "rates",
        "price",
        "amount",
        "total",
        "charge",
        "net",
    }
)

_BLED_HEADER_WORDS = _TABLE_HEADER_METRIC_TOKENS | frozenset(
    {
        "date",
        "name",
        "position",
        "time",
        "description",
        "details",
        "activity",
        "employee",
        "vat%",
    }
)

_STRONG_STOP_RE = re.compile(
    r"(?i)\b(?:"
    r"sub\s*total|subtotal|balance\s+due|grand\s+total|invoice\s+total|"
    r"total\s+eur|total\s+amount|remittance\s+due|remittance\s+advice|"
    r"total\s*\(\s*excl|total\s*\(\s*incl|total\s+vat|net\s+amount|"
    r"total\s+standard\s+vat|payments?\s+by\s+bank|please\s+pay|"
    r"bank(?:ing)?\s+details|assignment\s+notice|shift\s+notes|"
    r"company\s+reg(?:istration)?|registered\s+office|company\s+number"
    r")\b"
)

_FOOTER_CONTACT_RE = re.compile(
    r"(?i)\b(?:"
    r"iban|bic|swift|sort\s*code|account\s*(?:no|number)|"
    r"email\s*:|tel\s*:|vat\s*(?:reg|no|number)"
    r")\b"
)

_SUMMARY_LABEL_RE = re.compile(
    r"(?i)^(?:total|vat\b|tax\b|net\b|balance\b|amount\s+due|sub\s*total|subtotal)"
)

_METADATA_LINE_RE = re.compile(
    r"(?i)\b(?:"
    r"invoice\s*(?:no|number|date)|due\s*date|po\s*number|"
    r"account\s*name|terms\s*:"
    r")\b"
)

_DATA_NUMBER_RE = re.compile(r"^[^\dA-Za-z]*\d+(?:[.,]\d+)*\s*%?$")
_DECIMAL_OR_DATE_RE = re.compile(
    r"\d+\.\d{2}|\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b"
)


def _band_norm_x_span(
    band: RowBand, ox: float, rw: float
) -> tuple[float, float]:
    if not band.words or rw <= 0:
        return 0.0, 0.0
    bx0 = (min(w.x0 for w in band.words) - ox) / rw
    bx1 = (max(w.x1 for w in band.words) - ox) / rw
    return bx0, bx1


def _is_stamp_band(band: RowBand, ny0: float) -> bool:
    if ny0 > 0.08:
        return False
    text = _band_text(band)
    if _APPROVAL_STAMP_RE.search(text):
        return True
    if ny0 < 0.045 and re.search(r"(?i)\bdate\s*:", text):
        return True
    return False


def _is_table_header_band(
    band: RowBand,
    column_names: list[str],
    ox: float,
    rw: float,
) -> bool:
    if len(band.words) < 3:
        return False
    bx0, bx1 = _band_norm_x_span(band, ox, rw)
    if bx1 - bx0 < 0.35:
        return False
    text = _band_text(band)
    if _DECIMAL_OR_DATE_RE.search(text):
        return False
    custom_tokens = {
        tok
        for name in column_names
        for tok in re.split(r"[^a-zA-Z]+", name.lower())
        if len(tok) >= 2
    }
    vocab = _TABLE_HEADER_TOKENS | custom_tokens
    alpha_tokens = [
        tok
        for w in band.words
        for tok in re.split(r"[^a-zA-Z]+", w.text.lower())
        if len(tok) >= 2
    ]
    if len(alpha_tokens) < 3:
        return False
    matched = [tok for tok in alpha_tokens if tok in vocab]
    distinct_matched = set(matched)
    has_metric = bool(distinct_matched & (_TABLE_HEADER_METRIC_TOKENS | custom_tokens))
    return (
        len(distinct_matched) >= 3
        and has_metric
        and len(matched) >= 0.55 * len(alpha_tokens)
    )


def _is_stop_band(band: RowBand, ox: float, rw: float) -> bool:
    text = _band_text(band)
    if _STRONG_STOP_RE.search(text) or _FOOTER_CONTACT_RE.search(text):
        return True
    bx0, _ = _band_norm_x_span(band, ox, rw)
    if _SUMMARY_LABEL_RE.search(text.strip()) and (bx0 > 0.35 or len(band.words) <= 5):
        return True
    return False


def _is_data_anchor_band(
    band: RowBand,
    ny0: float,
    ox: float,
    rw: float,
    col_fracs: list[float] | None,
    column_names: list[str],
) -> bool:
    if len(band.words) < 4:
        return False
    if _is_stamp_band(band, ny0):
        return False
    if _is_stop_band(band, ox, rw):
        return False
    if _is_table_header_band(band, column_names, ox, rw):
        return False
    text = _band_text(band)
    if _METADATA_LINE_RE.search(text):
        return False

    bx0, bx1 = _band_norm_x_span(band, ox, rw)
    if bx0 > 0.32 or bx1 < 0.62 or (bx1 - bx0) < 0.45:
        return False

    ordered = sorted(band.words, key=lambda w: (w.x0 + w.x1) / 2.0)
    if not _DATA_NUMBER_RE.match(ordered[-1].text.strip()):
        return False

    right_numbers = sum(
        1
        for w in ordered
        if (((w.x0 + w.x1) / 2.0 - ox) / rw) > 0.45
        and _DATA_NUMBER_RE.match(w.text.strip())
    )
    if right_numbers < 2:
        return False

    if col_fracs and len(col_fracs) >= 4:
        n_cols = len(col_fracs) - 1
        span = max(bx1 - bx0, 1e-4)
        occupied = {
            _assign_col((((w.x0 + w.x1) / 2.0 - ox) / rw - bx0) / span, col_fracs)
            for w in ordered
        }
        if len(occupied) < max(3, (n_cols + 1) // 2):
            return False

    return True


def _fit_x_bounds_to_col_fracs(
    table_words: list[Word],
    ox: float,
    rw: float,
    col_fracs: list[float] | None,
    x_bounds: tuple[float, float] | None,
) -> tuple[float, float]:
    raw_x0 = (min(w.x0 for w in table_words) - ox) / rw
    raw_x1 = (max(w.x1 for w in table_words) - ox) / rw
    default_x0 = max(0.0, raw_x0 - 0.008)
    default_x1 = min(1.0, raw_x1 + 0.008)

    if not col_fracs or len(col_fracs) < 3:
        if x_bounds is not None:
            return (
                min(default_x0, max(0.0, x_bounds[0])),
                max(default_x1, min(1.0, x_bounds[1])),
            )
        return default_x0, default_x1

    internal = [f for f in col_fracs[1:-1] if 0.0 < f < 1.0]
    if not internal:
        return default_x0, default_x1

    word_spans = [
        (max(0.0, (w.x0 - ox) / rw), min(1.0, (w.x1 - ox) / rw))
        for w in table_words
    ]

    left_pads = [0.004, 0.008, 0.014, 0.022, 0.032, 0.045, 0.058]
    right_pads = [0.004, 0.008, 0.014, 0.022, 0.032, 0.045, 0.058]
    candidates: list[tuple[float, float]] = []
    for lp in left_pads:
        for rp in right_pads:
            cx0 = max(0.002, raw_x0 - lp)
            cx1 = min(0.998, raw_x1 + rp)
            if cx1 - cx0 >= 0.35:
                candidates.append((cx0, cx1))

    if x_bounds is not None:
        bx0 = min(raw_x0 - 0.003, max(0.002, x_bounds[0]))
        bx1 = max(raw_x1 + 0.003, min(0.998, x_bounds[1]))
        if bx1 - bx0 >= 0.35:
            candidates.append((bx0, bx1))

    best_pair = (default_x0, default_x1)
    best_score = float("inf")
    for cx0, cx1 in candidates:
        span = cx1 - cx0
        dividers = [cx0 + f * span for f in internal]
        straddle_penalty = 0.0
        clearance_Need = 0.0
        for d in dividers:
            min_dist = 0.02
            for wx0, wx1 in word_spans:
                if wx0 < d < wx1:
                    overlap = min(d - wx0, wx1 - d)
                    straddle_penalty += 100.0 * overlap + 1.0
                    min_dist = 0.0
                else:
                    dist = min(abs(d - wx0), abs(d - wx1))
                    if dist < min_dist:
                        min_dist = dist
            clearance_Need += (0.02 - min_dist) * 2.0

        reg = 0.25 * ((cx0 - default_x0) ** 2 + (cx1 - default_x1) ** 2)
        if x_bounds is not None:
            reg += 0.15 * ((cx0 - x_bounds[0]) ** 2 + (cx1 - x_bounds[1]) ** 2)
        score = straddle_penalty + clearance_Need + reg
        if score < best_score:
            best_score = score
            best_pair = (cx0, cx1)

    return best_pair


def infer_table_region(
    words: list[Word],
    origin: tuple[float, float],
    size: tuple[float, float],
    hints: InferHints,
    *,
    x_bounds: tuple[float, float] | None = None,
) -> tuple[float, float, float, float] | None:
    """Detect the normalized ``(x0, y0, x1, y1)`` line-item table region on a page.

    Returns ``None`` when the page contains no line-item rows matching the
    project layout (e.g. T&C pages, bank-detail backs, or timesheet backups).
    """
    ox, oy = origin
    rw, rh = size
    if rw <= 0 or rh <= 0:
        return None

    filtered = filter_words_in_region(words, origin, size)
    if len(filtered) < 4:
        return None

    bands = infer_row_bands(filtered, origin, size)
    if not bands:
        return None

    line_h = max(_median([_word_height(w) for w in filtered]), 1.0)
    col_fracs = (
        list(hints.col_fracs)
        if hints.col_fracs and len(hints.col_fracs) >= 2
        else None
    )

    header_indices = [
        i
        for i, b in enumerate(bands)
        if not _is_stamp_band(b, (b.y0 - oy) / rh)
        and _is_table_header_band(b, hints.column_names, ox, rw)
    ]

    all_anchors = [
        i
        for i, b in enumerate(bands)
        if _is_data_anchor_band(
            b, (b.y0 - oy) / rh, ox, rw, col_fracs, hints.column_names
        )
    ]
    if not all_anchors:
        return None

    # If a table header sits above data anchors, restrict to bands below it.
    top_header_idx: int | None = None
    for h_idx in header_indices:
        if any(a > h_idx for a in all_anchors):
            top_header_idx = h_idx

    if top_header_idx is not None:
        anchor_indices = [a for a in all_anchors if a > top_header_idx]
    else:
        anchor_indices = list(all_anchors)

    if not anchor_indices:
        return None

    first_anchor = anchor_indices[0]

    # Stop at the first summary/subtotal/footer band or a large vertical break
    # after the first data anchor.
    first_stop_idx = len(bands)
    prev_anchor_y1 = bands[first_anchor].y1
    for idx in range(first_anchor + 1, len(bands)):
        b = bands[idx]
        if _is_stop_band(b, ox, rw):
            first_stop_idx = idx
            break
        if idx in anchor_indices:
            if (b.y0 - prev_anchor_y1) > 8.0 * line_h:
                first_stop_idx = idx
                break
            prev_anchor_y1 = b.y1

    anchor_indices = [a for a in anchor_indices if a < first_stop_idx]
    if not anchor_indices:
        return None

    last_anchor = anchor_indices[-1]
    anchor_words = [w for i in anchor_indices for w in bands[i].words]
    anchor_min_x = (min(w.x0 for w in anchor_words) - ox) / rw

    # Include wrapped prefix bands immediately above first_anchor (e.g. 2-line dates
    # or wrapped shift/staff cells on bottom-aligned rows such as Vista).
    start_idx = first_anchor
    min_allowed_idx = (top_header_idx + 1) if top_header_idx is not None else 0
    while start_idx > min_allowed_idx:
        cand = bands[start_idx - 1]
        ny0 = (cand.y0 - oy) / rh
        gap = bands[start_idx].y0 - cand.y1
        if gap > 0.85 * line_h:
            break
        if (
            _is_stamp_band(cand, ny0)
            or _is_stop_band(cand, ox, rw)
            or _is_table_header_band(cand, hints.column_names, ox, rw)
        ):
            break
        cand_text = _band_text(cand)
        if _METADATA_LINE_RE.search(cand_text):
            break
        cx0, cx1 = _band_norm_x_span(cand, ox, rw)
        has_lower_or_digit = bool(re.search(r"[a-z0-9]", cand_text))
        if (
            cx0 >= max(0.02, anchor_min_x - 0.12)
            and cx1 <= 0.68
            and has_lower_or_digit
        ):
            start_idx -= 1
        else:
            break

    # Include wrapped suffix bands immediately below last_anchor (e.g. trailing
    # date year on Glenlorn or multi-line description on TTM / Access Nursing).
    end_idx = last_anchor
    max_trailing = min(first_stop_idx - 1, last_anchor + 3)
    while end_idx < max_trailing:
        cand = bands[end_idx + 1]
        ny0 = (cand.y0 - oy) / rh
        gap = cand.y0 - bands[end_idx].y1
        if gap > 0.85 * line_h:
            break
        if (
            _is_stamp_band(cand, ny0)
            or _is_stop_band(cand, ox, rw)
            or _is_table_header_band(cand, hints.column_names, ox, rw)
        ):
            break
        cand_text = _band_text(cand)
        if _METADATA_LINE_RE.search(cand_text):
            break
        cx0, cx1 = _band_norm_x_span(cand, ox, rw)
        if cx0 >= max(0.02, anchor_min_x - 0.12) and cx1 <= 0.68:
            end_idx += 1
        else:
            break

    table_bands = bands[start_idx : end_idx + 1]
    table_words = [w for b in table_bands for w in b.words]
    if not table_words:
        return None

    x0, x1 = _fit_x_bounds_to_col_fracs(
        table_words, ox, rw, col_fracs, x_bounds
    )

    raw_y0 = (min(b.y0 for b in table_bands) - oy) / rh
    raw_y1 = (max(b.y1 for b in table_bands) - oy) / rh
    pad_y = min(0.005, 0.3 * (line_h / rh))

    y0 = max(0.0, raw_y0 - pad_y)
    if start_idx > 0:
        upper_limit = ((bands[start_idx - 1].y1 + bands[start_idx].y0) / 2.0 - oy) / rh
        y0 = max(y0, min(raw_y0, upper_limit))

    y1 = min(1.0, raw_y1 + pad_y)
    if end_idx + 1 < len(bands):
        lower_limit = ((bands[end_idx].y1 + bands[end_idx + 1].y0) / 2.0 - oy) / rh
        y1 = min(y1, max(raw_y1, lower_limit))

    if x1 - x0 < 0.05 or y1 - y0 < 0.005:
        return None

    return (
        max(0.0, min(1.0, x0)),
        max(0.0, min(1.0, y0)),
        max(0.0, min(1.0, x1)),
        max(0.0, min(1.0, y1)),
    )

