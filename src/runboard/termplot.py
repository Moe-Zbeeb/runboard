import math

PALETTE = ("#3987e5", "#d95926", "#199e70", "#c98500", "#d55181", "#0ca30c", "#9085e9", "#e66767")
STATUS = {"good": "#3fb27f", "bad": "#e5645a", "warn": "#d9a43a", "accent": "#5b9be8"}
SUFFIXES = ((1e12, "T"), (1e9, "B"), (1e6, "M"), (1e3, "k"))
DOTS = ((0x01, 0x08), (0x02, 0x10), (0x04, 0x20), (0x40, 0x80))
CUBE = (0, 95, 135, 175, 215, 255)


def _number(x, precision):
    s = "%.*g" % (precision, x)
    if "e" in s:
        s = "{:f}".format(float(s))
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return "0" if s in ("-0", "") else s


def _finite(v):
    return v is not None and isinstance(v, (int, float)) and math.isfinite(v)


def fmt(v):
    if not _finite(v):
        return "–"
    a = abs(v)
    if a == 0:
        return "0"
    if a < 1e-3 or a >= 1e12:
        return "%.3e" % v
    if a >= 1e5:
        return "{:,.0f}".format(float("%.6g" % v))
    return _number(v, 5)


def tick(v):
    if v is None:
        return ""
    a = abs(v)
    if a == 0:
        return "0"
    if a < 1e-3:
        return "%.1e" % v
    for unit, suffix in SUFFIXES:
        if a >= unit:
            return _number(v / unit, 3) + suffix
    return _number(v, 4)


def nice_ticks(lo, hi, count=5):
    if hi < lo:
        lo, hi = hi, lo
    if hi == lo:
        return [lo]
    raw = (hi - lo) / max(1, count)
    mag = 10 ** math.floor(math.log10(raw))
    step = 10 * mag
    for m in (1, 2, 2.5, 5, 10):
        if raw <= m * mag:
            step = m * mag
            break
    first = math.ceil(lo / step - 1e-9)
    ticks = []
    i = first
    while i * step <= hi + step * 1e-9:
        ticks.append(round(i * step, 12) + 0.0)
        i += 1
    return [int(t) if t == int(t) and abs(t) < 1e15 else t for t in ticks]


def ema(ys, alpha):
    if not alpha:
        return list(ys)
    out, last, weight = [], 0.0, 0.0
    for v in ys:
        if v is None:
            out.append(None)
            continue
        last = last * alpha + (1 - alpha) * v
        weight = weight * alpha + (1 - alpha)
        out.append(last / weight)
    return out


def robust_range(values):
    clean = sorted(v for v in values if _finite(v))
    if len(clean) < 10:
        return None
    at = lambda q: clean[min(len(clean) - 1, max(0, int(round(q * (len(clean) - 1)))))]
    lo, hi = at(0.01), at(0.99)
    pad = (hi - lo) * 0.1 or abs(hi) * 0.05 or 1.0
    return lo - pad, hi + pad


class Canvas:
    def __init__(self, cols, rows):
        self.cols, self.rows = cols, rows
        self.pw, self.ph = cols * 2, rows * 4
        self.bits = [[0] * cols for _ in range(rows)]
        self.color = [[None] * cols for _ in range(rows)]

    def dot(self, x, y, color):
        x, y = int(x), int(y)
        if 0 <= x < self.pw and 0 <= y < self.ph:
            row, col = y // 4, x // 2
            self.bits[row][col] |= DOTS[y % 4][x % 2]
            self.color[row][col] = color

    def line(self, x0, y0, x1, y1, color):
        x0, y0, x1, y1 = int(round(x0)), int(round(y0)), int(round(x1)), int(round(y1))
        dx, dy = abs(x1 - x0), -abs(y1 - y0)
        sx, sy = (1 if x0 < x1 else -1), (1 if y0 < y1 else -1)
        err = dx + dy
        while True:
            self.dot(x0, y0, color)
            if x0 == x1 and y0 == y1:
                return
            e2 = 2 * err
            if e2 >= dy:
                err += dy
                x0 += sx
            if e2 <= dx:
                err += dx
                y0 += sy

    def cells(self):
        return [[(chr(0x2800 + b) if b else " ", c) for b, c in zip(bits, colors)] for bits, colors in zip(self.bits, self.color)]


class Line:
    def __init__(self, width):
        self.width = width
        self.segments = []
        self.length = 0

    def add(self, text, style=None):
        room = self.width - self.length
        if room <= 0 or not text:
            return self
        text = text[:room]
        if self.segments and self.segments[-1][1] == style:
            self.segments[-1] = (self.segments[-1][0] + text, style)
        else:
            self.segments.append((text, style))
        self.length += len(text)
        return self

    def add_segments(self, segments):
        for text, style in segments:
            self.add(text, style)
        return self

    def done(self):
        if self.length < self.width:
            self.add(" " * (self.width - self.length))
        return self.segments


def _clip_text(text, width):
    return text if len(text) <= width else text[: max(0, width - 1)] + "…"


def _points(series, logy):
    out = []
    for s in series:
        pts = []
        for x, y in zip(s["xs"], s["ys"]):
            if not (_finite(x) and _finite(y)):
                continue
            if logy:
                if y <= 0:
                    continue
                y = math.log10(y)
            pts.append((x, y))
        if any(pts[i][0] < pts[i - 1][0] for i in range(1, len(pts))):
            pts.sort(key=lambda p: p[0])
        out.append((pts, s.get("color")))
    return out


def _y_range(points, clip):
    ys = [y for pts, _ in points for _, y in pts]
    rng = robust_range(ys) if clip else None
    lo, hi = rng if rng else (min(ys), max(ys))
    if hi == lo:
        pad = abs(hi) * 0.05 or 1.0
        return lo - pad, hi + pad
    pad = (hi - lo) * 0.05
    return lo - pad, hi + pad


def _y_ticks(lo, hi, rows, logy):
    if logy:
        ticks = list(range(math.ceil(lo), math.floor(hi) + 1))
        step = max(1, math.ceil(len(ticks) / max(2, rows // 2)))
        ticks = ticks[::-1][::step][::-1] or [lo]
        return [(t, tick(10.0 ** t)) for t in ticks]
    return [(t, tick(t)) for t in nice_ticks(lo, hi, max(2, rows // 2))]


def _draw(canvas, pts, color, xlo, xhi, ylo, yhi):
    if not pts:
        return
    xspan, yspan = (xhi - xlo) or 1.0, (yhi - ylo) or 1.0
    top = canvas.ph - 1

    def py(y):
        return min(top, max(0, int(round((yhi - y) / yspan * top))))

    columns = []
    for x, y in pts:
        px = int(round((x - xlo) / xspan * (canvas.pw - 1))) if xhi > xlo else canvas.pw // 2
        if px < 0 or px >= canvas.pw:
            continue
        v = py(y)
        if columns and columns[-1][0] == px:
            c = columns[-1]
            c[2], c[3], c[4] = v, min(c[3], v), max(c[4], v)
        else:
            columns.append([px, v, v, v, v])
    prev = None
    for px, first, last, lo, hi in columns:
        if prev is not None:
            canvas.line(prev[0], prev[1], px, first, color)
        canvas.line(px, lo, px, hi, color)
        prev = (px, last)


def render_chart(series, width, height, title="", logy=False, clip=False, xfmt=tick, right="", xrange=None):
    rows = max(1, height - 3)
    head = Line(width)
    right = _clip_text(right, max(0, width // 2))
    head.add(_clip_text(title, max(1, width - len(right) - 1)), "bold")
    head.add(" " * max(0, width - head.length - len(right)))
    head.add(right, "dim")
    lines = [head.done()]
    points = _points(series, logy)
    xs = [x for pts, _ in points for x, _ in pts]
    if not xs:
        for r in range(rows):
            line = Line(width)
            if r == rows // 2:
                line.add(" " * max(0, (width - 7) // 2)).add("no data", "dim")
            lines.append(line.done())
        lines.append(Line(width).done())
        lines.append(Line(width).done())
        return lines
    xlo, xhi = xrange if xrange else (min(xs), max(xs))
    ylo, yhi = _y_range(points, clip)
    yticks = _y_ticks(ylo, yhi, rows, logy)
    label_w = max(len(label) for _, label in yticks) if yticks else 1
    cols = max(4, width - label_w - 1)
    canvas = Canvas(cols, rows)
    for pts, color in points:
        _draw(canvas, pts, color, xlo, xhi, ylo, yhi)
    tick_rows = {}
    for value, label in yticks:
        row = int(round((yhi - value) / ((yhi - ylo) or 1.0) * (canvas.ph - 1))) // 4
        if 0 <= row < rows and row not in tick_rows:
            tick_rows[row] = label
    for r, cells in enumerate(canvas.cells()):
        line = Line(width)
        line.add(tick_rows.get(r, "").rjust(label_w), "dim")
        line.add("┤" if r in tick_rows else "│", "dim")
        for char, color in cells:
            line.add(char, color if char != " " else None)
        lines.append(line.done())
    xticks = nice_ticks(xlo, xhi, max(2, cols // 12)) if xhi > xlo else [xlo]
    axis = ["─"] * cols
    labels = [" "] * cols
    last_end = -2
    for value in xticks:
        pos = int(round((value - xlo) / ((xhi - xlo) or 1.0) * (cols - 1))) if xhi > xlo else cols // 2
        if not 0 <= pos < cols:
            continue
        label = xfmt(value)
        start = min(max(0, pos - len(label) // 2), max(0, cols - len(label)))
        if start <= last_end + 1:
            continue
        axis[pos] = "┬"
        labels[start:start + len(label)] = list(label)
        last_end = start + len(label) - 1
    lines.append(Line(width).add(" " * label_w + "└" + "".join(axis), "dim").done())
    lines.append(Line(width).add(" " * (label_w + 1) + "".join(labels)[:cols], "dim").done())
    return lines


def render_legend(items, width):
    lines, line = [], Line(width)
    for label, value, color in items:
        piece = len(label) + len(value) + 5
        if line.length and line.length + piece > width:
            lines.append(line.done())
            line = Line(width)
        if line.length:
            line.add("  ")
        line.add("■ ", color).add(_clip_text(label, max(4, width - len(value) - 6)) + " ").add(value, "bold")
    lines.append(line.done())
    return lines


def xterm256(hex_color):
    r, g, b = (int(hex_color[i:i + 2], 16) for i in (1, 3, 5))
    near = lambda v: min(range(6), key=lambda i: abs(CUBE[i] - v))
    ci = [near(r), near(g), near(b)]
    cube = 16 + 36 * ci[0] + 6 * ci[1] + ci[2]
    cube_rgb = [CUBE[i] for i in ci]
    level = max(0, min(23, int(round(((r + g + b) / 3 - 8) / 10))))
    gray = 8 + 10 * level
    d_cube = sum((a - c) ** 2 for a, c in zip((r, g, b), cube_rgb))
    d_gray = sum((a - gray) ** 2 for a in (r, g, b))
    return cube if d_cube <= d_gray else 232 + level


def style_hex(style):
    if isinstance(style, int):
        return PALETTE[style % len(PALETTE)]
    return STATUS.get(style)


def ansi_code(style, colors):
    if style is None or colors is None:
        return ""
    if style == "bold":
        return "\x1b[1m"
    if style == "dim":
        return "\x1b[2m"
    if style == "rev":
        return "\x1b[7m"
    hex_color = style_hex(style)
    if not hex_color:
        return ""
    if colors == "truecolor":
        return "\x1b[38;2;%d;%d;%dm" % tuple(int(hex_color[i:i + 2], 16) for i in (1, 3, 5))
    return "\x1b[38;5;%dm" % xterm256(hex_color)


def to_ansi(lines, colors="256"):
    out = []
    for line in lines:
        parts = []
        for text, style in line:
            code = ansi_code(style, colors)
            parts.append(code + text + "\x1b[0m" if code else text)
        out.append("".join(parts).rstrip() if colors is None else "".join(parts))
    text = "\n".join(out)
    return text + "\x1b[0m" if colors else text
