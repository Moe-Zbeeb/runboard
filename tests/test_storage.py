import pytest

from runboard.storage import Storage


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


def test_read_rows_pages_large_files(tmp_path):
    s = Storage(tmp_path)
    s.append_rows("p", "r", [{"_step": i, "v": i} for i in range(1000)])
    got, off = [], 0
    while True:
        rows, new = s.read_rows("p", "r", off, max_bytes=500)
        got += rows
        if new == off:
            break
        off = new
    assert [r["v"] for r in got] == list(range(1000))


def test_resent_rows_are_deduplicated(tmp_path):
    s = Storage(tmp_path)
    batch = [{"_sid": "a", "_seq": i, "_step": i, "v": i} for i in range(10)]
    s.append_rows("p", "r", batch)
    s.append_rows("p", "r", batch[5:] + [{"_sid": "a", "_seq": 10, "_step": 10, "v": 10}])
    s2 = Storage(tmp_path)
    s2.append_rows("p", "r", batch)
    rows, _ = s2.read_rows("p", "r")
    assert [r["v"] for r in rows] == list(range(11))
