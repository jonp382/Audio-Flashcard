import numpy as np
import pytest

from audioflash import audio, config
from audioflash.audio import Busy, MicError, RingBuffer, decode_audio, encode_ogg_opus
from audioflash.stt import NoSpeech, WhisperNotReady, WhisperService, is_blocklisted

from .conftest import FakeSegment, install_fake_model_files, small_dir, tone

# --- STT ------------------------------------------------------------------------------


def test_missing_model_reports_missing(paths):
    calls = []
    w = WhisperService(small_dir(paths), factory=lambda *a: calls.append(a))
    w.load("cpu")
    assert w.snapshot()["state"] == "missing" and calls == []
    with pytest.raises(WhisperNotReady):
        w.transcribe_pcm(tone(1), "ja")


def test_loads_local_path_only(paths, fake_model):
    install_fake_model_files(small_dir(paths))
    seen = []
    w = WhisperService(small_dir(paths), factory=lambda p, d, ct: seen.append((p, d, ct)) or fake_model)
    w.load("cpu")
    assert seen == [(str(small_dir(paths)), "cpu", "int8")]


def test_cuda_failure_falls_back_to_cpu(paths, fake_model):
    install_fake_model_files(small_dir(paths))

    def factory(p, device, ct):
        if device == "cuda":
            raise RuntimeError("libcublas.so.12 not found")
        return fake_model

    w = WhisperService(small_dir(paths), factory=factory)
    w.load("cuda")
    assert w.snapshot() == {"state": "ready", "device": "cpu", "error": None}


def test_transcribe_options_and_language(whisper, fake_model):
    fake_model.next_segments = [FakeSegment(" はな"), FakeSegment("す ")]
    assert whisper.transcribe_pcm(tone(1), "ja") == "はなす"
    kw = fake_model.calls[-1]
    assert kw["language"] == "ja" and kw["vad_filter"] is True
    assert kw["condition_on_previous_text"] is False and kw["temperature"] == 0.0 and kw["beam_size"] == 5


def test_energy_gate_skips_whisper(whisper, fake_model):
    with pytest.raises(NoSpeech):
        whisper.transcribe_pcm(np.zeros(16000, dtype=np.float32), "ja")
    assert fake_model.calls == []


def test_empty_transcript_is_no_speech(whisper, fake_model):
    fake_model.next_segments = [FakeSegment("   ")]
    with pytest.raises(NoSpeech):
        whisper.transcribe_pcm(tone(1), "ja")


def test_silence_segments_dropped(whisper, fake_model):
    fake_model.next_segments = [FakeSegment("ご視聴", no_speech_prob=0.9, avg_logprob=-1.5), FakeSegment("ねこ")]
    assert whisper.transcribe_pcm(tone(1), "ja") == "ねこ"


def test_blocklisted_phrase_is_no_speech(whisper, fake_model):
    fake_model.next_segments = [FakeSegment("ご視聴ありがとうございました。")]
    with pytest.raises(NoSpeech):
        whisper.transcribe_pcm(tone(1), "ja")
    fake_model.next_segments = [FakeSegment("Thanks for watching!")]
    with pytest.raises(NoSpeech):
        whisper.transcribe_pcm(tone(1), "en")


def test_blocklisted_phrase_kept_when_expected(whisper, fake_model):
    fake_model.next_segments = [FakeSegment("Thank you.")]
    assert whisper.transcribe_pcm(tone(1), "en", expected=["thank you"]) == "Thank you."
    assert not is_blocklisted("ありがとうございました", "ja", ["ありがとうございました"])


# --- mic session -------------------------------------------------------------------------


def test_ring_buffer_never_exceeds_capacity():
    rb = RingBuffer(8000)
    for _ in range(50):
        rb.write(np.ones(1234, dtype=np.float32))
        assert len(rb) <= 8000 and rb.read().size <= 8000
    rb2 = RingBuffer(10)
    rb2.write(np.arange(7, dtype=np.float32))
    rb2.write(np.arange(7, 14, dtype=np.float32))
    assert rb2.read().tolist() == list(range(4, 14))
    rb2.write(np.arange(100, dtype=np.float32))
    assert rb2.read().tolist() == list(range(90, 100))


def test_preroll_prepended(mic, stream_factory):
    mic.open()
    s = stream_factory.current
    s.feed(np.full(16000, 0.1, dtype=np.float32))  # 1s idle audio
    assert mic.status()["mic_open"] and not mic.status()["recording"]
    mic.start_recording()
    s.feed(np.full(8000, 0.5, dtype=np.float32))
    clip = mic.stop_recording()
    assert clip.size == 8000 + int(config.PRE_ROLL_SECONDS * 16000)
    assert np.allclose(clip[:8000], 0.1) and np.allclose(clip[8000:], 0.5)


def test_no_preroll_after_autoclose(mic, stream_factory, mono):
    mic.open()
    stream_factory.current.feed(np.full(8000, 0.1, dtype=np.float32))
    mono.t += config.IDLE_MIC_TIMEOUT_S
    assert mic.check_idle() is True
    assert not mic.is_open and stream_factory.streams[0].closed
    mic.start_recording()  # re-opens
    assert mic.is_open and len(stream_factory.streams) == 2
    stream_factory.current.feed(np.full(1600, 0.5, dtype=np.float32))
    assert mic.stop_recording().size == 1600


def test_idle_close_not_while_recording(mic, mono):
    mic.open()
    mic.start_recording()
    mono.t += 1000
    assert mic.check_idle() is False and mic.is_open


def test_idle_timer_resets_on_activity(mic, mono):
    mic.open()
    mono.t += 100
    mic.start_recording()
    mic.cancel_recording()
    mono.t += 100
    assert mic.check_idle() is False


def test_start_twice_is_busy(mic):
    mic.open()
    mic.start_recording()
    with pytest.raises(Busy):
        mic.start_recording()


def test_15s_cap(mic, stream_factory):
    mic.open()
    mic.start_recording()
    stream_factory.current.feed(np.full(16000 * 20, 0.3, dtype=np.float32))
    st = mic.status()
    assert st["capped"] and st["recording"] and st["elapsed_s"] == pytest.approx(15.0)
    assert mic.stop_recording().size == 16000 * 15


def test_cancel_discards(mic, stream_factory):
    mic.open()
    mic.start_recording()
    stream_factory.current.feed(tone(1))
    assert mic.cancel_recording() is True
    assert not mic.status()["recording"]
    with pytest.raises(MicError):
        mic.stop_recording()


def test_open_close_idempotent_and_shutdown(mic, stream_factory):
    mic.open()
    mic.open()
    assert len(stream_factory.streams) == 1
    mic.close()
    mic.close()
    assert not mic.is_open
    mic.open()
    mic.start_recording()
    mic.shutdown()
    assert not mic.is_open and not mic.recording


def test_open_failure_is_mic_error(stream_factory, mono):
    stream_factory.fail = True
    m = audio.MicSession(stream_factory=stream_factory, clock=mono)
    with pytest.raises(MicError):
        m.open()


def test_level_meter(mic, stream_factory):
    mic.open()
    stream_factory.current.feed(np.full(1600, 0.2, dtype=np.float32))
    assert 0 < mic.status()["level"] <= 1


def test_resample():
    x = tone(1, rate=48000)
    assert audio.resample(x, 48000).size == 16000


class FakeSD:
    class default:
        device = (1, 3)

    def query_devices(self, *a):
        return [
            {"name": "out", "max_input_channels": 0},
            {"name": "USB Mic", "max_input_channels": 1},
            {"name": "Webcam", "max_input_channels": 2},
        ]


def test_list_and_resolve_devices():
    sd = FakeSD()
    assert audio.list_input_devices(sd) == [{"name": "USB Mic", "is_default": True}, {"name": "Webcam", "is_default": False}]
    assert audio.resolve_device("Webcam", sd) == (2, False)
    assert audio.resolve_device("Gone", sd) == (None, True)
    assert audio.resolve_device(None, sd) == (None, False)


# --- Ogg/Opus ----------------------------------------------------------------------------


def test_ogg_opus_roundtrip():
    x = tone(2.0, amp=0.3)
    data = encode_ogg_opus(x)
    assert data[:4] == b"OggS"
    assert len(data) < 2 * 8000  # roughly 3-4KB/s
    y = decode_audio(data)
    assert abs(y.size - x.size) < 1600
    rms_x = float(np.sqrt(np.mean(x**2)))
    rms_y = float(np.sqrt(np.mean(y**2)))
    assert rms_y == pytest.approx(rms_x, rel=0.2)


# --- gain, meter, normalization ----------------------------------------------------------


def test_gain_boosts_and_clips(mic, stream_factory):
    mic.set_gain(4.0)
    mic.open()
    mic.start_recording()
    stream_factory.current.feed(np.full(1600, 0.1, dtype=np.float32))
    stream_factory.current.feed(np.full(1600, 0.5, dtype=np.float32))
    st = mic.status()
    assert st["clipping"] is True and st["peak"] == 1.0
    clip = mic.stop_recording()
    assert np.allclose(clip[:1600], 0.4) and np.allclose(clip[1600:], 1.0)


def test_level_is_db_scaled():
    assert audio.level_from_rms(1.0) == 1.0
    assert audio.level_from_rms(0.001) == pytest.approx(0.0)
    assert audio.level_from_rms(0.01) == pytest.approx(1 / 3, abs=0.01)
    assert audio.level_from_rms(0.0) == 0.0


def test_quiet_clip_normalized_before_whisper(whisper, fake_model, monkeypatch):
    from audioflash import stt

    seen = []
    orig = fake_model.transcribe

    def spy(samples, **kw):
        seen.append(float(np.max(np.abs(samples))))
        return orig(samples, **kw)

    monkeypatch.setattr(fake_model, "transcribe", spy)
    whisper.transcribe_pcm(tone(1, amp=0.2), "ja")
    assert seen[-1] == pytest.approx(config.NORMALIZE_PEAK, rel=0.01)
    whisper.transcribe_pcm(tone(1, amp=0.95), "ja")
    assert seen[-1] == pytest.approx(0.95, rel=0.01)  # never attenuated
    assert stt.normalize_peak(np.full(10, 0.001, dtype=np.float32)).max() == pytest.approx(0.01)  # gain capped


def test_latin_ja_transcript_retried_with_generic_prompt(whisper, fake_model):
    fake_model.next_segments = [FakeSegment("Custer")]
    fake_model.prompted_segments = [FakeSegment("傘")]
    assert whisper.transcribe_pcm(tone(1), "ja") == "傘"
    assert fake_model.calls[0]["initial_prompt"] is None
    assert fake_model.calls[1]["initial_prompt"] == config.JA_RETRY_PROMPT


def test_latin_retry_kept_only_if_japanese(whisper, fake_model):
    fake_model.next_segments = [FakeSegment("Ben Kyo")]
    fake_model.prompted_segments = [FakeSegment("Ben Kyo")]
    assert whisper.transcribe_pcm(tone(1), "ja") == "Ben Kyo"  # romaji path handles it


def test_no_retry_for_japanese_or_english_decks(whisper, fake_model):
    fake_model.next_segments = [FakeSegment("べんきょう")]
    whisper.transcribe_pcm(tone(1), "ja")
    fake_model.next_segments = [FakeSegment("to study")]
    whisper.transcribe_pcm(tone(1), "en")
    assert len(fake_model.calls) == 2 and all(c["initial_prompt"] is None for c in fake_model.calls)


def test_prompt_echo_is_blocklisted(whisper, fake_model):
    fake_model.next_segments = [FakeSegment("Hmm")]
    fake_model.prompted_segments = [FakeSegment("日本語の単語を一つ言います。")]
    # retry output is the prompt itself -> rejected as hallucination
    with pytest.raises(NoSpeech):
        fake_model.next_segments = [FakeSegment("日本語の単語を一つ言います。")]
        whisper.transcribe_pcm(tone(1), "ja")
