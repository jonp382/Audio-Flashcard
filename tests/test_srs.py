from datetime import date, datetime, timedelta, timezone

import pytest

from audioflash.srs import CardState, DeckSteps, apply_review, format_steps, parse_steps, update_ease
from audioflash.timeutil import to_iso

NOW = datetime(2026, 3, 10, 9, 0, tzinfo=timezone.utc)
TODAY = date(2026, 3, 10)
STEPS = DeckSteps([1, 10], [10])


def due_in(minutes):
    return to_iso(NOW + timedelta(minutes=minutes))


def review(cs, score, deck=STEPS):
    return apply_review(cs, score, deck, NOW, TODAY)


def test_new_good_goes_to_step_1_not_graduation():
    cs = review(CardState(), 3)
    assert (cs.state, cs.learning_step, cs.due_at) == ("learning", 1, due_in(10))
    assert cs.ease_factor == 2.5
    assert cs.last_review == to_iso(NOW)


def test_learning_full_path_to_review():
    cs = review(CardState(), 3)
    cs = review(cs, 3)
    assert cs.state == "review"
    assert (cs.repetitions, cs.interval_days, cs.due_date, cs.due_at) == (1, 1, "2026-03-11", None)
    assert cs.ease_factor == 2.5  # unchanged in learning


@pytest.mark.parametrize("score", [0, 1])
def test_again_resets_to_step_0(score):
    cs = review(review(CardState(), 3), score)
    assert (cs.state, cs.learning_step, cs.due_at) == ("learning", 0, due_in(1))


def test_hard_repeats_current_step():
    cs = review(review(CardState(), 3), 2)
    assert (cs.learning_step, cs.due_at) == (1, due_in(10))
    cs = review(CardState(), 2)
    assert (cs.state, cs.learning_step, cs.due_at) == ("learning", 0, due_in(1))


def test_review_pass_intervals_and_ease():
    cs = CardState(state="review", repetitions=1, interval_days=1, due_date="2026-03-10")
    cs = review(cs, 3)  # q=5
    assert cs.repetitions == 2 and cs.interval_days == 6
    assert cs.ease_factor == pytest.approx(2.6)
    assert cs.due_date == "2026-03-16"
    cs = review(cs, 2)  # q=3 -> EF 2.6 - 0.14 = 2.46 ; interval round(6*2.46)=15
    assert cs.ease_factor == pytest.approx(2.46)
    assert cs.interval_days == 15
    assert cs.repetitions == 3


def test_interval_minimum_prev_plus_one():
    cs = CardState(state="review", repetitions=5, interval_days=1, ease_factor=1.3)
    cs = review(cs, 2)
    assert cs.interval_days == 2


def test_ease_floor():
    assert update_ease(1.3, 0) == 1.3
    assert update_ease(2.5, 0) == pytest.approx(1.7)


def test_lapse_goes_to_relearning_then_back_to_review():
    cs = CardState(state="review", repetitions=4, interval_days=20, ease_factor=2.5)
    cs = review(cs, 1)  # q=1: EF 2.5 + 0.1 - 4*(0.08+0.08) = 1.96
    assert cs.state == "relearning" and cs.learning_step == 0
    assert cs.repetitions == 0
    assert cs.ease_factor == pytest.approx(1.96)
    assert cs.due_at == due_in(10)
    ef = cs.ease_factor
    cs2 = review(cs, 1)  # again in relearning: EF unchanged
    assert cs2.ease_factor == ef and cs2.learning_step == 0
    cs = review(cs, 3)
    assert cs.state == "review" and cs.interval_days == 1 and cs.due_date == "2026-03-11"
    assert cs.ease_factor == ef


def test_empty_learning_steps_graduate_on_first_pass():
    deck = DeckSteps([], [])
    cs = review(CardState(), 2, deck)
    assert cs.state == "review" and cs.interval_days == 1
    cs = review(CardState(), 1, deck)
    assert cs.state == "learning"


def test_empty_relearning_steps_lapse_straight_to_review():
    deck = DeckSteps([1], [])
    cs = review(CardState(state="review", repetitions=3, interval_days=10), 0, deck)
    assert cs.state == "review" and cs.interval_days == 1 and cs.due_date == "2026-03-11"


def test_invalid_score():
    with pytest.raises(ValueError):
        review(CardState(), 4)


@pytest.mark.parametrize(
    "text,expected",
    [("1m 10m", [1, 10]), ("1h", [60]), ("1d", [1440]), ("10", [10]), ("1m, 1h 2d", [1, 60, 2880]), ("", [])],
)
def test_parse_steps(text, expected):
    assert parse_steps(text) == expected


@pytest.mark.parametrize("text", ["abc", "1x", "-1m", "0m", "1 m"])
def test_parse_steps_invalid(text):
    with pytest.raises(ValueError):
        parse_steps(text)


def test_format_steps():
    assert format_steps([1, 10, 60, 1440]) == "1m 10m 1h 1d"
