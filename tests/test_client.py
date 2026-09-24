import json
import time

import runboard
from runboard import config
from runboard.client import Run, sync
from runboard.server import Server
from runboard.storage import Storage

from helpers import http_get, wait_for


def test_client_http_mode(server):
    run = Run("proj", name="exp", config={"lr": 0.1}, server=f"http://127.0.0.1:{server.port}", token="secret", flush_interval=0.05)
    for i in range(10):
        run.log({"loss": 1.0 / (i + 1), "acc": i / 10})
    run.finish()
    _, body = http_get(server, "/api/runs")
    runs = json.loads(body)
    assert runs[0]["name"] == "exp" and runs[0]["status"] == "finished" and runs[0]["config"] == {"lr": 0.1}
    _, body = http_get(server, f"/api/metrics?project=proj&run={run.run_id}&offset=0")
    data = json.loads(body)
    assert [r["_step"] for r in data["rows"]] == list(range(10))
    _, body = http_get(server, f"/api/metrics?project=proj&run={run.run_id}&offset={data['offset']}")
    assert json.loads(body)["rows"] == []


def test_client_discovers_server_from_config(server):
    config.write_server_info({"url": f"http://127.0.0.1:{server.port}", "token": "secret"})
    r = runboard.init("auto", flush_interval=0.05)
    assert r.mode == "http"
    runboard.log({"x": 1})
    runboard.finish()
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


def test_large_backlog_is_sent_in_chunks(server):
    run = Run("p", server=f"http://127.0.0.1:{server.port}", token="secret", flush_interval=60, chunk_size=7)
    for i in range(100):
        run.log({"v": i})
    run.finish()
    rows, _ = server.storage.read_rows("p", run.run_id)
    assert [r["v"] for r in rows] == list(range(100))
    assert server.storage.read_meta("p", run.run_id)["status"] == "finished"


def test_rejected_rows_are_dropped_not_retried_forever(server, monkeypatch):
    import runboard.server as srvmod

    monkeypatch.setattr(srvmod, "MAX_BODY", 200)
    run = Run("p", server=f"http://127.0.0.1:{server.port}", token="secret", flush_interval=60, chunk_size=1000)
    for i in range(50):
        run.log({"v": i})
    t0 = time.time()
    run.finish(timeout=5)
    assert time.time() - t0 < 5
    assert not list((config.home() / "spool").glob("*.jsonl"))


def test_nonzero_rank_is_noop(server, monkeypatch):
    monkeypatch.setenv("RANK", "3")
    run = Run("p", server=f"http://127.0.0.1:{server.port}", token="secret", flush_interval=0.05)
    run.log({"v": 1})
    run.finish()
    assert run.mode == "disabled"
    assert server.storage.list_runs() == []


def test_sync_chunks_large_spool(tmp_path):
    run = Run("p", server="http://127.0.0.1:9", token="secret", flush_interval=60)
    for i in range(12000):
        run.log({"v": i})
    run.finish(timeout=0.1)
    srv = Server(tmp_path / "runs3", "secret", host="127.0.0.1", port=0).start()
    try:
        assert sync(server=f"http://127.0.0.1:{srv.port}", token="secret") == 12000
        rows, _ = srv.storage.read_rows("p", run.run_id, max_bytes=10**9)
        assert len(rows) == 12000
    finally:
        srv.stop()


def test_resumed_run_with_same_id_is_not_dropped(server):
    url = f"http://127.0.0.1:{server.port}"
    for part in range(2):
        run = Run("p", run_id="resume-me", server=url, token="secret", flush_interval=0.05)
        for i in range(5):
            run.log({"v": part * 5 + i}, step=part * 5 + i)
        run.finish()
    rows, _ = server.storage.read_rows("p", "resume-me")
    assert [r["v"] for r in rows] == list(range(10))


def test_unexpected_send_errors_never_reach_user_code(tmp_path):
    import http.client

    class Flaky:
        calls = 0

        def send(self, project, run_id, meta, rows):
            Flaky.calls += 1
            if Flaky.calls <= 2:
                raise http.client.IncompleteRead(b"", 10)
            self.got = rows

    run = Run("p", dir=str(tmp_path / "x"), flush_interval=0.05)
    sink = Flaky()
    run._sink = sink
    run.log({"v": 1})
    run.finish(timeout=5)
    assert [r["v"] for r in sink.got] == [1]
