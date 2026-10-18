# Tests

## Purpose

Automated unit tests for validation rules and pure business logic in `invoice_app`.

## Ownership

- Test modules under `tests/` (`test_validations.py`)

## Local Contracts

- Prefer fast, headless unit tests against pure functions and dataclasses (e.g. `invoice_app/validation/`, `invoice_app/grid_infer.py`) without requiring live PaddleOCR runs or interactive Qt windows unless explicitly requested.
- `test_validations.py` covers `hours-times-rate-equals-total` (exact match, currency/comma stripping, cent tolerance, mismatch fault on total, all-blank skip, partial-blank fault, non-numeric fault, custom tolerance) and `ProjectValidations.run_validations`.

## Work Guidance

## Verification

- `uv run python -m unittest discover -s tests`

## Child DOX Index
