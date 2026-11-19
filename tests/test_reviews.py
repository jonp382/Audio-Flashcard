import json

import pytest

from audioflash import config
from audioflash.errors import AppError
from audioflash.reviews import Grade, record_review


def setup(core, n=1, language="ja"):
    d = core.create_deck("D", language)
    cards = [core.create_card(d["id"], f"話{i}", "to speak", "はなす") for i in range(n)]
    return d, cards


def answer(core, card_id, score, ogg=b"OggS"):
    with core.db() as conn:
        return record_review(conn, core.paths, card_id, "はなす", Grade(score, "fb"), ogg, core.clock())


def card_state(core, cid):
    c = core.get_card(cid)
    return {k: c[k] for k in ("state", "learning_step", "ease_factor", "interval_days", "repetitions", "due_date", "due_at", "last_review")}


def test_review_snapshot_and_result_shape(core):
    _, [c] = setup(core)
    before = card_state(core, c["id"])
    r = answer(core, c["id"], 3)
    assert r["score"] == 3 and r["is_correct"] is True
    assert r["expected"] == {"front": "話0", "back": "to speak", "reading": "はなす", "alt_answers": []}
    assert r["state"] == "learning"
    assert r["recording_id"] is not None
    with core.db() as conn:
        row = conn.execute("SELECT * FROM reviews WHERE id=?", (r["review_id"],)).fetchone()
    assert row["state_before"] == before["state"] and row["ef_before"] == before["ease_factor"]
    assert row["state_after"] == "learning"
    json.dumps(r)


def test_recording_retention_10_per_card(core):
    _, [c] = setup(core)
    for _ in range(12):
        answer(core, c["id"], 1)
    recs = core.list_recordings(c["id"])
    assert len(recs) == config.RECORDINGS_PER_CARD
    files = list(core.paths.card_recordings(c["id"]).glob("*.ogg"))
    assert len(files) == config.RECORDINGS_PER_CARD


def test_override_right_and_wrong(core):
    _, [c] = setup(core)
    r = answer(core, c["id"], 1)  # again -> step 0
    assert r["state"] == "learning"
    o = core.override_grade(r["review_id"], True)
    assert o["score"] == 3 and o["score_original"] == 1 and o["overridden"] is True
    assert core.get_card(c["id"])["learning_step"] == 1
    o = core.override_grade(r["review_id"], False)
    assert o["score"] == 1 and o["score_original"] == 1  # original automatic score kept
    assert core.get_card(c["id"])["learning_step"] == 0


def test_override_only_latest_review(core):
    _, [c] = setup(core)
    r1 = answer(core, c["id"], 3)
    answer(core, c["id"], 3)
    with pytest.raises(AppError) as e:
        core.override_grade(r1["review_id"], False)
    assert e.value.code == "invalid_input"


def test_override_not_found(core):
    with pytest.raises(AppError) as e:
        core.override_grade(999, True)
    assert e.value.code == "not_found"


def test_undo_restores_exact_state_and_deletes_recording(core):
    _, [c] = setup(core)
    s0 = card_state(core, c["id"])
    r1 = answer(core, c["id"], 3)
    s1 = card_state(core, c["id"])
    core.clock.advance(minutes=11)
    r2 = answer(core, c["id"], 3)
    f2 = core.paths.recordings / f"{c['id']}/{r2['recording_id']}.ogg"
    assert f2.exists()
    assert core.undo_last_review() == {"card_id": c["id"], "deck_id": r2["deck_id"]}
    assert card_state(core, c["id"]) == s1
    assert not f2.exists()
    core.undo_last_review()  # repeatable
    assert card_state(core, c["id"]) == s0
    assert core.list_reviews(c["id"]) == []
    with pytest.raises(AppError) as e:
        core.undo_last_review()
    assert e.value.code == "not_found"


def test_undo_across_cards_newest_first(core):
    _, [a, b] = setup(core, 2)
    answer(core, a["id"], 3)
    answer(core, b["id"], 3)
    assert core.undo_last_review()["card_id"] == b["id"]
    assert core.undo_last_review()["card_id"] == a["id"]


def test_undo_refused_after_card_edit(core):
    _, [c] = setup(core)
    answer(core, c["id"], 3)
    core.clock.advance(seconds=5)
    core.update_card(c["id"], {"back": "to talk"})
    with pytest.raises(AppError) as e:
        core.undo_last_review()
    assert e.value.code == "invalid_input"


def test_card_edit_does_not_touch_schedule(core):
    _, [c] = setup(core)
    answer(core, c["id"], 3)
    s = card_state(core, c["id"])
    core.update_card(c["id"], {"alt_answers": ["しゃべる"]})
    assert card_state(core, c["id"]) == s
    assert core.get_card(c["id"])["alt_answers"] == ["しゃべる"]


def test_delete_card_removes_rows_and_files(core):
    _, [c] = setup(core)
    answer(core, c["id"], 3)
    d = core.paths.card_recordings(c["id"])
    assert d.exists()
    core.delete_card(c["id"])
    assert not d.exists()
    with core.db() as conn:
        assert conn.execute("SELECT COUNT(*) FROM reviews").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM recordings").fetchone()[0] == 0


def test_delete_deck_removes_cards_and_files(core):
    deck, cards = setup(core, 2)
    for c in cards:
        answer(core, c["id"], 3)
    dirs = [core.paths.card_recordings(c["id"]) for c in cards]
    assert all(d.exists() for d in dirs)
    core.delete_deck(deck["id"])
    assert not any(d.exists() for d in dirs)
    with core.db() as conn:
        assert conn.execute("SELECT COUNT(*) FROM cards").fetchone()[0] == 0
    with pytest.raises(AppError):
        core.list_cards(deck["id"])


def test_duplicate_front_rejected(core):
    d, [c] = setup(core)
    with pytest.raises(AppError) as e:
        core.create_card(d["id"], "話0", "x")
    assert e.value.code == "invalid_input"
