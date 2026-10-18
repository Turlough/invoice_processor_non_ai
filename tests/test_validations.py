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
    field_names: list[str] | None,
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

    def test_currency_only_string_faults_as_invalid_number(self) -> None:
        fn = PROJECT_VALIDATION_REGISTRY["hours-times-rate-equals-total"]
        ctx = _ctx(
            field_values={"Hours": "8", "Rate": "€", "Total": "160.00"},
            field_names=["Hours", "Rate", "Total"],
        )
        result = fn(ctx)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0][1], "Rate")

    def test_custom_tolerance_respected(self) -> None:
        fn = PROJECT_VALIDATION_REGISTRY["hours-times-rate-equals-total"]
        ctx = _ctx(
            field_values={"Hours": "10", "Rate": "10", "Total": "100.05"},
            field_names=["Hours", "Rate", "Total"],
            params={"tolerance": 0.10},
        )
        self.assertEqual(fn(ctx), [])

    def test_fewer_than_three_fields_is_noop(self) -> None:
        fn = PROJECT_VALIDATION_REGISTRY["hours-times-rate-equals-total"]
        ctx = _ctx(field_values={"Hours": "10"}, field_names=["Hours", "Rate"])
        self.assertEqual(fn(ctx), [])


class TestSumShouldEqualTotal(unittest.TestCase):
    def test_matching_sum_passes(self) -> None:
        fn = PROJECT_VALIDATION_REGISTRY["sum-should-equal-total"]
        ctx = _ctx(
            field_values={"Total": "£150.00", "Net": "125.00", "VAT": "25.00"},
            field_names=["Total", "Net", "VAT"],
        )
        self.assertEqual(fn(ctx), [])

    def test_mismatched_sum_faults_total_field(self) -> None:
        fn = PROJECT_VALIDATION_REGISTRY["sum_should_equal_total"]
        ctx = _ctx(
            field_values={"Total": "160.00", "Net": "125.00", "VAT": "25.00"},
            field_names=["Total", "Net", "VAT"],
            field_to_page={"Total": 4},
        )
        faults = fn(ctx)
        self.assertEqual(len(faults), 1)
        self.assertEqual(faults[0][0], 4)
        self.assertEqual(faults[0][1], "Total")
        self.assertIn("150.00", faults[0][2])

    def test_blank_total_counts_as_zero_and_faults_when_parts_nonzero(self) -> None:
        fn = PROJECT_VALIDATION_REGISTRY["sum-should-equal-total"]
        ctx = _ctx(
            field_values={"Total": "", "Net": "50.00", "VAT": "10.00"},
            field_names=["Total", "Net", "VAT"],
        )
        faults = fn(ctx)
        self.assertEqual(len(faults), 1)
        self.assertEqual(faults[0][1], "Total")

    def test_non_numeric_total_returns_immediate_fault(self) -> None:
        fn = PROJECT_VALIDATION_REGISTRY["sum-should-equal-total"]
        ctx = _ctx(
            field_values={"Total": "N/A", "Net": "50.00"},
            field_names=["Total", "Net"],
        )
        faults = fn(ctx)
        self.assertEqual(len(faults), 1)
        self.assertEqual(faults[0][1], "Total")
        self.assertIn("not a valid number", faults[0][2])

    def test_non_numeric_part_faults_part_and_total_if_mismatch(self) -> None:
        fn = PROJECT_VALIDATION_REGISTRY["sum-should-equal-total"]
        ctx = _ctx(
            field_values={"Total": "50.00", "A": "50.00", "B": "bad"},
            field_names=["Total", "A", "B"],
        )
        faults = fn(ctx)
        self.assertEqual(len(faults), 1)
        self.assertEqual(faults[0][1], "B")


class TestBetweenValues(unittest.TestCase):
    def test_values_inside_and_on_boundaries_pass(self) -> None:
        fn = PROJECT_VALIDATION_REGISTRY["between-values"]
        ctx = _ctx(
            field_values={"Rate": "10.0", "Hours": "40", "Blank": ""},
            field_names=["Rate", "Hours", "Blank"],
            params={"min": 10, "max": 40},
        )
        self.assertEqual(fn(ctx), [])

    def test_out_of_range_and_non_numeric_fault(self) -> None:
        fn = PROJECT_VALIDATION_REGISTRY["between_values"]
        ctx = _ctx(
            field_values={"Low": "4.99", "High": "100.01", "Bad": "abc"},
            field_names=["Low", "High", "Bad"],
            params={"min": 5, "max": 100},
            field_to_page={"Low": 1, "High": 2, "Bad": 3},
        )
        faults = fn(ctx)
        self.assertEqual([f[1] for f in faults], ["Low", "High", "Bad"])
        self.assertEqual([f[0] for f in faults], [1, 2, 3])

    def test_missing_or_invalid_bounds_is_noop(self) -> None:
        fn = PROJECT_VALIDATION_REGISTRY["between-values"]
        self.assertEqual(
            fn(_ctx({"A": "10"}, ["A"], params={"min": 5})),
            [],
        )
        self.assertEqual(
            fn(_ctx({"A": "10"}, ["A"], params={"min": "low", "max": 20})),
            [],
        )


class TestRegexStrategy(unittest.TestCase):
    def test_matching_and_blank_values_pass(self) -> None:
        fn = PROJECT_VALIDATION_REGISTRY["regex"]
        ctx = _ctx(
            field_values={"Code": "INV-123", "Empty": "  "},
            field_names=["Code", "Empty"],
            params={"regex": r"^INV-\d+$"},
        )
        self.assertEqual(fn(ctx), [])

    def test_non_matching_uses_default_or_custom_message(self) -> None:
        fn = PROJECT_VALIDATION_REGISTRY["regex"]
        default_faults = fn(
            _ctx(
                field_values={"Code": "BAD"},
                field_names=["Code"],
                params={"regex": r"^INV-\d+$"},
            )
        )
        self.assertEqual(len(default_faults), 1)
        self.assertIn("does not match regex", default_faults[0][2])

        custom_faults = fn(
            _ctx(
                field_values={"Code": "BAD"},
                field_names=["Code"],
                params={"regex": r"^INV-\d+$", "message": "Invalid code"},
            )
        )
        self.assertEqual(custom_faults[0][2], "Invalid code: 'BAD'")


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

    def test_deduplicates_failures_by_page_and_field_and_skips_unknown_strategy(self) -> None:
        config = {
            "validations": [
                {"strategy": "unknown-strategy", "field_names": ["Rate"]},
                {
                    "strategy": "between-values",
                    "field_names": ["Rate"],
                    "params": {"min": 10, "max": 20},
                },
                {
                    "strategy": "regex",
                    "field_names": ["Rate"],
                    "params": {"regex": r"^99$"},
                },
            ]
        }
        pv = ProjectValidations(config)
        failures = pv.run_validations(0, {"Rate": "5"}, {"Rate": 1})
        self.assertEqual(len(failures), 1)
        self.assertEqual(failures[0][1], "Rate")

    def test_empty_or_malformed_config_has_no_rules(self) -> None:
        self.assertFalse(ProjectValidations({}).has_rules())
        self.assertFalse(ProjectValidations({"validations": "not-a-list"}).has_rules())


if __name__ == "__main__":
    unittest.main()
