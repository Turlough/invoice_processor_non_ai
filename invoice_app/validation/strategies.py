"""Validation strategy definitions: config-driven rules executed per line-item row."""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

_CURRENCY_STRIP_RE = re.compile(r"[,\s€£$]")


@dataclass
class ValidationContext:
    """Everything a validation strategy needs for a single line-item row."""

    field_values: dict[str, Any]
    field_names: list[str] | None
    params: dict[str, Any] = field(default_factory=dict)
    field_to_page: dict[str, int] | None = None
    row_index: int = 0


def _parse_numeric_field_value(raw: Any) -> float | None:
    """Parse a cell value as float; None if blank, raises ValueError if invalid."""
    if raw is None:
        return None
    text = str(raw).strip()
    if text == "":
        return None
    cleaned = _CURRENCY_STRIP_RE.sub("", text)
    if cleaned == "":
        raise ValueError(f"Empty numeric string after stripping currency from {raw!r}")
    return float(cleaned)


def _strategy_hours_times_rate_equals_total(
    ctx: ValidationContext,
) -> list[tuple[int, str, str]]:
    """Check that ``field_names[0] * field_names[1] == field_names[2]`` within tolerance.

    ``field_names`` order: ``[hours, rate, total]``.
    ``params``:
        ``tolerance`` (float, optional): maximum allowed absolute difference
        between ``hours * rate`` and ``total``. Defaults to ``0.01`` (1 cent).

    Blank handling:
        - If all three fields are blank, the row is skipped (no fault).
        - If only some fields are blank or non-numeric, each blank/invalid field
          is reported as a fault.
    """
    if not ctx.field_names or len(ctx.field_names) < 3:
        return []

    pages = ctx.field_to_page or {}
    hours_field, rate_field, total_field = (
        ctx.field_names[0],
        ctx.field_names[1],
        ctx.field_names[2],
    )

    raw_hours = ctx.field_values.get(hours_field)
    raw_rate = ctx.field_values.get(rate_field)
    raw_total = ctx.field_values.get(total_field)

    def _is_blank(val: Any) -> bool:
        return val is None or str(val).strip() == ""

    if _is_blank(raw_hours) and _is_blank(raw_rate) and _is_blank(raw_total):
        return []

    faults: list[tuple[int, str, str]] = []
    parsed: dict[str, float] = {}

    for field_name, raw_val in (
        (hours_field, raw_hours),
        (rate_field, raw_rate),
        (total_field, raw_total),
    ):
        page = pages.get(field_name, 1)
        if _is_blank(raw_val):
            faults.append((page, field_name, f"Missing value for '{field_name}'."))
            continue
        try:
            val = _parse_numeric_field_value(raw_val)
        except ValueError:
            faults.append(
                (page, field_name, f"Value '{raw_val}' is not a valid number.")
            )
            continue
        if val is not None:
            parsed[field_name] = val

    if faults:
        return faults

    hours = parsed[hours_field]
    rate = parsed[rate_field]
    total = parsed[total_field]

    raw_tol = ctx.params.get("tolerance", 0.01)
    try:
        tolerance = float(raw_tol)
    except (TypeError, ValueError):
        tolerance = 0.01

    expected = hours * rate
    if abs(expected - total) > tolerance + 1e-9:
        total_page = pages.get(total_field, 1)
        return [
            (
                total_page,
                total_field,
                (
                    f"{hours_field} ({hours:g}) * {rate_field} ({rate:g}) = "
                    f"{expected:.2f}, but {total_field} is {total:.2f}."
                ),
            )
        ]

    return []


def _strategy_sum_should_equal_total(
    ctx: ValidationContext,
) -> list[tuple[int, str, str]]:
    """Check that the sum of ``field_names[1:]`` equals ``field_names[0]``."""
    if not ctx.field_names:
        return []
    pages = ctx.field_to_page or {}
    faults: list[tuple[int, str, str]] = []

    total_field = ctx.field_names[0]
    total_value = ctx.field_values.get(total_field)
    total_page = pages.get(total_field, 1)

    try:
        parsed_total = _parse_numeric_field_value(total_value)
    except ValueError:
        return [
            (
                total_page,
                total_field,
                f"Total value '{total_value}' is not a valid number.",
            )
        ]
    total = 0.0 if parsed_total is None else parsed_total

    raw_tol = ctx.params.get("tolerance", 0.01)
    try:
        tolerance = float(raw_tol)
    except (TypeError, ValueError):
        tolerance = 0.01

    sum_of_fields = 0.0
    for field_name in ctx.field_names[1:]:
        value = ctx.field_values.get(field_name)
        page = pages.get(field_name, 1)
        try:
            num = _parse_numeric_field_value(value)
        except ValueError:
            faults.append(
                (page, field_name, f"Value '{value}' is not a valid number.")
            )
            continue
        if num is not None:
            sum_of_fields += num

    if abs(sum_of_fields - total) > tolerance + 1e-9:
        faults.append(
            (
                total_page,
                total_field,
                f"The sum of the fields is {sum_of_fields:.2f}, but the total is {total:.2f}.",
            )
        )
    return faults


def _strategy_between_values(
    ctx: ValidationContext,
) -> list[tuple[int, str, str]]:
    """Check that each field value in ``field_names`` is between ``min`` and ``max``."""
    if not ctx.field_names:
        return []
    pages = ctx.field_to_page or {}
    min_value = ctx.params.get("min")
    max_value = ctx.params.get("max")
    if min_value is None or max_value is None:
        return []
    try:
        min_f = float(min_value)
        max_f = float(max_value)
    except (TypeError, ValueError):
        return []

    faults: list[tuple[int, str, str]] = []
    for field_name in ctx.field_names:
        raw = ctx.field_values.get(field_name)
        page = pages.get(field_name, 1)
        try:
            val = _parse_numeric_field_value(raw)
        except ValueError:
            faults.append(
                (page, field_name, f"Value '{raw}' is not a valid number.")
            )
            continue
        if val is None:
            continue
        if val < min_f or val > max_f:
            faults.append(
                (
                    page,
                    field_name,
                    f"Value '{raw}' is not between {min_value} and {max_value}.",
                )
            )
    return faults


def _strategy_regex(ctx: ValidationContext) -> list[tuple[int, str, str]]:
    """Check that each non-empty field value matches ``params['regex']``."""
    if not ctx.field_names:
        return []
    regex = ctx.params.get("regex")
    if not regex:
        return []
    pages = ctx.field_to_page or {}
    faults: list[tuple[int, str, str]] = []
    for field_name in ctx.field_names:
        value = ctx.field_values.get(field_name)
        if value is None or str(value).strip() == "":
            continue
        text = str(value).strip()
        if not re.match(regex, text):
            msg = ctx.params.get("message")
            if not msg:
                msg = f"'{text}' does not match regex: {regex}"
            else:
                msg = f"{msg}: '{text}'"
            faults.append((pages.get(field_name, 1), field_name, msg))
    return faults


SUPPLIER_VALIDATION_REGISTRY: dict[
    str, Callable[[ValidationContext], list[tuple[int, str, str]]]
] = {
    "hours-times-rate-equals-total": _strategy_hours_times_rate_equals_total,
    "hours_times_rate_equals_total": _strategy_hours_times_rate_equals_total,
    "sum-should-equal-total": _strategy_sum_should_equal_total,
    "sum_should_equal_total": _strategy_sum_should_equal_total,
    "between-values": _strategy_between_values,
    "between_values": _strategy_between_values,
    "regex": _strategy_regex,
}
