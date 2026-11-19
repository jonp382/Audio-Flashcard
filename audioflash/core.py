"""Application services behind the bridge. Owns shared state (mic, whisper, ollama,
download, held audio, picked import path) and runs the review flow. Raises AppError;
bridge.py turns that into the response envelope.

Every public method opens its own short-lived SQLite connection (never shared
across pywebview's worker threads).
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
import webbrowser
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterator

import numpy as np

from . import audio, config, database, importer, reading_match, reviews, scheduler, srs
from .errors import AppError
from .evaluator import GradingFailed, LLMUnavailable, OllamaClient
from .grading import grade_answer
from .model_setup import ModelDownloader
from .paths import DataPaths
from .stt import NoSpeech, WhisperNotReady, WhisperService
from .timeutil import to_iso, utc_now

log = logging.getLogger(__name__)

CONTENT_FIELDS = ("front", "back", "reading", "alt_answers")


@dataclass
class HeldAnswer:
    """Audio + transcript kept after grading_failed / llm_unavailable for retry."""

    card_id: int
    samples: np.ndarray
    transcript: str


class Core:
    def __init__(
        self,
        paths: DataPaths,
        whisper: WhisperService | None = None,
        ollama: OllamaClient | None = None,
        mic: audio.MicSession | None = None,
        player: audio.Player | None = None,
        downloader: ModelDownloader | None = None,
        clock: Callable[[], datetime] = utc_now,
        encoder: Callable[[np.ndarray], bytes] = audio.encode_ogg_opus,
        open_browser: Callable[[str], Any] = webbrowser.open,
    ):
        self.paths = paths
        self.clock = clock
        self.whisper = whisper or WhisperService(paths.whisper_model)
        self.ollama = ollama or OllamaClient()
        self.mic = mic or audio.MicSession()
        self.player = player or audio.Player()
        self.downloader = downloader or ModelDownloader(paths.models, on_done=self._after_download)
        self.encoder = encoder
        self.open_browser = open_browser
        self.file_dialog: Callable[[], str | None] | None = None  # set by __main__ once the window exists

        self._review_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._rec_card: int | None = None
        self._held: HeldAnswer | None = None
        self._picked_path: Path | None = None
        self._session_deck: int | None = None
        self._shutdown = False

        with self.db() as conn:
            s = database.get_settings(conn)
        self.ollama.configure(s["ollama_host"], s["ollama_port"], s["llm_model"])
        self.mic.set_gain(s["mic_gain"])
        self._test_clip: np.ndarray | None = None

    # --- infra ---------------------------------------------------------------------

    @contextmanager
    def db(self) -> Iterator[sqlite3.Connection]:
        conn = database.connect(self.paths.db)
        try:
            yield conn
        finally:
            conn.close()

    @contextmanager
    def review_lock(self) -> Iterator[None]:
        if not self._review_lock.acquire(blocking=False):
            raise AppError("busy", "Another review action is in progress")
        try:
            yield
        finally:
            self._review_lock.release()

    def settings(self) -> dict[str, Any]:
        with self.db() as conn:
            return database.get_settings(conn)

    def start_background(self) -> None:
        """Whisper load + ollama check. Must not block the window."""
        self.whisper.load_async(self.settings()["whisper_device"])
        threading.Thread(target=self.ollama.check, name="ollama-check", daemon=True).start()

    def _after_download(self) -> None:
        self.whisper.load(self.settings()["whisper_device"])

    # --- status & setup ----------------------------------------------------------------

    def get_status(self) -> dict[str, Any]:
        w = self.whisper.snapshot()
        state = "downloading" if self.downloader.running else w["state"]
        st = self.ollama.status
        steps: list[str] = []
        if state in ("missing", "downloading"):
            steps.append("Download the speech model (about 480MB, once).")
        if state == "error":
            steps.append(f"The speech model failed to load: {w['error']}")
        steps += self.ollama.setup_steps()
        s = self.settings()
        return {
            "whisper_state": state,
            "whisper_device": w["device"],
            "whisper_error": w["error"],
            "ollama_checked": st.checked,
            "ollama_reachable": st.reachable,
            "llm_model": self.ollama.model,
            "llm_model_present": st.model_present,
            "mic_open": self.mic.is_open,
            "mic_device": s["mic_device"],
            "mic_fallback_used": self.mic.fallback_used,
            "setup_steps": steps,
        }

    def download_whisper_model(self) -> dict[str, Any]:
        if not self.downloader.start():
            raise AppError("busy", "The download is already running")
        return self.downloader.status()

    def get_download_status(self) -> dict[str, Any]:
        return self.downloader.status()

    def open_ollama_download_page(self) -> None:
        self.open_browser(config.OLLAMA_DOWNLOAD_PAGE)  # hardcoded URL only

    def recheck_ollama(self) -> dict[str, Any]:
        self.ollama.check()
        return self.get_status()

    # --- settings & devices -------------------------------------------------------------

    def get_settings(self) -> dict[str, Any]:
        return self.settings()

    def update_settings(self, patch: dict[str, Any]) -> dict[str, Any]:
        clean = validate_settings_patch(patch)
        with self.db() as conn:
            database.set_settings(conn, clean)
            s = database.get_settings(conn)
        if {"ollama_host", "ollama_port", "llm_model"} & clean.keys():
            self.ollama.configure(s["ollama_host"], s["ollama_port"], s["llm_model"])
            threading.Thread(target=self.ollama.check, name="ollama-check", daemon=True).start()
        if "mic_gain" in clean:
            self.mic.set_gain(s["mic_gain"])
        if "whisper_device" in clean:
            self.whisper.load_async(s["whisper_device"])
        if "mic_device" in clean and self.mic.is_open and not self.mic.recording:
            self.mic.close()
            try:
                self.mic.open(s["mic_device"])
            except audio.MicError as e:
                raise AppError("mic_error", str(e)) from e
        return s

    def list_input_devices(self) -> list[dict]:
        try:
            return audio.list_input_devices()
        except Exception as e:
            raise AppError("mic_error", f"Could not list audio devices: {e}") from e

    # --- decks ------------------------------------------------------------------------

    def _deck_or_404(self, conn: sqlite3.Connection, deck_id: int) -> sqlite3.Row:
        deck = database.get_deck(conn, deck_id)
        if deck is None:
            raise AppError("not_found", "Deck not found")
        return deck

    def _card_or_404(self, conn: sqlite3.Connection, card_id: int) -> sqlite3.Row:
        card = database.get_card(conn, card_id)
        if card is None:
            raise AppError("not_found", "Card not found")
        return card

    def list_decks(self) -> list[dict]:
        now = self.clock()
        with self.db() as conn:
            out = []
            for row in conn.execute("SELECT * FROM decks ORDER BY name COLLATE NOCASE, id"):
                d = database.deck_to_dict(row)
                d["counts"] = scheduler.counts(conn, row, now)
                d["total_cards"] = conn.execute("SELECT COUNT(*) FROM cards WHERE deck_id = ?", (row["id"],)).fetchone()[0]
                out.append(d)
            return out

    def get_deck(self, deck_id: int) -> dict:
        with self.db() as conn:
            row = self._deck_or_404(conn, deck_id)
            d = database.deck_to_dict(row)
            d["counts"] = scheduler.counts(conn, row, self.clock())
            return d

    def create_deck(self, name: str, language: str) -> dict:
        name = _req_str(name, "name")
        if language not in ("ja", "en"):
            raise AppError("invalid_input", "language must be 'ja' or 'en'")
        with self.db() as conn:
            with database.transaction(conn):
                cur = conn.execute(
                    "INSERT INTO decks(name, language, new_per_day, reviews_per_day, learning_steps, relearning_steps, created_at) "
                    "VALUES (?,?,?,?,?,?,?)",
                    (
                        name,
                        language,
                        config.DEFAULT_NEW_PER_DAY,
                        config.DEFAULT_REVIEWS_PER_DAY,
                        json.dumps(config.DEFAULT_LEARNING_STEPS),
                        json.dumps(config.DEFAULT_RELEARNING_STEPS),
                        to_iso(self.clock()),
                    ),
                )
            return database.deck_to_dict(database.get_deck(conn, cur.lastrowid))

    def update_deck(self, deck_id: int, patch: dict) -> dict:
        if not isinstance(patch, dict) or not patch:
            raise AppError("invalid_input", "patch must be a non-empty object")
        sets: dict[str, Any] = {}
        for k, v in patch.items():
            if k == "name":
                sets[k] = _req_str(v, "name")
            elif k == "language":
                if v not in ("ja", "en"):
                    raise AppError("invalid_input", "language must be 'ja' or 'en'")
                sets[k] = v
            elif k in ("new_per_day", "reviews_per_day"):
                if not isinstance(v, int) or isinstance(v, bool) or v < 0 or v > 100_000:
                    raise AppError("invalid_input", f"{k} must be a whole number >= 0")
                sets[k] = v
            elif k in ("learning_steps", "relearning_steps"):
                try:
                    sets[k] = json.dumps(srs.validate_steps(v))
                except ValueError as e:
                    raise AppError("invalid_input", f"{k}: {e}") from e
            else:
                raise AppError("invalid_input", f"unknown deck field {k!r}")
        with self.db() as conn:
            self._deck_or_404(conn, deck_id)
            with database.transaction(conn):
                conn.execute(
                    f"UPDATE decks SET {', '.join(f'{k} = ?' for k in sets)} WHERE id = ?",
                    (*sets.values(), deck_id),
                )
        return self.get_deck(deck_id)

    def delete_deck(self, deck_id: int) -> None:
        with self.db() as conn:
            self._deck_or_404(conn, deck_id)
            ids = [r[0] for r in conn.execute("SELECT id FROM cards WHERE deck_id = ?", (deck_id,))]
            with database.transaction(conn):
                conn.execute("DELETE FROM decks WHERE id = ?", (deck_id,))
        reviews.delete_card_dirs(self.paths, ids)
        with self._state_lock:
            if self._held and self._held.card_id in ids:
                self._held = None

    # --- cards ------------------------------------------------------------------------

    def list_cards(self, deck_id: int) -> list[dict]:
        with self.db() as conn:
            self._deck_or_404(conn, deck_id)
            rows = conn.execute("SELECT * FROM cards WHERE deck_id = ? ORDER BY created_at, id", (deck_id,))
            return [database.card_to_dict(r) for r in rows]

    def create_card(self, deck_id: int, front: str, back: str, reading: str | None = None, alt_answers: list | None = None) -> dict:
        front, back = _req_str(front, "front"), _req_str(back, "back")
        reading = _opt_str(reading, "reading")
        alts = _alt_answers(alt_answers)
        now = to_iso(self.clock())
        with self.db() as conn:
            self._deck_or_404(conn, deck_id)
            try:
                with database.transaction(conn):
                    cur = conn.execute(
                        "INSERT INTO cards(deck_id, front, back, reading, alt_answers, created_at, updated_at) VALUES (?,?,?,?,?,?,?)",
                        (deck_id, front, back, reading, json.dumps(alts) if alts else None, now, now),
                    )
            except sqlite3.IntegrityError as e:
                raise AppError("invalid_input", "A card with this front already exists in the deck") from e
            return database.card_to_dict(database.get_card(conn, cur.lastrowid))

    def update_card(self, card_id: int, patch: dict) -> dict:
        if not isinstance(patch, dict) or not patch:
            raise AppError("invalid_input", "patch must be a non-empty object")
        sets: dict[str, Any] = {}
        for k, v in patch.items():
            if k in ("front", "back"):
                sets[k] = _req_str(v, k)
            elif k == "reading":
                sets[k] = _opt_str(v, k)
            elif k == "alt_answers":
                alts = _alt_answers(v)
                sets[k] = json.dumps(alts) if alts else None
            else:
                raise AppError("invalid_input", f"unknown card field {k!r}")
        sets["updated_at"] = to_iso(self.clock())
        with self.db() as conn:
            self._card_or_404(conn, card_id)
            try:
                with database.transaction(conn):
                    conn.execute(
                        f"UPDATE cards SET {', '.join(f'{k} = ?' for k in sets)} WHERE id = ?",
                        (*sets.values(), card_id),
                    )
            except sqlite3.IntegrityError as e:
                raise AppError("invalid_input", "A card with this front already exists in the deck") from e
            return database.card_to_dict(database.get_card(conn, card_id))

    def delete_card(self, card_id: int) -> None:
        with self.db() as conn:
            self._card_or_404(conn, card_id)
            with database.transaction(conn):
                conn.execute("DELETE FROM cards WHERE id = ?", (card_id,))
        reviews.delete_card_dirs(self.paths, [card_id])
        with self._state_lock:
            if self._held and self._held.card_id == card_id:
                self._held = None

    def get_custom_study_options(self, deck_id: int) -> dict:
        with self.db() as conn:
            deck = self._deck_or_404(conn, deck_id)
            return scheduler.custom_study_options(conn, deck, self.clock())

    def start_custom_study(self, deck_id: int, extra_new: int, review_studied: bool) -> dict:
        """'Continue studying' inside the same deck: more new cards today and/or a
        practice pass over cards already studied today. Returns the next card."""
        if isinstance(extra_new, bool) or not isinstance(extra_new, int) or not 0 <= extra_new <= 1000:
            raise AppError("invalid_input", "extra_new must be a whole number 0-1000")
        if not isinstance(review_studied, bool):
            raise AppError("invalid_input", "review_studied must be true or false")
        if extra_new == 0 and not review_studied:
            raise AppError("invalid_input", "Choose at least one option")
        now = self.clock()
        with self.db() as conn:
            deck = self._deck_or_404(conn, deck_id)
            with database.transaction(conn):
                scheduler.extend(conn, deck_id, now, extra_new, review_studied)
            return scheduler.next_card(conn, deck, now)

    def get_next_card(self, deck_id: int) -> dict:
        with self.db() as conn:
            deck = self._deck_or_404(conn, deck_id)
            return scheduler.next_card(conn, deck, self.clock())

    # --- import -----------------------------------------------------------------------

    def pick_import_file(self) -> dict | None:
        if self.file_dialog is None:
            raise AppError("internal_error", "File dialog not available")
        picked = self.file_dialog()
        if not picked:
            return None
        p = Path(picked).resolve()
        if p.suffix.lower() not in importer.ALLOWED_SUFFIXES or not p.is_file():
            raise AppError("import_error", "Pick a .csv, .tsv or .txt file")
        with self._state_lock:
            self._picked_path = p
        return {"path": str(p)}

    def _checked_path(self, path: str) -> Path:
        with self._state_lock:
            picked = self._picked_path
        if picked is None or not isinstance(path, str) or Path(path).resolve() != picked:
            raise AppError("invalid_input", "Only the file picked in the dialog can be imported")
        return picked

    def import_preview(self, path: str) -> dict:
        p = self._checked_path(path)
        try:
            return importer.preview(importer.parse_file(p))
        except importer.ImportError_ as e:
            raise AppError("import_error", str(e)) from e

    def import_cards(self, path: str, deck_id: int, mapping: dict | None) -> dict:
        p = self._checked_path(path)
        with self.db() as conn:
            self._deck_or_404(conn, deck_id)
            try:
                return importer.import_into_deck(conn, importer.parse_file(p), deck_id, mapping, to_iso(self.clock()))
            except importer.ImportError_ as e:
                raise AppError("import_error", str(e)) from e

    # --- session & recording ----------------------------------------------------------------

    def begin_session(self, deck_id: int) -> dict:
        with self.db() as conn:
            deck = self._deck_or_404(conn, deck_id)
            language = deck["language"]
        self._session_deck = deck_id
        try:
            self.mic.open(self.settings()["mic_device"])
        except audio.MicError as e:
            raise AppError("mic_error", str(e)) from e
        if language == "en":
            def warm():
                if not self.ollama.status.checked or not self.ollama.ready:
                    self.ollama.check()
                if self.ollama.ready:
                    self.ollama.warm_up()

            threading.Thread(target=warm, name="ollama-warmup", daemon=True).start()
        return self.get_next_card(deck_id)

    def end_session(self) -> None:
        self._session_deck = None
        self.mic.cancel_recording()
        with self._state_lock:
            self._rec_card = None
        self.mic.close()

    def open_mic(self) -> None:
        try:
            self.mic.open(self.settings()["mic_device"])
        except audio.MicError as e:
            raise AppError("mic_error", str(e)) from e

    def close_mic(self) -> None:
        if self.mic.recording:
            return  # never cut off an active answer; the UI cancels explicitly
        self.mic.close()

    def start_recording(self, card_id: int) -> None:
        with self.review_lock():
            if self.mic.recording:
                raise AppError("busy", "Already recording")
            with self.db() as conn:
                card = self._card_or_404(conn, card_id)
                deck = database.get_deck(conn, card["deck_id"])
            if not self.whisper.ready:
                raise AppError("whisper_not_ready", "The speech model is not ready yet")
            if deck["language"] == "en" and not self.ollama.ready:
                self.ollama.check()
                if not self.ollama.ready:
                    raise AppError("llm_unavailable", "ollama is not running or the model is missing")
            self.player.stop()
            with self._state_lock:
                self._held = None  # a new recording discards any held audio
            try:
                self.mic.start_recording(self.settings()["mic_device"])
            except audio.Busy as e:
                raise AppError("busy", "Already recording") from e
            except audio.MicError as e:
                raise AppError("mic_error", str(e)) from e
            with self._state_lock:
                self._rec_card = card_id

    def get_recording_status(self) -> dict:
        return self.mic.status()

    def cancel_recording(self) -> None:
        self.mic.cancel_recording()
        with self._state_lock:
            self._rec_card = None

    def stop_recording(self) -> dict:
        with self.review_lock():
            with self._state_lock:
                card_id, self._rec_card = self._rec_card, None
            if card_id is None or not self.mic.recording:
                raise AppError("invalid_input", "Not recording")
            samples = self.mic.stop_recording()
            return self._transcribe_grade_persist(card_id, samples)

    def retry_grading(self) -> dict:
        with self.review_lock():
            with self._state_lock:
                held = self._held
            if held is None:
                raise AppError("not_found", "No answer to retry")
            return self._grade_persist(held.card_id, held.samples, held.transcript)

    def _card_context(self, card_id: int) -> tuple[sqlite3.Row, str]:
        with self.db() as conn:
            card = self._card_or_404(conn, card_id)
            deck = database.get_deck(conn, card["deck_id"])
            return card, deck["language"]

    def _transcribe_grade_persist(self, card_id: int, samples: np.ndarray) -> dict:
        card, language = self._card_context(card_id)
        alts = json.loads(card["alt_answers"]) if card["alt_answers"] else []
        expected = [card["front"], card["back"], card["reading"] or "", *alts]
        expected += reading_match.split_answers(card["back"]) + reading_match.split_answers(card["reading"])
        try:
            transcript = self.whisper.transcribe_pcm(samples, language, expected)
        except NoSpeech as e:
            raise AppError("no_speech", "Didn't catch that, try again.") from e
        except WhisperNotReady as e:
            raise AppError("whisper_not_ready", "The speech model is not ready yet") from e
        return self._grade_persist(card_id, samples, transcript)

    def _grade_persist(self, card_id: int, samples: np.ndarray, transcript: str) -> dict:
        card, language = self._card_context(card_id)
        alts = json.loads(card["alt_answers"]) if card["alt_answers"] else []
        try:
            grade = grade_answer(language, transcript, card["front"], card["back"], card["reading"], alts, self.ollama)
        except LLMUnavailable as e:
            self._hold(card_id, samples, transcript)
            raise AppError("llm_unavailable", "Could not reach ollama. Start it and press retry.") from e
        except GradingFailed as e:
            self._hold(card_id, samples, transcript)
            raise AppError("grading_failed", "The grader returned an unusable answer. Press retry.") from e
        try:
            ogg = self.encoder(samples) if samples.size else None
        except Exception:
            log.exception("encoding recording failed; saving review without audio")
            ogg = None
        with self.db() as conn:
            try:
                result = reviews.record_review(conn, self.paths, card_id, transcript, grade, ogg, self.clock())
            except reviews.ReviewError as e:
                raise AppError(e.code, e.message) from e
        with self._state_lock:
            self._held = None
        return result

    def _hold(self, card_id: int, samples: np.ndarray, transcript: str) -> None:
        with self._state_lock:
            self._held = HeldAnswer(card_id, samples, transcript)

    def override_grade(self, review_id: int, correct: bool) -> dict:
        with self.review_lock(), self.db() as conn:
            try:
                return reviews.override_grade(conn, review_id, bool(correct))
            except reviews.ReviewError as e:
                raise AppError(e.code, e.message) from e

    def undo_last_review(self) -> dict:
        with self.review_lock(), self.db() as conn:
            try:
                return reviews.undo_last_review(conn, self.paths)
            except reviews.ReviewError as e:
                raise AppError(e.code, e.message) from e

    def get_card(self, card_id: int) -> dict:
        with self.db() as conn:
            return database.card_to_dict(self._card_or_404(conn, card_id))

    # --- mic test (settings page) -----------------------------------------------------------

    def mic_test_start(self) -> None:
        """Record a short test clip (not tied to any card, never saved to disk)."""
        with self.review_lock():
            if self.mic.recording:
                raise AppError("busy", "The microphone is already recording")
            with self._state_lock:
                self._test_clip = None
            try:
                self.mic.start_recording(self.settings()["mic_device"])
            except audio.MicError as e:
                raise AppError("mic_error", str(e)) from e

    def mic_test_stop(self, language: str = "ja") -> dict:
        """Stop the test clip; return level stats and what Whisper heard."""
        if language not in ("ja", "en"):
            raise AppError("invalid_input", "language must be 'ja' or 'en'")
        with self.review_lock():
            with self._state_lock:
                if self._rec_card is not None:
                    raise AppError("busy", "A review recording is in progress")
            if not self.mic.recording:
                raise AppError("invalid_input", "Not recording")
            x = self.mic.stop_recording()
            with self._state_lock:
                self._test_clip = x
            rms = float(np.sqrt(np.mean(np.square(x)))) if x.size else 0.0
            peak = float(np.max(np.abs(x))) if x.size else 0.0
            clipped = float(np.mean(np.abs(x) >= 0.99)) if x.size else 0.0
            out: dict[str, Any] = {
                "seconds": round(x.size / config.SAMPLE_RATE, 2),
                "level": round(audio.level_from_rms(rms), 3),
                "peak": round(peak, 3),
                "gate_level": round(audio.level_from_rms(config.ENERGY_GATE_RMS), 3),
                "too_quiet": rms < config.ENERGY_GATE_RMS,
                "clipping": clipped > 0.001,
                "transcript": None,
                "transcript_note": None,
            }
            if not self.whisper.ready:
                out["transcript_note"] = "Speech model not loaded yet"
            else:
                try:
                    out["transcript"] = self.whisper.transcribe_pcm(x, language)  # type: ignore[arg-type]
                except NoSpeech:
                    out["transcript_note"] = "Too quiet: this would be ignored as silence" if out["too_quiet"] else "No speech detected"
                except WhisperNotReady:
                    out["transcript_note"] = "Speech model not loaded yet"
            return out

    def mic_test_cancel(self) -> None:
        with self._state_lock:
            if self._rec_card is not None:
                return  # never cancel a review answer from the settings page
        self.mic.cancel_recording()

    def mic_test_play(self) -> None:
        with self._state_lock:
            clip = self._test_clip
        if clip is None or clip.size == 0:
            raise AppError("not_found", "Record a test clip first")
        if self.mic.recording:
            raise AppError("busy", "Can't play while recording")
        try:
            self.player.play_array(clip, config.SAMPLE_RATE)
        except Exception as e:
            log.warning("test playback failed", exc_info=True)
            raise AppError("mic_error", f"Could not play the test clip: {e}") from e

    # --- history & playback ----------------------------------------------------------------

    def list_reviews(self, card_id: int) -> list[dict]:
        with self.db() as conn:
            self._card_or_404(conn, card_id)
            rows = conn.execute(
                "SELECT id, reviewed_at, transcript, score, score_original, overridden, is_correct, feedback, "
                "state_before, state_after, interval_after, ease_after, recording_id FROM reviews "
                "WHERE card_id = ? ORDER BY id DESC",
                (card_id,),
            )
            return [{**dict(r), "is_correct": bool(r["is_correct"]), "overridden": bool(r["overridden"])} for r in rows]

    def list_recordings(self, card_id: int) -> list[dict]:
        with self.db() as conn:
            self._card_or_404(conn, card_id)
            rows = conn.execute(
                "SELECT id, created_at FROM recordings WHERE card_id = ? ORDER BY id DESC LIMIT ?",
                (card_id, config.RECORDINGS_PER_CARD),
            )
            return [dict(r) for r in rows]

    def play_recording(self, recording_id: int) -> None:
        if self.mic.recording:
            raise AppError("busy", "Can't play while recording")
        with self.db() as conn:
            p = reviews.recording_path(conn, self.paths, recording_id)
        if p is None or not p.is_file():
            raise AppError("not_found", "Recording not found")
        try:
            self.player.play(p)
        except Exception as e:
            log.warning("playback failed", exc_info=True)
            raise AppError("mic_error", f"Could not play the recording: {e}") from e

    def stop_playback(self) -> None:
        self.player.stop()

    # --- lifecycle ------------------------------------------------------------------------

    def shutdown(self) -> None:
        if self._shutdown:
            return
        self._shutdown = True
        try:
            self.mic.shutdown()
        except Exception:
            log.warning("mic shutdown failed", exc_info=True)
        self.player.stop()
        self.downloader.stop()


# --- validation helpers ---------------------------------------------------------------


def _req_str(v: Any, name: str) -> str:
    if not isinstance(v, str) or not v.strip():
        raise AppError("invalid_input", f"{name} is required")
    return v.strip()


def _opt_str(v: Any, name: str) -> str | None:
    if v is None:
        return None
    if not isinstance(v, str):
        raise AppError("invalid_input", f"{name} must be text")
    return v.strip() or None


def _alt_answers(v: Any) -> list[str]:
    if v is None:
        return []
    if not isinstance(v, list) or not all(isinstance(x, str) for x in v):
        raise AppError("invalid_input", "alt_answers must be a list of strings")
    out = []
    for x in v:
        x = x.strip()
        if x and x not in out:
            out.append(x)
    return out


def validate_settings_patch(patch: Any) -> dict[str, Any]:
    if not isinstance(patch, dict) or not patch:
        raise AppError("invalid_input", "patch must be a non-empty object")
    out: dict[str, Any] = {}
    for k, v in patch.items():
        if k not in config.DEFAULT_SETTINGS:
            raise AppError("invalid_input", f"unknown setting {k!r}")
        if k in ("ollama_host", "llm_model"):
            out[k] = _req_str(v, k)
        elif k == "ollama_port":
            if not isinstance(v, int) or isinstance(v, bool) or not 1 <= v <= 65535:
                raise AppError("invalid_input", "ollama_port must be 1-65535")
            out[k] = v
        elif k == "whisper_device":
            if v not in ("cpu", "cuda"):
                raise AppError("invalid_input", "whisper_device must be 'cpu' or 'cuda'")
            out[k] = v
        elif k == "mic_device":
            out[k] = _opt_str(v, k)
        elif k == "mic_gain":
            if isinstance(v, bool) or not isinstance(v, (int, float)) or not config.MIC_GAIN_MIN <= v <= config.MIC_GAIN_MAX:
                raise AppError("invalid_input", f"mic_gain must be {config.MIC_GAIN_MIN}-{config.MIC_GAIN_MAX}")
            out[k] = float(v)
        elif k == "record_mode":
            if v not in ("toggle", "hold"):
                raise AppError("invalid_input", "record_mode must be 'toggle' or 'hold'")
            out[k] = v
    return out
