"""Two-step cards: say the reading (graded by reading match), then the meaning (graded
by the LLM). One SRS update per card; combined score = lowest step score."""

import numpy as np
import pytest

from audioflash.database import has_meaning_step

from .conftest import FakeSegment, tone
from .test_bridge import api, code, ja, ja2, ok  # noqa: F401  (fixtures)


def say(api, stream_factory, fake_model, card_id, text, step=None):
    fake_model.next_segments = [FakeSegment(text)]
    ok(api.start_recording(card_id, step) if step else api.start_recording(card_id))
    stream_factory.current.feed(tone(1))
    return api.stop_recording()


@pytest.mark.parametrize(
    "language, back, reading, expected",
    [
        ("ja", "to eat", "たべる", True),
        ("ja", "I (polite, general)", "わたし", True),
        ("ja", "to eat", None, False),  # nothing to say for the reading
        ("ja", "to eat", "", False),
        ("ja", "たべる", "たべる", False),  # back is Japanese
        ("ja", "eat; たべる", "たべる", False),
        ("en", "to eat", "たべる", False),  # English-answer decks stay one step
    ],
)
def test_has_meaning_step(language, back, reading, expected):
    assert has_meaning_step(language, back, reading) is expected


def test_cards_report_meaning_step(api, ja, ja2):
    d, c = ja2
    assert ok(api.get_next_card(d["id"]))["card"]["meaning_step"] is True
    assert ok(api.get_card(c["id"]))["meaning_step"] is True
    assert ok(api.get_card(ja[1]["id"]))["meaning_step"] is False


def test_two_step_flow(api, core, stream_factory, fake_model, ollama_fake, ja2):
    d, c = ja2
    ok(api.begin_session(d["id"]))
    heard = ok(say(api, stream_factory, fake_model, c["id"], "たべる"))
    assert heard == {"stage": "heard", "step": "reading", "card_id": c["id"], "transcript": "たべる"}
    assert fake_model.calls[-1]["language"] == "ja"
    # Not graded yet: nothing persisted, schedule untouched, ollama not asked to grade.
    assert ok(api.list_reviews(c["id"])) == []
    assert core.get_card(c["id"])["state"] == "new"
    assert "/api/chat" not in ollama_fake.paths_called()

    ollama_fake.reply = {"score": 3, "feedback": "Correct meaning."}
    r = ok(say(api, stream_factory, fake_model, c["id"], "to eat", "meaning"))
    assert fake_model.calls[-1]["language"] == "en"
    assert r["score"] == 3 and r["is_correct"]
    assert [s["step"] for s in r["steps"]] == ["reading", "meaning"]
    assert [s["score"] for s in r["steps"]] == [3, 3]
    assert r["steps"][0]["transcript"] == "たべる" and r["steps"][1]["transcript"] == "to eat"
    assert r["transcript"] == "たべる / to eat"
    assert r["recording_id"]
    assert core.get_card(c["id"])["state"] == "learning"
    # the meaning grader got the card's meaning, not the kana
    body = [b for p, b in ollama_fake.requests if p == "/api/chat"][-1]
    assert "Expected answer (back): to eat" in body["messages"][1]["content"]
    hist = ok(api.list_reviews(c["id"]))
    assert len(hist) == 1 and hist[0]["steps"][1]["feedback"] == "Correct meaning."


def test_one_review_saves_both_clips_as_one_recording(api, core, stream_factory, fake_model, ja2):
    d, c = ja2
    sizes = []
    core.encoder = lambda samples: sizes.append(samples.size) or b"OggS-fake"
    ok(api.begin_session(d["id"]))
    ok(say(api, stream_factory, fake_model, c["id"], "たべる"))
    ok(say(api, stream_factory, fake_model, c["id"], "to eat", "meaning"))
    assert len(ok(api.list_recordings(c["id"]))) == 1
    assert sizes[0] > 2 * 16000  # reading + gap + meaning


def test_weakest_step_decides(api, core, stream_factory, fake_model, ollama_fake, ja2):
    d, c = ja2
    ok(api.begin_session(d["id"]))
    ok(say(api, stream_factory, fake_model, c["id"], "のむ"))  # wrong reading
    ollama_fake.reply = {"score": 3, "feedback": "Correct."}
    r = ok(say(api, stream_factory, fake_model, c["id"], "to eat", "meaning"))
    assert [s["score"] for s in r["steps"]] == [1, 3]
    assert r["score"] == 1 and not r["is_correct"]


def test_rerecord_reading_replaces_it(api, core, stream_factory, fake_model, ja2):
    d, c = ja2
    ok(api.begin_session(d["id"]))
    assert ok(say(api, stream_factory, fake_model, c["id"], "たべぺ"))["transcript"] == "たべぺ"
    assert ok(say(api, stream_factory, fake_model, c["id"], "たべる", "reading"))["transcript"] == "たべる"
    r = ok(say(api, stream_factory, fake_model, c["id"], "to eat", "meaning"))
    assert r["steps"][0]["transcript"] == "たべる" and r["steps"][0]["score"] == 3


def test_meaning_needs_reading_first(api, core, stream_factory, fake_model, ja, ja2):
    d, c = ja2
    ok(api.begin_session(d["id"]))
    assert code(api.start_recording(c["id"], "meaning")) == "invalid_input"
    assert code(api.start_recording(c["id"], "bogus")) == "invalid_input"
    # a reading heard for another card doesn't count
    other = ok(api.create_card(d["id"], "飲む", "to drink", "のむ"))
    ok(say(api, stream_factory, fake_model, other["id"], "のむ"))
    assert code(api.start_recording(c["id"], "meaning")) == "invalid_input"


def test_one_step_card_ignores_step(api, core, stream_factory, fake_model, ja):
    d, c = ja
    ok(api.begin_session(d["id"]))
    r = ok(say(api, stream_factory, fake_model, c["id"], "はなす", "reading"))
    assert r["score"] == 3 and r["steps"] is None


def test_cancel_meaning_keeps_reading(api, core, stream_factory, fake_model, ja2):
    d, c = ja2
    ok(api.begin_session(d["id"]))
    ok(say(api, stream_factory, fake_model, c["id"], "たべる"))
    ok(api.start_recording(c["id"], "meaning"))
    ok(api.cancel_recording())
    r = ok(say(api, stream_factory, fake_model, c["id"], "to eat", "meaning"))
    assert r["steps"][0]["transcript"] == "たべる"


def test_no_speech_on_meaning_keeps_reading(api, core, stream_factory, fake_model, ja2):
    d, c = ja2
    ok(api.begin_session(d["id"]))
    ok(say(api, stream_factory, fake_model, c["id"], "たべる"))
    ok(api.start_recording(c["id"], "meaning"))
    stream_factory.current.feed(np.zeros(16000, dtype=np.float32))
    assert code(api.stop_recording()) == "no_speech"
    assert ok(say(api, stream_factory, fake_model, c["id"], "to eat", "meaning"))["score"] == 3


def test_two_step_needs_ollama_before_reading(api, ollama_fake, ja2):
    d, c = ja2
    ollama_fake.down = True
    assert code(api.start_recording(c["id"])) == "llm_unavailable"


def test_two_step_deck_warms_up_ollama(api, ollama_fake, ja2):
    import time

    d, c = ja2
    ok(api.begin_session(d["id"]))
    for _ in range(100):
        if "/api/generate" in ollama_fake.paths_called():
            break
        time.sleep(0.01)
    assert "/api/generate" in ollama_fake.paths_called()


def test_meaning_grading_failed_then_retry(api, core, stream_factory, fake_model, ollama_fake, ja2):
    d, c = ja2
    ok(api.begin_session(d["id"]))
    ok(say(api, stream_factory, fake_model, c["id"], "たべる"))
    ollama_fake.reply = "garbage"
    assert code(say(api, stream_factory, fake_model, c["id"], "to eat", "meaning")) == "grading_failed"
    assert ok(api.list_reviews(c["id"])) == []
    ollama_fake.reply = {"score": 2, "feedback": "Close."}
    r = ok(api.retry_grading())
    assert [s["score"] for s in r["steps"]] == [3, 2] and r["score"] == 2
    assert r["steps"][0]["transcript"] == "たべる"


def test_override_one_step(api, core, stream_factory, fake_model, ollama_fake, ja2):
    d, c = ja2
    ok(api.begin_session(d["id"]))
    ok(say(api, stream_factory, fake_model, c["id"], "のむ"))  # misheard as a different word
    ollama_fake.reply = {"score": 3, "feedback": "Correct."}
    r = ok(say(api, stream_factory, fake_model, c["id"], "to eat", "meaning"))
    assert r["score"] == 1
    o = ok(api.override_grade(r["review_id"], True, "reading"))
    assert [s["score"] for s in o["steps"]] == [3, 3]
    assert o["steps"][0]["overridden"] and o["steps"][0]["score_original"] == 1
    assert not o["steps"][1]["overridden"]
    assert o["score"] == 3 and o["score_original"] == 1 and o["is_correct"]
    o = ok(api.override_grade(r["review_id"], False, "meaning"))
    assert [s["score"] for s in o["steps"]] == [3, 1] and o["score"] == 1
    assert o["steps"][1]["score_original"] == 3
    # schedule recomputed from the snapshot each time, never compounded
    assert core.get_card(c["id"])["state"] == "learning"
    assert code(api.override_grade(r["review_id"], True, "bogus")) == "invalid_input"


def test_override_whole_card_sets_every_step(api, core, stream_factory, fake_model, ollama_fake, ja2):
    d, c = ja2
    ok(api.begin_session(d["id"]))
    ok(say(api, stream_factory, fake_model, c["id"], "たべる"))
    r = ok(say(api, stream_factory, fake_model, c["id"], "to eat", "meaning"))
    o = ok(api.override_grade(r["review_id"], False))
    assert [s["score"] for s in o["steps"]] == [1, 1] and o["score"] == 1


def test_step_override_refused_on_one_step_review(api, core, stream_factory, fake_model, ja):
    d, c = ja
    ok(api.begin_session(d["id"]))
    r = ok(say(api, stream_factory, fake_model, c["id"], "はなす"))
    assert code(api.override_grade(r["review_id"], True, "reading")) == "invalid_input"


def test_undo_two_step_review(api, core, stream_factory, fake_model, ja2):
    d, c = ja2
    ok(api.begin_session(d["id"]))
    ok(say(api, stream_factory, fake_model, c["id"], "たべる"))
    ok(say(api, stream_factory, fake_model, c["id"], "to eat", "meaning"))
    assert ok(api.undo_last_review())["card_id"] == c["id"]
    assert core.get_card(c["id"])["state"] == "new"
    assert ok(api.list_reviews(c["id"])) == [] and ok(api.list_recordings(c["id"])) == []
    # the old reading is gone: the card starts again from the reading step
    assert code(api.start_recording(c["id"], "meaning")) == "invalid_input"


def test_end_session_drops_heard_reading(api, core, stream_factory, fake_model, ja2):
    d, c = ja2
    ok(api.begin_session(d["id"]))
    ok(say(api, stream_factory, fake_model, c["id"], "たべる"))
    ok(api.end_session())
    ok(api.begin_session(d["id"]))
    assert code(api.start_recording(c["id"], "meaning")) == "invalid_input"


def test_shared_front_needs_the_right_reading(api, core, stream_factory, fake_model, ja2):
    """方 ほう / 方 かた: a bare 方 transcript can't prove which reading was said."""
    d, _ = ja2
    hou = ok(api.create_card(d["id"], "方", "direction, side", "ほう"))
    kata = ok(api.create_card(d["id"], "方", "person (polite form)", "かた"))
    ok(api.begin_session(d["id"]))
    ok(say(api, stream_factory, fake_model, kata["id"], "方"))
    r = ok(say(api, stream_factory, fake_model, kata["id"], "person", "meaning"))
    assert r["steps"][0]["score"] < 3
    ok(say(api, stream_factory, fake_model, kata["id"], "かた"))
    r = ok(say(api, stream_factory, fake_model, kata["id"], "person", "meaning"))
    assert r["steps"][0]["score"] == 3
    assert hou["id"] != kata["id"]


def test_unique_front_keeps_kanji_shortcut(api, core, stream_factory, fake_model, ja2):
    d, c = ja2
    ok(api.begin_session(d["id"]))
    ok(say(api, stream_factory, fake_model, c["id"], "食べる"))
    r = ok(say(api, stream_factory, fake_model, c["id"], "to eat", "meaning"))
    assert r["steps"][0]["score"] == 3


def test_meaning_grader_gets_example_sentence(api, core, stream_factory, fake_model, ollama_fake, ja2):
    """人 じん 'person from (nationality)': the sentence shows which sense is meant."""
    d, _ = ja2
    c = ok(api.create_card(d["id"], "人", "person from (nationality)", "じん", [], "私はイギリス<b>人</b>です。", "I am British."))
    ok(api.begin_session(d["id"]))
    ok(say(api, stream_factory, fake_model, c["id"], "じん"))
    ok(say(api, stream_factory, fake_model, c["id"], "of a nationality", "meaning"))
    body = [b for p, b in ollama_fake.requests if p == "/api/chat"][-1]
    user = body["messages"][1]["content"]
    assert "Expected answer (back): person from (nationality)" in user
    assert "Example sentence: 私はイギリス<b>人</b>です。 (I am British.)" in user
    system = body["messages"][0]["content"]
    assert "placeholder" in system and "not whether they used the same words" in system


def test_no_sentence_line_without_sentence(api, core, stream_factory, fake_model, ollama_fake, ja2):
    d, c = ja2
    ok(api.begin_session(d["id"]))
    ok(say(api, stream_factory, fake_model, c["id"], "たべる"))
    ok(say(api, stream_factory, fake_model, c["id"], "to eat", "meaning"))
    body = [b for p, b in ollama_fake.requests if p == "/api/chat"][-1]
    assert "Example sentence" not in body["messages"][1]["content"]


def test_en_deck_grader_gets_example_sentence(api, core, stream_factory, fake_model, ollama_fake):
    d = ok(api.create_deck("EN", "en"))
    c = ok(api.create_card(d["id"], "高い", "expensive", None, [], "この時計は<b>高い</b>。", "This watch is expensive."))
    ok(api.begin_session(d["id"]))
    ok(say(api, stream_factory, fake_model, c["id"], "expensive"))
    body = [b for p, b in ollama_fake.requests if p == "/api/chat"][-1]
    assert "Example sentence: この時計は<b>高い</b>。 (This watch is expensive.)" in body["messages"][1]["content"]


def test_meaning_grader_gets_card_notes(api, core, stream_factory, fake_model, ollama_fake, ja2):
    """さん: the deck's meaning is just "san"; the notes say what it is."""
    d, _ = ja2
    c = ok(api.create_card(d["id"], "さん", "san", "さん", [], None, None, "Most commonplace neutral polite honorific."))
    assert c["notes"] == "Most commonplace neutral polite honorific."
    ok(api.begin_session(d["id"]))
    ok(say(api, stream_factory, fake_model, c["id"], "さん"))
    ok(say(api, stream_factory, fake_model, c["id"], "honorific", "meaning"))
    body = [b for p, b in ollama_fake.requests if p == "/api/chat"][-1]
    assert "Card notes: Most commonplace neutral polite honorific." in body["messages"][1]["content"]
    assert "Card notes" in body["messages"][0]["content"]
    ok(api.update_card(c["id"], {"notes": "  "}))
    assert ok(api.get_card(c["id"]))["notes"] is None


# --- weak spots -------------------------------------------------------------------------


def two_step(api, stream_factory, fake_model, ollama_fake, card_id, reading_said, meaning_score):
    ok(say(api, stream_factory, fake_model, card_id, reading_said))
    ollama_fake.reply = {"feedback": "fb", "score": meaning_score}
    return ok(say(api, stream_factory, fake_model, card_id, "x", "meaning"))


def test_weak_spots(api, core, stream_factory, fake_model, ollama_fake, clock, ja, ja2):
    d, eat = ja2
    drink = ok(api.create_card(d["id"], "飲む", "to drink", "のむ"))
    see = ok(api.create_card(d["id"], "見る", "to see", "みる"))
    ok(api.begin_session(d["id"]))
    # 食べる: reading missed twice, then right
    two_step(api, stream_factory, fake_model, ollama_fake, eat["id"], "のむ", 3)
    clock.advance(minutes=1)
    two_step(api, stream_factory, fake_model, ollama_fake, eat["id"], "のむ", 3)
    clock.advance(minutes=1)
    two_step(api, stream_factory, fake_model, ollama_fake, eat["id"], "たべる", 3)
    # 飲む: meaning missed (still wrong on the latest answer)
    clock.advance(minutes=1)
    two_step(api, stream_factory, fake_model, ollama_fake, drink["id"], "のむ", 1)
    # 見る: always right -> not listed
    clock.advance(minutes=1)
    two_step(api, stream_factory, fake_model, ollama_fake, see["id"], "みる", 3)
    # one-step deck reviews don't count
    ok(say(api, stream_factory, fake_model, ja[1]["id"], "はなす"))

    w = ok(api.get_weak_spots(d["id"]))
    assert w["steps"]["reading"] == {"answers": 5, "correct": 3}
    assert w["steps"]["meaning"] == {"answers": 5, "correct": 4}
    cards = {c["front"]: c for c in w["cards"]}
    assert set(cards) == {"食べる", "飲む"}
    assert cards["食べる"]["reading_misses"] == 2 and cards["食べる"]["meaning_misses"] == 0
    assert cards["食べる"]["answers"] == 3 and cards["食べる"]["recovered"] is True
    assert cards["飲む"]["meaning_misses"] == 1 and cards["飲む"]["recovered"] is False
    # unrecovered cards come first even with fewer misses
    assert [c["front"] for c in w["cards"]] == ["飲む", "食べる"]


def test_weak_spots_use_overrides_and_window(api, core, stream_factory, fake_model, ollama_fake, clock, ja2):
    d, c = ja2
    ok(api.begin_session(d["id"]))
    r = two_step(api, stream_factory, fake_model, ollama_fake, c["id"], "のむ", 3)
    assert ok(api.get_weak_spots(d["id"]))["cards"][0]["reading_misses"] == 1
    ok(api.override_grade(r["review_id"], True, "reading"))  # "I was right"
    w = ok(api.get_weak_spots(d["id"]))
    assert w["cards"] == [] and w["steps"]["reading"]["correct"] == 1
    ok(api.override_grade(r["review_id"], False, "reading"))
    clock.advance(days=40)
    assert ok(api.get_weak_spots(d["id"], 30))["steps"]["reading"]["answers"] == 0
    assert ok(api.get_weak_spots(d["id"], 0))["cards"][0]["reading_misses"] == 1  # 0 = all history
    assert code(api.get_weak_spots(d["id"], -1)) == "invalid_input"
    assert code(api.get_weak_spots(999)) == "not_found"


def test_question_marks_stripped_from_meaning(api, core, stream_factory, fake_model, ollama_fake, ja2):
    """大きな: Whisper wrote "big or large?" and the LLM marked it down as a question."""
    d, c = ja2
    ok(api.begin_session(d["id"]))
    assert ok(say(api, stream_factory, fake_model, c["id"], "たべる？"))["transcript"] == "たべる？"  # reading untouched
    r = ok(say(api, stream_factory, fake_model, c["id"], "big or large?", "meaning"))
    assert r["steps"][1]["transcript"] == "big or large"
    body = [b for p, b in ollama_fake.requests if p == "/api/chat"][-1]
    assert "Transcript of learner's answer: big or large\n" in body["messages"][1]["content"] + "\n"
    assert "never judge" in body["messages"][0]["content"]


def test_only_question_marks_is_no_speech(api, core, stream_factory, fake_model, ja2):
    d, c = ja2
    ok(api.begin_session(d["id"]))
    ok(say(api, stream_factory, fake_model, c["id"], "たべる"))
    assert code(say(api, stream_factory, fake_model, c["id"], "?", "meaning")) == "no_speech"


def test_en_deck_strips_question_marks(api, core, stream_factory, fake_model, ollama_fake):
    d = ok(api.create_deck("EN", "en"))
    c = ok(api.create_card(d["id"], "大きな", "big, large"))
    ok(api.begin_session(d["id"]))
    assert ok(say(api, stream_factory, fake_model, c["id"], "Big?"))["transcript"] == "Big"
