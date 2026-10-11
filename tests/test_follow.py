import json
import os
import time

import pytest

from runboard.follow import Record, to_record
from runboard.follow.csvfile import CsvFollower
from runboard.follow.discover import best, classify, scan
from runboard.follow.hf_state import HfStateFollower
from runboard.follow.jsonl import JsonlFollower
from runboard.follow.text import StreamFollower, TextFollower, parse_line


def test_to_record_reads_step_time_and_metrics():
    assert to_record({"global_step": 4, "wall_time": 10.5, "loss": "0.5", "name": "x", "_hidden": 1}) == Record(4, 10.5, {"loss": 0.5})
    assert to_record({"step": 1, "note": "text"}) is None


def test_jsonl_follower_flattens_and_waits_for_complete_lines(tmp_path):
    path = tmp_path / "metrics.jsonl"
    path.write_text(json.dumps({"step": 1, "train": {"loss": 0.5}}) + "\nnot json\n" + '{"step": 2, "train": {"loss": 0.4')
    follower = JsonlFollower(path)
    assert follower.poll() == [Record(1, None, {"train/loss": 0.5})]
    with open(path, "a") as f:
        f.write("}}\n")
    assert follower.poll() == [Record(2, None, {"train/loss": 0.4})]
    assert follower.poll() == []


def test_jsonl_follower_resumes_from_state(tmp_path):
    path = tmp_path / "metrics.jsonl"
    path.write_text('{"step": 1, "loss": 1}\n{"step": 2, "loss": 2}\n')
    first = JsonlFollower(path)
    first.poll()
    with open(path, "a") as f:
        f.write('{"step": 3, "loss": 3}\n')
    assert JsonlFollower(path, json.loads(json.dumps(first.state()))).poll() == [Record(3, None, {"loss": 3.0})]


def test_truncated_file_is_read_again_from_start(tmp_path):
    path = tmp_path / "metrics.jsonl"
    path.write_text('{"step": 1, "loss": 1}\n{"step": 2, "loss": 2}\n')
    follower = JsonlFollower(path)
    follower.poll()
    path.write_text('{"step": 1, "loss": 9}\n')
    assert follower.poll() == [Record(1, None, {"loss": 9.0})]


def test_csv_follower_skips_blank_cells(tmp_path):
    path = tmp_path / "metrics.csv"
    path.write_text("epoch,step,train_loss,val_acc\n0,10,0.5,\n0,10,,0.8\n")
    assert CsvFollower(path).poll() == [
        Record(10, None, {"epoch": 0.0, "train_loss": 0.5}),
        Record(10, None, {"epoch": 0.0, "val_acc": 0.8}),
    ]


def test_csv_follower_rereads_header_after_truncation(tmp_path):
    path = tmp_path / "metrics.csv"
    path.write_text("step,loss\n1,0.5\n2,0.4\n")
    follower = CsvFollower(path)
    follower.poll()
    path.write_text("step,acc\n1,0.9\n")
    assert follower.poll() == [Record(1, None, {"acc": 0.9})]


def _write_state(directory, history, mtime):
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "trainer_state.json"
    path.write_text(json.dumps({"global_step": history[-1]["step"], "log_history": history}))
    os.utime(path, (mtime, mtime))


def test_hf_state_follows_newest_checkpoint_without_repeats(tmp_path):
    first = [{"loss": 1.0, "learning_rate": 5e-05, "epoch": 0.5, "step": 10}, {"eval_loss": 0.9, "epoch": 0.5, "step": 10}]
    _write_state(tmp_path / "checkpoint-10", first, time.time() - 100)
    follower = HfStateFollower(tmp_path)
    assert follower.poll() == [
        Record(10, None, {"loss": 1.0, "learning_rate": 5e-05, "epoch": 0.5}),
        Record(10, None, {"eval_loss": 0.9, "epoch": 0.5}),
    ]
    assert follower.poll() == []
    _write_state(tmp_path / "checkpoint-20", first + [{"loss": 0.5, "epoch": 1.0, "step": 20}], time.time())
    assert follower.poll() == [Record(20, None, {"loss": 0.5, "epoch": 1.0})]
    assert HfStateFollower(tmp_path, follower.state()).poll() == []


def test_hf_state_reads_a_direct_file(tmp_path):
    _write_state(tmp_path, [{"loss": 2.0, "step": 1}], time.time())
    assert HfStateFollower(tmp_path / "trainer_state.json").poll() == [Record(1, None, {"loss": 2.0})]


METRIC_LINES = [
    ("{'loss': 0.42, 'grad_norm': 1.5, 'learning_rate': 5e-05, 'epoch': 0.1}", Record(None, None, {"loss": 0.42, "grad_norm": 1.5, "learning_rate": 5e-05, "epoch": 0.1})),
    ("step:12 - actor/loss:0.3 - critic/vf_loss:-0.1", Record(12, None, {"actor/loss": 0.3, "critic/vf_loss": -0.1})),
    ("\x1b[32mINFO\x1b[0m step=7 loss=0.5", Record(7, None, {"loss": 0.5})),
    (" 45%|####5     | 450/1000 [00:10<00:12, 43.2it/s, loss=0.3, lr=1e-4]", Record(None, None, {"loss": 0.3, "lr": 0.0001})),
]


@pytest.mark.parametrize("line,expected", METRIC_LINES)
def test_parse_line_extracts_metrics(line, expected):
    assert parse_line(line) == expected


@pytest.mark.parametrize("line", ["Downloading model: 50%", "x = 5", "Running on http://127.0.0.1:8080", "Saved checkpoint to /tmp/model-3.pt", "version 1.2.3"])
def test_parse_line_ignores_ordinary_output(line):
    assert parse_line(line) is None


def test_stream_follower_handles_partial_lines_and_carriage_returns():
    stream = StreamFollower()
    stream.feed(b"step=1 loss=0.5\rstep=2 lo")
    assert stream.poll() == [Record(1, None, {"loss": 0.5})]
    stream.feed(b"ss=0.25\nstep=3 loss=0.125")
    assert stream.poll() == [Record(2, None, {"loss": 0.25})]
    assert stream.poll(final=True) == [Record(3, None, {"loss": 0.125})]
    assert stream.poll(final=True) == []


def test_text_follower_reads_a_log_file(tmp_path):
    path = tmp_path / "slurm-1.out"
    path.write_text("step=1 loss=0.5\rstep=2 loss=0.25\nloading data\n")
    assert TextFollower(path).poll() == [Record(1, None, {"loss": 0.5}), Record(2, None, {"loss": 0.25})]


def _touch(path, mtime=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("x")
    if mtime is not None:
        os.utime(path, (mtime, mtime))


def test_classify_known_files():
    assert classify("events.out.tfevents.1.host") == "tensorboard"
    assert classify("train.jsonl") == "table"
    assert classify("metrics.csv") == "table"
    assert classify("results.csv") is None
    assert classify("trainer_state.json") == "hf"
    assert classify("train.log") is None


def test_scan_prefers_tensorboard_and_skips_noise(tmp_path):
    _touch(tmp_path / "runs/exp/events.out.tfevents.1.host")
    _touch(tmp_path / "logs/metrics.jsonl")
    _touch(tmp_path / ".hidden/events.out.tfevents.2.host")
    _touch(tmp_path / "node_modules/x/metrics.jsonl")
    _touch(tmp_path / "a/b/c/d/e/events.out.tfevents.3.host")
    _touch(tmp_path / "results.csv")
    found = scan([tmp_path])
    assert found["tensorboard"] == [(tmp_path / "runs/exp/events.out.tfevents.1.host").resolve()]
    assert found["table"] == [(tmp_path / "logs/metrics.jsonl").resolve()]
    assert best(found) == ("tensorboard", found["tensorboard"])


def test_scan_ignores_files_older_than_since(tmp_path):
    _touch(tmp_path / "old/metrics.jsonl", mtime=1000)
    _touch(tmp_path / "new/metrics.jsonl")
    assert scan([tmp_path], since=2000)["table"] == [(tmp_path / "new/metrics.jsonl").resolve()]


def test_scan_stops_at_the_file_limit_and_deduplicates_roots(tmp_path):
    for index in range(3):
        _touch(tmp_path / f"logs/{index}.jsonl")
    assert len(scan([tmp_path], max_files=2)["table"]) == 2
    assert len(scan([tmp_path, tmp_path / "logs"])["table"]) == 3
    assert best(scan([tmp_path / "missing"])) == (None, [])
