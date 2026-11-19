"""Api called as plain Python: envelope, error codes, review flow, concurrency."""

import json
import threading

import numpy as np
import pytest

from audioflash.bridge import Api
from audioflash.errors import CODES

from .conftest import FakeSegment, tone


@pytest.fixture
def api(core):
    return Api(core)


def ok(res):
    assert res["ok"] is True, res
    json.dumps(res)
    return res["data"]


def code(res):
    assert res["ok"] is False, res
    assert set(res["error"]) == {"code", "message"}
    assert res["error"]["code"] in CODES
    json.dumps(res)
    return res["error"]["code"]


@pytest.fixture
def ja(api):
    """One-step Japanese card (the back is Japanese, so there is no meaning step)."""
    d = ok(api.create_deck("JA", "ja"))
    c = ok(api.create_card(d["id"], "話す", "はなす", "はなす"))
    return d, c


@pytest.fixture
def ja2(api):
    """Two-step card: say the reading, then the English meaning."""
    d = ok(api.create_deck("JA2", "ja"))
    c = ok(api.create_card(d["id"], "食べる", "to eat", "たべる"))
    return d, c


@pytest.fixture
def en(api):
    d = ok(api.create_deck("EN", "en"))
    c = ok(api.create_card(d["id"], "話す", "to speak"))
    return d, c


def record(api, core, stream_factory, card_id, audio=None):
    ok(api.start_recording(card_id))
    stream_factory.current.feed(tone(1) if audio is None else audio)
    return api.stop_recording()


def test_public_surface_has_no_unwrapped_objects(api):
    """pywebview exposes public attributes recursively; only envelope methods may be public."""
    public = [n for n in dir(api) if not n.startswith("_")]
    for n in public:
        assert callable(getattr(api, n)), n
        assert hasattr(getattr(api, n), "__wrapped__"), f"{n} is not wrapped in the envelope"


def test_status_shape(api):
    s = ok(api.get_status())
    for k in ("whisper_state", "whisper_device", "ollama_reachable", "llm_model", "llm_model_present", "mic_open", "mic_device", "mic_fallback_used", "setup_steps"):
        assert k in s
    assert s["whisper_state"] == "ready"


def test_invalid_input_codes(api, ja):
    d, c = ja
    assert code(api.create_deck("", "ja")) == "invalid_input"
    assert code(api.create_deck("x", "fr")) == "invalid_input"
    assert code(api.update_deck(d["id"], {"bogus": 1})) == "invalid_input"
    assert code(api.update_deck(d["id"], {"learning_steps": "1x"})) == "invalid_input"
    assert code(api.update_settings({"nope": 1})) == "invalid_input"
    assert code(api.update_settings({"ollama_port": 0})) == "invalid_input"
    assert code(api.get_deck("1")) == "invalid_input"
    assert code(api.get_deck(True)) == "invalid_input"
    assert code(api.get_deck()) == "invalid_input"  # missing arg
    assert code(api.override_grade(1, "yes")) == "invalid_input"
    assert code(api.stop_recording()) == "invalid_input"


def test_not_found_codes(api):
    assert code(api.get_deck(99)) == "not_found"
    assert code(api.delete_card(99)) == "not_found"
    assert code(api.start_recording(99)) == "not_found"
    assert code(api.retry_grading()) == "not_found"
    assert code(api.undo_last_review()) == "not_found"
    assert code(api.play_recording(99)) == "not_found"


def test_unexpected_exception_is_internal_error(api, core, monkeypatch):
    def boom():
        raise TypeError("bug inside")

    monkeypatch.setattr(core, "list_decks", boom)
    assert code(api.list_decks()) == "internal_error"


def test_update_deck_steps_string(api, ja):
    d, _ = ja
    out = ok(api.update_deck(d["id"], {"learning_steps": "1m 10m 1h", "new_per_day": 5}))
    assert out["learning_steps"] == [1, 10, 60] and out["new_per_day"] == 5


def test_settings_roundtrip(api):
    s = ok(api.update_settings({"record_mode": "hold", "whisper_device": "cpu"}))
    assert s["record_mode"] == "hold"
    assert ok(api.get_settings())["record_mode"] == "hold"


def test_full_ja_review_flow(api, core, stream_factory, fake_model, ja):
    d, c = ja
    nxt = ok(api.begin_session(d["id"]))
    assert nxt["card"]["id"] == c["id"]
    assert ok(api.get_recording_status())["mic_open"] is True
    fake_model.next_segments = [FakeSegment("はなす")]
    r = ok(record(api, core, stream_factory, c["id"]))
    assert r["score"] == 3 and r["is_correct"] and r["transcript"] == "はなす"
    assert r["recording_id"] and ok(api.list_recordings(c["id"]))[0]["id"] == r["recording_id"]
    ok(api.play_recording(r["recording_id"]))
    assert len(core.player.played) == 1
    hist = ok(api.list_reviews(c["id"]))
    assert hist[0]["id"] == r["review_id"]
    ok(api.end_session())
    assert ok(api.get_recording_status())["mic_open"] is False


def test_no_speech_discards(api, core, stream_factory, ja):
    d, c = ja
    ok(api.begin_session(d["id"]))
    res = record(api, core, stream_factory, c["id"], audio=np.zeros(16000, dtype=np.float32))
    assert code(res) == "no_speech"
    assert ok(api.list_reviews(c["id"])) == []
    assert ok(api.list_recordings(c["id"])) == []
    assert code(api.retry_grading()) == "not_found"


def test_whisper_not_ready(api, core, ja):
    d, c = ja
    core.whisper.state = "loading"
    assert code(api.start_recording(c["id"])) == "whisper_not_ready"


def test_start_twice_busy(api, core, stream_factory, ja):
    d, c = ja
    ok(api.begin_session(d["id"]))
    ok(api.start_recording(c["id"]))
    assert code(api.start_recording(c["id"])) == "busy"
    ok(api.cancel_recording())
    assert ok(api.get_recording_status())["recording"] is False


def test_playback_refused_while_recording(api, core, stream_factory, ja):
    d, c = ja
    ok(api.begin_session(d["id"]))
    r = ok(record(api, core, stream_factory, c["id"]))
    ok(api.start_recording(c["id"]))
    assert code(api.play_recording(r["recording_id"])) == "busy"


def test_mic_error(api, core, stream_factory, ja):
    d, c = ja
    stream_factory.fail = True
    assert code(api.begin_session(d["id"])) == "mic_error"
    assert code(api.start_recording(c["id"])) == "mic_error"


def test_en_llm_unavailable_upfront(api, ollama_fake, en):
    d, c = en
    ollama_fake.down = True
    assert code(api.start_recording(c["id"])) == "llm_unavailable"


def test_en_grading_failed_then_retry(api, core, stream_factory, fake_model, ollama_fake, en):
    d, c = en
    ok(api.begin_session(d["id"]))
    fake_model.next_segments = [FakeSegment("to talk")]
    ollama_fake.reply = "garbage"
    assert code(record(api, core, stream_factory, c["id"])) == "grading_failed"
    assert ok(api.list_reviews(c["id"])) == []  # nothing persisted
    assert core.get_card(c["id"])["state"] == "new"  # SRS untouched
    n_transcribe = len(fake_model.calls)
    ollama_fake.reply = {"score": 3, "feedback": "Good."}
    r = ok(api.retry_grading())
    assert r["score"] == 3 and r["transcript"] == "to talk"
    assert len(fake_model.calls) == n_transcribe  # same audio, not re-transcribed
    assert code(api.retry_grading()) == "not_found"


def test_en_llm_down_mid_review_then_retry(api, core, stream_factory, fake_model, ollama_fake, en):
    d, c = en
    ok(api.begin_session(d["id"]))
    fake_model.next_segments = [FakeSegment("to speak")]
    ok(api.start_recording(c["id"]))
    stream_factory.current.feed(tone(1))
    ollama_fake.down = True
    assert code(api.stop_recording()) == "llm_unavailable"
    ollama_fake.down = False
    assert ok(api.retry_grading())["score"] == 3


def test_new_recording_discards_held_audio(api, core, stream_factory, fake_model, ollama_fake, en):
    d, c = en
    ok(api.begin_session(d["id"]))
    ollama_fake.reply = "garbage"
    assert code(record(api, core, stream_factory, c["id"])) == "grading_failed"
    ok(api.start_recording(c["id"]))
    ok(api.cancel_recording())
    assert code(api.retry_grading()) == "not_found"


def test_en_session_warms_up(api, ollama_fake, en):
    d, c = en
    ok(api.begin_session(d["id"]))
    import time

    for _ in range(100):
        if "/api/generate" in ollama_fake.paths_called():
            break
        time.sleep(0.01)
    assert "/api/generate" in ollama_fake.paths_called()


def test_ja_session_never_touches_ollama(api, core, stream_factory, ollama_fake, ja):
    d, c = ja
    ollama_fake.requests.clear()
    ok(api.begin_session(d["id"]))
    ok(record(api, core, stream_factory, c["id"]))
    assert ollama_fake.requests == []


def test_override_and_undo_via_api(api, core, stream_factory, fake_model, ja):
    d, c = ja
    ok(api.begin_session(d["id"]))
    fake_model.next_segments = [FakeSegment("ねこ")]
    r = ok(record(api, core, stream_factory, c["id"]))
    assert r["score"] == 1
    o = ok(api.override_grade(r["review_id"], True))
    assert o["score"] == 3 and o["score_original"] == 1
    u = ok(api.undo_last_review())
    assert u["card_id"] == c["id"]
    assert core.get_card(c["id"])["state"] == "new"


def test_open_ollama_page_hardcoded(api, core):
    opened = []
    core.open_browser = opened.append
    ok(api.open_ollama_download_page())
    assert opened == ["https://ollama.com/download"]


def test_concurrent_calls_do_not_corrupt(api, core, ja):
    d, _ = ja
    errors = []

    def worker(i):
        try:
            for j in range(10):
                res = api.create_card(d["id"], f"w{i}-{j}", "b")
                assert res["ok"], res
                assert api.list_decks()["ok"]
                assert api.get_next_card(d["id"])["ok"]
        except Exception as e:  # pragma: no cover
            errors.append(e)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    assert len(ok(api.list_cards(d["id"]))) == 81


def test_concurrent_review_lock_returns_busy(api, core, ja):
    d, c = ja
    ok(api.begin_session(d["id"]))
    core._review_lock.acquire()
    try:
        assert code(api.start_recording(c["id"])) == "busy"
        assert code(api.undo_last_review()) == "busy"
    finally:
        core._review_lock.release()


def test_shutdown_idempotent(core, mic):
    mic.open()
    core.shutdown()
    core.shutdown()
    assert not mic.is_open


# --- mic gain & mic test ---------------------------------------------------------------


def test_mic_gain_setting(api, core):
    assert ok(api.get_settings())["mic_gain"] == 1.0
    ok(api.update_settings({"mic_gain": 4}))
    assert core.mic.gain == 4.0
    for bad in (0.5, 20, "2", True):
        assert code(api.update_settings({"mic_gain": bad})) == "invalid_input"


def test_mic_test_flow(api, core, stream_factory, fake_model):
    assert code(api.mic_test_play()) == "not_found"
    ok(api.mic_test_start())
    assert code(api.mic_test_start()) == "busy"
    fake_model.next_segments = [FakeSegment("べんきょう")]
    stream_factory.current.feed(tone(1, amp=0.3))
    r = ok(api.mic_test_stop("ja"))
    assert r["transcript"] == "べんきょう" and not r["too_quiet"] and not r["clipping"]
    assert 0 < r["gate_level"] < r["level"] <= 1
    ok(api.mic_test_play())
    assert core.player.played[-1][0] == "array"
    assert ok(api.list_decks()) == []  # nothing saved


def test_mic_test_too_quiet(api, stream_factory):
    ok(api.mic_test_start())
    stream_factory.current.feed(np.full(16000, 0.0005, dtype=np.float32))
    r = ok(api.mic_test_stop("en"))
    assert r["too_quiet"] and r["transcript"] is None and "quiet" in r["transcript_note"]


def test_mic_test_does_not_touch_review_recording(api, core, stream_factory, ja):
    d, c = ja
    ok(api.begin_session(d["id"]))
    ok(api.start_recording(c["id"]))
    assert code(api.mic_test_start()) == "busy"
    ok(api.mic_test_cancel())
    assert ok(api.get_recording_status())["recording"] is True
    assert code(api.mic_test_stop()) == "busy"
