import json
import os
import shlex
import signal
import subprocess
import sys
import time

import pytest

from helpers import wait_for
from runboard.client import Run, sync
from runboard.runner import _pipe, build_config, default_name


CHILD = """
import json, sys
with open("metrics.jsonl", "a") as f:
    for step in range(3):
        f.write(json.dumps({"step": step, "loss": 1.0 / (step + 1)}) + "\\n")
print("child output", flush=True)
sys.exit(int(sys.argv[1]))
"""

SLEEPER = """
import json, time
with open("metrics.jsonl", "a") as f:
    f.write(json.dumps({"step": 0, "loss": 1.0}) + "\\n")
open("ready", "w").close()
time.sleep(60)
"""

PRINTER = """
for step in range(3):
    print(f"step={step} loss={1.0 / (step + 1)}", flush=True)
"""


def _env(server):
    return dict(os.environ, RUNBOARD_SERVER=f"http://127.0.0.1:{server.port}", RUNBOARD_TOKEN="secret")


def _wrap_args(*args):
    return [sys.executable, "-m", "runboard.cli", "run", "--project", "p", "--run-id", "job", *args]


def _wrap(tmp_path, server, *args):
    (tmp_path / "child.py").write_text(CHILD)
    return subprocess.run(_wrap_args(*args), cwd=tmp_path, env=_env(server), capture_output=True, text=True, timeout=60)


@pytest.mark.parametrize("code,status", [(0, "finished"), (3, "crashed")])
def test_run_streams_files_and_keeps_exit_code(tmp_path, server, code, status):
    result = _wrap(tmp_path, server, "--", sys.executable, "child.py", str(code))
    assert result.returncode == code
    assert "child output" in result.stdout
    assert f"{status}: logged 3 rows to p/job" in result.stderr
    rows, _ = server.storage.read_rows("p", "job")
    assert [r["loss"] for r in rows] == [1.0, 0.5, pytest.approx(1 / 3)]
    meta = server.storage.read_meta("p", "job")
    assert meta["status"] == status and meta["config"]["source"] == "JSONL/CSV"


def test_run_forwards_sigterm_and_marks_killed(tmp_path, server):
    (tmp_path / "sleeper.py").write_text(SLEEPER)
    proc = subprocess.Popen(_wrap_args("--", sys.executable, "sleeper.py"), cwd=tmp_path, env=_env(server), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    assert wait_for(lambda: (tmp_path / "ready").exists(), timeout=30)
    proc.send_signal(signal.SIGTERM)
    proc.communicate(timeout=60)
    assert proc.returncode == 128 + signal.SIGTERM
    assert server.storage.read_meta("p", "job")["status"] == "killed"
    assert [r["loss"] for r in server.storage.read_rows("p", "job")[0]] == [1.0]


def test_run_reports_missing_command(tmp_path, server):
    result = _wrap(tmp_path, server, "--", "definitely-not-a-command-xyz")
    assert result.returncode == 127
    assert "cannot start definitely-not-a-command-xyz" in result.stderr
    assert server.storage.read_meta("p", "job")["status"] == "crashed"


def test_run_parses_output_when_no_files_are_written(tmp_path, server):
    (tmp_path / "printer.py").write_text(PRINTER)
    result = _wrap(tmp_path, server, "--", sys.executable, "printer.py")
    assert result.returncode == 0 and "step=2" in result.stdout
    assert [r["_step"] for r in server.storage.read_rows("p", "job")[0]] == [0, 1, 2]


def test_run_on_a_nonzero_rank_only_runs_the_command(tmp_path, server, monkeypatch):
    monkeypatch.setenv("SLURM_PROCID", "1")
    result = _wrap(tmp_path, server, "--", sys.executable, "child.py", "0")
    assert result.returncode == 0 and "child output" in result.stdout
    assert server.storage.list_runs() == []


def test_run_without_a_command_is_a_usage_error(tmp_path, server):
    result = _wrap(tmp_path, server)
    assert result.returncode == 2 and "nothing to run" in result.stderr


def test_pipe_keeps_draining_when_output_is_closed():
    class Closed:
        def write(self, data):
            raise BrokenPipeError()

        def flush(self):
            pass

    class Collector:
        def __init__(self):
            self.data = []

        def feed(self, name, data):
            self.data.append((name, data))

    read_end, write_end = os.pipe()
    os.write(write_end, b"step=1 loss=0.5\n")
    os.close(write_end)
    collector = Collector()
    _pipe(os.fdopen(read_end, "rb"), Closed(), collector, "stdout")
    assert collector.data == [("stdout", b"step=1 loss=0.5\n")]


def test_build_config_and_default_name(tmp_path):
    (tmp_path / "hp.json").write_text('{"lr": 0.1}')
    config = build_config(["python", "train.py"], str(tmp_path / "hp.json"), ["batch=32", "opt=adam", "bad"])
    assert config["lr"] == 0.1 and config["batch"] == 32 and config["opt"] == "adam" and "bad" not in config
    assert config["command"] == ["python", "train.py"]
    assert default_name(["torchrun", "--nproc_per_node", "8", "train.py"]).startswith("train-")
    assert default_name(["python", "-m", "pkg.trainer"]).startswith("trainer-")


def test_watch_once_twice_continues_one_run_without_duplicates(tmp_path, server, monkeypatch):
    from runboard.cli import main

    monkeypatch.setenv("RUNBOARD_SERVER", f"http://127.0.0.1:{server.port}")
    monkeypatch.setenv("RUNBOARD_TOKEN", "secret")
    logs = tmp_path / "out"
    logs.mkdir()
    (logs / "metrics.jsonl").write_text('{"step": 1, "loss": 1}\n')
    main(["watch", str(logs), "--once", "--project", "p"])
    created = server.storage.list_runs()[0]["created"]
    with open(logs / "metrics.jsonl", "a") as f:
        f.write('{"step": 2, "loss": 2}\n')
    main(["watch", str(logs), "--once", "--project", "p"])
    runs = server.storage.list_runs()
    assert len(runs) == 1 and runs[0]["status"] == "finished" and runs[0]["created"] == created
    assert [r["loss"] for r in server.storage.read_rows("p", runs[0]["run_id"])[0]] == [1.0, 2.0]


def test_watch_reads_a_slurm_output_file(tmp_path, server, monkeypatch):
    from runboard.cli import main

    monkeypatch.setenv("RUNBOARD_SERVER", f"http://127.0.0.1:{server.port}")
    monkeypatch.setenv("RUNBOARD_TOKEN", "secret")
    (tmp_path / "slurm-1.out").write_text("loading\nstep=1 loss=0.5\n")
    main(["watch", "--stdout-file", str(tmp_path / "slurm-1.out"), "--once", "--project", "p", "--run-id", "slurm"])
    assert [r["loss"] for r in server.storage.read_rows("p", "slurm")[0]] == [0.5]


def test_watch_rejects_missing_paths(tmp_path):
    from runboard.cli import main

    with pytest.raises(SystemExit) as e:
        main(["watch", str(tmp_path / "missing")])
    assert e.value.code == 2
    with pytest.raises(SystemExit) as e:
        main(["watch"])
    assert e.value.code == 2


STUBBORN = """
import signal, time
count = []
signal.signal(signal.SIGTERM, lambda *a: count.append(1))
open("ready", "w").close()
end = time.time() + 3
while time.time() < end:
    time.sleep(0.05)
open("terms", "w").write(str(len(count)))
"""

LOUD = """
import sys
for i in range(20000):
    print("line", i)
sys.exit(3)
"""


def test_watch_after_an_outage_keeps_spooled_rows(tmp_path, server, monkeypatch):
    from runboard.cli import main

    finish = Run.finish
    monkeypatch.setattr(Run, "finish", lambda self, status="finished", timeout=10.0: finish(self, status, 0.2))
    logs = tmp_path / "out"
    logs.mkdir()
    (logs / "metrics.jsonl").write_text("".join(json.dumps({"step": s, "loss": float(s)}) + "\n" for s in range(5)))
    monkeypatch.setenv("RUNBOARD_TOKEN", "secret")
    monkeypatch.setenv("RUNBOARD_SERVER", "http://127.0.0.1:9")
    main(["watch", str(logs), "--once", "--project", "p"])
    with open(logs / "metrics.jsonl", "a") as f:
        f.write("".join(json.dumps({"step": s, "loss": float(s)}) + "\n" for s in range(5, 8)))
    monkeypatch.setenv("RUNBOARD_SERVER", f"http://127.0.0.1:{server.port}")
    main(["watch", str(logs), "--once", "--project", "p"])
    sync(server=f"http://127.0.0.1:{server.port}", token="secret")
    run_id = server.storage.list_runs()[0]["run_id"]
    assert sorted(r["_step"] for r in server.storage.read_rows("p", run_id)[0]) == list(range(8))


def test_watch_interrupt_keeps_rows_read_so_far(tmp_path, server, monkeypatch):
    from runboard.cli import main
    from runboard.follow.jsonl import JsonlFollower

    monkeypatch.setenv("RUNBOARD_SERVER", f"http://127.0.0.1:{server.port}")
    monkeypatch.setenv("RUNBOARD_TOKEN", "secret")
    logs = tmp_path / "out"
    logs.mkdir()
    (logs / "metrics.jsonl").write_text("".join(json.dumps({"step": s, "loss": float(s)}) + "\n" for s in range(3)))
    original = JsonlFollower.poll

    def poll(self):
        records = original(self)
        if records:
            os.kill(os.getpid(), signal.SIGINT)
        return records

    monkeypatch.setattr(JsonlFollower, "poll", poll)
    main(["watch", str(logs), "--project", "p", "--run-id", "w"])
    assert [r["_step"] for r in server.storage.read_rows("p", "w")[0]] == [0, 1, 2]
    assert server.storage.read_meta("p", "w")["status"] == "killed"


def test_run_forwards_sigterm_once(tmp_path, server):
    (tmp_path / "stubborn.py").write_text(STUBBORN)
    proc = subprocess.Popen(_wrap_args("--", sys.executable, "stubborn.py"), cwd=tmp_path, env=_env(server), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    assert wait_for(lambda: (tmp_path / "ready").exists(), timeout=30)
    proc.send_signal(signal.SIGTERM)
    time.sleep(0.3)
    proc.send_signal(signal.SIGTERM)
    proc.communicate(timeout=60)
    assert (tmp_path / "terms").read_text() == "1"


def test_run_keeps_exit_code_when_output_is_cut_short(tmp_path, server):
    (tmp_path / "loud.py").write_text(LOUD)
    command = " ".join(shlex.quote(part) for part in _wrap_args("--", sys.executable, "loud.py")) + " | head -1; exit ${PIPESTATUS[0]}"
    result = subprocess.run(["bash", "-c", command], cwd=tmp_path, env=_env(server), capture_output=True, text=True, timeout=60)
    assert result.returncode == 3
    assert "Exception ignored" not in result.stderr


def test_run_warns_about_missing_watch_and_source_paths(tmp_path, server):
    result = _wrap(tmp_path, server, "--watch", "nope", "--source", str(tmp_path), "--", sys.executable, "child.py", "0")
    assert "--watch nope does not exist" in result.stderr
    assert "is a directory" in result.stderr


def test_run_with_source_ignores_other_files(tmp_path, server):
    (tmp_path / "train.log").write_text("step=1 loss=0.5\n")
    result = _wrap(tmp_path, server, "--source", "train.log", "--", sys.executable, "child.py", "0")
    assert result.returncode == 0
    assert [r["loss"] for r in server.storage.read_rows("p", "job")[0]] == [0.5]
