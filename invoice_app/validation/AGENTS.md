# Validation package

## Purpose

Config-driven per-row line-item validation executed when the invoice table is populated or edited.

## Ownership

- `supplier_validations.py` (`SupplierValidations` runner)
- `strategies.py` (`ValidationContext`, strategy functions, `SUPPLIER_VALIDATION_REGISTRY`)
- `__init__.py` (re-exports `SupplierValidations`, `ValidationContext`, `SUPPLIER_VALIDATION_REGISTRY`)

## Local Contracts

- Add or modify validation rules in `strategies.py` and register them in `SUPPLIER_VALIDATION_REGISTRY` (supporting both hyphenated and underscored names) rather than embedding rule logic in `main_window.py`.
- Each strategy accepts a `ValidationContext(field_values, field_names, params, field_to_page, row_index)` and returns `list[tuple[int, str, str]]` of `(page_num, field_name, message)`.
- `_parse_numeric_field_value` strips commas, whitespace, and currency symbols (`€`, `£`, `$`) before parsing floats.
- `hours-times-rate-equals-total`: `field_names` is `[hours, rate, total]`; `params.tolerance` defaults to `0.01`. If all three fields are blank, the row is skipped; if any is blank or non-numeric, that specific field is faulted; if `abs(hours * rate - total) > tolerance`, faults on `total`.
- `sum-should-equal-total`: `field_names[0]` is the total and `field_names[1:]` are summed (`params.tolerance` default `0.01`). Blank total counts as `0.0`; non-numeric parts fault on their own field.
- `between-values`: checks each numeric field in `field_names` lies within `[params.min, params.max]` inclusive; blank values are skipped.
- `regex`: checks each non-empty field in `field_names` matches `params.regex` (`re.match`), using optional `params.message`.

## Work Guidance

## Verification

- `uv run python -m unittest discover -s tests` (`tests/test_validations.py`)

## Child DOX Index
