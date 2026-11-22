"""faster-whisper wrapper (agent.md §5 STT).

The model is only ever loaded from the local data dir with ``local_files_only=True``;
it is never given a bare model name (that would trigger a download outside the wizard).
"""

from __future__ import annotations

import logging
import os
import re
import threading
import unicodedata
from typing import Any, Callable, Iterable, Literal

import numpy as np

from . import config
from .model_setup import model_present

log = logging.getLogger(__name__)

os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
os.environ.setdefault("HF_HUB_OFFLINE", "1")


_LATIN = re.compile(r"[A-Za-z]")
_JA_SCRIPT = re.compile(r"[\u3040-\u30ff\u3400-\u9fff]")


class NoSpeech(Exception):
    pass


class WhisperNotReady(Exception):
    pass


def clip_rms(samples: np.ndarray) -> float:
    if samples.size == 0:
        return 0.0
    return float(np.sqrt(np.mean(np.square(samples.astype(np.float64)))))


def normalize_peak(samples: np.ndarray) -> np.ndarray:
    """Boost quiet clips toward NORMALIZE_PEAK (never attenuates, gain capped)."""
    x = samples.astype(np.float32, copy=False)
    peak = float(np.max(np.abs(x))) if x.size else 0.0
    if peak <= 0:
        return x
    g = min(config.MAX_NORMALIZE_GAIN, config.NORMALIZE_PEAK / peak)
    return (x * g).astype(np.float32) if g > 1.0 else x


def normalize_for_blocklist(text: str) -> str:
    s = unicodedata.normalize("NFKC", text).lower()
    return "".join(c for c in s if unicodedata.category(c)[0] not in ("P", "Z", "S", "C"))


def is_blocklisted(transcript: str, language: str, expected: Iterable[str]) -> bool:
    norm = normalize_for_blocklist(transcript)
    if not norm:
        return False
    if norm in {normalize_for_blocklist(e) for e in expected if e}:
        return False
    return norm in {normalize_for_blocklist(p) for p in config.HALLUCINATION_BLOCKLIST.get(language, ())}


def keep_segment(no_speech_prob: float, avg_logprob: float) -> bool:
    """Whisper's own 'this was probably silence' signal (drop if both are bad)."""
    return not (no_speech_prob > config.NO_SPEECH_PROB_MAX and avg_logprob < config.AVG_LOGPROB_MIN)


ModelFactory = Callable[[str, str, str], Any]


def _default_factory(path: str, device: str, compute_type: str):
    from faster_whisper import WhisperModel

    return WhisperModel(path, device=device, compute_type=compute_type, local_files_only=True)


class WhisperService:
    def __init__(self, model_dir, factory: ModelFactory = _default_factory):
        self.model_dir = model_dir
        self._factory = factory
        self._lock = threading.Lock()  # guards state fields
        self._infer_lock = threading.Lock()  # one transcription at a time
        self._model = None
        self.state = "missing"  # missing | downloading | loading | ready | error
        self.device = "cpu"
        self.error: str | None = None
        self._generation = 0

    def snapshot(self) -> dict:
        with self._lock:
            return {"state": self.state, "device": self.device, "error": self.error}

    @property
    def loaded_dir(self):
        """Folder of the model currently loaded (or being loaded)."""
        with self._lock:
            return self.model_dir

    def set_downloading(self) -> None:
        with self._lock:
            if self.state != "ready":
                self.state = "downloading"

    def load(self, device: str = "cpu", model_dir=None) -> None:
        """Blocking load (call from a background thread). CUDA failure falls back to CPU.

        ``model_dir`` switches to another model folder (the whisper_model setting)."""
        with self._lock:
            self._generation += 1
            gen = self._generation
            if model_dir is not None:
                self.model_dir = model_dir
            path = self.model_dir
            if not model_present(path):
                self.state, self._model, self.error = "missing", None, None
                return
            # Drop the old model first so two large models are never in (V)RAM at once.
            self.state, self.error, self._model = "loading", None, None
        model, used, err = None, "cpu", None
        if device == "cuda":
            try:
                model = self._factory(str(path), "cuda", "int8_float16")
                used = "cuda"
            except Exception as e:  # missing cuBLAS/cuDNN etc.
                log.warning("CUDA Whisper init failed, falling back to CPU: %s", e)
        if model is None:
            try:
                model = self._factory(str(path), "cpu", "int8")
            except Exception as e:
                log.exception("Whisper load failed")
                err = str(e)
        with self._lock:
            if gen != self._generation:
                return  # a newer load superseded this one
            self._model = model
            self.device = used
            self.state = "ready" if model is not None else "error"
            self.error = err

    def load_async(self, device: str = "cpu", model_dir=None) -> threading.Thread:
        t = threading.Thread(target=self.load, args=(device, model_dir), name="whisper-load", daemon=True)
        t.start()
        return t

    @property
    def ready(self) -> bool:
        with self._lock:
            return self.state == "ready" and self._model is not None

    def _run(self, model, samples: np.ndarray, language: str, prompt: str | None) -> str:
        with self._infer_lock:
            segments, _info = model.transcribe(
                samples,
                language=language,
                task="transcribe",
                beam_size=config.WHISPER_BEAM_SIZE,
                temperature=0.0,
                condition_on_previous_text=False,
                vad_filter=True,
                initial_prompt=prompt,
            )
            # Guard 3: drop "probably silence" segments.
            parts = [s.text for s in segments if keep_segment(s.no_speech_prob, s.avg_logprob)]
        sep = "" if language == "ja" else " "
        return sep.join(p.strip() for p in parts).strip()

    def transcribe_pcm(
        self,
        samples: np.ndarray,
        language: Literal["ja", "en"],
        expected: Iterable[str] = (),
    ) -> str:
        """16kHz mono float32 -> transcript. Raises NoSpeech / WhisperNotReady."""
        with self._lock:
            model = self._model if self.state == "ready" else None
        if model is None:
            raise WhisperNotReady("speech model not loaded")
        # Guard 1: energy gate.
        if clip_rms(samples) < config.ENERGY_GATE_RMS:
            raise NoSpeech("too quiet")
        samples = normalize_peak(samples)
        text = self._run(model, samples, language, None)
        if language == "ja" and _LATIN.search(text):
            # Whisper sometimes writes English words for a Japanese answer. Retry once
            # with a generic Japanese prompt; keep the retry only if it is Japanese script.
            retry = self._run(model, samples, language, config.JA_RETRY_PROMPT)
            if retry and _JA_SCRIPT.search(retry) and not _LATIN.search(retry):
                log.info("ja transcript was Latin; using Japanese-prompted retry")
                text = retry
        if not text:
            raise NoSpeech("empty transcript")
        # Guard 4: whole-transcript blocklist (unless it's an expected answer).
        if is_blocklisted(text, language, expected):
            raise NoSpeech("blocklisted hallucination")
        return text
