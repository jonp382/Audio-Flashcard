"""ollama structured-output grader for English answers (agent.md §6b).

Only talks to the configured ollama host. Never sends audio, only text.
"""

from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass, field
from typing import Any, Callable

import httpx
from pydantic import BaseModel, ValidationError

from . import config

log = logging.getLogger(__name__)


class LLMUnavailable(Exception):
    """ollama unreachable, timed out, or the model is missing."""


class GradingFailed(Exception):
    """ollama answered but the output could not be parsed."""


class LLMGrade(BaseModel):
    score: int
    feedback: str


# Feedback comes BEFORE score: ollama generates the fields in schema order, so the model
# writes its judgement first and then scores it. With score first (and thinking off) it
# committed to a number blind and wrote feedback to justify it, sometimes nonsense.
GRADE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "feedback": {"type": "string"},
        "score": {"type": "integer", "minimum": 0, "maximum": 3},
    },
    "required": ["feedback", "score"],
}

SYSTEM_PROMPT = """You grade a language learner's spoken flashcard answer.
You receive the card's prompt (front), the expected answer (back), optionally a reading and an
example sentence, the language the answer was spoken in, and a speech-to-text transcript of what
the learner said. Judge only from the transcript. Fillers and pauses are removed by speech recognition, so do not
judge fluency or hesitation.

Scores:
0 = the transcript is unrelated to the card, or gibberish
1 = wrong answer
2 = partially correct, or correct with a minor error (wrong particle, partial meaning,
    a small mispronunciation that changed a word)
3 = fully correct. Synonyms and equivalent phrasings with the same meaning are correct.
    Differences in writing system (kana / kanji / romaji), capitalization and punctuation
    are NOT errors. Speech recognition guesses the punctuation from intonation, so never judge
    whether an answer sounded like a question or a statement.
    If the expected answer lists several meanings (separated by commas or semicolons), any ONE
    of them is fully correct. Text in parentheses in the expected answer may be a usage note
    ("polite form") that the learner need not say, or a placeholder ("person from (country)").
    Judge whether the learner understood the meaning, not whether they used the same words: an
    answer that explains the meaning or its use in other words is correct.

Card notes, when given, are the deck author's explanation of the word. An answer that matches
the notes' description of the meaning is correct even if the expected answer is worded poorly
(e.g. expected "san" with notes "polite honorific": "honorific" is correct).

When an example sentence is given, the card's word is marked with <b>...</b>. Use the sentence
to see which sense of the word the card is about: a word with several senses (高い = expensive,
or high/tall) is graded against the sense the expected answer and the sentence use.

Write the feedback first, in English, 1-2 short sentences: compare the learner's answer with the
expected answer, and when there is a mistake, name it specifically. Then give the score that
matches your feedback. Reply only with the JSON object."""


@dataclass(frozen=True)
class CardContext:
    """Extra card fields that help the LLM judge which sense of a word is meant."""

    sentence: str | None = None
    sentence_meaning: str | None = None
    notes: str | None = None

    @classmethod
    def from_card(cls, card: Any) -> "CardContext":
        return cls(card["sentence"], card["sentence_meaning"], card["notes"])


def build_messages(
    front: str,
    back: str,
    reading: str | None,
    language: str,
    transcript: str,
    context: CardContext | None = None,
) -> list[dict[str, str]]:
    ctx = context or CardContext()
    lines = [
        f"Card prompt (front): {front}",
        f"Expected answer (back): {back}",
    ]
    if reading:
        lines.append(f"Reading: {reading}")
    if ctx.sentence:
        lines.append(f"Example sentence: {ctx.sentence}" + (f" ({ctx.sentence_meaning})" if ctx.sentence_meaning else ""))
    if ctx.notes:
        lines.append(f"Card notes: {ctx.notes}")
    if language == "en":
        lines.append("Task: the learner saw the card prompt and said its meaning in English.")
    else:
        lines.append("Task: the learner saw the card prompt and answered in Japanese.")
    lines.append(f"Transcript of learner's answer: {transcript}")
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": "\n".join(lines)},
    ]


def parse_grade(content: str) -> LLMGrade:
    try:
        g = LLMGrade.model_validate(json.loads(content))
    except (json.JSONDecodeError, ValidationError, TypeError) as e:
        raise GradingFailed(f"could not parse grader output: {e}") from e
    return LLMGrade(score=max(0, min(3, g.score)), feedback=g.feedback.strip() or "No feedback.")


@dataclass
class OllamaStatus:
    reachable: bool = False
    model: str = config.DEFAULT_LLM_MODEL
    model_present: bool = False
    supports_thinking: bool | None = None
    checked: bool = False
    error: str | None = None


@dataclass
class OllamaClient:
    host: str = config.DEFAULT_OLLAMA_HOST
    port: int = config.DEFAULT_OLLAMA_PORT
    model: str = config.DEFAULT_LLM_MODEL
    timeout: float = config.OLLAMA_TIMEOUT_S
    transport: httpx.BaseTransport | None = None  # tests inject a MockTransport
    status: OllamaStatus = field(default_factory=OllamaStatus)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    @property
    def base_url(self) -> str:
        host = self.host
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"  # IPv6 literal
        return f"http://{host}:{int(self.port)}"

    def _client(self, timeout: float | None = None) -> httpx.Client:
        return httpx.Client(base_url=self.base_url, timeout=timeout or self.timeout, transport=self.transport, trust_env=False)

    def configure(self, host: str, port: int, model: str) -> None:
        with self._lock:
            self.host, self.port, self.model = host, int(port), model
            self.status = OllamaStatus(model=model)

    # --- checks ------------------------------------------------------------------

    def check(self) -> OllamaStatus:
        """GET /api/tags, verify the model is present, detect capabilities."""
        st = OllamaStatus(model=self.model, checked=True)
        try:
            with self._client(timeout=5.0) as c:
                r = c.get("/api/tags")
                r.raise_for_status()
                names = {m.get("name") for m in r.json().get("models", [])} | {
                    m.get("model") for m in r.json().get("models", [])
                }
                st.reachable = True
                st.model_present = self.model in names or (
                    ":" not in self.model and f"{self.model}:latest" in names
                )
                if st.model_present:
                    st.supports_thinking = self._detect_thinking(c)
        except (httpx.HTTPError, ValueError) as e:
            st.error = str(e)
            log.info("ollama check failed: %s", e)
        with self._lock:
            self.status = st
        return st

    def _detect_thinking(self, c: httpx.Client) -> bool:
        r = c.post("/api/show", json={"model": self.model})
        r.raise_for_status()
        caps = r.json().get("capabilities") or []
        return "thinking" in caps

    def setup_steps(self) -> list[str]:
        st = self.status
        if not st.checked:
            return []
        if not st.reachable:
            return ["Install ollama (only needed for English-answer decks) and make sure it is running."]
        if not st.model_present:
            return [f"ollama pull {self.model}"]
        return []

    @property
    def ready(self) -> bool:
        return self.status.reachable and self.status.model_present

    # --- calls ---------------------------------------------------------------------

    def warm_up(self) -> None:
        """Load the model without generating (POST /api/generate with no prompt)."""
        try:
            with self._client() as c:
                c.post("/api/generate", json={"model": self.model, "keep_alive": config.OLLAMA_KEEP_ALIVE}).raise_for_status()
        except httpx.HTTPError as e:
            log.info("ollama warm-up failed: %s", e)

    def chat_payload(self, messages: list[dict[str, str]]) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "stream": False,
            "format": GRADE_SCHEMA,
            "keep_alive": config.OLLAMA_KEEP_ALIVE,
            "options": {"temperature": config.LLM_TEMPERATURE, "num_predict": config.LLM_NUM_PREDICT},
        }
        if self.status.supports_thinking:
            payload["think"] = False
        return payload

    def grade(
        self,
        front: str,
        back: str,
        reading: str | None,
        language: str,
        transcript: str,
        context: CardContext | None = None,
    ) -> LLMGrade:
        if not self.status.checked or not self.ready:
            self.check()
        if not self.ready:
            raise LLMUnavailable("ollama is not running or the model is missing")
        payload = self.chat_payload(build_messages(front, back, reading, language, transcript, context))
        try:
            with self._client() as c:
                r = c.post("/api/chat", json=payload)
                if r.status_code == 404:
                    self.check()
                    raise LLMUnavailable(f"model {self.model} not found in ollama")
                r.raise_for_status()
                data = r.json()
        except httpx.HTTPError as e:
            raise LLMUnavailable(str(e)) from e
        except ValueError as e:
            raise GradingFailed(f"invalid JSON from ollama: {e}") from e
        content = (data.get("message") or {}).get("content", "")
        return parse_grade(content)


def run_in_background(fn: Callable[[], Any], name: str) -> threading.Thread:
    t = threading.Thread(target=fn, name=name, daemon=True)
    t.start()
    return t
