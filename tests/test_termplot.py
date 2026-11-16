import math

from runboard import termplot as tp


def text(line):
    return "".join(segment for segment, _ in line)


def test_fmt_matches_the_dashboard():
    assert tp.fmt(None) == "–"
    assert tp.fmt(float("nan")) == "–"
    assert tp.fmt(0) == "0"
    assert tp.fmt(1.03) == "1.03"
    assert tp.fmt(753.7612) == "753.76"
    assert tp.fmt(0.0021581234) == "0.0021581"
    assert tp.fmt(104857.6) == "104,858"
    assert tp.fmt(0.00001234) == "1.234e-05"
    assert tp.fmt(-2.5) == "-2.5"


def test_tick_uses_compact_suffixes():
    assert tp.tick(0) == "0"
    assert tp.tick(20000) == "20k"
    assert tp.tick(1500) == "1.5k"
    assert tp.tick(1250000) == "1.25M"
    assert tp.tick(2.5e9) == "2.5B"
    assert tp.tick(-3000) == "-3k"
    assert tp.tick(999) == "999"
    assert tp.tick(0.5) == "0.5"
    assert tp.tick(0.00012) == "1.2e-04"


def test_nice_ticks_are_round_and_inside_the_range():
    assert tp.nice_ticks(0, 1, 5) == [0, 0.2, 0.4, 0.6, 0.8, 1.0]
    assert tp.nice_ticks(0, 620, 4) == [0, 200, 400, 600]
    ticks = tp.nice_ticks(-0.55, 0.83, 4)
    assert all(-0.55 <= t <= 0.83 for t in ticks) and len(ticks) >= 3
    assert tp.nice_ticks(3, 3, 4) == [3]


def test_ema_keeps_gaps_and_debiases():
    out = tp.ema([1.0, None, 1.0, 3.0], 0.5)
    assert out[0] == 1.0 and out[1] is None and out[2] == 1.0
    assert 1.0 < out[3] < 3.0
    assert tp.ema([1.0, 2.0], 0) == [1.0, 2.0]


def test_robust_range_ignores_rare_spikes():
    values = [1 + (i % 10) / 100 for i in range(1000)]
    values[500] = 50.0
    values[700] = -40.0
    lo, hi = tp.robust_range(values)
    assert 0.9 < lo < 1 and 1.09 < hi < 1.2
    assert tp.robust_range([1.0, 2.0]) is None


def test_canvas_draws_braille_dots():
    canvas = tp.Canvas(2, 1)
    canvas.dot(0, 0, 3)
    canvas.dot(3, 3, 3)
    cells = canvas.cells()
    assert cells[0][0] == ("⠁", 3)
    assert cells[0][1] == ("⢀", 3)
    canvas.dot(99, 99, 1)
    assert len(cells) == 1 and len(cells[0]) == 2


def test_canvas_line_is_continuous():
    canvas = tp.Canvas(10, 3)
    canvas.line(0, 11, 19, 0, 0)
    lit = sum(1 for row in canvas.cells() for char, _ in row if char != " ")
    assert lit >= 10


def test_chart_has_exact_size_and_labels():
    xs = list(range(100))
    ys = [math.exp(-x / 30) for x in xs]
    lines = tp.render_chart([{"xs": xs, "ys": ys, "color": 0}], 60, 14, title="train/loss")
    assert len(lines) == 14
    assert all(len(text(line)) == 60 for line in lines)
    assert "train/loss" in text(lines[0])
    body = "\n".join(text(line) for line in lines)
    assert any("⠀" < ch <= "⣿" for ch in body)
    assert "0" in text(lines[-1]) and "50" in text(lines[-1])


def test_chart_keeps_a_single_spike_visible_after_downsampling():
    xs = list(range(200000))
    ys = [1.0] * len(xs)
    ys[123456] = 100.0
    lines = tp.render_chart([{"xs": xs, "ys": ys, "color": 0}], 50, 12)
    top = text(lines[1])
    assert any("⠀" < ch <= "⣿" for ch in top)


def test_chart_handles_empty_constant_and_log_data():
    empty = tp.render_chart([], 40, 8, title="x")
    assert "no data" in "\n".join(text(line) for line in empty)
    flat = tp.render_chart([{"xs": [0, 1, 2], "ys": [5.0, 5.0, 5.0], "color": 1}], 40, 8)
    assert len(flat) == 8
    log = tp.render_chart([{"xs": [0, 1, 2, 3], "ys": [1.0, 10.0, 0.0, 1000.0], "color": 2}], 40, 10, logy=True)
    assert "1k" in "\n".join(text(line) for line in log)


def test_chart_colors_segments_by_series():
    a = {"xs": [0, 1, 2], "ys": [0.0, 0.0, 0.0], "color": 4}
    b = {"xs": [0, 1, 2], "ys": [1.0, 1.0, 1.0], "color": 6}
    styles = {style for line in tp.render_chart([a, b], 40, 10) for _, style in line}
    assert 4 in styles and 6 in styles


def test_ansi_output_and_plain_output():
    lines = tp.render_chart([{"xs": [0, 1], "ys": [0.0, 1.0], "color": 0}], 30, 8, title="t")
    colored = tp.to_ansi(lines, colors="256")
    assert "\x1b[38;5;" in colored and colored.endswith("\x1b[0m")
    truecolor = tp.to_ansi(lines, colors="truecolor")
    assert "\x1b[38;2;" in truecolor
    plain = tp.to_ansi(lines, colors=None)
    assert "\x1b" not in plain


def test_xterm256_maps_palette_hex_to_nearby_cube_colors():
    assert tp.xterm256("#000000") == 16
    assert tp.xterm256("#ffffff") == 231
    assert 16 <= tp.xterm256(tp.PALETTE[0]) <= 255


def test_legend_wraps_entries_to_the_width():
    items = [("kl_coef=0.01", "1.0211", 0), ("kl_coef=0 (diverged)", "1.5443", 1), ("kl_coef=0.001", "0.937", 2)]
    lines = tp.render_legend(items, 40)
    assert all(len(text(line)) == 40 for line in lines)
    joined = "\n".join(text(line) for line in lines)
    assert "kl_coef=0.01" in joined and "0.937" in joined and len(lines) >= 2
    assert {style for line in lines for _, style in line} >= {0, 1, 2}
