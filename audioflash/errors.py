"""Error codes that cross the bridge (agent.md §3)."""

from __future__ import annotations

CODES = (
    "not_found",
    "invalid_input",
    "busy",
    "whisper_not_ready",
    "no_speech",
    "llm_unavailable",
    "grading_failed",
    "mic_error",
    "import_error",
    "internal_error",
)


class AppError(Exception):
    def __init__(self, code: str, message: str):
        assert code in CODES, code
        super().__init__(message)
        self.code = code
        self.message = message
