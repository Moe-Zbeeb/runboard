import json

import pytest

from runboard import config
from runboard.storage import Storage
from runboard.viewer import Source, Store, ViewerError, default_selection, metric_names, series_for


def seed(root, project="p", run_id="r1", name="run one", status="finished", n=50, updated=None, extra=None):
    storage = Storage(root)
    storage.update_meta(project, run_id, {"name": name, "status": status, "config": {"lr": 0.1}, **(extra or {})})
    rows = [{"train/loss": 1.0 / (i + 1), "_step": i, "_time": 1000.0 + i * 10, "_seq": i, "_sid": "s"} for i in range(n)]
    for i in range(0, n, 10):
        rows[i]["eval/acc"] = i / n
    storage.append_rows(project, run_id, rows)
    return storage


def test_local_source_lists_runs_and_pages_rows(tmp_path):
    seed(tmp_path / "runs")
    source = Source.resolve(dir=str(tmp_path / "runs"))
    assert "runs" in source.label
    runs = source.runs()
    assert [(r["project"], r["run_id"], r["name"]) for r in runs] == [("p", "r1", "run one")]
    rows, offset = source.page("p", "r1", 0)
    assert len(rows) == 50 and offset > 0
    assert source.page("p", "r1", offset) == ([], offset)


def test_http_source_reads_a_server(server):
    seed(server.storage.root)
    source = Source.resolve(server=f"http://127.0.0.1:{server.port}", token="secret")
    assert [r["run_id"] for r in source.runs()] == ["r1"]
    rows, offset = source.page("p", "r1", 0)
    assert len(rows) == 50 and offset > 0


def test_http_source_explains_a_rejected_token(server):
    source = Source.resolve(server=f"http://127.0.0.1:{server.port}", token="wrong")
    with pytest.raises(ViewerError) as e:
        source.runs()
    assert "token" in str(e.value) and "wrong" not in str(e.value)


def test_resolve_uses_the_configured_server(server):
    config.write_server_info({"url": f"http://127.0.0.1:{server.port}", "token": "secret"})
    source = Source.resolve()
    assert source.label == f"http://127.0.0.1:{server.port}"


def test_resolve_falls_back_to_local_runs(tmp_path):
    assert Source.resolve().label.endswith("runboard-runs")


class CountingSource:
    def __init__(self, inner):
        self.inner = inner
        self.calls = 0

    def runs(self):
        return self.inner.runs()

    def page(self, project, run_id, offset):
        self.calls += 1
        return self.inner.page(project, run_id, offset)


def test_store_fetches_only_when_a_run_changes(tmp_path):
    storage = seed(tmp_path / "runs")
    source = CountingSource(Source.resolve(dir=str(tmp_path / "runs")))
    store = Store(source)
    store.refresh_runs()
    assert store.load("p/r1") is True
    calls = source.calls
    assert store.load("p/r1") is False and source.calls == calls
    storage.append_rows("p", "r1", [{"train/loss": 0.01, "_step": 50, "_time": 1600.0, "_seq": 50, "_sid": "s"}])
    store.runs["p/r1"]["updated"] = 1e12
    assert store.load("p/r1") is True
    assert store.data["p/r1"].series["train/loss"]["y"][-1] == 0.01


def test_default_selection_prefers_the_most_recently_active_project():
    runs = [
        {"project": "a", "run_id": "1", "updated": 10},
        {"project": "b", "run_id": "2", "updated": 50},
        {"project": "b", "run_id": "3", "updated": 40},
        {"project": "a", "run_id": "4", "updated": 45},
        {"project": "b", "run_id": "5", "updated": 30},
        {"project": "b", "run_id": "6", "updated": 20},
    ]
    assert default_selection(runs, None, 3) == ["b/2", "b/3", "b/5"]
    assert default_selection(runs, "a", 3) == ["a/4", "a/1"]
    assert default_selection([], None, 3) == []


def test_series_for_supports_steps_minutes_and_smoothing(tmp_path):
    seed(tmp_path / "runs")
    store = Store(Source.resolve(dir=str(tmp_path / "runs")))
    store.refresh_runs()
    store.load("p/r1")
    data = store.data["p/r1"]
    xs, ys = series_for(data, "train/loss", "step", 0)
    assert xs[:3] == [0, 1, 2] and ys[0] == 1.0
    xs, _ = series_for(data, "train/loss", "minutes", 0)
    assert xs[0] == 0 and abs(xs[6] - 1.0) < 1e-9
    _, smooth = series_for(data, "train/loss", "step", 0.9)
    assert smooth[10] > ys[10]
    assert series_for(data, "missing", "step", 0) == ([], [])


def test_metric_names_filter_with_regex_or_plain_text(tmp_path):
    seed(tmp_path / "runs")
    store = Store(Source.resolve(dir=str(tmp_path / "runs")))
    store.refresh_runs()
    store.load("p/r1")
    datas = [store.data["p/r1"]]
    assert metric_names(datas, "") == ["eval/acc", "train/loss"]
    assert metric_names(datas, "^train") == ["train/loss"]
    assert metric_names(datas, "(loss") == []
