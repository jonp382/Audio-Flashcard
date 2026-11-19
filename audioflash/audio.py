"""Mic capture with pre-roll, device listing, Ogg/Opus encoding, playback (agent.md §5).

Mic audio only lives in process memory (ring buffer / current recording) until a
graded recording is encoded and saved by the caller. Nothing here logs or sends audio.
"""

from __future__ import annotations

import io
import logging
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable

import numpy as np

from . import config

log = logging.getLogger(__name__)


class MicError(Exception):
    pass


class Busy(Exception):
    pass


def _sd():
    import sounddevice  # imported lazily so tests / import never require PortAudio

    return sounddevice


# --- Ring buffer ---------------------------------------------------------------------


class RingBuffer:
    """Fixed-size float32 ring buffer. Never grows."""

    def __init__(self, capacity: int):
        self.capacity = max(1, int(capacity))
        self._buf = np.zeros(self.capacity, dtype=np.float32)
        self._pos = 0
        self._filled = 0

    def write(self, x: np.ndarray) -> None:
        x = np.asarray(x, dtype=np.float32).reshape(-1)
        if x.size >= self.capacity:
            self._buf[:] = x[-self.capacity :]
            self._pos, self._filled = 0, self.capacity
            return
        end = self._pos + x.size
        if end <= self.capacity:
            self._buf[self._pos : end] = x
        else:
            k = self.capacity - self._pos
            self._buf[self._pos :] = x[:k]
            self._buf[: x.size - k] = x[k:]
        self._pos = end % self.capacity
        self._filled = min(self.capacity, self._filled + x.size)

    def read(self) -> np.ndarray:
        if self._filled < self.capacity:
            return self._buf[self._pos - self._filled : self._pos].copy() if self._pos >= self._filled else np.concatenate(
                [self._buf[self.capacity - (self._filled - self._pos) :], self._buf[: self._pos]]
            )
        return np.concatenate([self._buf[self._pos :], self._buf[: self._pos]])

    def clear(self) -> None:
        self._pos = self._filled = 0

    def __len__(self) -> int:
        return self._filled


def level_from_rms(rms: float) -> float:
    """RMS -> 0..1 meter position on a dB scale (-60 dBFS = 0, 0 dBFS = 1)."""
    if rms <= 1e-9:
        return 0.0
    return float(min(1.0, max(0.0, (20 * np.log10(rms) + 60) / 60)))


def apply_gain(x: np.ndarray, gain: float) -> np.ndarray:
    """Software mic boost with hard clipping to [-1, 1]."""
    if gain == 1.0:
        return x
    return np.clip(x * gain, -1.0, 1.0).astype(np.float32, copy=False)


def resample(x: np.ndarray, src_rate: int, dst_rate: int = config.SAMPLE_RATE) -> np.ndarray:
    """Linear-interpolation resample (adequate for speech -> Whisper)."""
    if src_rate == dst_rate or x.size == 0:
        return x.astype(np.float32, copy=False)
    n = int(round(x.size * dst_rate / src_rate))
    t_src = np.arange(x.size) / src_rate
    t_dst = np.arange(n) / dst_rate
    return np.interp(t_dst, t_src, x).astype(np.float32)


# --- Devices -------------------------------------------------------------------------


def list_input_devices(sd: Any = None) -> list[dict]:
    sd = sd or _sd()
    try:
        default_in = sd.default.device[0]
    except Exception:
        default_in = None
    out, seen = [], set()
    for idx, d in enumerate(sd.query_devices()):
        if d.get("max_input_channels", 0) > 0 and d["name"] not in seen:
            seen.add(d["name"])
            out.append({"name": d["name"], "is_default": idx == default_in})
    return out


def resolve_device(name: str | None, sd: Any = None) -> tuple[int | None, bool]:
    """Device name -> index. Returns (index or None for default, fallback_used)."""
    if not name:
        return None, False
    sd = sd or _sd()
    for idx, d in enumerate(sd.query_devices()):
        if d["name"] == name and d.get("max_input_channels", 0) > 0:
            return idx, False
    return None, True


# --- Mic session ---------------------------------------------------------------------


@dataclass
class _Rec:
    chunks: list
    samples: int
    started: float
    capped: bool = False


StreamFactory = Callable[..., Any]


class MicSession:
    """Owns the input stream, the 500ms pre-roll ring buffer and the active recording."""

    def __init__(
        self,
        stream_factory: StreamFactory | None = None,
        sd: Any = None,
        clock: Callable[[], float] = time.monotonic,
        idle_timeout: float = config.IDLE_MIC_TIMEOUT_S,
    ):
        self._sd = sd
        self._factory = stream_factory
        self._clock = clock
        self.idle_timeout = idle_timeout
        self._lock = threading.RLock()
        self._stream = None
        self._rate = config.SAMPLE_RATE
        self._ring = RingBuffer(int(config.PRE_ROLL_SECONDS * self._rate))
        self._rec: _Rec | None = None
        self._level = 0.0
        self._peak = 0.0
        self.gain = 1.0
        self._last_activity = clock()
        self.device_name: str | None = None
        self.fallback_used = False
        self._watchdog: threading.Thread | None = None
        self._stop_event = threading.Event()

    # -- stream management --

    def _sdmod(self):
        if self._sd is None:
            self._sd = _sd()
        return self._sd

    def _pick_rate(self, device: int | None) -> int:
        sd = self._sdmod()
        try:
            sd.check_input_settings(device=device, samplerate=config.SAMPLE_RATE, channels=1, dtype="float32")
            return config.SAMPLE_RATE
        except Exception:
            info = sd.query_devices(device if device is not None else sd.default.device[0])
            return int(info["default_samplerate"])

    def open(self, device_name: str | None = None) -> None:
        with self._lock:
            if self._stream is not None:
                return
            try:
                if self._factory is not None:
                    rate = config.SAMPLE_RATE
                    stream = self._factory(samplerate=rate, callback=self._callback)
                    self.fallback_used = False
                else:
                    sd = self._sdmod()
                    idx, self.fallback_used = resolve_device(device_name, sd)
                    rate = self._pick_rate(idx)
                    stream = sd.InputStream(
                        device=idx, samplerate=rate, channels=1, dtype="float32", callback=self._callback
                    )
                stream.start()
            except Exception as e:
                log.warning("mic open failed: %s", e)
                raise MicError(
                    "Could not open the microphone. Check that a mic is connected"
                    + (" and that 'Let desktop apps access your microphone' is on in Windows privacy settings." if _is_windows() else ".")
                ) from e
            self._stream = stream
            self._rate = rate
            self._ring = RingBuffer(int(config.PRE_ROLL_SECONDS * rate))
            self.device_name = device_name
            self._last_activity = self._clock()
            self._ensure_watchdog()

    def close(self) -> None:
        with self._lock:
            stream, self._stream = self._stream, None
            self._ring.clear()
            self._level = 0.0
            self._peak = 0.0
        if stream is not None:
            try:
                stream.stop()
                stream.close()
            except Exception:
                log.warning("error closing mic stream", exc_info=True)

    @property
    def is_open(self) -> bool:
        with self._lock:
            return self._stream is not None

    def _callback(self, indata, frames, time_info, status) -> None:  # PortAudio thread
        x = np.asarray(indata, dtype=np.float32)
        x = x[:, 0] if x.ndim == 2 else x.reshape(-1)
        with self._lock:
            if self._stream is None:  # late callback after close
                return
            x = apply_gain(x, self.gain)
            if x.size:
                self._level = level_from_rms(float(np.sqrt(np.mean(np.square(x)))))
                self._peak = float(np.max(np.abs(x)))
            rec = self._rec
            if rec is None:
                self._ring.write(x)
                return
            if rec.capped:
                return
            cap = int(config.MAX_CLIP_SECONDS * self._rate)
            room = cap - rec.samples
            if x.size >= room:
                x = x[:room]
                rec.capped = True
            rec.chunks.append(x.copy())
            rec.samples += x.size

    # -- recording --

    def start_recording(self, device_name: str | None = None) -> None:
        with self._lock:
            if self._rec is not None:
                raise Busy("already recording")
            if self._stream is None:
                self.open(device_name)  # re-open after auto-close: no pre-roll
                pre = np.zeros(0, dtype=np.float32)
            else:
                pre = self._ring.read()
            self._ring.clear()
            self._rec = _Rec(chunks=[pre] if pre.size else [], samples=pre.size, started=self._clock())
            self._last_activity = self._clock()

    def stop_recording(self) -> np.ndarray:
        """Returns the captured clip at 16kHz mono float32."""
        with self._lock:
            rec, self._rec = self._rec, None
            self._last_activity = self._clock()
            rate = self._rate
        if rec is None:
            raise MicError("not recording")
        x = np.concatenate(rec.chunks) if rec.chunks else np.zeros(0, dtype=np.float32)
        return resample(x, rate)

    def cancel_recording(self) -> bool:
        with self._lock:
            had = self._rec is not None
            self._rec = None
            self._last_activity = self._clock()
            return had

    def status(self) -> dict:
        with self._lock:
            rec = self._rec
            return {
                "mic_open": self._stream is not None,
                "recording": rec is not None,
                "elapsed_s": round(rec.samples / self._rate, 2) if rec else 0.0,
                "level": round(self._level, 3) if self._stream is not None else 0.0,
                "peak": round(self._peak, 3) if self._stream is not None else 0.0,
                "clipping": self._stream is not None and self._peak >= 0.99,
                "capped": bool(rec and rec.capped),
            }

    @property
    def recording(self) -> bool:
        with self._lock:
            return self._rec is not None

    # -- idle auto-close --

    def set_gain(self, gain: float) -> None:
        with self._lock:
            self.gain = float(gain)

    def check_idle(self) -> bool:
        """Close the mic if open, not recording, and idle too long. Returns True if closed."""
        with self._lock:
            idle = (
                self._stream is not None
                and self._rec is None
                and self._clock() - self._last_activity >= self.idle_timeout
            )
        if idle:
            log.info("closing idle mic")
            self.close()
        return idle

    def _ensure_watchdog(self) -> None:
        if self._watchdog is not None and self._watchdog.is_alive():
            return
        self._stop_event.clear()

        def run():
            while not self._stop_event.wait(1.0):
                self.check_idle()

        self._watchdog = threading.Thread(target=run, name="mic-idle", daemon=True)
        self._watchdog.start()

    def shutdown(self) -> None:
        self.cancel_recording()
        self.close()
        self._stop_event.set()


def _is_windows() -> bool:
    import sys

    return sys.platform.startswith("win")


# --- Ogg/Opus ------------------------------------------------------------------------


def encode_ogg_opus(samples: np.ndarray, rate: int = config.SAMPLE_RATE, bitrate: int = 24_000) -> bytes:
    import av

    buf = io.BytesIO()
    with av.open(buf, "w", format="ogg") as c:
        s = c.add_stream("libopus", rate=rate, layout="mono")
        s.bit_rate = bitrate
        res = av.AudioResampler(format=s.format.name, layout="mono", rate=rate, frame_size=rate // 50)
        frame = av.AudioFrame.from_ndarray(np.ascontiguousarray(samples, dtype=np.float32).reshape(1, -1), format="flt", layout="mono")
        frame.sample_rate = rate
        for rf in res.resample(frame) + res.resample(None):
            for p in s.encode(rf):
                c.mux(p)
        for p in s.encode(None):
            c.mux(p)
    return buf.getvalue()


def decode_audio(path_or_bytes, rate: int = config.SAMPLE_RATE) -> np.ndarray:
    import av

    src = io.BytesIO(path_or_bytes) if isinstance(path_or_bytes, (bytes, bytearray)) else str(path_or_bytes)
    out = []
    with av.open(src) as c:
        st = c.streams.audio[0]
        res = av.AudioResampler(format="flt", layout="mono", rate=rate)
        for fr in c.decode(st):
            for o in res.resample(fr):
                out.append(o.to_ndarray().reshape(-1))
        for o in res.resample(None):
            out.append(o.to_ndarray().reshape(-1))
    return np.concatenate(out).astype(np.float32) if out else np.zeros(0, dtype=np.float32)


# --- Playback ------------------------------------------------------------------------


class Player:
    def __init__(self, sd: Any = None):
        self._sd = sd
        self._lock = threading.Lock()

    def _sdmod(self):
        if self._sd is None:
            self._sd = _sd()
        return self._sd

    def play(self, path) -> None:
        self.play_array(decode_audio(path, rate=48_000), 48_000)

    def play_array(self, samples: np.ndarray, rate: int = config.SAMPLE_RATE) -> None:
        """Play in-memory audio (e.g. the mic test clip). Resampled to 48kHz, which
        every output device supports."""
        data = resample(samples, rate, 48_000) if rate != 48_000 else samples
        with self._lock:
            sd = self._sdmod()
            sd.stop()
            sd.play(data, 48_000)

    def stop(self) -> None:
        with self._lock:
            if self._sd is not None:
                try:
                    self._sd.stop()
                except Exception:
                    log.warning("stop playback failed", exc_info=True)
