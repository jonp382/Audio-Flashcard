"""All application defaults in one place (agent.md §12.2)."""

from __future__ import annotations

from typing import NamedTuple

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
WHISPER_URL_TEMPLATE = "https://huggingface.co/{repo}/resolve/{revision}/{file}"


class WhisperModelSpec(NamedTuple):
    """A downloadable faster-whisper (CTranslate2) model, pinned to an exact revision."""

    label: str
    blurb: str
    repo: str
    revision: str
    # file -> (size in bytes, sha256), from the pinned revision. Verified after download.
    files: dict[str, tuple[int, str]]


# Models the app can download itself (whisper_model setting = the key). Each lives in
# models/faster-whisper-<key>/. Any other faster-whisper model folder dropped into
# models/ also shows up in Settings, by folder name (like picking any ollama tag).
WHISPER_MODELS: dict[str, WhisperModelSpec] = {
    "small": WhisperModelSpec(
        "small",
        "Fast on CPU. Weakest Japanese.",
        "Systran/faster-whisper-small",
        "536b0662742c02347bc0e980a01041f333bce120",
        {
            "config.json": (2370, "b55496ac7940a7ae47d2c01eab40edfd8701feec1229d9cce3b40014383fb828"),
            "tokenizer.json": (2203239, "fb7b63191e9bb045082c79fd742a3106a12c99513ab30df4a0d47fa6cb6fd0ab"),
            "vocabulary.txt": (459861, "34ce3fe1c5041027b3f8d42912270993f986dbc4bb34cf27f951e34a1e453913"),
            "model.bin": (483546902, "3e305921506d8872816023e4c273e75d2419fb89b24da97b4fe7bce14170d671"),
        },
    ),
    "medium": WhisperModelSpec(
        "medium",
        "Better Japanese than small; 2-3x slower on CPU.",
        "Systran/faster-whisper-medium",
        "08e178d48790749d25932bbc082711ddcfdfbc4f",
        {
            "config.json": (2257, "3622a2ddc41ec0e0fd4e68c13c6830f03b90c38d89aaad184de02c8c642cf807"),
            "tokenizer.json": (2203239, "fb7b63191e9bb045082c79fd742a3106a12c99513ab30df4a0d47fa6cb6fd0ab"),
            "vocabulary.txt": (459861, "34ce3fe1c5041027b3f8d42912270993f986dbc4bb34cf27f951e34a1e453913"),
            "model.bin": (1527906378, "9b45e1009dcc4ab601eff815b61d80e60ce3fd8c74c1a14f4a282258286b51ae"),
        },
    ),
    "large-v3-turbo": WhisperModelSpec(
        "large-v3-turbo",
        "Near large-v3 Japanese. Best on GPU (~1.2GB VRAM); slow on CPU.",
        "dropbox-dash/faster-whisper-large-v3-turbo",
        "0a363e9161cbc7ed1431c9597a8ceaf0c4f78fcf",
        {
            "config.json": (2263, "b0253ea6c0d3bea6b1e19e91a02acfd3b53f4467362efcb5a3e6b16c9b3a9b7e"),
            "preprocessor_config.json": (340, "7ccc62c6f2765af1f3b46c00c9b5894426835a05021c8b9c01eecb6dfb542711"),
            "tokenizer.json": (2710337, "297b13372ac43916285644fb9687add3cc62ee2a1adb60da3dc25cc94c1871fd"),
            "vocabulary.json": (1068114, "c69260f2ab26d659b7c398f9a2b2b48ed0df16c3b47d7326782fd9cba71690c1"),
            "model.bin": (1617884929, "e76620f83d5f5b69efd3d87e3dc180c1bd21df9fbebacfd4335e5e1efcc018da"),
        },
    ),
    "large-v3": WhisperModelSpec(
        "large-v3",
        "Most accurate. Slow on CPU; needs ~2GB+ VRAM on GPU.",
        "Systran/faster-whisper-large-v3",
        "edaa852ec7e145841d8ffdb056a99866b5f0a478",
        {
            "config.json": (2394, "a9306624f5ec14270a014b647e5c316b6e03a662c369758d1b90697a7b0655b9"),
            "preprocessor_config.json": (340, "7ccc62c6f2765af1f3b46c00c9b5894426835a05021c8b9c01eecb6dfb542711"),
            "tokenizer.json": (2480617, "6d8cbd7cd0d8d5815e478dac67b85a26bbe77c1f5e0c6d76d1ce2abc0e5f21ca"),
            "vocabulary.json": (1068114, "c69260f2ab26d659b7c398f9a2b2b48ed0df16c3b47d7326782fd9cba71690c1"),
            "model.bin": (3087284237, "69f74147e3334731bc3a76048724833325d2ec74642fb52620eda87352e3d4f1"),
        },
    ),
}
DEFAULT_WHISPER_MODEL = "small"
WHISPER_DIR_PREFIX = "faster-whisper-"
# A folder in models/ counts as a (custom) faster-whisper model if it has these files.
WHISPER_REQUIRED_FILES = ("model.bin", "config.json", "tokenizer.json")
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
    "whisper_model": DEFAULT_WHISPER_MODEL,
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
