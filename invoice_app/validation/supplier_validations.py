"""Supplier-level validations: config-driven business rules per supplier."""

from __future__ import annotations

import logging
from typing import Any

from .strategies import SUPPLIER_VALIDATION_REGISTRY, ValidationContext

logger = logging.getLogger(__name__)


class SupplierValidations:
    """Runs configured supplier validations against line-item rows."""

    def __init__(self, supplier_config: dict[str, Any]) -> None:
        self.supplier_config = supplier_config
        raw_validations = supplier_config.get("validations", [])
        self.validations: list[dict[str, Any]] = (
            [r for r in raw_validations if isinstance(r, dict)]
            if isinstance(raw_validations, list)
            else []
        )

    def has_rules(self) -> bool:
        return bool(self.validations)

    def run_validations(
        self,
        row_index: int,
        field_values: dict[str, Any],
        field_to_page: dict[str, int] | None = None,
    ) -> list[tuple[int, str, str]]:
        """Run all supplier validations for one row.

        Returns a list of ``(page, field_name, message)`` tuples, deduplicated
        by ``(page, field_name)``.
        """
        seen: set[tuple[int, str]] = set()
        all_failures: list[tuple[int, str, str]] = []

        for rule in self.validations:
            strategy_name = rule.get("strategy")
            if not strategy_name:
                continue

            fn = SUPPLIER_VALIDATION_REGISTRY.get(strategy_name)
            if fn is None:
                logger.warning("Unknown validation strategy: %s", strategy_name)
                continue

            ctx = ValidationContext(
                field_values=field_values,
                field_names=rule.get("field_names", []),
                params=rule.get("params") or {},
                field_to_page=field_to_page,
                row_index=row_index,
            )

            try:
                failures = fn(ctx)
                for page, field_name, message in failures:
                    key = (page, field_name)
                    if key not in seen:
                        seen.add(key)
                        all_failures.append((page, field_name, message))
            except Exception as exc:
                logger.warning("Validation strategy %s failed: %s", strategy_name, exc)

        return all_failures
