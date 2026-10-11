"""Project configuration: CONFIG_DIRECTORY, project_config.json, batches.

A *project* is a subdirectory of CONFIG_DIRECTORY holding a
``project_config.json``. The config records the project's *data directory* —
the root folder whose subdirectories are batches of similar invoice PDFs.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

CONFIG_FILE = "project_config.json"
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

    def list_batches(self) -> list[Path]:
        """Subdirectories of the data directory, each a batch of PDFs."""
        root = self.data_directory
        if root is None or not root.is_dir():
            return []
        return sorted(
            (p for p in root.iterdir() if p.is_dir()),
            key=lambda p: p.name.lower(),
        )
