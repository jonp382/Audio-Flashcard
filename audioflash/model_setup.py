"""One-time Whisper model download (agent.md §9 download_whisper_model).

This is the ONLY non-loopback network path in the app. Files come from a pinned
Hugging Face revision and are verified by size and sha256 before the temp dir is
renamed into place, so a partial download is never treated as present.
"""

from __future__ import annotations

import hashlib
import logging
import os
import shutil
import tempfile
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import httpx

from . import config

log = logging.getLogger(__name__)


def model_present(model_dir: Path) -> bool:
    return all(
        (model_dir / name).is_file() and (model_dir / name).stat().st_size == size
        for name, (size, _) in config.WHISPER_FILES.items()
    )


@dataclass
class DownloadStatus:
    state: str = "idle"  # idle | downloading | done | error
    bytes_done: int = 0
    bytes_total: int = field(default_factory=lambda: sum(s for s, _ in config.WHISPER_FILES.values()))
    error: str | None = None

    def to_dict(self) -> dict:
        return {"state": self.state, "bytes_done": self.bytes_done, "bytes_total": self.bytes_total, "error": self.error}


class ModelDownloader:
    def __init__(
        self,
        models_dir: Path,
        on_done: Callable[[], None] | None = None,
        transport: httpx.BaseTransport | None = None,
    ):
        self.models_dir = models_dir
        self.target = models_dir / config.WHISPER_DIR_NAME
        self.on_done = on_done
        self.transport = transport
        self._lock = threading.Lock()
        self._status = DownloadStatus()
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    def status(self) -> dict:
        with self._lock:
            return self._status.to_dict()

    @property
    def running(self) -> bool:
        with self._lock:
            return self._status.state == "downloading"

    def start(self) -> bool:
        """Start in a background thread. False if already running."""
        with self._lock:
            if self._status.state == "downloading":
                return False
            self._status = DownloadStatus(state="downloading")
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="whisper-download", daemon=True)
        self._thread.start()
        return True

    def stop(self) -> None:
        self._stop.set()

    def _add(self, n: int) -> None:
        with self._lock:
            self._status.bytes_done += n

    def _run(self) -> None:
        tmp: Path | None = None
        try:
            self.models_dir.mkdir(parents=True, exist_ok=True)
            tmp = Path(tempfile.mkdtemp(prefix=config.WHISPER_TMP_PREFIX, dir=self.models_dir))
            with httpx.Client(follow_redirects=True, timeout=httpx.Timeout(30.0, read=60.0), transport=self.transport) as client:
                for name, (size, sha) in config.WHISPER_FILES.items():
                    self._download_file(client, name, size, sha, tmp / name)
            if self.target.exists():
                shutil.rmtree(self.target)
            os.replace(tmp, self.target)
            tmp = None
            with self._lock:
                self._status.state = "done"
            log.info("whisper model downloaded")
            if self.on_done:
                self.on_done()
        except Exception as e:
            log.exception("whisper download failed")
            with self._lock:
                self._status.state = "error"
                self._status.error = "Cancelled" if self._stop.is_set() else str(e) or e.__class__.__name__
        finally:
            if tmp is not None:
                shutil.rmtree(tmp, ignore_errors=True)

    def _download_file(self, client: httpx.Client, name: str, size: int, sha: str, dest: Path) -> None:
        url = config.WHISPER_URL_TEMPLATE.format(repo=config.WHISPER_REPO, revision=config.WHISPER_REVISION, file=name)
        h = hashlib.sha256()
        got = 0
        with client.stream("GET", url) as r:
            r.raise_for_status()
            with open(dest, "wb") as f:
                for chunk in r.iter_bytes(1 << 16):
                    if self._stop.is_set():
                        raise RuntimeError("cancelled")
                    f.write(chunk)
                    h.update(chunk)
                    got += len(chunk)
                    self._add(len(chunk))
        if got != size:
            raise RuntimeError(f"{name}: expected {size} bytes, got {got}")
        if h.hexdigest() != sha:
            raise RuntimeError(f"{name}: checksum mismatch")
