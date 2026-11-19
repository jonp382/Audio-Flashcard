import hashlib
import time

import httpx
import pytest
from filelock import FileLock, Timeout

from audioflash import config
from audioflash.model_setup import ModelDownloader, model_present


def fake_files():
    return {name: (b"x" * 10) + name.encode() for name in config.WHISPER_FILES}


@pytest.fixture
def pinned(monkeypatch):
    files = fake_files()
    monkeypatch.setattr(
        config, "WHISPER_FILES", {n: (len(b), hashlib.sha256(b).hexdigest()) for n, b in files.items()}
    )
    return files


def transport_for(files, corrupt=None, hosts=None):
    def handler(req: httpx.Request):
        if hosts is not None:
            hosts.append(req.url.host)
        name = req.url.path.rsplit("/", 1)[-1]
        assert config.WHISPER_REVISION in req.url.path
        body = files[name]
        if name == corrupt:
            body = body[:-1] + b"!"
        return httpx.Response(200, content=body)

    return httpx.MockTransport(handler)


def wait(dl):
    for _ in range(500):
        if dl.status()["state"] != "downloading":
            return dl.status()
        time.sleep(0.01)
    raise AssertionError("download did not finish")


def test_download_success_renames_into_place(paths, pinned):
    done = []
    hosts = []
    dl = ModelDownloader(paths.models, on_done=lambda: done.append(1), transport=transport_for(pinned, hosts=hosts))
    assert dl.start()
    st = wait(dl)
    assert st["state"] == "done" and st["bytes_done"] == st["bytes_total"]
    assert model_present(paths.whisper_model)
    assert done == [1]
    assert set(hosts) == {"huggingface.co"}
    assert not [p for p in paths.models.iterdir() if p.name.startswith(config.WHISPER_TMP_PREFIX)]


def test_checksum_mismatch_leaves_nothing(paths, pinned):
    dl = ModelDownloader(paths.models, transport=transport_for(pinned, corrupt="model.bin"))
    dl.start()
    st = wait(dl)
    assert st["state"] == "error" and "checksum" in st["error"]
    assert not paths.whisper_model.exists()
    assert list(paths.models.iterdir()) == []


def test_partial_download_dir_cleaned_on_startup(paths):
    tmp = paths.models / f"{config.WHISPER_TMP_PREFIX}abc"
    tmp.mkdir()
    (tmp / "model.bin").write_bytes(b"partial")
    paths.clean_partial_downloads()
    assert not tmp.exists()


def test_download_busy_via_core(core, monkeypatch):
    monkeypatch.setattr(core.downloader, "start", lambda: False)
    from audioflash.bridge import Api

    assert Api(core).download_whisper_model()["error"]["code"] == "busy"


def test_single_instance_lock(paths):
    first = FileLock(str(paths.lock_file), timeout=0)
    first.acquire()
    try:
        # filelock is re-entrant per object/thread, so test from another thread like a 2nd process would.
        import threading

        result = []

        def try_second():
            second = FileLock(str(paths.lock_file), timeout=0, thread_local=False, is_singleton=False)
            try:
                second.acquire()
                result.append("acquired")
                second.release()
            except Timeout:
                result.append("timeout")

        t = threading.Thread(target=try_second)
        t.start()
        t.join()
        assert result == ["timeout"]
    finally:
        first.release()
