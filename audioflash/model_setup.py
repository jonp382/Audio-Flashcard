"""Whisper models: which are installed, and the download (agent.md §9 download_whisper_model).

The download is the ONLY non-loopback network path in the app. Files come from a pinned
Hugging Face revision and are verified by size and sha256 before the temp dir is
renamed into place, so a partial download is never treated as present.

Model ids: a key of ``config.WHISPER_MODELS`` (downloadable, lives in
``models/faster-whisper-<id>``), or the folder name of any other faster-whisper model
the user put in ``models/`` themselves (a "custom" model, never downloaded by the app).
"""

from __future__ import annotations

import hashlib
import logging
import os
import shutil
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import httpx

from . import config

log = logging.getLogger(__name__)


def catalog_dir_name(model_id: str) -> str:
    return config.WHISPER_DIR_PREFIX + model_id


def model_dir(models_dir: Path, model_id: str) -> Path:
    """Where the model with this id lives (it may not exist yet)."""
    if model_id in config.WHISPER_MODELS:
        return models_dir / catalog_dir_name(model_id)
    return models_dir / model_id


def _spec_for_dir(path: Path) -> config.WhisperModelSpec | None:
    for mid, spec in config.WHISPER_MODELS.items():
        if path.name == catalog_dir_name(mid):
            return spec
    return None


def model_present(path: Path) -> bool:
    """Catalog models: every pinned file at its exact size. Custom: the required files exist."""
    spec = _spec_for_dir(path)
    if spec is not None:
        return all((path / n).is_file() and (path / n).stat().st_size == size for n, (size, _) in spec.files.items())
    return all((path / n).is_file() and (path / n).stat().st_size > 0 for n in config.WHISPER_REQUIRED_FILES)


def custom_model_ids(models_dir: Path) -> list[str]:
    """Folders in models/ that hold a faster-whisper model but aren't catalog models."""
    if not models_dir.is_dir():
        return []
    catalog = {catalog_dir_name(m) for m in config.WHISPER_MODELS}
    return sorted(
        p.name
        for p in models_dir.iterdir()
        if p.is_dir() and not p.name.startswith(".") and p.name not in catalog and model_present(p)
    )


def valid_model_id(models_dir: Path, model_id: str) -> bool:
    return model_id in config.WHISPER_MODELS or model_id in custom_model_ids(models_dir)


def _dir_size(path: Path) -> int:
    return sum(p.stat().st_size for p in path.rglob("*") if p.is_file())


def list_models(models_dir: Path) -> list[dict]:
    """Catalog models (installed or not) followed by custom ones found on disk."""
    out = []
    for mid, spec in config.WHISPER_MODELS.items():
        out.append({
            "id": mid,
            "label": spec.label,
            "blurb": spec.blurb,
            "size_bytes": sum(s for s, _ in spec.files.values()),
            "installed": model_present(model_dir(models_dir, mid)),
            "custom": False,
        })
    for mid in custom_model_ids(models_dir):
        out.append({
            "id": mid,
            "label": mid,
            "blurb": "Your own model folder.",
            "size_bytes": _dir_size(models_dir / mid),
            "installed": True,
            "custom": True,
        })
    return out


def model_size_bytes(model_id: str) -> int:
    spec = config.WHISPER_MODELS.get(model_id)
    return sum(s for s, _ in spec.files.values()) if spec else 0


@dataclass
class DownloadStatus:
    state: str = "idle"  # idle | downloading | done | error
    model: str | None = None
    bytes_done: int = 0
    bytes_total: int = 0
    error: str | None = None

    def to_dict(self) -> dict:
        return {
            "state": self.state,
            "model": self.model,
            "bytes_done": self.bytes_done,
            "bytes_total": self.bytes_total,
            "error": self.error,
        }


class ModelDownloader:
    def __init__(
        self,
        models_dir: Path,
        on_done: Callable[[str], None] | None = None,
        transport: httpx.BaseTransport | None = None,
    ):
        self.models_dir = models_dir
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

    @property
    def running_model(self) -> str | None:
        with self._lock:
            return self._status.model if self._status.state == "downloading" else None

    def start(self, model_id: str = config.DEFAULT_WHISPER_MODEL) -> bool:
        """Start downloading a catalog model in a background thread. False if already running."""
        spec = config.WHISPER_MODELS[model_id]  # KeyError: caller validates
        with self._lock:
            if self._status.state == "downloading":
                return False
            self._status = DownloadStatus(
                state="downloading", model=model_id, bytes_total=sum(s for s, _ in spec.files.values())
            )
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, args=(model_id, spec), name="whisper-download", daemon=True)
        self._thread.start()
        return True

    def stop(self) -> None:
        self._stop.set()

    def _add(self, n: int) -> None:
        with self._lock:
            self._status.bytes_done += n

    def _run(self, model_id: str, spec: config.WhisperModelSpec) -> None:
        tmp: Path | None = None
        target = model_dir(self.models_dir, model_id)
        try:
            self.models_dir.mkdir(parents=True, exist_ok=True)
            tmp = Path(tempfile.mkdtemp(prefix=config.WHISPER_TMP_PREFIX, dir=self.models_dir))
            with httpx.Client(follow_redirects=True, timeout=httpx.Timeout(30.0, read=60.0), transport=self.transport) as client:
                for name, (size, sha) in spec.files.items():
                    self._download_file(client, spec, name, size, sha, tmp / name)
            if target.exists():
                shutil.rmtree(target)
            os.replace(tmp, target)
            tmp = None
            with self._lock:
                self._status.state = "done"
            log.info("whisper model %s downloaded", model_id)
            if self.on_done:
                self.on_done(model_id)
        except Exception as e:
            log.exception("whisper download failed")
            with self._lock:
                self._status.state = "error"
                self._status.error = "Cancelled" if self._stop.is_set() else str(e) or e.__class__.__name__
        finally:
            if tmp is not None:
                shutil.rmtree(tmp, ignore_errors=True)

    def _download_file(
        self, client: httpx.Client, spec: config.WhisperModelSpec, name: str, size: int, sha: str, dest: Path
    ) -> None:
        url = config.WHISPER_URL_TEMPLATE.format(repo=spec.repo, revision=spec.revision, file=name)
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
