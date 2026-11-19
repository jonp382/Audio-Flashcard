# PyInstaller spec (onedir). Build from the repo root after `npm run build`:
#   .venv/bin/pip install pyinstaller
#   .venv/bin/pyinstaller packaging/audioflash.spec
# Not yet verified end-to-end; see agent.md §11 for the required standalone checks
# (window opens, real mic capture, VAD transcription, playback).
# ruff: noqa
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_data_files, collect_dynamic_libs

ROOT = Path(SPECPATH).parent

datas = [(str(ROOT / "frontend" / "dist" / "index.html"), "frontend/dist")]
binaries = []
hiddenimports = []

datas += collect_data_files("faster_whisper")  # Silero VAD .onnx asset
datas += collect_data_files("unidic_lite")
binaries += collect_dynamic_libs("ctranslate2")
binaries += collect_dynamic_libs("onnxruntime")
for pkg in ("fugashi", "av", "tokenizers", "sounddevice", "_sounddevice_data"):
    try:
        d, b, h = collect_all(pkg)
        datas += d
        binaries += b
        hiddenimports += h
    except Exception:
        pass

if sys.platform == "win32":
    hiddenimports += ["webview.platforms.edgechromium", "clr"]
elif sys.platform == "darwin":
    hiddenimports += ["webview.platforms.cocoa"]
else:
    hiddenimports += ["webview.platforms.gtk"]  # uses the system GTK/WebKit2GTK; not bundled

a = Analysis(
    [str(ROOT / "audioflash" / "__main__.py")],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=["tkinter"],
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="AudioFlashcard", console=False)
coll = COLLECT(exe, a.binaries, a.datas, name="AudioFlashcard")

if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name="Audio Flashcard.app",
        bundle_identifier="io.github.audioflashcard",
        info_plist={"NSMicrophoneUsageDescription": "Audio Flashcard records your spoken answers to grade them."},
    )
