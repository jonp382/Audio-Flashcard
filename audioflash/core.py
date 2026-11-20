"""Application services behind the bridge. Owns shared state (mic, whisper, ollama,
download, held audio, picked import path) and runs the review flow. Raises AppError;
bridge.py turns that into the response envelope.

Every public method opens its own short-lived SQLite connection (never shared
across pywebview's worker threads).
"""

from __future__ import annotations

import json
import logging
import random
import re
import sqlite3
import threading
import webbrowser
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Iterator

import numpy as np

from . import audio, config, database, deckfile, importer, reading_match, reviews, scheduler, srs
from .errors import AppError
from .evaluator import CardContext, GradingFailed, LLMUnavailable, OllamaClient
from .grading import grade_answer, grade_meaning
from .model_setup import ModelDownloader
from .paths import DataPaths
from .stt import NoSpeech, WhisperNotReady, WhisperService
from .timeutil import to_iso, utc_now

log = logging.getLogger(__name__)

CONTENT_FIELDS = ("front", "back", "reading", "alt_answers", "sentence", "sentence_meaning", "notes")


@dataclass
class HeardReading:
    """Reading step of a two-step card: transcribed but not graded until the meaning
    step is recorded. Re-recording the reading replaces it."""

    card_id: int
    samples: np.ndarray
    transcript: str
    skipped: bool = False  # "I don't know": scored 0, no audio, empty transcript


@dataclass
class HeldAnswer:
    """Audio + transcript kept after grading_failed / llm_unavailable for retry.
    `reading` is set when the answer is the meaning step of a two-step card."""

    card_id: int
    samples: np.ndarray
    transcript: str
    reading: HeardReading | None = None
    skipped: bool = False


STEPS = ("reading", "meaning")
_QUESTION_MARKS = re.compile(r"[?？]")
DUPLICATE_CARD = "This deck already has a card with the same front, reading and back"
# Silence between the reading and meaning clips in the saved recording.
STEP_GAP_S = 0.4
# "I don't know": the answer (or one step of a two-step card) scores 0 ("again").
DONT_KNOW_GRADE = reviews.Grade(score=0, feedback="You didn't know this one.")


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
        self.rng = random.Random()  # queue shuffle; tests replace it with a seeded one
        self.file_dialog: Callable[[], str | None] | None = None  # set by __main__ once the window exists
        # Deck files (.afdeck): native open / save dialogs, also set by __main__. The save
        # dialog gets a suggested file name. Paths only ever come from these dialogs.
        self.deck_open_dialog: Callable[[], str | None] | None = None
        self.deck_save_dialog: Callable[[str], str | None] | None = None

        self._review_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._rec_card: int | None = None
        self._rec_step: str | None = None  # 'reading' / 'meaning' for two-step cards
        self._heard: HeardReading | None = None
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
                d["learning_later"] = scheduler.learning_later(conn, row["id"], now)
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
            if self._heard and self._heard.card_id in ids:
                self._heard = None

    # --- cards ------------------------------------------------------------------------

    def list_cards(self, deck_id: int) -> list[dict]:
        with self.db() as conn:
            self._deck_or_404(conn, deck_id)
            rows = conn.execute("SELECT * FROM cards WHERE deck_id = ? ORDER BY created_at, id", (deck_id,))
            return [database.card_to_dict(r) for r in rows]

    def create_card(
        self,
        deck_id: int,
        front: str,
        back: str,
        reading: str | None = None,
        alt_answers: list | None = None,
        sentence: str | None = None,
        sentence_meaning: str | None = None,
        notes: str | None = None,
    ) -> dict:
        front, back = _req_str(front, "front"), _req_str(back, "back")
        reading = _opt_str(reading, "reading")
        sentence = _opt_str(sentence, "sentence")
        sentence_meaning = _opt_str(sentence_meaning, "sentence_meaning")
        notes = _opt_str(notes, "notes")
        alts = _alt_answers(alt_answers)
        now = to_iso(self.clock())
        with self.db() as conn:
            self._deck_or_404(conn, deck_id)
            try:
                with database.transaction(conn):
                    cur = conn.execute(
                        "INSERT INTO cards(deck_id, front, back, reading, alt_answers, sentence, sentence_meaning, notes, "
                        "created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                        (
                            deck_id,
                            front,
                            back,
                            reading,
                            json.dumps(alts) if alts else None,
                            sentence,
                            sentence_meaning,
                            notes,
                            now,
                            now,
                        ),
                    )
            except sqlite3.IntegrityError as e:
                raise AppError("invalid_input", DUPLICATE_CARD) from e
            return database.card_to_dict(database.get_card(conn, cur.lastrowid))

    def update_card(self, card_id: int, patch: dict) -> dict:
        if not isinstance(patch, dict) or not patch:
            raise AppError("invalid_input", "patch must be a non-empty object")
        sets: dict[str, Any] = {}
        for k, v in patch.items():
            if k in ("front", "back"):
                sets[k] = _req_str(v, k)
            elif k in ("reading", "sentence", "sentence_meaning", "notes"):
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
                raise AppError("invalid_input", DUPLICATE_CARD) from e
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
            if self._heard and self._heard.card_id == card_id:
                self._heard = None

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
            return self._next(conn, deck, now)

    def get_next_card(self, deck_id: int) -> dict:
        with self.db() as conn:
            deck = self._deck_or_404(conn, deck_id)
            return self._next(conn, deck, self.clock())

    def _next(self, conn: sqlite3.Connection, deck: sqlite3.Row, now: datetime) -> dict:
        shuffle = bool(database.get_settings(conn)["shuffle_due"])
        return scheduler.next_card(conn, deck, now, shuffle=shuffle, rng=self.rng)

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

    def import_plan(self, path: str, deck_id: int, mapping: dict | None) -> dict:
        """What importing would do, without changing anything: new / updated / unchanged
        rows and the deck's cards that are no longer in the file."""
        p = self._checked_path(path)
        with self.db() as conn:
            self._deck_or_404(conn, deck_id)
            try:
                return importer.plan_import(conn, importer.parse_file(p), deck_id, mapping).summary()
            except importer.ImportError_ as e:
                raise AppError("import_error", str(e)) from e

    def import_cards(self, path: str, deck_id: int, mapping: dict | None, missing: str = "keep") -> dict:
        """Import or re-import. Cards already in the deck are updated in place (schedule
        and history kept). `missing` = 'keep' or 'remove' for deck cards not in the file."""
        if missing not in ("keep", "remove"):
            raise AppError("invalid_input", "missing must be 'keep' or 'remove'")
        p = self._checked_path(path)
        with self.review_lock(), self.db() as conn:
            self._deck_or_404(conn, deck_id)
            try:
                res = importer.import_into_deck(
                    conn, importer.parse_file(p), deck_id, mapping, to_iso(self.clock()), missing
                )
            except importer.ImportError_ as e:
                raise AppError("import_error", str(e)) from e
        removed = res.pop("removed_ids")
        if removed:
            reviews.delete_card_dirs(self.paths, removed)
            with self._state_lock:
                if self._held and self._held.card_id in removed:
                    self._held = None
                if self._heard and self._heard.card_id in removed:
                    self._heard = None
        return res

    # --- deck files (backup / restore with progress) -------------------------------------------

    def export_deck(self, deck_id: int) -> dict | None:
        """Save the deck and all its progress to a .afdeck file the user picks. None if
        the dialog was cancelled."""
        with self.db() as conn:
            deck = self._deck_or_404(conn, deck_id)
            data = deckfile.export_deck(conn, deck_id, self.clock())
        if self.deck_save_dialog is None:
            raise AppError("internal_error", "File dialog not available")
        safe = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", deck["name"]).strip() or "deck"
        picked = self.deck_save_dialog(f"{safe}{deckfile.SUFFIX}")
        if not picked:
            return None
        p = Path(picked)
        if p.suffix.lower() != deckfile.SUFFIX:
            p = p.with_name(p.name + deckfile.SUFFIX)
        tmp = p.with_name(p.name + ".part")
        try:
            tmp.write_text(deckfile.dumps(data), encoding="utf-8")
            tmp.replace(p)
        except OSError as e:
            tmp.unlink(missing_ok=True)
            raise AppError("import_error", f"Could not write the file: {e}") from e
        return {"path": str(p), "cards": len(data["cards"])}

    def import_deck_file(self) -> dict | None:
        """Restore a .afdeck file the user picks as a NEW deck. None if cancelled."""
        if self.deck_open_dialog is None:
            raise AppError("internal_error", "File dialog not available")
        picked = self.deck_open_dialog()
        if not picked:
            return None
        p = Path(picked)
        try:
            if p.stat().st_size > deckfile.MAX_BYTES:
                raise AppError("import_error", "The file is too large to be a deck file.")
            raw = p.read_bytes()
        except OSError as e:
            raise AppError("import_error", f"Could not read the file: {e}") from e
        try:
            parsed = deckfile.parse(raw)
            with self.db() as conn:
                res = deckfile.import_deck(conn, parsed, self.clock())
                deck = database.deck_to_dict(database.get_deck(conn, res["deck_id"]))
        except deckfile.DeckFileError as e:
            raise AppError("import_error", str(e)) from e
        return {"deck": deck, "cards": res["cards"], "reviews": res["reviews"]}

    # --- session & recording ----------------------------------------------------------------

    def begin_session(self, deck_id: int) -> dict:
        with self.db() as conn:
            deck = self._deck_or_404(conn, deck_id)
            language = deck["language"]
            needs_llm = language == "en" or self._deck_has_meaning_steps(conn, deck_id)
        self._session_deck = deck_id
        with self._state_lock:
            self._heard = None
        try:
            self.mic.open(self.settings()["mic_device"])
        except audio.MicError as e:
            raise AppError("mic_error", str(e)) from e
        if needs_llm:
            def warm():
                if not self.ollama.status.checked or not self.ollama.ready:
                    self.ollama.check()
                if self.ollama.ready:
                    self.ollama.warm_up()

            threading.Thread(target=warm, name="ollama-warmup", daemon=True).start()
        return self.get_next_card(deck_id)

    @staticmethod
    def _deck_has_meaning_steps(conn: sqlite3.Connection, deck_id: int) -> bool:
        rows = conn.execute(
            "SELECT back, reading FROM cards WHERE deck_id = ? AND reading IS NOT NULL AND reading != ''", (deck_id,)
        )
        return any(database.has_meaning_step("ja", r["back"], r["reading"]) for r in rows)

    def end_session(self) -> None:
        self._session_deck = None
        self.mic.cancel_recording()
        with self._state_lock:
            self._rec_card = None
            self._rec_step = None
            self._heard = None
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

    def start_recording(self, card_id: int, step: str | None = None) -> None:
        """`step` is 'reading' or 'meaning' for two-step cards (default 'reading') and is
        ignored for one-step cards. The meaning step needs the reading recorded first."""
        if step is not None and step not in STEPS:
            raise AppError("invalid_input", "step must be 'reading' or 'meaning'")
        with self.review_lock():
            if self.mic.recording:
                raise AppError("busy", "Already recording")
            with self.db() as conn:
                card = self._card_or_404(conn, card_id)
                deck = database.get_deck(conn, card["deck_id"])
            two_step = database.has_meaning_step(deck["language"], card["back"], card["reading"])
            step = (step or "reading") if two_step else None
            if step == "meaning":
                with self._state_lock:
                    heard = self._heard
                if heard is None or heard.card_id != card_id:
                    raise AppError("invalid_input", "Record the reading first")
            if not self.whisper.ready:
                raise AppError("whisper_not_ready", "The speech model is not ready yet")
            if deck["language"] == "en" or two_step:
                if not self.ollama.ready:
                    self.ollama.check()
                if not self.ollama.ready:
                    raise AppError("llm_unavailable", "ollama is not running or the model is missing")
            self.player.stop()
            with self._state_lock:
                self._held = None  # a new recording discards any held audio
                if step != "meaning":
                    self._heard = None  # re-recording the reading replaces it
            try:
                self.mic.start_recording(self.settings()["mic_device"])
            except audio.Busy as e:
                raise AppError("busy", "Already recording") from e
            except audio.MicError as e:
                raise AppError("mic_error", str(e)) from e
            with self._state_lock:
                self._rec_card = card_id
                self._rec_step = step

    def get_recording_status(self) -> dict:
        return self.mic.status()

    def cancel_recording(self) -> None:
        """Cancelling the meaning step keeps the heard reading."""
        self.mic.cancel_recording()
        with self._state_lock:
            self._rec_card = None
            self._rec_step = None

    def stop_recording(self) -> dict:
        """One-step card or meaning step: the graded review result. Reading step: only
        what Whisper heard, `{"stage": "heard", "step": "reading", ...}`, not graded."""
        with self.review_lock():
            with self._state_lock:
                card_id, self._rec_card = self._rec_card, None
                step, self._rec_step = self._rec_step, None
            if card_id is None or not self.mic.recording:
                raise AppError("invalid_input", "Not recording")
            samples = self.mic.stop_recording()
            card, language = self._card_context(card_id)
            if step == "reading":
                transcript = self._transcribe(samples, "ja", self._reading_expected(card))
                with self._state_lock:
                    self._heard = HeardReading(card_id, samples, transcript)
                return {"stage": "heard", "step": "reading", "card_id": card_id, "transcript": transcript}
            if step == "meaning":
                with self._state_lock:
                    heard = self._heard
                if heard is None or heard.card_id != card_id:
                    raise AppError("invalid_input", "Record the reading first")
                back = card["back"]
                transcript = self._transcribe(samples, "en", [back, *reading_match.split_answers(back)])
                return self._grade_persist(card_id, samples, transcript, heard)
            transcript = self._transcribe(samples, language, self._reading_expected(card))
            return self._grade_persist(card_id, samples, transcript)

    def retry_grading(self) -> dict:
        with self.review_lock():
            with self._state_lock:
                held = self._held
            if held is None:
                raise AppError("not_found", "No answer to retry")
            return self._grade_persist(held.card_id, held.samples, held.transcript, held.reading, held.skipped)

    def dont_know(self, card_id: int, step: str | None = None) -> dict:
        """"I don't know" for the current answer: it scores 0 with nothing recorded.
        One-step card: graded and saved like a normal answer. Two-step card, reading step
        (default): returns the same `{"stage": "heard", ...}` shape as a recorded reading
        (with `skipped`), and the meaning is asked next; the reading is graded 0 with the
        meaning. Meaning step: graded right away (reading by reading match, meaning 0), so
        no LLM call is needed."""
        if step is not None and step not in STEPS:
            raise AppError("invalid_input", "step must be 'reading' or 'meaning'")
        with self.review_lock():
            if self.mic.recording:
                raise AppError("busy", "Already recording")
            card, language = self._card_context(card_id)
            two_step = database.has_meaning_step(language, card["back"], card["reading"])
            step = (step or "reading") if two_step else None
            empty = np.zeros(0, dtype=np.float32)
            with self._state_lock:
                self._held = None
                if step == "reading":
                    self._heard = HeardReading(card_id, empty, "", skipped=True)
                    return {"stage": "heard", "step": "reading", "card_id": card_id, "transcript": "", "skipped": True}
                heard = self._heard
            if step == "meaning" and (heard is None or heard.card_id != card_id):
                raise AppError("invalid_input", "Record the reading first")
            return self._grade_persist(card_id, empty, "", heard if step == "meaning" else None, skipped=True)

    def _card_context(self, card_id: int) -> tuple[sqlite3.Row, str]:
        with self.db() as conn:
            card = self._card_or_404(conn, card_id)
            deck = database.get_deck(conn, card["deck_id"])
            return card, deck["language"]

    @staticmethod
    def _reading_expected(card: sqlite3.Row) -> list[str]:
        """Answers that must survive Whisper's hallucination blocklist."""
        alts = json.loads(card["alt_answers"]) if card["alt_answers"] else []
        expected = [card["front"], card["back"], card["reading"] or "", *alts]
        return expected + reading_match.split_answers(card["back"]) + reading_match.split_answers(card["reading"])

    def _transcribe(self, samples: np.ndarray, language: str, expected: list[str]) -> str:
        try:
            text = self.whisper.transcribe_pcm(samples, language, expected)  # type: ignore[arg-type]
        except NoSpeech as e:
            raise AppError("no_speech", "Didn't catch that, try again.") from e
        except WhisperNotReady as e:
            raise AppError("whisper_not_ready", "The speech model is not ready yet") from e
        if language == "en":
            # Whisper guesses "?" from intonation; the LLM then grades the meaning as "said
            # as a question" (大きな: "big or large?" scored 1). A spoken answer never needs it.
            text = _QUESTION_MARKS.sub("", text).strip()
            if not text:
                raise AppError("no_speech", "Didn't catch that, try again.")
        return text

    def _grade_persist(
        self,
        card_id: int,
        samples: np.ndarray,
        transcript: str,
        reading: HeardReading | None = None,
        skipped: bool = False,
    ) -> dict:
        """`skipped`: the answer being graded (one-step answer, or the meaning step) was
        "I don't know"; it scores 0 without calling a grader."""
        card, language = self._card_context(card_id)
        alts = json.loads(card["alt_answers"]) if card["alt_answers"] else []
        # Reading match accepts a transcript that is exactly the front (今日 guard). When
        # another card shares the front with a different reading (方 ほう / 方 かた), the
        # kanji alone doesn't show which reading was said, so that shortcut is skipped.
        ja_front = "" if self._front_is_ambiguous(card) else card["front"]
        steps: list[reviews.StepResult] | None = None
        try:
            if reading is None and skipped:
                grade = DONT_KNOW_GRADE
            elif reading is None:
                front = ja_front if language == "ja" else card["front"]
                grade = grade_answer(
                    language,
                    transcript,
                    front,
                    card["back"],
                    card["reading"],
                    alts,
                    self.ollama,
                    CardContext.from_card(card),
                )
            else:
                # Two-step card: reading by the deterministic match, meaning by the LLM.
                if reading.skipped:
                    rg = DONT_KNOW_GRADE
                else:
                    rg = grade_answer("ja", reading.transcript, ja_front, card["back"], card["reading"], alts, self.ollama)
                if skipped:
                    mg = DONT_KNOW_GRADE
                else:
                    mg = grade_meaning(
                        transcript,
                        card["front"],
                        card["back"],
                        card["reading"],
                        self.ollama,
                        CardContext.from_card(card),
                    )
                steps = [
                    reviews.StepResult("reading", reading.transcript, rg),
                    reviews.StepResult("meaning", transcript, mg),
                ]
        except LLMUnavailable as e:
            self._hold(card_id, samples, transcript, reading, skipped)
            raise AppError("llm_unavailable", "Could not reach ollama. Start it and press retry.") from e
        except GradingFailed as e:
            self._hold(card_id, samples, transcript, reading, skipped)
            raise AppError("grading_failed", "The grader returned an unusable answer. Press retry.") from e
        full_transcript = transcript
        audio_out = samples
        if steps is not None and reading is not None:
            full_transcript, grade = reviews.combine_steps(steps)
            clips = [c.astype(np.float32) for c in (reading.samples, samples) if c.size]
            gap = np.zeros(int(STEP_GAP_S * config.SAMPLE_RATE), dtype=np.float32)
            # A skipped step has no audio; then the saved recording is just the other clip.
            audio_out = np.concatenate([clips[0], gap, clips[1]]) if len(clips) == 2 else (clips[0] if clips else samples)
        try:
            ogg = self.encoder(audio_out) if audio_out.size else None
        except Exception:
            log.exception("encoding recording failed; saving review without audio")
            ogg = None
        with self.db() as conn:
            try:
                result = reviews.record_review(conn, self.paths, card_id, full_transcript, grade, ogg, self.clock(), steps)
            except reviews.ReviewError as e:
                raise AppError(e.code, e.message) from e
        with self._state_lock:
            self._held = None
            if reading is not None and self._heard is reading:
                self._heard = None
        return result

    def _front_is_ambiguous(self, card: sqlite3.Row) -> bool:
        with self.db() as conn:
            row = conn.execute(
                "SELECT 1 FROM cards WHERE deck_id = ? AND front = ? AND id != ? "
                "AND COALESCE(reading, '') != COALESCE(?, '') LIMIT 1",
                (card["deck_id"], card["front"], card["id"], card["reading"]),
            ).fetchone()
        return row is not None

    def _hold(
        self,
        card_id: int,
        samples: np.ndarray,
        transcript: str,
        reading: HeardReading | None = None,
        skipped: bool = False,
    ) -> None:
        with self._state_lock:
            self._held = HeldAnswer(card_id, samples, transcript, reading, skipped)

    def override_grade(self, review_id: int, correct: bool, step: str | None = None) -> dict:
        if step is not None and step not in STEPS:
            raise AppError("invalid_input", "step must be 'reading' or 'meaning'")
        with self.review_lock(), self.db() as conn:
            try:
                return reviews.override_grade(conn, review_id, bool(correct), step)
            except reviews.ReviewError as e:
                raise AppError(e.code, e.message) from e

    def undo_last_review(self) -> dict:
        with self._state_lock:
            self._heard = None
        with self.review_lock(), self.db() as conn:
            try:
                return reviews.undo_last_review(conn, self.paths)
            except reviews.ReviewError as e:
                raise AppError(e.code, e.message) from e

    def get_card(self, card_id: int) -> dict:
        with self.db() as conn:
            card = self._card_or_404(conn, card_id)
            deck = database.get_deck(conn, card["deck_id"])
            return database.card_to_dict(card, deck["language"])

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
                "state_before, state_after, interval_after, ease_after, recording_id, steps FROM reviews "
                "WHERE card_id = ? ORDER BY id DESC",
                (card_id,),
            )
            return [
                {
                    **dict(r),
                    "is_correct": bool(r["is_correct"]),
                    "overridden": bool(r["overridden"]),
                    "steps": json.loads(r["steps"]) if r["steps"] else None,
                }
                for r in rows
            ]

    def get_weak_spots(self, deck_id: int, days: int = 30) -> dict:
        """Where two-step answers go wrong, from `reviews.steps` (scores after overrides).
        `days` = 0 means all history. A step counts as missed below score 2."""
        if isinstance(days, bool) or not isinstance(days, int) or not 0 <= days <= 3650:
            raise AppError("invalid_input", "days must be a whole number 0-3650")
        with self.db() as conn:
            self._deck_or_404(conn, deck_id)
            since = to_iso(self.clock() - timedelta(days=days)) if days else ""
            rows = conn.execute(
                "SELECT r.card_id, r.reviewed_at, json_extract(s.value, '$.step') AS step, "
                "json_extract(s.value, '$.score') AS score "
                "FROM reviews r, json_each(r.steps) s "
                "WHERE r.deck_id = ? AND r.steps IS NOT NULL AND r.reviewed_at >= ? "
                "ORDER BY r.id",
                (deck_id, since),
            ).fetchall()
            totals = {st: {"answers": 0, "correct": 0} for st in STEPS}
            per_card: dict[int, dict[str, Any]] = {}
            for r in rows:
                st = r["step"]
                if st not in totals:
                    continue
                ok = r["score"] >= 2
                totals[st]["answers"] += 1
                totals[st]["correct"] += int(ok)
                c = per_card.setdefault(
                    r["card_id"],
                    {"answers": 0, "reading_misses": 0, "meaning_misses": 0, "last_missed_at": None, "last": {}},
                )
                if st == "reading":
                    c["answers"] += 1  # one reading per two-step review
                if not ok:
                    c[f"{st}_misses"] += 1
                    c["last_missed_at"] = r["reviewed_at"]
                c["last"][st] = r["score"]  # rows are in review order, so this ends on the latest
            missed = [cid for cid, c in per_card.items() if c["reading_misses"] or c["meaning_misses"]]
            cards_by_id = {}
            if missed:
                q = ",".join("?" * len(missed))
                cards_by_id = {
                    r["id"]: r for r in conn.execute(f"SELECT id, front, reading, back FROM cards WHERE id IN ({q})", missed)
                }
        out_cards = []
        for cid in missed:
            card = cards_by_id.get(cid)
            if card is None:
                continue
            c = per_card[cid]
            out_cards.append(
                {
                    "card_id": cid,
                    "front": card["front"],
                    "reading": card["reading"],
                    "back": card["back"],
                    "answers": c["answers"],
                    "reading_misses": c["reading_misses"],
                    "meaning_misses": c["meaning_misses"],
                    "last_missed_at": c["last_missed_at"],
                    # latest answer right for every step = probably learned since
                    "recovered": all(v >= 2 for v in c["last"].values()),
                }
            )
        # Not-yet-recovered first, then most misses; ties: most recently missed first.
        out_cards.sort(key=lambda c: c["last_missed_at"] or "", reverse=True)
        out_cards.sort(key=lambda c: (c["recovered"], -(c["reading_misses"] + c["meaning_misses"])))
        return {"days": days, "steps": totals, "cards": out_cards[:100]}

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
        elif k == "theme":
            if v not in ("dark", "light", "system"):
                raise AppError("invalid_input", "theme must be 'dark', 'light' or 'system'")
            out[k] = v
        elif k == "shuffle_due":
            if not isinstance(v, bool):
                raise AppError("invalid_input", "shuffle_due must be true or false")
            out[k] = v
    return out
