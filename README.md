# Audio Flashcard

Local, private voice flashcards for learning Japanese. You see a card, say the answer out loud,
and the app transcribes it (faster-whisper), grades it (exact reading match for Japanese answers, a
local LLM via ollama for English answers) and schedules the card with SM-2 plus Anki-style
learning steps. No cloud, no telemetry: after a one-time ~480 MB speech-model download
everything runs offline.

## Run from source (Linux)

System packages (Ubuntu/Debian names; all already present on the dev machine):

- `libportaudio2` (mic capture / playback)
- `python3-gi`, `gir1.2-webkit2-4.1` (pywebview GTK backend)

The venv is created from the system Python **with `--system-site-packages`** so it can use the
apt-installed PyGObject (`gi`); PyGObject has no pip wheels.

```bash
python3 -m venv --system-site-packages .venv
.venv/bin/pip install -r requirements.txt

cd frontend && npm ci && npm run build && cd ..
.venv/bin/python -m audioflash
```

Dev mode (hot reload, devtools):

```bash
cd frontend && npm run dev          # terminal 1, serves http://127.0.0.1:5173
.venv/bin/python -m audioflash --dev  # terminal 2
```

Opening `http://127.0.0.1:5173` in a normal browser uses an in-memory mock of the Python
bridge, which is handy for layout work. The mock is excluded from production builds.

### English-answer decks: ollama

Install ollama yourself, then `ollama pull qwen3:8b` (the default grading model). Japanese-answer
decks never need ollama.

### GPU Whisper (optional)

Settings → "Run Whisper on: GPU". Needs the CUDA 12 cuBLAS/cuDNN libraries, e.g.
`.venv/bin/pip install nvidia-cublas-cu12 nvidia-cudnn-cu12`. If they are missing, the app logs
the error and falls back to CPU.

## Tests

```bash
.venv/bin/python -m pytest
```

Whisper, ollama, audio devices and the window are all mocked.

## Data

Everything lives in the OS user data dir (`~/.local/share/AudioFlashcard` on Linux): `app.db`
(SQLite, backed up on every launch, 7 kept), `models/`, `recordings/` (last 10 per card), `logs/`.

## Layout

- `audioflash/`: the Python app (`bridge.py` = JS API, `core.py` = services, plus one module per concern)
- `frontend/`: React + TypeScript + Tailwind UI, built to one self-contained `dist/index.html`
- `tests/`: the pytest suite
- `packaging/`: PyInstaller spec

## License

MIT
