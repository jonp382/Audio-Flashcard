"""Shared fixtures. No real models, mics, networks or windows are used in tests."""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx
import numpy as np
import pytest

os.environ["TZ"] = "UTC"
time.tzset()
os.environ["HF_HUB_OFFLINE"] = "1"

from audioflash import config, database  # noqa: E402
from audioflash.audio import MicSession  # noqa: E402
from audioflash.core import Core  # noqa: E402
from audioflash.evaluator import OllamaClient  # noqa: E402
from audioflash.model_setup import ModelDownloader  # noqa: E402
from audioflash.paths import DataPaths  # noqa: E402
from audioflash.stt import WhisperService  # noqa: E402


class Clock:
    def __init__(self, dt: datetime):
        self.now = dt

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **kw):
        from datetime import timedelta

        self.now = self.now + timedelta(**kw)


@pytest.fixture
def clock():
    return Clock(datetime(2026, 3, 10, 9, 0, 0, tzinfo=timezone.utc))


@pytest.fixture
def paths(tmp_path: Path) -> DataPaths:
    p = DataPaths(tmp_path / "data")
    p.ensure()
    return p


@pytest.fixture
def conn(paths):
    c = database.connect(paths.db)
    database.migrate(c)
    yield c
    c.close()


# --- fake whisper -------------------------------------------------------------------


class FakeSegment:
    def __init__(self, text, no_speech_prob=0.0, avg_logprob=-0.2):
        self.text = text
        self.no_speech_prob = no_speech_prob
        self.avg_logprob = avg_logprob


class FakeWhisperModel:
    def __init__(self):
        self.next_segments: list[FakeSegment] = [FakeSegment("はなす")]
        self.calls: list[dict] = []

        self.prompted_segments: list[FakeSegment] | None = None  # returned when initial_prompt is set

    def transcribe(self, samples, **kw):
        self.calls.append(kw)
        if kw.get("initial_prompt") and self.prompted_segments is not None:
            return iter(self.prompted_segments), None
        return iter(self.next_segments), None


def install_fake_model_files(model_dir: Path) -> None:
    model_dir.mkdir(parents=True, exist_ok=True)
    for name, (size, _) in config.WHISPER_FILES.items():
        with open(model_dir / name, "wb") as f:
            f.truncate(size)  # sparse file of the right size


@pytest.fixture
def fake_model():
    return FakeWhisperModel()


@pytest.fixture
def whisper(paths, fake_model):
    install_fake_model_files(paths.whisper_model)
    w = WhisperService(paths.whisper_model, factory=lambda path, device, ct: fake_model)
    w.load("cpu")
    assert w.ready
    return w


# --- fake ollama ----------------------------------------------------------------------


class FakeOllama:
    def __init__(self):
        self.models = ["qwen3:8b"]
        self.capabilities = ["completion", "thinking"]
        self.reply: object = {"score": 3, "feedback": "Correct."}
        self.down = False
        self.requests: list[tuple[str, dict | None]] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else None
        self.requests.append((request.url.path, body))
        if self.down:
            raise httpx.ConnectError("connection refused", request=request)
        path = request.url.path
        if path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": m, "model": m} for m in self.models]})
        if path == "/api/show":
            return httpx.Response(200, json={"capabilities": self.capabilities})
        if path == "/api/generate":
            return httpx.Response(200, json={"done": True})
        if path == "/api/chat":
            content = self.reply if isinstance(self.reply, str) else json.dumps(self.reply)
            return httpx.Response(200, json={"message": {"role": "assistant", "content": content}})
        return httpx.Response(404)

    def client(self) -> OllamaClient:
        return OllamaClient(transport=httpx.MockTransport(self.handler))

    def paths_called(self) -> list[str]:
        return [p for p, _ in self.requests]


@pytest.fixture
def ollama_fake():
    return FakeOllama()


# --- fake mic -----------------------------------------------------------------------


class FakeStream:
    def __init__(self, samplerate, callback):
        self.samplerate = samplerate
        self.callback = callback
        self.started = False
        self.closed = False

    def start(self):
        self.started = True

    def stop(self):
        self.started = False

    def close(self):
        self.closed = True

    def feed(self, x: np.ndarray, block: int = 1600):
        x = np.asarray(x, dtype=np.float32)
        for i in range(0, x.size, block):
            chunk = x[i : i + block].reshape(-1, 1)
            self.callback(chunk, chunk.shape[0], None, None)


class FakeStreamFactory:
    def __init__(self):
        self.streams: list[FakeStream] = []
        self.fail = False

    def __call__(self, samplerate, callback):
        if self.fail:
            raise RuntimeError("no device")
        s = FakeStream(samplerate, callback)
        self.streams.append(s)
        return s

    @property
    def current(self) -> FakeStream:
        return self.streams[-1]


class MonoClock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


@pytest.fixture
def stream_factory():
    return FakeStreamFactory()


@pytest.fixture
def mono():
    return MonoClock()


@pytest.fixture
def mic(stream_factory, mono):
    m = MicSession(stream_factory=stream_factory, clock=mono)
    yield m
    m.shutdown()


class FakePlayer:
    def __init__(self):
        self.played = []
        self.stopped = 0

    def play(self, path):
        self.played.append(path)

    def play_array(self, samples, rate=16000):
        self.played.append(("array", samples.size, rate))

    def stop(self):
        self.stopped += 1


def tone(seconds: float, amp: float = 0.2, rate: int = 16000) -> np.ndarray:
    t = np.arange(int(seconds * rate)) / rate
    return (amp * np.sin(2 * np.pi * 220 * t)).astype(np.float32)


@pytest.fixture
def core(paths, conn, whisper, ollama_fake, mic, clock):
    c = Core(
        paths,
        whisper=whisper,
        ollama=ollama_fake.client(),
        mic=mic,
        player=FakePlayer(),
        downloader=ModelDownloader(paths.models),
        clock=clock,
        encoder=lambda samples: b"OggS-fake",
        open_browser=lambda url: None,
    )
    yield c
    c.shutdown()
