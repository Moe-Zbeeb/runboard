import json
import time
import urllib.error
import urllib.request

import pytest

import logtool
from logtool import config
from logtool.client import Run, sync
from logtool.server import Server
from logtool.storage import Storage


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("LOGTOOL_HOME", str(tmp_path / "home"))
    for k in ("LOGTOOL_SERVER", "LOGTOOL_TOKEN", "LOGTOOL_DIR"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.chdir(tmp_path)


@pytest.fixture
def server(tmp_path):
    srv = Server(tmp_path / "runs", "secret", host="127.0.0.1", port=0).start()
    yield srv
    srv.stop()


def get(srv, path, token="secret"):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    req = urllib.request.Request(f"http://127.0.0.1:{srv.port}{path}", headers=headers)
    with urllib.request.urlopen(req, timeout=5) as r:
        return r.status, r.read()


def wait_for(fn, timeout=5):
    end = time.time() + timeout
    while time.time() < end:
        v = fn()
        if v:
            return v
        time.sleep(0.05)
    return fn()


def test_storage_roundtrip_and_offsets(tmp_path):
    s = Storage(tmp_path)
    s.append_rows("p", "r1", [{"_step": 0, "loss": 1.0}, {"_step": 1, "loss": float("nan"), "bad": "x"}])
    rows, off = s.read_rows("p", "r1")
    assert rows == [{"_step": 0, "loss": 1.0}, {"_step": 1, "loss": None}]
    s.append_rows("p", "r1", [{"_step": 2, "loss": 0.5}])
    rows2, off2 = s.read_rows("p", "r1", off)
    assert rows2 == [{"_step": 2, "loss": 0.5}] and off2 > off
    with open(tmp_path / "p" / "r1" / "metrics.jsonl", "a") as f:
        f.write('{"_step": 3, "lo')
    assert s.read_rows("p", "r1", off2) == ([], off2)
    runs = s.list_runs()
    assert runs[0]["run_id"] == "r1" and runs[0]["project"] == "p"


def test_storage_rejects_traversal(tmp_path):
    with pytest.raises(ValueError):
        Storage(tmp_path).append_rows("..", "x", [{"a": 1}])


def test_server_requires_token(server):
    with pytest.raises(urllib.error.HTTPError) as e:
        get(server, "/api/runs", token=None)
    assert e.value.code == 401
    with pytest.raises(urllib.error.HTTPError):
        get(server, "/api/runs", token="wrong")
    assert get(server, "/api/runs")[0] == 200


def test_server_cookie_login(server):
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *a, **k):
            return None

    opener = urllib.request.build_opener(NoRedirect)
    with pytest.raises(urllib.error.HTTPError) as e:
        opener.open(f"http://127.0.0.1:{server.port}/?token=secret", timeout=5)
    assert e.value.code == 302
    cookie = e.value.headers["Set-Cookie"].split(";")[0]
    req = urllib.request.Request(f"http://127.0.0.1:{server.port}/", headers={"Cookie": cookie})
    with urllib.request.urlopen(req, timeout=5) as r:
        assert b"<html" in r.read().lower()


def test_client_http_mode(server):
    run = Run("proj", name="exp", config={"lr": 0.1}, server=f"http://127.0.0.1:{server.port}", token="secret", flush_interval=0.05)
    for i in range(10):
        run.log({"loss": 1.0 / (i + 1), "acc": i / 10})
    run.finish()
    _, body = get(server, "/api/runs")
    runs = json.loads(body)
    assert runs[0]["name"] == "exp" and runs[0]["status"] == "finished" and runs[0]["config"] == {"lr": 0.1}
    _, body = get(server, f"/api/metrics?project=proj&run={run.run_id}&offset=0")
    data = json.loads(body)
    assert [r["_step"] for r in data["rows"]] == list(range(10))
    _, body = get(server, f"/api/metrics?project=proj&run={run.run_id}&offset={data['offset']}")
    assert json.loads(body)["rows"] == []


def test_client_discovers_server_from_config(server):
    config.write_server_info({"url": f"http://127.0.0.1:{server.port}", "token": "secret"})
    r = logtool.init("auto", flush_interval=0.05)
    assert r.mode == "http"
    logtool.log({"x": 1})
    logtool.finish()
    assert wait_for(lambda: server.storage.read_rows("auto", r.run_id)[0])


def test_client_file_mode(tmp_path):
    run = Run("p", dir=str(tmp_path / "shared"), flush_interval=0.05)
    run.log({"loss": 2.0}, step=5)
    run.log({"loss": 1.0})
    run.finish()
    rows, _ = Storage(tmp_path / "shared").read_rows("p", run.run_id)
    assert [(r["_step"], r["loss"]) for r in rows] == [(5, 2.0), (6, 1.0)]


def test_offline_buffer_spool_and_sync(tmp_path):
    run = Run("p", server="http://127.0.0.1:9", token="secret", flush_interval=0.05)
    run.log({"loss": 1.0})
    run.log({"loss": 0.5})
    t0 = time.time()
    run.finish(timeout=0.1)
    assert time.time() - t0 < 10
    spooled = list((tmp_path / "home" / "spool").glob("*.jsonl"))
    assert len(spooled) == 1
    srv = Server(tmp_path / "runs2", "secret", host="127.0.0.1", port=0).start()
    try:
        n = sync(server=f"http://127.0.0.1:{srv.port}", token="secret")
        assert n == 2
        rows, _ = srv.storage.read_rows("p", run.run_id)
        assert [r["loss"] for r in rows] == [1.0, 0.5]
        assert srv.storage.read_meta("p", run.run_id)["status"] == "finished"
        assert not list((tmp_path / "home" / "spool").glob("*.jsonl"))
    finally:
        srv.stop()


def test_buffer_survives_server_outage(tmp_path):
    srv = Server(tmp_path / "runs", "secret", host="127.0.0.1", port=0)
    port = srv.port
    srv.httpd.server_close()
    run = Run("p", server=f"http://127.0.0.1:{port}", token="secret", flush_interval=0.05)
    run.log({"loss": 1.0})
    time.sleep(0.3)
    srv2 = Server(tmp_path / "runs", "secret", host="127.0.0.1", port=port).start()
    try:
        run.log({"loss": 0.5})
        run.finish(timeout=10)
        rows, _ = srv2.storage.read_rows("p", run.run_id)
        assert [r["loss"] for r in rows] == [1.0, 0.5]
    finally:
        srv2.stop()
