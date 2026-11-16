import os
import pty
import select
import struct
import sys
import time

import pytest

from runboard.storage import Storage
from runboard.top import Model, screen
from runboard.viewer import Source, Store


def text(line):
    return "".join(segment for segment, _ in line)


def seed(root):
    storage = Storage(root)
    now = time.time()
    specs = [("rl", "a", "rl run a", 0), ("rl", "b", "rl run b", 1), ("rl", "c", "rl run c", 2), ("rl", "d", "rl run d", 3), ("vision", "v", "vision run", 9)]
    for project, run_id, name, age in specs:
        storage.update_meta(project, run_id, {"name": name, "status": "finished", "created": now - age * 100})
        rows = [{"train/loss": 1 / (s + 1), "_step": s, "_time": now - 1000 + s, "_seq": s, "_sid": run_id} for s in range(120)]
        for s in range(0, 120, 20):
            rows[s]["eval/acc"] = s / 120
        storage.append_rows(project, run_id, rows)
        path = root / project / run_id / "metrics.jsonl"
        os.utime(path, (now - age * 100, now - age * 100))


@pytest.fixture
def model(tmp_path):
    seed(tmp_path / "runs")
    store = Store(Source.resolve(dir=str(tmp_path / "runs")))
    m = Model(store)
    m.refresh()
    return m


def test_model_starts_on_the_most_recent_project_with_recent_runs(model):
    assert model.project == "rl"
    assert model.selected == ["rl/a", "rl/b", "rl/c"]
    assert [r["run_id"] for r in model.visible()] == ["a", "b", "c", "d"]


def test_keys_move_select_and_switch_projects(model):
    model.handle("down")
    model.handle("down")
    model.handle("down")
    model.handle(" ")
    assert "rl/d" in model.selected
    model.handle(" ")
    assert "rl/d" not in model.selected
    model.handle("c")
    assert model.selected == []
    model.handle("]")
    assert model.project == "vision" and model.selected == ["vision/v"]
    model.handle("]")
    assert model.project is None
    model.handle("[")
    assert model.project == "vision"


def test_filter_editing_applies_on_enter_and_cancels_on_escape(model):
    for key in ["/", "a", "c", "c", "enter"]:
        model.handle(key)
    assert model.filter == "acc" and model.metrics() == ["eval/acc"]
    for key in ["/", "x", "esc"]:
        model.handle(key)
    assert model.filter == "acc"
    for key in ["/", "backspace", "backspace", "backspace", "enter"]:
        model.handle(key)
    assert model.filter == "" and model.metrics() == ["eval/acc", "train/loss"]


def test_view_options_cycle(model):
    model.handle("s")
    assert model.opts["smoothing"] == 0.6
    model.handle("l")
    model.handle("o")
    model.handle("x")
    assert model.opts["logy"] and model.opts["clip"] and model.opts["xmode"] == "minutes"
    assert model.handle("q") is False


def test_screen_fills_the_terminal(model):
    lines = screen(model, 120, 40)
    assert len(lines) == 40 and all(len(text(line)) == 120 for line in lines)
    body = "\n".join(text(line) for line in lines)
    assert "runboard" in body and "rl run a" in body and "train/loss" in body
    assert any("⠀" < ch <= "⣿" for ch in body)


def test_screen_handles_tiny_terminals_help_and_fullscreen(model):
    tiny = "\n".join(text(line) for line in screen(model, 40, 10))
    assert "larger" in tiny
    model.handle("?")
    assert "space" in "\n".join(text(line) for line in screen(model, 120, 40))
    model.handle("?")
    model.handle("tab")
    model.handle("enter")
    full = "\n".join(text(line) for line in screen(model, 120, 40))
    assert "eval/acc" in full and "train/loss" not in full
    model.handle("right")
    assert "train/loss" in "\n".join(text(line) for line in screen(model, 120, 40))
    model.handle("esc")
    assert model.single is None


def test_charts_scroll_within_bounds(model):
    model.handle("tab")
    screen(model, 120, 20)
    model.handle("pgdn")
    model.handle("pgdn")
    model.handle("pgdn")
    screen(model, 120, 20)
    first = model.scroll
    assert first > 0
    model.handle("end")
    screen(model, 120, 20)
    assert model.scroll >= first
    model.handle("home")
    assert model.scroll == 0


@pytest.mark.skipif(sys.platform == "win32", reason="needs a pty")
def test_top_runs_in_a_real_terminal_and_quits(tmp_path):
    import fcntl
    import subprocess
    import termios

    seed(tmp_path / "runs")
    master, slave = pty.openpty()
    fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 40, 120, 0, 0))
    env = dict(os.environ, TERM="xterm-256color", RUNBOARD_HOME=str(tmp_path / "home"))
    proc = subprocess.Popen(
        [sys.executable, "-m", "runboard.cli", "top", "--dir", str(tmp_path / "runs")],
        stdin=slave, stdout=slave, stderr=slave, env=env, start_new_session=True,
    )
    os.close(slave)
    output = b""
    sent = False
    deadline = time.time() + 15
    try:
        while time.time() < deadline and proc.poll() is None:
            ready, _, _ = select.select([master], [], [], 0.2)
            if ready:
                try:
                    output += os.read(master, 65536)
                except OSError:
                    break
            if not sent and b"rl run a" in output:
                os.write(master, b"q")
                sent = True
        code = proc.wait(timeout=5)
    finally:
        if proc.poll() is None:
            proc.kill()
        os.close(master)
    assert sent, output[-500:]
    assert code == 0
