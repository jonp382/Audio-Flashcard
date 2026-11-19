import pytest

"""Session queue + daily limits, exercised through Core (plain Python, no window)."""

from datetime import timedelta

from audioflash import database, scheduler
from audioflash.reviews import Grade, record_review
from audioflash.timeutil import to_iso


def make_deck(core, n_cards=0, **patch):
    d = core.create_deck("Deck", "ja")
    if patch:
        core.update_deck(d["id"], patch)
    cards = [core.create_card(d["id"], f"f{i}", f"b{i}", f"よみ{i}") for i in range(n_cards)]
    return d, cards


def answer(core, card_id, score):
    with core.db() as conn:
        return record_review(conn, core.paths, card_id, "t", Grade(score, "fb"), None, core.clock())


def set_card(core, card_id, **fields):
    with core.db() as conn:
        conn.execute(
            f"UPDATE cards SET {', '.join(f'{k}=?' for k in fields)} WHERE id=?", (*fields.values(), card_id)
        )


def test_new_cards_in_created_at_order(core):
    d, cards = make_deck(core, 3)
    nxt = core.get_next_card(d["id"])
    assert nxt["card"]["id"] == cards[0]["id"]
    assert nxt["counts"] == {"new": 3, "learning": 0, "review": 0, "practice": 0}


def test_due_learning_first_then_review_then_new(core, clock):
    d, cards = make_deck(core, 3)
    a, b, c = (x["id"] for x in cards)
    set_card(core, b, state="review", due_date="2026-03-09", interval_days=1, repetitions=1)
    set_card(core, c, state="learning", learning_step=0, due_at=to_iso(clock.now - timedelta(minutes=1)))
    assert core.get_next_card(d["id"])["card"]["id"] == c
    set_card(core, c, due_at=to_iso(clock.now + timedelta(hours=2)))
    assert core.get_next_card(d["id"])["card"]["id"] == b
    set_card(core, b, due_date="2026-03-20")
    assert core.get_next_card(d["id"])["card"]["id"] == a


def test_oldest_review_due_first(core):
    d, cards = make_deck(core, 2)
    set_card(core, cards[0]["id"], state="review", due_date="2026-03-09")
    set_card(core, cards[1]["id"], state="review", due_date="2026-03-01")
    assert core.get_next_card(d["id"])["card"]["id"] == cards[1]["id"]


def test_learn_ahead_within_20_minutes(core, clock):
    d, cards = make_deck(core, 1, new_per_day=0)
    cid = cards[0]["id"]
    set_card(core, cid, state="learning", learning_step=1, due_at=to_iso(clock.now + timedelta(minutes=15)))
    assert core.get_next_card(d["id"])["card"]["id"] == cid
    set_card(core, cid, due_at=to_iso(clock.now + timedelta(minutes=35)))
    nxt = core.get_next_card(d["id"])
    assert nxt["card"] is None
    assert nxt["next_learning_due_at"] == to_iso(clock.now + timedelta(minutes=35))


def test_deck_list_offers_learning_cards_due_soon(core, clock):
    """Owner's bug: 13 learning cards due in 6 min, but the deck list showed nothing due
    (no Study button) because it only counted cards due this second."""
    d, cards = make_deck(core, 2, new_per_day=0)
    a, b = cards[0]["id"], cards[1]["id"]
    set_card(core, a, state="learning", learning_step=1, due_at=to_iso(clock.now + timedelta(minutes=6)))
    set_card(core, b, state="learning", learning_step=1, due_at=to_iso(clock.now + timedelta(minutes=45)))
    deck = next(x for x in core.list_decks() if x["id"] == d["id"])
    assert deck["counts"]["learning"] == 1  # the 6-minute card: Study is offered
    assert deck["learning_later"] == {"count": 1, "next_due_at": to_iso(clock.now + timedelta(minutes=45))}
    assert core.get_next_card(d["id"])["card"]["id"] == a  # and Study shows it
    opts = core.get_custom_study_options(d["id"])
    assert opts["learning_today"] == 0 and opts["studied_today"] == 0  # never studied today


def test_done_for_now_when_empty(core):
    d, _ = make_deck(core, 0)
    nxt = core.get_next_card(d["id"])
    assert nxt == {"card": None, "counts": {"new": 0, "learning": 0, "review": 0, "practice": 0}, "next_learning_due_at": None}


def test_new_limit(core):
    d, cards = make_deck(core, 3, new_per_day=2)
    assert core.get_next_card(d["id"])["counts"]["new"] == 2
    answer(core, cards[0]["id"], 3)
    answer(core, cards[1]["id"], 3)
    nxt = core.get_next_card(d["id"])
    assert nxt["counts"]["new"] == 0
    # Only learning cards (learn-ahead) remain, never the 3rd new card.
    assert nxt["card"]["id"] in (cards[0]["id"], cards[1]["id"])


def test_review_limit_and_learning_repeats_not_counted(core, clock):
    d, cards = make_deck(core, 3, reviews_per_day=1, new_per_day=0)
    for c in cards:
        set_card(core, c["id"], state="review", due_date="2026-03-10", interval_days=1, repetitions=1)
    assert core.get_next_card(d["id"])["counts"]["review"] == 1
    answer(core, cards[0]["id"], 0)  # review -> relearning (counts as 1 review)
    assert core.get_next_card(d["id"])["counts"]["review"] == 0
    clock.advance(minutes=11)
    answer(core, cards[0]["id"], 0)  # relearning repeat: not counted
    with core.db() as conn:
        deck = database.get_deck(conn, d["id"])
        assert scheduler.done_today(conn, d["id"], clock.now) == {"new": 0, "review": 1}


def test_counts_reset_at_local_midnight(core, clock):
    d, cards = make_deck(core, 2, new_per_day=1)
    answer(core, cards[0]["id"], 3)
    assert core.get_next_card(d["id"])["counts"]["new"] == 0
    clock.now = clock.now.replace(hour=23, minute=59)
    assert core.get_next_card(d["id"])["counts"]["new"] == 0
    clock.advance(minutes=2)  # next local day (TZ=UTC in tests)
    assert core.get_next_card(d["id"])["counts"]["new"] == 1


def test_undone_review_stops_counting(core):
    d, cards = make_deck(core, 2, new_per_day=1)
    answer(core, cards[0]["id"], 3)
    assert core.get_next_card(d["id"])["counts"]["new"] == 0
    core.undo_last_review()
    assert core.get_next_card(d["id"])["counts"]["new"] == 1


def test_pending_learning_card_survives_restart_and_days(core, clock):
    d, cards = make_deck(core, 2)
    answer(core, cards[0]["id"], 3)  # learning step 1, due in 10m
    clock.advance(days=3)
    # A "restart" is just a new connection; state is all in the DB.
    nxt = core.get_next_card(d["id"])
    assert nxt["card"]["id"] == cards[0]["id"]
    assert nxt["card"]["state"] == "learning"


# --- custom study ("continue studying" in the same deck) ---------------------------------


def answer_later(core, card_id, score):
    core.clock.advance(seconds=1)
    return answer(core, card_id, score)


def test_extra_new_cards_raise_today_limit_and_count_normally(core, clock):
    d, cards = make_deck(core, 5, new_per_day=2)
    for c in cards[:2]:
        answer_later(core, c["id"], 3)
    assert core.get_custom_study_options(d["id"]) == {
        "new_available": 3, "studied_today": 0, "learning_today": 2, "extra_new_today": 0
    }  # still in learning steps, due again soon
    nxt = core.start_custom_study(d["id"], 2, False)
    assert nxt["counts"]["new"] == 2
    # learn-ahead learning cards come first, then the extra new cards in file order
    seen = []
    for _ in range(10):
        n = core.get_next_card(d["id"])
        if n["card"] is None or n["card"]["state"] != "new":
            if n["card"] is None:
                break
            answer_later(core, n["card"]["id"], 3)
            continue
        seen.append(n["card"]["id"])
        answer_later(core, n["card"]["id"], 3)
    assert seen == [cards[2]["id"], cards[3]["id"]]
    assert core.get_card(cards[4]["id"])["state"] == "new"
    assert core.get_custom_study_options(d["id"])["extra_new_today"] == 2


def test_extra_new_expires_next_day(core, clock):
    d, cards = make_deck(core, 3, new_per_day=1)
    answer_later(core, cards[0]["id"], 3)
    core.start_custom_study(d["id"], 5, False)
    clock.advance(days=1)
    assert core.get_next_card(d["id"])["counts"]["new"] == 1


def test_practice_pass_shows_each_studied_card_once(core, clock):
    d, cards = make_deck(core, 2, learning_steps=[])
    for c in cards:
        answer_later(core, c["id"], 3)  # graduate -> review, due tomorrow
    assert core.get_next_card(d["id"])["card"] is None
    nxt = core.start_custom_study(d["id"], 0, True)
    assert nxt["card"]["id"] == cards[0]["id"] and nxt["card"]["practice"] is True
    assert nxt["counts"]["practice"] == 2
    before = core.get_card(cards[0]["id"])
    r = answer_later(core, cards[0]["id"], 3)
    assert r["practice"] is True
    after = core.get_card(cards[0]["id"])
    assert {k: after[k] for k in ("state", "due_date", "interval_days", "ease_factor", "repetitions", "last_review")} == {
        k: before[k] for k in ("state", "due_date", "interval_days", "ease_factor", "repetitions", "last_review")
    }
    nxt = core.get_next_card(d["id"])
    assert nxt["card"]["id"] == cards[1]["id"] and nxt["counts"]["practice"] == 1
    answer_later(core, cards[1]["id"], 3)
    assert core.get_next_card(d["id"])["card"] is None


def test_practice_miss_is_a_real_lapse(core, clock):
    d, [c] = make_deck(core, 1, learning_steps=[])
    answer_later(core, c["id"], 3)
    core.start_custom_study(d["id"], 0, True)
    r = answer_later(core, c["id"], 1)
    assert r["practice"] is True and r["state"] == "relearning"
    assert core.get_card(c["id"])["ease_factor"] < 2.5


def test_practice_answers_do_not_count_against_limits(core, clock):
    d, cards = make_deck(core, 3, new_per_day=1, learning_steps=[])
    answer_later(core, cards[0]["id"], 3)
    core.start_custom_study(d["id"], 0, True)
    answer_later(core, cards[0]["id"], 1)  # practice miss: state_before=review, but not counted
    with core.db() as conn:
        assert scheduler.done_today(conn, d["id"], clock.now) == {"new": 1, "review": 0}


def test_practice_override_and_undo(core, clock):
    d, [c] = make_deck(core, 1, learning_steps=[])
    answer_later(core, c["id"], 3)
    core.start_custom_study(d["id"], 0, True)
    before = core.get_card(c["id"])
    r = answer_later(core, c["id"], 1)
    o = core.override_grade(r["review_id"], True)  # "I was right" -> schedule restored
    assert o["practice"] and core.get_card(c["id"])["due_date"] == before["due_date"]
    core.undo_last_review()
    assert core.get_card(c["id"])["state"] == before["state"]
    assert core.get_next_card(d["id"])["card"]["practice"] is True  # back in the pass


def test_due_cards_are_not_practice(core, clock):
    d, [c] = make_deck(core, 1)
    answer_later(core, c["id"], 3)  # learning step 1, due in 10 min (within learn-ahead)
    core.start_custom_study(d["id"], 0, True)
    n = core.get_next_card(d["id"])
    assert n["card"]["id"] == c["id"] and n["card"]["practice"] is False
    assert n["counts"]["practice"] == 0


def test_custom_study_validation(core):
    from audioflash.errors import AppError

    d, _ = make_deck(core, 1)
    for args in ((0, False), (-1, True), (True, True), (5, "yes")):
        with pytest.raises(AppError) as e:
            core.start_custom_study(d["id"], *args)
        assert e.value.code == "invalid_input"
    with pytest.raises(AppError) as e:
        core.start_custom_study(999, 5, False)
    assert e.value.code == "not_found"
