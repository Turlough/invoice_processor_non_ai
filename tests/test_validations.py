"""Unit tests for line-item validation strategies and ProjectValidations runner."""

from __future__ import annotations

import unittest

from invoice_app.validation import (
    PROJECT_VALIDATION_REGISTRY,
    ProjectValidations,
    ValidationContext,
)


def _ctx(
    field_values: dict,
    field_names: list[str],
    params: dict | None = None,
    field_to_page: dict | None = None,
    row_index: int = 0,
) -> ValidationContext:
    return ValidationContext(
        field_values=field_values,
        field_names=field_names,
        params=params or {},
        field_to_page=field_to_page or {},
        row_index=row_index,
    )


class TestHoursTimesRateEqualsTotal(unittest.TestCase):
    def test_exact_match_passes(self) -> None:
        fn = PROJECT_VALIDATION_REGISTRY["hours-times-rate-equals-total"]
        ctx = _ctx(
            field_values={"Shift Hours": "12", "Rates": "25.50", "Total": "306.00"},
            field_names=["Shift Hours", "Rates", "Total"],
        )
        self.assertEqual(fn(ctx), [])

    def test_currency_and_commas_pass(self) -> None:
        fn = PROJECT_VALIDATION_REGISTRY["hours-times-rate-equals-total"]
        ctx = _ctx(
            field_values={"Hours": "100", "Rate": "€12.50", "Total": "€1,250.00"},
            field_names=["Hours", "Rate", "Total"],
        )
        self.assertEqual(fn(ctx), [])

    def test_within_default_cent_tolerance_passes(self) -> None:
        fn = PROJECT_VALIDATION_REGISTRY["hours-times-rate-equals-total"]
        # 7.5 * 19.333 = 144.9975 -> 145.00 is within 0.01
        ctx = _ctx(
            field_values={"Hours": "7.5", "Rate": "19.333", "Total": "145.00"},
            field_names=["Hours", "Rate", "Total"],
        )
        self.assertEqual(fn(ctx), [])

    def test_mismatch_beyond_tolerance_fails_on_total(self) -> None:
        fn = PROJECT_VALIDATION_REGISTRY["hours-times-rate-equals-total"]
        ctx = _ctx(
            field_values={"Hours": "8", "Rate": "20.00", "Total": "165.00"},
            field_names=["Hours", "Rate", "Total"],
            field_to_page={"Hours": 2, "Rate": 2, "Total": 2},
        )
        result = fn(ctx)
        self.assertEqual(len(result), 1)
        page, field_name, msg = result[0]
        self.assertEqual(page, 2)
        self.assertEqual(field_name, "Total")
        self.assertIn("160.00", msg)
        self.assertIn("165.00", msg)

    def test_all_blank_row_is_skipped(self) -> None:
        fn = PROJECT_VALIDATION_REGISTRY["hours-times-rate-equals-total"]
        ctx = _ctx(
            field_values={"Hours": "", "Rate": "   ", "Total": None},
            field_names=["Hours", "Rate", "Total"],
        )
        self.assertEqual(fn(ctx), [])

    def test_partially_blank_row_faults_missing_fields(self) -> None:
        fn = PROJECT_VALIDATION_REGISTRY["hours-times-rate-equals-total"]
        ctx = _ctx(
            field_values={"Hours": "8", "Rate": "", "Total": "160.00"},
            field_names=["Hours", "Rate", "Total"],
            field_to_page={"Hours": 1, "Rate": 1, "Total": 1},
        )
        result = fn(ctx)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0][1], "Rate")
        self.assertIn("Missing value", result[0][2])

    def test_non_numeric_field_faults_that_field(self) -> None:
        fn = PROJECT_VALIDATION_REGISTRY["hours-times-rate-equals-total"]
        ctx = _ctx(
            field_values={"Hours": "eight", "Rate": "20.00", "Total": "160.00"},
            field_names=["Hours", "Rate", "Total"],
            field_to_page={"Hours": 1, "Rate": 1, "Total": 1},
        )
        result = fn(ctx)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0][1], "Hours")
        self.assertIn("not a valid number", result[0][2])

    def test_custom_tolerance_respected(self) -> None:
        fn = PROJECT_VALIDATION_REGISTRY["hours-times-rate-equals-total"]
        ctx = _ctx(
            field_values={"Hours": "10", "Rate": "10", "Total": "100.05"},
            field_names=["Hours", "Rate", "Total"],
            params={"tolerance": 0.10},
        )
        self.assertEqual(fn(ctx), [])


class TestProjectValidationsRunner(unittest.TestCase):
    def test_run_validations_with_project_config(self) -> None:
        config = {
            "validations": [
                {
                    "strategy": "hours-times-rate-equals-total",
                    "field_names": ["Shift Hours", "Rates", "Total"],
                    "params": {"tolerance": 0.01},
                }
            ]
        }
        pv = ProjectValidations(config)
        self.assertTrue(pv.has_rules())

        ok_row = {"Shift Hours": "11.5", "Rates": "22.00", "Total": "253.00"}
        self.assertEqual(pv.run_validations(0, ok_row), [])

        bad_row = {"Shift Hours": "11.5", "Rates": "22.00", "Total": "250.00"}
        failures = pv.run_validations(1, bad_row, {"Total": 3})
        self.assertEqual(len(failures), 1)
        self.assertEqual(failures[0][0], 3)
        self.assertEqual(failures[0][1], "Total")


if __name__ == "__main__":
    unittest.main()
