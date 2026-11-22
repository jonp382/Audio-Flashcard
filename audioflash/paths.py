"""Data directory layout (agent.md §3 startup step 1)."""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

import platformdirs

from . import config


@dataclass(frozen=True)
class DataPaths:
    root: Path

    @property
    def db(self) -> Path:
        return self.root / "app.db"

    @property
    def backups(self) -> Path:
        return self.root / "backups"

    @property
    def models(self) -> Path:
        return self.root / "models"

    @property
    def recordings(self) -> Path:
        return self.root / "recordings"

    def card_recordings(self, card_id: int) -> Path:
        return self.recordings / str(int(card_id))

    @property
    def logs(self) -> Path:
        return self.root / "logs"

    @property
    def log_file(self) -> Path:
        return self.logs / "app.log"

    @property
    def lock_file(self) -> Path:
        return self.root / "app.lock"

    def ensure(self) -> None:
        for d in (self.root, self.backups, self.models, self.recordings, self.logs):
            d.mkdir(parents=True, exist_ok=True)

    def clean_partial_downloads(self) -> None:
        """Remove leftover temp dirs from an interrupted model download."""
        if not self.models.exists():
            return
        for p in self.models.iterdir():
            if p.name.startswith(config.WHISPER_TMP_PREFIX) and p.is_dir():
                shutil.rmtree(p, ignore_errors=True)


def default_paths() -> DataPaths:
    return DataPaths(Path(platformdirs.user_data_dir(config.APP_NAME, appauthor=False)))
