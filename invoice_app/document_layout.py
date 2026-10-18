"""Persist per-page table regions beside each PDF as ``<stem>.json``."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from invoice_app.ocr_cache import cache_matches_pdf

SCHEMA_VERSION = 1


def layout_path_for_pdf(pdf_path: Path) -> Path:
    return pdf_path.with_suffix(".json")


def _parse_region(raw: Any) -> tuple[float, float, float, float] | None:
    if not isinstance(raw, list) or len(raw) != 4:
        return None
    try:
        x0, y0, x1, y1 = (float(v) for v in raw)
    except (TypeError, ValueError):
        return None
    left, right = sorted((x0, x1))
    top, bottom = sorted((y0, y1))
    if right - left < 0.01 or bottom - top < 0.01:
        return None
    return (left, top, right, bottom)


def _parse_layout_payload(
    payload: dict[str, Any],
) -> dict[int, tuple[float, float, float, float]]:
    pages_raw = payload.get("pages")
    if not isinstance(pages_raw, list):
        return {}
    regions: dict[int, tuple[float, float, float, float]] = {}
    for entry in pages_raw:
        if not isinstance(entry, dict):
            continue
        try:
            page_index = int(entry.get("page", -1))
        except (TypeError, ValueError):
            continue
        if page_index < 0:
            continue
        region = _parse_region(entry.get("table-region"))
        if region is not None:
            regions[page_index] = region
    return regions


def _read_layout_file(pdf_path: Path) -> dict[str, Any] | None:
    path = layout_path_for_pdf(pdf_path)
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    if payload.get("schema-version") != SCHEMA_VERSION:
        return None
    return payload


def load_layout(pdf_path: Path) -> dict[int, tuple[float, float, float, float]]:
    """Return page index → normalised table region (0..1 on page preview)."""
    payload = _read_layout_file(pdf_path)
    if payload is None or not cache_matches_pdf(pdf_path, payload):
        return {}
    return _parse_layout_payload(payload)


def save_page_region(
    pdf_path: Path,
    page_index: int,
    region: tuple[float, float, float, float] | None,
) -> None:
    """Merge one page's region into the sidecar and write it."""
    from invoice_app.ocr_cache import pdf_source_fingerprint

    path = layout_path_for_pdf(pdf_path)
    payload = _read_layout_file(pdf_path)
    existing = _parse_layout_payload(payload) if payload is not None else {}
    if region is None:
        existing.pop(page_index, None)
    else:
        existing[page_index] = region
    mtime, size = pdf_source_fingerprint(pdf_path)
    pages = [
        {"page": idx, "table-region": list(reg)}
        for idx, reg in sorted(existing.items())
    ]
    payload = {
        "schema-version": SCHEMA_VERSION,
        "source-mtime": mtime,
        "source-size": size,
        "pages": pages,
    }
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
