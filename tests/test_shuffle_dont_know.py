"""Shuffle of due cards, "I don't know", and the theme / shuffle settings."""

import random
from datetime import timedelta

import pytest

from audioflash.errors import AppError
from audioflash.timeutil import to_iso

from .test_bridge import api, code, en, ja, ja2, ok  # noqa: F401  (fixtures)
from .test_meaning_step import say
from .test_scheduler import answer, make_deck, set_card

# --- settings -------------------------------------------------------------------------


def test_theme_and_shuffle_defaults_and_validation(api):
    s = ok(api.get_settings())
    assert s["theme"] == "dark" and s["shuffle_due"] is False
    s = ok(api.update_settings({"theme": "light", "shuffle_due": True}))
    assert s["theme"] == "light" and s["shuffle_due"] is True
    assert code(api.update_settings({"theme": "purple"})) == "invalid_input"
    assert code(api.update_settings({"shuffle_due": 1})) == "invalid_input"


def test_ui_scale_setting(api):
    assert ok(api.get_settings())["ui_scale"] == 1.0
    assert ok(api.update_settings({"ui_scale": 1.25}))["ui_scale"] == 1.25
    assert ok(api.update_settings({"ui_scale": 1.2000001}))["ui_scale"] == 1.2
    for bad in (0.1, 3, True, "1.5"):
        assert code(api.update_settings({"ui_scale": bad})) == "invalid_input"


# --- shuffle --------------------------------------------------------------------------


def learning_deck(core, clock, n):
    """n learning cards, all due now, in a fixed due order (card 0 due first)."""
    d, cards = make_deck(core, n, new_per_day=0)
    for i, c in enumerate(cards):
        set_card(core, c["id"], state="learning", learning_step=1, due_at=to_iso(clock.now - timedelta(minutes=n - i)))
    return d, [c["id"] for c in cards]


def firsts(core, deck_id, seeds):
    out = set()
    for seed in seeds:
        core.rng = random.Random(seed)
        out.add(core.get_next_card(deck_id)["card"]["id"])
    return out


def test_shuffle_off_keeps_due_order(core, clock):
    d, ids = learning_deck(core, clock, 5)
    assert firsts(core, d["id"], range(20)) == {ids[0]}


def test_shuffle_picks_any_due_learning_card(core, clock):
    d, ids = learning_deck(core, clock, 5)
    core.update_settings({"shuffle_due": True})
    assert firsts(core, d["id"], range(50)) == set(ids)


def test_shuffle_also_covers_learn_ahead(core, clock):
    d, ids = learning_deck(core, clock, 4)
    for i, cid in enumerate(ids):
        set_card(core, cid, due_at=to_iso(clock.now + timedelta(minutes=5 + i)))
    core.update_settings({"shuffle_due": True})
    assert firsts(core, d["id"], range(50)) == set(ids)


def test_shuffle_never_repeats_the_card_just_answered(core, clock):
    d, ids = learning_deck(core, clock, 2)
    core.update_settings({"shuffle_due": True})
    answer(core, ids[0], 0)  # back to step 0: due again in 1 min, inside learn-ahead
    clock.advance(minutes=5)  # both due now
    assert firsts(core, d["id"], range(30)) == {ids[1]}
    answer(core, ids[1], 3)  # graduates step 1 -> review, leaves ids[0] as the only one
    assert firsts(core, d["id"], range(5)) == {ids[0]}


def test_shuffle_reviews_only_within_the_oldest_due_date(core):
    d, cards = make_deck(core, 4)
    ids = [c["id"] for c in cards]
    for cid in ids[:3]:
        set_card(core, cid, state="review", due_date="2026-03-08", interval_days=1, repetitions=1)
    set_card(core, ids[3], state="review", due_date="2026-03-01", interval_days=1, repetitions=1)
    core.update_settings({"shuffle_due": True})
    assert firsts(core, d["id"], range(20)) == {ids[3]}  # older date still first
    set_card(core, ids[3], due_date="2026-03-20")
    assert firsts(core, d["id"], range(50)) == set(ids[:3])


def test_shuffle_never_touches_new_card_order(core):
    d, cards = make_deck(core, 5)
    core.update_settings({"shuffle_due": True})
    assert firsts(core, d["id"], range(20)) == {cards[0]["id"]}


# --- "I don't know" -------------------------------------------------------------------


def test_dont_know_one_step_card(api, core, ja):
    d, c = ja
    ok(api.begin_session(d["id"]))
    r = ok(api.dont_know(c["id"]))
    assert r["score"] == 0 and not r["is_correct"]
    assert r["transcript"] == "" and r["recording_id"] is None
    assert core.get_card(c["id"])["state"] == "learning"
    assert ok(api.list_recordings(c["id"])) == []
    # undo works like any review
    ok(api.undo_last_review())
    assert core.get_card(c["id"])["state"] == "new"


def test_dont_know_en_card_needs_no_ollama(api, core, ollama_fake, en):
    d, c = en
    ollama_fake.down = True
    r = ok(api.dont_know(c["id"]))
    assert r["score"] == 0
    assert "/api/chat" not in ollama_fake.paths_called()


def test_dont_know_reading_then_say_meaning(api, core, stream_factory, fake_model, ollama_fake, ja2):
    d, c = ja2
    ok(api.begin_session(d["id"]))
    heard = ok(api.dont_know(c["id"], "reading"))
    assert heard == {"stage": "heard", "step": "reading", "card_id": c["id"], "transcript": "", "skipped": True}
    assert ok(api.list_reviews(c["id"])) == []  # nothing graded yet
    ollama_fake.reply = {"score": 3, "feedback": "Correct."}
    sizes = []
    core.encoder = lambda samples: sizes.append(samples.size) or b"OggS-fake"
    r = ok(say(api, stream_factory, fake_model, c["id"], "to eat", "meaning"))
    assert [s["score"] for s in r["steps"]] == [0, 3]
    assert r["steps"][0]["transcript"] == ""
    assert r["score"] == 0 and core.get_card(c["id"])["state"] == "learning"
    assert r["recording_id"] and sizes[0] < 2 * 16000  # just the meaning clip, no gap


def test_dont_know_meaning_grades_reading_without_llm(api, core, stream_factory, fake_model, ollama_fake, ja2):
    d, c = ja2
    ok(api.begin_session(d["id"]))
    ok(say(api, stream_factory, fake_model, c["id"], "たべる"))
    chats = ollama_fake.paths_called().count("/api/chat")
    r = ok(api.dont_know(c["id"], "meaning"))
    assert [s["score"] for s in r["steps"]] == [3, 0]
    assert r["score"] == 0
    assert ollama_fake.paths_called().count("/api/chat") == chats
    assert r["recording_id"]  # the reading clip is kept
    # per-step override still works on a skipped step
    r = ok(api.override_grade(r["review_id"], True, "meaning"))
    assert [s["score"] for s in r["steps"]] == [3, 3] and r["is_correct"]


def test_dont_know_both_steps(api, core, ja2):
    d, c = ja2
    ok(api.dont_know(c["id"]))
    r = ok(api.dont_know(c["id"], "meaning"))
    assert [s["score"] for s in r["steps"]] == [0, 0] and r["recording_id"] is None
    assert ok(api.get_weak_spots(d["id"]))["steps"]["reading"]["answers"] == 1


def test_dont_know_meaning_needs_reading_first(api, ja2):
    _, c = ja2
    assert code(api.dont_know(c["id"], "meaning")) == "invalid_input"


def test_dont_know_while_recording_is_busy(api, core, stream_factory, ja):
    d, c = ja
    ok(api.begin_session(d["id"]))
    ok(api.start_recording(c["id"]))
    assert code(api.dont_know(c["id"])) == "busy"


def test_dont_know_bad_step(core, ja):
    with pytest.raises(AppError):
        core.dont_know(ja[1]["id"], "both")
