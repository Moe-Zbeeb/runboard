import json
import os

from helpers import tf_event, tf_record
from runboard.client import Run
from runboard.runner import Streamer
from runboard.storage import Storage


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def _rows(tmp_path, run):
    return Storage(tmp_path / "store").read_rows(run.project, run.run_id)[0]


def _run(tmp_path, run_id=None):
    return Run("p", run_id=run_id, dir=str(tmp_path / "store"), flush_interval=60)


def test_streamer_follows_best_format_and_switches_up(tmp_path):
    work = tmp_path / "work"
    (work / "logs").mkdir(parents=True)
    (work / "logs/metrics.jsonl").write_text('{"step": 1, "loss": 0.5}\n')
    run = _run(tmp_path)
    clock = Clock()
    streamer = Streamer(run, roots=[work], config={"command": ["x"]}, clock=clock)
    assert streamer.step() == 1 and streamer.kind == "table"
    (work / "runs").mkdir()
    (work / "runs/events.out.tfevents.1.h").write_bytes(tf_record(tf_event(2, 5.0, scalars=[("loss", 0.25)])))
    clock.now += 6
    assert streamer.step() == 1 and streamer.kind == "tensorboard"
    run.finish()
    assert [(r["_step"], r["loss"]) for r in _rows(tmp_path, run)] == [(1, 0.5), (2, 0.25)]
    assert Storage(tmp_path / "store").read_meta("p", run.run_id)["config"] == {"command": ["x"], "source": "TensorBoard"}


def test_explicit_sources_disable_discovery(tmp_path):
    (tmp_path / "events.out.tfevents.1.h").write_bytes(tf_record(tf_event(1, 1.0, scalars=[("tb", 1.0)])))
    log = tmp_path / "train.log"
    log.write_text("step=1 loss=0.5\n")
    run = _run(tmp_path)
    streamer = Streamer(run, roots=[tmp_path], sources=[("stdout", log)])
    assert streamer.drain() == 1 and streamer.kind == "explicit"
    run.finish()
    assert [r.get("loss") for r in _rows(tmp_path, run)] == [0.5]


def test_streamer_ignores_files_older_than_since(tmp_path):
    work = tmp_path / "work"
    work.mkdir()
    old = work / "dataset.jsonl"
    old.write_text('{"step": 1, "label": 3}\n')
    os.utime(old, (1000, 1000))
    run = _run(tmp_path)
    streamer = Streamer(run, roots=[work], since=2000)
    assert streamer.drain() == 0 and streamer.kind is None
    run.finish()


def test_stdout_auto_waits_for_structured_files(tmp_path):
    (tmp_path / "empty").mkdir()
    run = _run(tmp_path)
    clock = Clock()
    streamer = Streamer(run, roots=[tmp_path / "empty"], stdout="auto", clock=clock)
    streamer.feed("stdout", b"step=1 loss=0.5\n")
    assert streamer.step() == 0
    clock.now += 61
    assert streamer.step() == 1
    run.finish()


def test_stdout_auto_parses_output_when_a_short_job_exits(tmp_path):
    (tmp_path / "empty").mkdir()
    run = _run(tmp_path)
    streamer = Streamer(run, roots=[tmp_path / "empty"], stdout="auto", clock=Clock())
    streamer.feed("stderr", b"step=1 loss=0.5")
    assert streamer.drain() == 1
    run.finish()


def test_stdout_is_discarded_once_files_are_found(tmp_path):
    (tmp_path / "metrics.jsonl").write_text('{"step": 1, "loss": 0.5}\n')
    run = _run(tmp_path)
    clock = Clock()
    streamer = Streamer(run, roots=[tmp_path], stdout="auto", clock=clock)
    streamer.feed("stdout", b"step=9 loss=9\n")
    clock.now += 61
    assert streamer.step() == 1
    assert streamer.drain() == 0
    run.finish()


def test_resumed_streamer_does_not_duplicate_or_drop_rows(tmp_path):
    work = tmp_path / "work"
    work.mkdir()
    log = work / "metrics.jsonl"
    state = tmp_path / "state.json"
    log.write_text('{"step": 1, "loss": 1}\n')
    first_run = _run(tmp_path, run_id="r")
    first = Streamer(first_run, roots=[work], state_file=state)
    first.step()
    first_run._flush_once()
    assert first.checkpoint()
    with open(log, "a") as f:
        f.write('{"step": 2, "loss": 2}\n')
    first.step()
    first_run._flush_once()
    first_run.finish("killed")
    with open(log, "a") as f:
        f.write('{"step": 3, "loss": 3}\n')
    second_run = _run(tmp_path, run_id="r")
    second = Streamer(second_run, roots=[work], state_file=state)
    second.drain()
    second_run.finish()
    second.save()
    assert [r["loss"] for r in _rows(tmp_path, second_run)] == [1.0, 2.0, 3.0]
    saved = json.loads(state.read_text())
    assert saved["nonce"] == first.nonce
    assert saved["sources"][str(log.resolve())]["count"] == 3
    assert state.stat().st_mode & 0o777 == 0o600
