"""Unit tests for supplier configuration and per-user app state (`invoice_app/suppliers.py`)."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from invoice_app.suppliers import (
    AppState,
    ConfigError,
    Supplier,
    config_root,
    list_suppliers,
)


class TestSuppliersConfig(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_config_root_raises_when_unset_or_missing(self) -> None:
        with patch.dict(os.environ, {"CONFIG_DIRECTORY": ""}, clear=False):
            with self.assertRaises(ConfigError):
                config_root()
        with patch.dict(
            os.environ,
            {"CONFIG_DIRECTORY": str(self.root / "does_not_exist")},
            clear=False,
        ):
            with self.assertRaises(ConfigError):
                config_root()

    def test_list_suppliers_returns_sorted_subdirectories(self) -> None:
        (self.root / "Zeta").mkdir()
        (self.root / "Alpha").mkdir()
        (self.root / "ignore.txt").write_text("hi", encoding="utf-8")
        with patch.dict(os.environ, {"CONFIG_DIRECTORY": str(self.root)}, clear=False):
            self.assertEqual(list_suppliers(), ["Alpha", "Zeta"])

    def test_supplier_load_creates_config_and_persists_settings(self) -> None:
        supp_dir = self.root / "Emerald"
        supp_dir.mkdir()
        data_dir = self.root / "batches"
        (data_dir / "batch_b").mkdir(parents=True)
        (data_dir / "Batch_A").mkdir(parents=True)

        with patch.dict(os.environ, {"CONFIG_DIRECTORY": str(self.root)}, clear=False):
            supplier = Supplier.load("Emerald")
            self.assertTrue(supplier.config_path.is_file())
            self.assertIsNone(supplier.data_directory)

            supplier.set_data_directory(data_dir)
            supplier.set_col_fracs([0.0, 0.4, 0.7, 1.0])
            supplier.set_table_x_range(0.045, 0.933)
            supplier.set_column_entry(0, name="Description", anchor=False, n_cols=3)
            supplier.set_column_entry(1, name="Hours", anchor=True, n_cols=3)
            supplier.set_column_entry(2, name="  ", anchor=True, n_cols=3)

            reloaded = Supplier.load("Emerald")
            self.assertEqual(reloaded.data_directory, data_dir)
            self.assertEqual(reloaded.col_fracs(), [0.0, 0.4, 0.7, 1.0])
            self.assertEqual(reloaded.table_x_range(), (0.045, 0.933))
            self.assertEqual(reloaded.column_names(), ["Description", "Hours", "Col 3"])
            self.assertEqual(reloaded.anchor_cols(3), [False, True, True])
            self.assertEqual(reloaded.column_entry(0, 3), ("Description", False))
            self.assertEqual(reloaded.column_entry(2, 3), ("", True))
            self.assertEqual(
                [p.name for p in reloaded.list_batches()],
                ["Batch_A", "batch_b"],
            )


class TestAppState(unittest.TestCase):
    def test_app_state_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state_file = Path(tmp) / "app_config.json"
            with patch("invoice_app.suppliers.user_state_path", return_value=state_file):
                initial = AppState.load()
                self.assertIsNone(initial.supplier)

                state = AppState(supplier="Emerald", batch="2026-10", pdf="inv01.pdf")
                state.save()

                loaded = AppState.load()
                self.assertEqual(loaded.supplier, "Emerald")
                self.assertEqual(loaded.batch, "2026-10")
                self.assertEqual(loaded.pdf, "inv01.pdf")


if __name__ == "__main__":
    unittest.main()
