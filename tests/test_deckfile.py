"""Deck files (.afdeck): export a deck with its progress, restore it as a new deck."""

import json

import pytest

from audioflash import deckfile
from audioflash.errors import AppError

from .conftest import FakeSegment, tone

KAISHI = (
    "#separator:tab\n#html:true\n#tags column:8\n"
    "私\tわたし\tI (polite, general)\t[sound:a.mp3]\t<b>私</b>はアンです。\tI am Ann.\tCan also be read わたくし.\tN5\n"
    "食べる\tたべる\tto eat\t[sound:b.mp3]\t<b>食べる</b>。\tEat.\t\tN5\n"
    "方\tほう\tdirection, side\t\t彼は私の<b>方</b>を見ました。\tHe looked my way.\t\tN5\n"
)
MAP = {"front": 0, "back": 2, "reading": 1, "sentence": 4, "sentence_meaning": 5, "notes": 6}


@pytest.fixture
def deck_with_progress(core, tmp_path, stream_factory, fake_model, ollama_fake, clock):
    d = core.create_deck("Kaishi", "ja")
    core.update_deck(d["id"], {"new_per_day": 15, "learning_steps": "1m 10m 1h"})
    f = tmp_path / "k.txt"
    f.write_text(KAISHI, encoding="utf-8")
    core.file_dialog = lambda: str(f)
    core.pick_import_file()
    core.import_cards(str(f), d["id"], MAP)
    cards = {c["front"]: c for c in core.list_cards(d["id"])}
    core.begin_session(d["id"])

    def say(card_id, text, step=None):
        fake_model.next_segments = [FakeSegment(text)]
        core.start_recording(card_id, step)
        stream_factory.current.feed(tone(1))
        return core.stop_recording()

    say(cards["私"]["id"], "わたし")
    r = say(cards["私"]["id"], "I", "meaning")
    core.override_grade(r["review_id"], True, "meaning")
    clock.advance(minutes=2)
    say(cards["食べる"]["id"], "のむ")
    ollama_fake.reply = {"feedback": "Wrong.", "score": 1}
    say(cards["食べる"]["id"], "to drink", "meaning")
    core.start_custom_study(d["id"], 5, False)
    core.end_session()
    return d


def export(core, tmp_path, deck_id, name="out"):
    target = tmp_path / name
    core.deck_save_dialog = lambda suggested: str(target)
    return core.export_deck(deck_id)


def restore(core, path):
    core.deck_open_dialog = lambda: str(path)
    return core.import_deck_file()


def snapshot(core, deck_id):
    """Everything that must survive a round trip, without ids."""
    with core.db() as conn:
        cards = [
            {k: c[k] for k in c.keys() if k not in ("id", "deck_id")}
            for c in conn.execute("SELECT * FROM cards WHERE deck_id = ? ORDER BY created_at, front", (deck_id,))
        ]
        ids = {c["id"]: c["front"] for c in conn.execute("SELECT id, front FROM cards WHERE deck_id = ?", (deck_id,))}
        revs = [
            {**{k: r[k] for k in r.keys() if k not in ("id", "card_id", "deck_id", "recording_id")}, "card": ids[r["card_id"]]}
            for r in conn.execute("SELECT * FROM reviews WHERE deck_id = ? ORDER BY reviewed_at, id", (deck_id,))
        ]
        ext = [tuple(e)[1:] for e in conn.execute("SELECT * FROM study_extensions WHERE deck_id = ?", (deck_id,))]
        deck = {k: v for k, v in dict(conn.execute("SELECT * FROM decks WHERE id = ?", (deck_id,)).fetchone()).items() if k not in ("id", "name")}
    return cards, revs, ext, deck


def test_round_trip_keeps_everything(core, tmp_path, deck_with_progress):
    d = deck_with_progress
    res = export(core, tmp_path, d["id"])
    assert res["path"].endswith("out.afdeck") and res["cards"] == 3
    data = json.loads((tmp_path / "out.afdeck").read_text(encoding="utf-8"))
    assert data["format"] == "audio-flashcard-deck" and data["version"] == 1
    watashi = next(c for c in data["cards"] if c["front"] == "私")
    assert watashi["source_fields"][3] == "[sound:a.mp3]"  # every Anki column kept
    assert data["deck"]["source_info"]["meta"]["tags column"] == "8"
    assert watashi["reviews"][0]["steps"][1]["overridden"] is True

    r = restore(core, tmp_path / "out.afdeck")
    assert (r["cards"], r["reviews"]) == (3, 2)
    assert r["deck"]["name"].startswith("Kaishi (restored ")  # original still exists
    assert snapshot(core, r["deck"]["id"]) == snapshot(core, d["id"])


def test_restored_deck_is_usable(core, tmp_path, deck_with_progress):
    export(core, tmp_path, deck_with_progress["id"])
    core.delete_deck(deck_with_progress["id"])  # the wipe this is for
    r = restore(core, tmp_path / "out.afdeck")
    assert r["deck"]["name"] == "Kaishi"  # no clash once the old deck is gone
    nxt = core.get_next_card(r["deck"]["id"])
    assert nxt["card"] is not None
    weak = core.get_weak_spots(r["deck"]["id"])
    assert weak["steps"]["reading"]["answers"] == 2
    assert core.undo_last_review()["deck_id"] == r["deck"]["id"]  # history snapshots work


def test_cancelled_dialogs(core, deck_with_progress):
    core.deck_save_dialog = lambda s: None
    assert core.export_deck(deck_with_progress["id"]) is None
    core.deck_open_dialog = lambda: None
    assert core.import_deck_file() is None


def test_export_suggests_safe_name(core, tmp_path):
    d = core.create_deck('JLPT: N5/verbs?', "ja")
    seen = []
    core.deck_save_dialog = lambda s: seen.append(s) or str(tmp_path / "x.afdeck")
    core.export_deck(d["id"])
    assert seen == ["JLPT_ N5_verbs_.afdeck"]


def good(core, tmp_path, deck_id):
    export(core, tmp_path, deck_id)
    return json.loads((tmp_path / "out.afdeck").read_text(encoding="utf-8"))


@pytest.mark.parametrize(
    "mutate, message",
    [
        (lambda d: d.update(format="something-else"), "not an Audio Flashcard deck file"),
        (lambda d: d.update(version=99), "newer version"),
        (lambda d: d["cards"][0].update(state="mastered"), "damaged"),
        (lambda d: d["cards"][0].update(due_at="tomorrow-ish"), "damaged"),
        (lambda d: d["cards"][0].update(ease_factor="2.5"), "damaged"),
        (lambda d: d["cards"][0]["reviews"][0].update(score=7), "damaged"),
        (lambda d: d["deck"].update(language="fr"), "damaged"),
        (lambda d: d["deck"].update(learning_steps=[0]), "damaged"),
        (lambda d: d["cards"].append(dict(d["cards"][0], reviews=[])), "same card twice"),
    ],
)
def test_bad_files_are_rejected_whole(core, tmp_path, deck_with_progress, mutate, message):
    data = good(core, tmp_path, deck_with_progress["id"])
    mutate(data)
    bad = tmp_path / "bad.afdeck"
    bad.write_text(json.dumps(data), encoding="utf-8")
    n_decks = len(core.list_decks())
    with pytest.raises(AppError) as e:
        restore(core, bad)
    assert e.value.code == "import_error" and message in e.value.message
    assert len(core.list_decks()) == n_decks  # nothing half-imported


def test_not_json(core, tmp_path):
    bad = tmp_path / "bad.afdeck"
    bad.write_bytes(b"\xff\xfe not json")
    with pytest.raises(AppError) as e:
        restore(core, bad)
    assert "not an Audio Flashcard deck file" in e.value.message


def test_parse_size_limit():
    with pytest.raises(deckfile.DeckFileError):
        deckfile.parse(b" " * (deckfile.MAX_BYTES + 1))
