"""Unit tests for project configuration and per-user app state (`invoice_app/projects.py`)."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from invoice_app.projects import (
    AppState,
    ConfigError,
    Project,
    config_root,
    list_projects,
)


class TestProjectsConfig(unittest.TestCase):
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

    def test_list_projects_returns_sorted_subdirectories(self) -> None:
        (self.root / "Zeta").mkdir()
        (self.root / "Alpha").mkdir()
        (self.root / "ignore.txt").write_text("hi", encoding="utf-8")
        with patch.dict(os.environ, {"CONFIG_DIRECTORY": str(self.root)}, clear=False):
            self.assertEqual(list_projects(), ["Alpha", "Zeta"])

    def test_project_load_creates_config_and_persists_settings(self) -> None:
        proj_dir = self.root / "Emerald"
        proj_dir.mkdir()
        data_dir = self.root / "batches"
        (data_dir / "batch_b").mkdir(parents=True)
        (data_dir / "Batch_A").mkdir(parents=True)

        with patch.dict(os.environ, {"CONFIG_DIRECTORY": str(self.root)}, clear=False):
            project = Project.load("Emerald")
            self.assertTrue(project.config_path.is_file())
            self.assertIsNone(project.data_directory)

            project.set_data_directory(data_dir)
            project.set_col_fracs([0.0, 0.4, 0.7, 1.0])
            project.set_table_x_range(0.045, 0.933)
            project.set_column_entry(0, name="Description", anchor=False, n_cols=3)
            project.set_column_entry(1, name="Hours", anchor=True, n_cols=3)
            project.set_column_entry(2, name="  ", anchor=True, n_cols=3)

            reloaded = Project.load("Emerald")
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
            with patch("invoice_app.projects.user_state_path", return_value=state_file):
                initial = AppState.load()
                self.assertIsNone(initial.project)

                state = AppState(project="Emerald", batch="2026-10", pdf="inv01.pdf")
                state.save()

                loaded = AppState.load()
                self.assertEqual(loaded.project, "Emerald")
                self.assertEqual(loaded.batch, "2026-10")
                self.assertEqual(loaded.pdf, "inv01.pdf")


if __name__ == "__main__":
    unittest.main()
