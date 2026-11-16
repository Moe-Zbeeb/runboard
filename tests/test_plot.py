import re

import pytest

from runboard.cli import main
from runboard.storage import Storage

ANSI = re.compile(r"\x1b\[[0-9;]*m")


def seed(root):
    storage = Storage(root)
    for i, (run_id, name, lr) in enumerate([("a", "lr=0.1", 0.1), ("b", "lr=0.01", 0.01)]):
        storage.update_meta("demo", run_id, {"name": name, "status": "finished", "config": {"lr": lr}})
        rows = [{"train/loss": 2.0 / (s * lr + 1), "_step": s, "_time": 1000.0 + s, "_seq": s, "_sid": run_id} for s in range(300)]
        for s in range(0, 300, 25):
            rows[s]["eval/acc"] = 1 - 1 / (s * lr + 1)
        storage.append_rows("demo", run_id, rows)


def run_plot(args, capsys):
    try:
        main(["plot", *args])
        code = 0
    except SystemExit as e:
        code = e.code
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def test_plot_prints_every_metric_for_the_default_runs(tmp_path, capsys):
    seed(tmp_path / "runs")
    code, out, _ = run_plot(["--dir", str(tmp_path / "runs"), "--color", "never", "--width", "100"], capsys)
    assert code == 0
    assert "train/loss" in out and "eval/acc" in out
    assert "lr=0.1" in out and "lr=0.01" in out
    assert any("⠀" < ch <= "⣿" for ch in out)
    assert "\x1b" not in out
    assert max(len(line) for line in out.splitlines()) <= 100


def test_plot_filters_metrics_and_runs(tmp_path, capsys):
    seed(tmp_path / "runs")
    code, out, _ = run_plot(["lr=0.01", "--dir", str(tmp_path / "runs"), "--metric", "^eval", "--color", "never"], capsys)
    assert code == 0
    assert "eval/acc" in out and "train/loss" not in out
    assert "lr=0.01" in out and "lr=0.1 " not in out


def test_plot_colors_when_asked(tmp_path, capsys):
    seed(tmp_path / "runs")
    code, out, _ = run_plot(["--dir", str(tmp_path / "runs"), "--color", "always", "--width", "120", "--height", "10"], capsys)
    assert code == 0 and "\x1b[" in out
    assert max(len(ANSI.sub("", line)) for line in out.splitlines()) <= 120


def test_plot_reports_missing_runs(tmp_path, capsys):
    code, _, err = run_plot(["--dir", str(tmp_path / "nothing")], capsys)
    assert code == 1 and "no runs" in err
    seed(tmp_path / "runs")
    code, _, err = run_plot(["no-such-run", "--dir", str(tmp_path / "runs")], capsys)
    assert code == 1 and "no-such-run" in err
