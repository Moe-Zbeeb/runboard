import json
import time
from pathlib import Path
from urllib.parse import quote

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


def test_configure_saves_verified_server(server, capsys):
    from runboard.cli import main

    url = f"http://127.0.0.1:{server.port}"
    main(["configure", url + "/", "--token", "secret"])
    assert config.read_server_info() == {"url": url, "token": "secret"}
    output = capsys.readouterr().out
    assert f"dashboard: {url}/?token={quote('secret')}" in output
    main(["url"])
    assert capsys.readouterr().out.strip() == f"{url}/?token=secret"


def test_cloudflare_dashboard_matches_python_package():
    root = Path(__file__).parents[1]
    packaged = root / "src" / "runboard" / "static"
    cloudflare = root / "cloudflare" / "public"
    assert {path.name for path in packaged.iterdir()} == {path.name for path in cloudflare.iterdir()}
    for path in packaged.iterdir():
        assert path.read_bytes() == (cloudflare / path.name).read_bytes()


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


def test_missing_or_corrupt_spool_does_not_block_delivery(server):
    run = Run("p", server=f"http://127.0.0.1:{server.port}", token="secret", flush_interval=60)
    spool = config.home() / "spool"
    spool.mkdir(parents=True)
    corrupt = spool / "corrupt.jsonl"
    corrupt.write_text('{"rows": [')
    run._pending_spools.extend([spool / "gone.jsonl", corrupt])
    run.log({"v": 1})
    run.finish(timeout=3)
    rows, _ = server.storage.read_rows("p", run.run_id)
    assert [r["v"] for r in rows] == [1]
    assert run._pending_spools == []
    assert not corrupt.exists()


def _strict_json(data):
    def reject(value):
        raise ValueError(f"non-standard JSON constant {value}")

    return json.loads(data, parse_constant=reject)


def test_non_finite_config_values_stay_valid_json(server):
    cfg = {"max_grad_norm": float("inf"), "floor": float("-inf"), "nested": [float("nan"), 1.5], "t": (1, 2)}
    run = Run("p", config=cfg, server=f"http://127.0.0.1:{server.port}", token="secret", flush_interval=0.05)
    run.log({"v": 1})
    run.finish()
    _, body = http_get(server, "/api/runs")
    got = _strict_json(body)[0]["config"]
    assert got == {"max_grad_norm": "inf", "floor": "-inf", "nested": ["nan", 1.5], "t": [1, 2]}
    assert server.storage.read_rows("p", run.run_id)[0][0]["v"] == 1.0


def test_storage_meta_is_valid_json(tmp_path):
    s = Storage(tmp_path)
    s.update_meta("p", "r", {"config": {"x": float("nan")}})
    assert _strict_json((tmp_path / "p" / "r" / "meta.json").read_text())["config"] == {"x": "nan"}


def test_default_flush_interval_depends_on_backend(server, tmp_path):
    http_run = Run("p", server=f"http://127.0.0.1:{server.port}", token="secret")
    file_run = Run("p", dir=str(tmp_path / "files"))
    explicit = Run("p", server=f"http://127.0.0.1:{server.port}", token="secret", flush_interval=0.5)
    try:
        assert (http_run.flush_interval, file_run.flush_interval, explicit.flush_interval) == (10.0, 1.0, 0.5)
    finally:
        for run in (http_run, file_run, explicit):
            run.finish()


def test_serve_does_not_replace_hosted_configuration(tmp_path):
    from runboard.cli import _claim_server_info

    hosted = {"url": "https://runboard.example.workers.dev", "token": "cloud"}
    config.write_server_info(hosted)
    assert not _claim_server_info({"url": "http://node:8080", "token": "local", "dir": str(tmp_path)})
    assert config.read_server_info() == hosted
    config.write_server_info({"url": "http://old:8080", "token": "local", "dir": str(tmp_path)})
    assert _claim_server_info({"url": "http://node:8080", "token": "local", "dir": str(tmp_path)})
    assert config.read_server_info()["url"] == "http://node:8080"


def _run_and_terminate(tmp_path, prelude):
    import signal
    import subprocess
    import sys
    import textwrap

    script = tmp_path / "job.py"
    script.write_text(textwrap.dedent(f"""
        import os, signal, sys, time
        {prelude}
        import runboard
        run = runboard.init("p", run_id="term", dir=sys.argv[1])
        for i in range(5):
            runboard.log({{"v": i}})
        print("ready", flush=True)
        while True:
            runboard.log({{"v": 99}})
            time.sleep(0.01)
    """))
    proc = subprocess.Popen([sys.executable, str(script), str(tmp_path / "runs")], stdout=subprocess.PIPE, text=True)
    assert proc.stdout.readline().strip() == "ready"
    proc.send_signal(signal.SIGTERM)
    out, _ = proc.communicate(timeout=30)
    return proc.returncode, out, Storage(tmp_path / "runs")


def test_sigterm_marks_run_killed_and_keeps_signal_exit(tmp_path):
    import signal

    code, _, storage = _run_and_terminate(tmp_path, "")
    assert code == -signal.SIGTERM
    assert storage.read_meta("p", "term")["status"] == "killed"
    assert [r["v"] for r in storage.read_rows("p", "term")[0][:5]] == [0, 1, 2, 3, 4]


def test_sigterm_chains_existing_handler(tmp_path):
    prelude = 'signal.signal(signal.SIGTERM, lambda *a: (print("previous", flush=True), sys.exit(3)))'
    code, out, storage = _run_and_terminate(tmp_path, prelude)
    assert code == 3 and "previous" in out
    assert storage.read_meta("p", "term")["status"] == "killed"


def test_transient_failure_keeps_rows_in_memory_below_max_buffer(tmp_path):
    class Flaky:
        fail = True
        got = []

        def send(self, project, run_id, meta, rows):
            if Flaky.fail:
                raise OSError("down")
            Flaky.got += rows

    run = Run("p", dir=str(tmp_path / "x"), flush_interval=0.05)
    run._sink = Flaky()
    for i in range(10):
        run.log({"v": i})
    time.sleep(0.5)
    assert not list((config.home() / "spool").glob("*.jsonl"))
    Flaky.fail = False
    run.log({"v": 10})
    run.finish(timeout=5)
    assert [r["v"] for r in Flaky.got] == list(range(11))


def test_restore_preserves_order_and_respects_max_buffer(tmp_path):
    run = Run("p", dir=str(tmp_path / "x"), flush_interval=60, max_buffer=3)
    try:
        run._overflow = [{"v": 2}]
        run._rows = [{"v": 3}, {"v": 4}]
        run._restore({}, [{"v": 0}, {"v": 1}])
        assert [r["v"] for r in run._overflow] == [0, 1]
        assert [r["v"] for r in run._rows] == [2, 3, 4]
    finally:
        run._overflow, run._rows = [], []
        run.finish()


def test_finish_drains_many_spools_without_waiting_between_them(server):
    run = Run("p", server=f"http://127.0.0.1:{server.port}", token="secret", flush_interval=60)
    for i in range(6):
        run._pending_spools.append(run._spool({}, [{"v": i, "_step": i, "_seq": i, "_sid": run._sid}]))
    run._seq = 6
    t0 = time.time()
    run.finish(timeout=3)
    assert time.time() - t0 < 3
    rows, _ = server.storage.read_rows("p", run.run_id)
    assert [r["v"] for r in rows] == list(range(6))
    assert not list((config.home() / "spool").glob("*.jsonl"))


def test_background_sender_drains_spools_back_to_back(server):
    run = Run("p", server=f"http://127.0.0.1:{server.port}", token="secret", flush_interval=2)
    for i in range(4):
        run._pending_spools.append(run._spool({}, [{"v": i, "_step": i, "_seq": i, "_sid": run._sid}]))
    run._seq = 4
    assert wait_for(lambda: len(server.storage.read_rows("p", run.run_id)[0]) == 4, timeout=3.5)
    run.finish()
