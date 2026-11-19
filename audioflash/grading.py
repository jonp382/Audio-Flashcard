"""Picks the grader by deck language; common result shape (agent.md §6).

ja -> deterministic reading match (never calls ollama).
en -> LLM evaluator.
"""

from __future__ import annotations

from typing import Iterable

from . import reading_match
from .evaluator import OllamaClient
from .reviews import Grade


def grade_answer(
    language: str,
    transcript: str,
    front: str,
    back: str,
    reading: str | None,
    alt_answers: Iterable[str] | None,
    ollama: OllamaClient,
    sentence: str | None = None,
    sentence_meaning: str | None = None,
) -> Grade:
    """Raises evaluator.LLMUnavailable / GradingFailed for en decks."""
    if language == "ja":
        r = reading_match.match(transcript, front, back, reading, alt_answers)
        return Grade(score=r.score, feedback=r.feedback)
    if language == "en":
        alts = list(alt_answers or [])
        expected = back if not alts else f"{back} (also accepted: {'; '.join(alts)})"
        g = ollama.grade(front, expected, reading, language, transcript, sentence, sentence_meaning)
        return Grade(score=g.score, feedback=g.feedback)
    raise ValueError(f"unknown deck language {language!r}")


def grade_meaning(
    transcript: str,
    front: str,
    back: str,
    reading: str | None,
    ollama: OllamaClient,
    sentence: str | None = None,
    sentence_meaning: str | None = None,
) -> Grade:
    """Meaning step of a two-step card: the English meaning (`back`), graded by the LLM,
    with the example sentence as context for which sense is meant.
    Raises evaluator.LLMUnavailable / GradingFailed."""
    g = ollama.grade(front, back, reading, "en", transcript, sentence, sentence_meaning)
    return Grade(score=g.score, feedback=g.feedback)
