import json

from runboard.follow import Record, to_record
from runboard.follow.csvfile import CsvFollower
from runboard.follow.jsonl import JsonlFollower


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
