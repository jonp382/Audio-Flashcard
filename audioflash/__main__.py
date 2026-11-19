"""Entry point: ``python -m audioflash [--dev]`` (agent.md §3 startup order)."""

from __future__ import annotations

import os

# Before anything can import huggingface_hub: no telemetry, never go online implicitly.
os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
os.environ["HF_HUB_OFFLINE"] = "1"

import argparse  # noqa: E402
import logging  # noqa: E402
import sys  # noqa: E402
from logging.handlers import RotatingFileHandler  # noqa: E402
from pathlib import Path  # noqa: E402

from filelock import FileLock, Timeout  # noqa: E402

from . import config, database  # noqa: E402
from .paths import DataPaths, default_paths  # noqa: E402

log = logging.getLogger("audioflash")


def _gui_backend() -> str | None:
    if sys.platform.startswith("linux"):
        return "gtk"
    if sys.platform == "win32":
        return "edgechromium"
    if sys.platform == "darwin":
        return "cocoa"
    return None


def _setup_logging(paths: DataPaths, dev: bool) -> None:
    handler = RotatingFileHandler(paths.log_file, maxBytes=config.LOG_MAX_BYTES, backupCount=config.LOG_BACKUP_COUNT, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s [%(threadName)s] %(message)s"))
    root = logging.getLogger()
    root.setLevel(logging.DEBUG if dev else logging.INFO)
    root.addHandler(handler)
    if dev:
        root.addHandler(logging.StreamHandler())


def _frontend_html() -> str:
    """The production UI is a single self-contained HTML file (vite-plugin-singlefile),
    loaded directly so pywebview never starts its local HTTP server (see agent.md §4)."""
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
    index = base / "frontend" / "dist" / "index.html"
    if not index.is_file():
        raise SystemExit(f"Frontend not built: {index} is missing. Run `npm run build` in frontend/.")
    return index.read_text(encoding="utf-8")


def _already_running() -> None:
    import webview

    webview.create_window(
        config.WINDOW_TITLE,
        html=(
            "<!doctype html><meta charset=utf-8><body style='font-family:sans-serif;display:flex;flex-direction:column;"
            "align-items:center;justify-content:center;height:90vh;margin:0'>"
            "<p>Audio Flashcard is already running.</p>"
            "<button onclick='pywebview.api.close()' style='padding:6px 24px'>OK</button></body>"
        ),
        js_api=_CloseApi(),
        width=360,
        height=160,
        resizable=False,
    )
    webview.start(gui=_gui_backend())


class _CloseApi:
    def close(self) -> None:
        import webview

        for w in list(webview.windows):
            w.destroy()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="audioflash")
    ap.add_argument("--dev", action="store_true", help="load the Vite dev server and enable devtools")
    args = ap.parse_args(argv)

    # 1. Data dir.
    paths = default_paths()
    paths.ensure()
    # 2. Single instance (OS releases the lock if the process dies).
    lock = FileLock(str(paths.lock_file), timeout=0)
    try:
        lock.acquire()
    except Timeout:
        _already_running()
        return 1
    try:
        paths.clean_partial_downloads()
        # 3. Logging.
        _setup_logging(paths, args.dev)
        log.info("starting (dev=%s) data_dir=%s", args.dev, paths.root)
        # 4. Backup before migrations.
        database.backup(paths.db, paths.backups)
        # 5. Migrate.
        conn = database.connect(paths.db)
        try:
            database.migrate(conn)
        finally:
            conn.close()

        import webview

        from .bridge import Api
        from .core import Core

        core = Core(paths)
        # 6. Background loaders (never block the window).
        core.start_background()

        # 7. Window.
        api = Api(core)
        if args.dev:
            window = webview.create_window(config.WINDOW_TITLE, url=config.DEV_SERVER_URL, js_api=api, min_size=config.MIN_WINDOW_SIZE, width=1100, height=760)
        else:
            window = webview.create_window(config.WINDOW_TITLE, html=_frontend_html(), js_api=api, min_size=config.MIN_WINDOW_SIZE, width=1100, height=760)

        def file_dialog() -> str | None:
            res = window.create_file_dialog(
                webview.FileDialog.OPEN,
                allow_multiple=False,
                file_types=("Flashcard text files (*.csv;*.tsv;*.txt)", "All files (*.*)"),
            )
            if not res:
                return None
            return res[0] if isinstance(res, (list, tuple)) else str(res)

        core.file_dialog = file_dialog
        window.events.closing += lambda *a: core.shutdown()
        try:
            webview.start(gui=_gui_backend(), debug=args.dev, private_mode=True)
        finally:
            core.shutdown()
        log.info("exited cleanly")
        return 0
    finally:
        lock.release()


if __name__ == "__main__":
    raise SystemExit(main())
