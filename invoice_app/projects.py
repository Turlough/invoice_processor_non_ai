"""Project configuration: CONFIG_DIRECTORY, project_config.json, batches.

A *project* is a subdirectory of CONFIG_DIRECTORY holding a
``project_config.json``. The config records the project's *data directory* —
the root folder whose subdirectories are batches of similar invoice PDFs.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path

from platformdirs import user_config_dir

APP_NAME = "invoice_processor"
CONFIG_FILE = "project_config.json"
APP_CONFIG_FILE = "app_config.json"
DATA_DIRECTORY_KEY = "data-directory"


class ConfigError(Exception):
    """Raised when CONFIG_DIRECTORY is unset or points nowhere."""


def config_root() -> Path:
    raw = os.environ.get("CONFIG_DIRECTORY", "").strip()
    if not raw:
        raise ConfigError(
            "CONFIG_DIRECTORY is not set. Add it to .env (see env.example)."
        )
    root = Path(raw).expanduser()
    if not root.is_dir():
        raise ConfigError(f"CONFIG_DIRECTORY is not a directory: {root}")
    return root


def list_projects() -> list[str]:
    """Names of the project directories inside CONFIG_DIRECTORY."""
    return sorted(p.name for p in config_root().iterdir() if p.is_dir())


@dataclass
class Project:
    name: str
    directory: Path
    config: dict = field(default_factory=dict)

    @property
    def config_path(self) -> Path:
        return self.directory / CONFIG_FILE

    @classmethod
    def load(cls, name: str) -> "Project":
        """Open a project, creating project_config.json if it is missing."""
        directory = config_root() / name
        if not directory.is_dir():
            raise ConfigError(f"Project directory not found: {directory}")
        config: dict = {}
        path = directory / CONFIG_FILE
        if path.is_file():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    config = data
            except (OSError, ValueError):
                config = {}
        project = cls(name=name, directory=directory, config=config)
        if not path.exists():
            project.save()
        return project

    def save(self) -> None:
        self.config_path.write_text(
            json.dumps(self.config, indent=2) + "\n", encoding="utf-8"
        )

    @property
    def data_directory(self) -> Path | None:
        raw = self.config.get(DATA_DIRECTORY_KEY)
        if not raw:
            return None
        return Path(raw).expanduser()

    def set_data_directory(self, path: Path) -> None:
        self.config[DATA_DIRECTORY_KEY] = str(path)
        self.save()

    def column_names(self) -> list[str]:
        """Column `name` values from project config (visualisation / infer hints)."""
        raw = self.config.get("columns")
        if not isinstance(raw, list):
            return []
        names: list[str] = []
        for entry in raw:
            if isinstance(entry, dict):
                name = entry.get("name")
                if isinstance(name, str) and name.strip():
                    names.append(name.strip())
        return names

    def header_row_count(self) -> int:
        raw = self.config.get("header-row-count", 1)
        if isinstance(raw, int) and raw >= 0:
            return raw
        return 1

    def col_fracs(self) -> list[float] | None:
        raw = self.config.get("col-fracs")
        if not isinstance(raw, list) or len(raw) < 2:
            return None
        try:
            return [float(v) for v in raw]
        except (TypeError, ValueError):
            return None

    def set_col_fracs(self, fracs: list[float]) -> None:
        """Persist column divider fractions (0..1 inside the table region)."""
        self.config["col-fracs"] = [float(v) for v in fracs]
        self.save()

    def list_batches(self) -> list[Path]:
        """Subdirectories of the data directory, each a batch of PDFs."""
        root = self.data_directory
        if root is None or not root.is_dir():
            return []
        return sorted(
            (p for p in root.iterdir() if p.is_dir()),
            key=lambda p: p.name.lower(),
        )


def user_state_path() -> Path:
    """Per-user state file, e.g. %APPDATA% on Windows, ~/.config on Linux."""
    return Path(user_config_dir(APP_NAME)) / APP_CONFIG_FILE


@dataclass
class AppState:
    """Last-used location, so the app reopens where the user left off."""

    project: str | None = None
    batch: str | None = None
    pdf: str | None = None

    @classmethod
    def load(cls) -> "AppState":
        path = user_state_path()
        if path.is_file():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    return cls(
                        project=data.get("project") or None,
                        batch=data.get("batch") or None,
                        pdf=data.get("pdf") or None,
                    )
            except (OSError, ValueError):
                pass
        return cls()

    def save(self) -> None:
        path = user_state_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(asdict(self), indent=2) + "\n", encoding="utf-8"
        )
