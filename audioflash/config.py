"""All application defaults in one place (agent.md §12.2)."""

from __future__ import annotations

APP_NAME = "AudioFlashcard"
WINDOW_TITLE = "Audio Flashcard"
MIN_WINDOW_SIZE = (900, 600)
DEV_SERVER_URL = "http://localhost:5173"

# --- LLM evaluator (ollama) -------------------------------------------------
DEFAULT_LLM_MODEL = "qwen3:8b"
DEFAULT_OLLAMA_HOST = "localhost"
DEFAULT_OLLAMA_PORT = 11434
OLLAMA_TIMEOUT_S = 60.0
OLLAMA_KEEP_ALIVE = "30m"
LLM_TEMPERATURE = 0.1
LLM_NUM_PREDICT = 256
OLLAMA_DOWNLOAD_PAGE = "https://ollama.com/download"

# --- Audio ------------------------------------------------------------------
SAMPLE_RATE = 16_000
MAX_CLIP_SECONDS = 15.0
PRE_ROLL_SECONDS = 0.5
IDLE_MIC_TIMEOUT_S = 120.0
RECORDINGS_PER_CARD = 10
# Software mic boost (settings key mic_gain, linear). 1.0 = no boost; 16.0 = +24 dB.
MIC_GAIN_MIN = 1.0
MIC_GAIN_MAX = 16.0

# Interface scale (ui_scale setting): 1.0 = 100%. Ctrl+scroll / Ctrl+= / Ctrl+- in the UI.
UI_SCALE_MIN = 0.5
UI_SCALE_MAX = 2.5
MIC_TEST_MAX_SECONDS = 5.0
# Before Whisper, quiet clips are peak-normalized to this level (boost only, capped),
# so a quiet mic still gives Whisper / its VAD a healthy signal.
NORMALIZE_PEAK = 0.9
MAX_NORMALIZE_GAIN = 10.0
# Clips whose overall RMS is below this never reach Whisper (hallucination guard 1).
# Checked 2026-11-18 on the owner's mic at max boost: normal speech -28 dBFS, silence
# is digital zero (upstream noise gate), so -48 dBFS leaves ~20 dB of headroom.
ENERGY_GATE_RMS = 0.004

# --- Whisper ----------------------------------------------------------------
WHISPER_REPO = "Systran/faster-whisper-small"
WHISPER_REVISION = "536b0662742c02347bc0e980a01041f333bce120"
WHISPER_URL_TEMPLATE = "https://huggingface.co/{repo}/resolve/{revision}/{file}"
# file -> (size in bytes, sha256), from the pinned revision. Verified after download.
WHISPER_FILES: dict[str, tuple[int, str]] = {
    "config.json": (2370, "b55496ac7940a7ae47d2c01eab40edfd8701feec1229d9cce3b40014383fb828"),
    "tokenizer.json": (2203239, "fb7b63191e9bb045082c79fd742a3106a12c99513ab30df4a0d47fa6cb6fd0ab"),
    "vocabulary.txt": (459861, "34ce3fe1c5041027b3f8d42912270993f986dbc4bb34cf27f951e34a1e453913"),
    "model.bin": (483546902, "3e305921506d8872816023e4c273e75d2419fb89b24da97b4fe7bce14170d671"),
}
WHISPER_DIR_NAME = "faster-whisper-small"
WHISPER_TMP_PREFIX = ".download-"
WHISPER_BEAM_SIZE = 5
# If a Japanese transcript comes back in Latin letters (Whisper decided it heard English),
# transcribe once more with this generic prompt nudging it toward Japanese script.
# Never the card's answer: that would bias the transcript and hide mistakes (§13).
JA_RETRY_PROMPT = "日本語の単語を一つ言います。"
NO_SPEECH_PROB_MAX = 0.6
AVG_LOGPROB_MIN = -1.0

# Whole-transcript hallucinations Whisper produces on silence/noise. Compared after
# normalization (see stt.normalize_for_blocklist).
HALLUCINATION_BLOCKLIST: dict[str, tuple[str, ...]] = {
    "ja": (
        "ご視聴ありがとうございました",
        "ご視聴ありがとうございます",
        "ありがとうございました",
        "チャンネル登録よろしくお願いします",
        "チャンネル登録お願いします",
        "おやすみなさい",
        "字幕",
        "字幕作成者",
        "お疲れ様でした",
        "日本語の単語を一つ言います",  # JA_RETRY_PROMPT echoed back on silence
    ),
    "en": (
        "thanks for watching",
        "thank you for watching",
        "thank you",
        "thanks",
        "please subscribe",
        "subscribe",
        "bye",
        "you",
    ),
}

# --- SRS --------------------------------------------------------------------
INITIAL_EASE = 2.5
MIN_EASE = 1.3
DEFAULT_NEW_PER_DAY = 20
DEFAULT_REVIEWS_PER_DAY = 200
DEFAULT_LEARNING_STEPS = [1, 10]
DEFAULT_RELEARNING_STEPS = [10]
LEARN_AHEAD_MINUTES = 20

# --- Storage ----------------------------------------------------------------
BACKUPS_TO_KEEP = 7
LOG_MAX_BYTES = 1_000_000
LOG_BACKUP_COUNT = 3

# --- Settings defaults (settings table falls back to these) -----------------
DEFAULT_SETTINGS: dict[str, object] = {
    "ollama_host": DEFAULT_OLLAMA_HOST,
    "ollama_port": DEFAULT_OLLAMA_PORT,
    "llm_model": DEFAULT_LLM_MODEL,
    "whisper_device": "cpu",
    "mic_device": None,
    "record_mode": "toggle",
    "mic_gain": 1.0,
    # UI colour theme: 'dark' | 'light' | 'system' (follow the OS preference).
    "theme": "dark",
    # Shuffle due cards (learning cards due now, reviews due the same day) so the order
    # isn't memorised. New cards always keep their file order. Toggled in the review header.
    "shuffle_due": False,
    "ui_scale": 1.0,
}
