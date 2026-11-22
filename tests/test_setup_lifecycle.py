import hashlib
import time

import httpx
import pytest
from filelock import FileLock, Timeout

from audioflash import config
from audioflash.bridge import Api
from audioflash.model_setup import ModelDownloader, custom_model_ids, list_models, model_dir, model_present

from .conftest import install_fake_model_files, small_dir


def fake_files():
    return {name: (b"x" * 10) + name.encode() for name in config.WHISPER_MODELS["small"].files}


@pytest.fixture
def pinned(monkeypatch):
    files = fake_files()
    spec = config.WHISPER_MODELS["small"]._replace(
        files={n: (len(b), hashlib.sha256(b).hexdigest()) for n, b in files.items()}
    )
    monkeypatch.setitem(config.WHISPER_MODELS, "small", spec)
    return files


def transport_for(files, corrupt=None, hosts=None, revision=None):
    revision = revision or config.WHISPER_MODELS["small"].revision

    def handler(req: httpx.Request):
        if hosts is not None:
            hosts.append(req.url.host)
        name = req.url.path.rsplit("/", 1)[-1]
        assert revision in req.url.path
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
    dl = ModelDownloader(paths.models, on_done=done.append, transport=transport_for(pinned, hosts=hosts))
    assert dl.start("small")
    st = wait(dl)
    assert st["state"] == "done" and st["bytes_done"] == st["bytes_total"] and st["model"] == "small"
    assert model_present(small_dir(paths))
    assert done == ["small"]
    assert set(hosts) == {"huggingface.co"}
    assert not [p for p in paths.models.iterdir() if p.name.startswith(config.WHISPER_TMP_PREFIX)]


def test_download_other_model_uses_its_repo_and_folder(paths, monkeypatch):
    files = {n: b"turbo-" + n.encode() for n in config.WHISPER_MODELS["large-v3-turbo"].files}
    spec = config.WHISPER_MODELS["large-v3-turbo"]._replace(
        files={n: (len(b), hashlib.sha256(b).hexdigest()) for n, b in files.items()}
    )
    monkeypatch.setitem(config.WHISPER_MODELS, "large-v3-turbo", spec)
    urls = []
    base = transport_for(files, revision=spec.revision)
    transport = httpx.MockTransport(lambda req: urls.append(str(req.url)) or base.handle_request(req))
    dl = ModelDownloader(paths.models, transport=transport)
    dl.start("large-v3-turbo")
    assert wait(dl)["state"] == "done"
    target = model_dir(paths.models, "large-v3-turbo")
    assert target.name == "faster-whisper-large-v3-turbo" and model_present(target)
    assert all(spec.repo in u for u in urls)
    assert "preprocessor_config.json" in {u.rsplit("/", 1)[-1] for u in urls}


def test_checksum_mismatch_leaves_nothing(paths, pinned):
    dl = ModelDownloader(paths.models, transport=transport_for(pinned, corrupt="model.bin"))
    dl.start("small")
    st = wait(dl)
    assert st["state"] == "error" and "checksum" in st["error"]
    assert not small_dir(paths).exists()
    assert list(paths.models.iterdir()) == []


def test_list_models_reports_installed_and_custom(paths):
    install_fake_model_files(model_dir(paths.models, "large-v3-turbo"), "large-v3-turbo")
    custom = paths.models / "kotoba-whisper-v2.0-faster"
    custom.mkdir(parents=True)
    for n in config.WHISPER_REQUIRED_FILES:
        (custom / n).write_bytes(b"x")
    (paths.models / "not-a-model").mkdir()
    (paths.models / f"{config.WHISPER_TMP_PREFIX}x").mkdir()
    by_id = {m["id"]: m for m in list_models(paths.models)}
    assert list(by_id) == [*config.WHISPER_MODELS, "kotoba-whisper-v2.0-faster"]
    assert by_id["large-v3-turbo"]["installed"] and not by_id["small"]["installed"]
    assert by_id["kotoba-whisper-v2.0-faster"]["custom"] and by_id["kotoba-whisper-v2.0-faster"]["installed"]
    assert custom_model_ids(paths.models) == ["kotoba-whisper-v2.0-faster"]


def test_switching_model_reloads_from_its_folder(core, paths):
    turbo = model_dir(paths.models, "large-v3-turbo")
    install_fake_model_files(turbo, "large-v3-turbo")
    api = Api(core)
    r = api.update_settings({"whisper_model": "large-v3-turbo"})
    assert r["ok"] and r["data"]["whisper_model"] == "large-v3-turbo"
    for _ in range(200):
        if core.whisper.ready and core.whisper.loaded_dir == turbo:
            break
        time.sleep(0.01)
    assert core.whisper.loaded_dir == turbo and core.whisper.ready
    assert api.get_status()["data"]["whisper_model"] == "large-v3-turbo"


def test_switching_to_missing_model_needs_download(core, paths):
    api = Api(core)
    assert api.update_settings({"whisper_model": "medium"})["ok"]
    for _ in range(200):
        if core.whisper.snapshot()["state"] == "missing":
            break
        time.sleep(0.01)
    st = api.get_status()["data"]
    assert st["whisper_state"] == "missing" and st["whisper_model_downloadable"]
    assert any("medium" in step for step in st["setup_steps"])


def test_unknown_model_rejected(core, paths):
    api = Api(core)
    assert api.update_settings({"whisper_model": "../../etc"})["error"]["code"] == "invalid_input"
    assert api.update_settings({"whisper_model": "nope"})["error"]["code"] == "invalid_input"
    assert api.download_whisper_model("nope")["error"]["code"] == "invalid_input"


def test_partial_download_dir_cleaned_on_startup(paths):
    tmp = paths.models / f"{config.WHISPER_TMP_PREFIX}abc"
    tmp.mkdir()
    (tmp / "model.bin").write_bytes(b"partial")
    paths.clean_partial_downloads()
    assert not tmp.exists()


def test_download_busy_via_core(core, monkeypatch):
    monkeypatch.setattr(core.downloader, "start", lambda model_id: False)
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
