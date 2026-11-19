"""The ``Api`` object exposed to JS as ``window.pywebview.api`` (agent.md §3, §9).

Thin layer: validate argument types, call Core, wrap the result in the envelope
``{"ok": true, "data": ...}`` / ``{"ok": false, "error": {"code", "message"}}``.
No exception ever escapes to JS.

pywebview exposes every public attribute of this object (recursively), so all state
lives on underscore-prefixed attributes, which it skips.
"""

from __future__ import annotations

import functools
import inspect
import logging
from typing import Any, Callable

from .core import Core
from .errors import AppError

log = logging.getLogger(__name__)


def ok(data: Any = None) -> dict:
    return {"ok": True, "data": data}


def err(code: str, message: str) -> dict:
    return {"ok": False, "error": {"code": code, "message": message}}


def _envelope(fn: Callable) -> Callable:
    sig = inspect.signature(fn)

    @functools.wraps(fn)
    def wrapper(self, *args, **kwargs):
        try:
            sig.bind(self, *args, **kwargs)
        except TypeError:
            return err("invalid_input", f"Invalid arguments for {fn.__name__}")
        try:
            return ok(fn(self, *args, **kwargs))
        except AppError as e:
            return err(e.code, e.message)
        except Exception:
            log.exception("internal error in %s", fn.__name__)
            return err("internal_error", "Something went wrong. Details are in the log file.")

    return wrapper


def _id(v: Any, name: str = "id") -> int:
    if isinstance(v, bool) or not isinstance(v, (int, float)) or int(v) != v or v < 1:
        raise AppError("invalid_input", f"{name} must be a positive integer")
    return int(v)


class Api:
    def __init__(self, core: Core):
        self._core = core

    # --- status & setup ---
    @_envelope
    def get_status(self):
        return self._core.get_status()

    @_envelope
    def download_whisper_model(self):
        return self._core.download_whisper_model()

    @_envelope
    def get_download_status(self):
        return self._core.get_download_status()

    @_envelope
    def open_ollama_download_page(self):
        return self._core.open_ollama_download_page()

    @_envelope
    def recheck_ollama(self):
        return self._core.recheck_ollama()

    # --- settings & devices ---
    @_envelope
    def get_settings(self):
        return self._core.get_settings()

    @_envelope
    def update_settings(self, patch):
        return self._core.update_settings(patch)

    @_envelope
    def list_input_devices(self):
        return self._core.list_input_devices()

    # --- decks & cards ---
    @_envelope
    def list_decks(self):
        return self._core.list_decks()

    @_envelope
    def get_deck(self, deck_id):
        return self._core.get_deck(_id(deck_id, "deck_id"))

    @_envelope
    def create_deck(self, name, language):
        return self._core.create_deck(name, language)

    @_envelope
    def update_deck(self, deck_id, patch):
        return self._core.update_deck(_id(deck_id, "deck_id"), patch)

    @_envelope
    def delete_deck(self, deck_id):
        return self._core.delete_deck(_id(deck_id, "deck_id"))

    @_envelope
    def list_cards(self, deck_id):
        return self._core.list_cards(_id(deck_id, "deck_id"))

    @_envelope
    def get_card(self, card_id):
        return self._core.get_card(_id(card_id, "card_id"))

    @_envelope
    def create_card(
        self, deck_id, front, back, reading=None, alt_answers=None, sentence=None, sentence_meaning=None, notes=None
    ):
        return self._core.create_card(
            _id(deck_id, "deck_id"), front, back, reading, alt_answers, sentence, sentence_meaning, notes
        )

    @_envelope
    def update_card(self, card_id, patch):
        return self._core.update_card(_id(card_id, "card_id"), patch)

    @_envelope
    def delete_card(self, card_id):
        return self._core.delete_card(_id(card_id, "card_id"))

    @_envelope
    def get_custom_study_options(self, deck_id):
        return self._core.get_custom_study_options(_id(deck_id, "deck_id"))

    @_envelope
    def start_custom_study(self, deck_id, extra_new, review_studied):
        return self._core.start_custom_study(_id(deck_id, "deck_id"), extra_new, review_studied)

    @_envelope
    def get_next_card(self, deck_id):
        return self._core.get_next_card(_id(deck_id, "deck_id"))

    # --- import ---
    @_envelope
    def pick_import_file(self):
        return self._core.pick_import_file()

    @_envelope
    def import_preview(self, path):
        return self._core.import_preview(path)

    @_envelope
    def import_cards(self, path, deck_id, mapping=None):
        return self._core.import_cards(path, _id(deck_id, "deck_id"), mapping)

    # --- recording & review ---
    @_envelope
    def begin_session(self, deck_id):
        return self._core.begin_session(_id(deck_id, "deck_id"))

    @_envelope
    def end_session(self):
        return self._core.end_session()

    @_envelope
    def open_mic(self):
        return self._core.open_mic()

    @_envelope
    def close_mic(self):
        return self._core.close_mic()

    @_envelope
    def start_recording(self, card_id, step=None):
        return self._core.start_recording(_id(card_id, "card_id"), step)

    @_envelope
    def get_recording_status(self):
        return self._core.get_recording_status()

    @_envelope
    def cancel_recording(self):
        return self._core.cancel_recording()

    @_envelope
    def stop_recording(self):
        return self._core.stop_recording()

    @_envelope
    def retry_grading(self):
        return self._core.retry_grading()

    @_envelope
    def override_grade(self, review_id, correct, step=None):
        if not isinstance(correct, bool):
            raise AppError("invalid_input", "correct must be true or false")
        return self._core.override_grade(_id(review_id, "review_id"), correct, step)

    @_envelope
    def undo_last_review(self):
        return self._core.undo_last_review()

    # --- mic test ---
    @_envelope
    def mic_test_start(self):
        return self._core.mic_test_start()

    @_envelope
    def mic_test_stop(self, language="ja"):
        return self._core.mic_test_stop(language)

    @_envelope
    def mic_test_cancel(self):
        return self._core.mic_test_cancel()

    @_envelope
    def mic_test_play(self):
        return self._core.mic_test_play()

    # --- history & playback ---
    @_envelope
    def list_reviews(self, card_id):
        return self._core.list_reviews(_id(card_id, "card_id"))

    @_envelope
    def get_weak_spots(self, deck_id, days=30):
        return self._core.get_weak_spots(_id(deck_id, "deck_id"), days)

    @_envelope
    def list_recordings(self, card_id):
        return self._core.list_recordings(_id(card_id, "card_id"))

    @_envelope
    def play_recording(self, recording_id):
        return self._core.play_recording(_id(recording_id, "recording_id"))

    @_envelope
    def stop_playback(self):
        return self._core.stop_playback()
