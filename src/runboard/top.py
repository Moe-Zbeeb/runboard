import locale
import os
import sys
import threading
import time

from .plot import MAX_RUNS, STATUS_MARK, STATUS_STYLE, grid, metric_block, pick_runs, status_of
from .termplot import PALETTE, STATUS, Line, xterm256
from .viewer import Source, Store, ViewerError, binned_prefixes, default_selection, key, metric_names

SMOOTHING = (0, 0.6, 0.8, 0.9, 0.95, 0.98)
X_MODES = ("step", "minutes", "time")
MIN_W, MIN_H = 60, 14
HELP = [
    ("Runs", "↑ ↓ or j k  move     space  select     a  select all     c  clear     [ ]  project"),
    ("Charts", "tab  switch to charts     ↑ ↓  scroll     PgUp PgDn  page     enter or f  full screen     ← →  next chart"),
    ("View", "/  filter metrics     s  smoothing     l  log y     o  clip outliers     x  x axis"),
    ("Other", "r  refresh now     ?  close help     q  quit"),
]


def _ago(t):
    if not t:
        return ""
    s = time.time() - t
    if s < 60:
        return "now"
    if s < 3600:
        return f"{int(s // 60)}m"
    if s < 86400:
        return f"{int(s // 3600)}h"
    return f"{int(s // 86400)}d"


class Model:
    def __init__(self, store, project=None, metric="", keys=None, opts=None):
        self.store = store
        self.project = project
        self.filter = metric or ""
        self.selected = []
        self.slots = {}
        for k in keys or []:
            self._select(k)
        self.touched = bool(keys)
        self.started = project is not None or bool(keys)
        self.cursor = 0
        self.focus = "runs"
        self.single = None
        self.scroll = 0
        self.max_scroll = 0
        self.page = 10
        self.top_metric = None
        self.editing = None
        self.help = False
        self.message = ""
        self.error = ""
        self.updated_at = None
        self.wants_refresh = False
        self.opts = {"smoothing": 0, "logy": False, "clip": False, "xmode": "step"}
        self.opts.update(opts or {})
        self.lock = threading.RLock()

    def _select(self, k):
        if k in self.selected:
            return True
        if len(self.selected) >= MAX_RUNS:
            return False
        used = set(self.slots.values())
        slot = 0
        while slot in used:
            slot += 1
        self.slots[k] = slot
        self.selected.append(k)
        return True

    def _deselect(self, k):
        if k in self.selected:
            self.selected.remove(k)
            self.slots.pop(k, None)

    def refresh(self):
        try:
            runs = self.store.refresh_runs()
            with self.lock:
                self._sync(runs)
                keys = list(self.selected)
            for k in keys:
                self.store.load(k)
            self.error = ""
            self.updated_at = time.time()
        except ViewerError as e:
            self.error = str(e)

    def _sync(self, runs):
        if self.project and self.project not in self.projects():
            self.project = None
        for k in [k for k in self.selected if k not in self.store.runs]:
            self._deselect(k)
        if not self.started and runs:
            self.started = True
            if self.project is None:
                self.project = sorted(runs, key=lambda r: r.get("updated") or 0, reverse=True)[0]["project"]
        if not self.selected and not self.touched:
            for k in default_selection(runs, self.project, 3):
                self._select(k)
        self.cursor = max(0, min(self.cursor, len(self.visible()) - 1))

    def visible(self):
        runs = [r for r in self.store.runs.values() if not self.project or r["project"] == self.project]
        return sorted(runs, key=lambda r: r.get("created") or 0, reverse=True)

    def projects(self):
        return sorted({r["project"] for r in self.store.runs.values()})

    def metrics(self):
        hidden = [p for k in self.selected for p in binned_prefixes(self.store.runs.get(k))]
        return metric_names([self.store.data.get(k) for k in self.selected], self.filter, hidden)

    def _switch_project(self, step):
        order = self.projects() + [None]
        i = order.index(self.project) if self.project in order else len(order) - 1
        self.project = order[(i + step) % len(order)]
        if self.project:
            for k in [k for k in self.selected if self.store.runs.get(k, {}).get("project") != self.project]:
                self._deselect(k)
            if not self.selected:
                for k in default_selection(list(self.store.runs.values()), self.project, 3):
                    self._select(k)
        self.cursor = self.scroll = 0
        self.single = None
        self.wants_refresh = True

    def handle(self, k):
        self.message = ""
        if self.editing is not None:
            if k == "enter":
                self.filter, self.editing = self.editing, None
                self.scroll, self.single = 0, None
            elif k == "esc":
                self.editing = None
            elif k == "backspace":
                self.editing = self.editing[:-1]
            elif len(k) == 1 and k.isprintable():
                self.editing += k
            return True
        if self.help:
            self.help = False
            return k != "q"
        if k == "q":
            return False
        if k == "?":
            self.help = True
        elif k == "tab":
            self.focus = "charts" if self.focus == "runs" else "runs"
        elif k == "/":
            self.editing = self.filter
        elif k == "s":
            i = SMOOTHING.index(self.opts["smoothing"]) if self.opts["smoothing"] in SMOOTHING else 0
            self.opts["smoothing"] = SMOOTHING[(i + 1) % len(SMOOTHING)]
        elif k == "l":
            self.opts["logy"] = not self.opts["logy"]
        elif k == "o":
            self.opts["clip"] = not self.opts["clip"]
        elif k == "x":
            self.opts["xmode"] = X_MODES[(X_MODES.index(self.opts["xmode"]) + 1) % len(X_MODES)]
        elif k in ("[", "]"):
            self._switch_project(1 if k == "]" else -1)
        elif k == "c":
            for run_key in list(self.selected):
                self._deselect(run_key)
            self.touched = True
        elif k == "a":
            for run in self.visible():
                if not self._select(key(run)):
                    self.message = f"Up to {MAX_RUNS} runs at once"
                    break
            self.touched = self.wants_refresh = True
        elif k == "r":
            self.wants_refresh = True
            self.message = "Refreshing…"
        elif k in ("f",) or (k == "enter" and self.focus == "charts" and self.single is None):
            metrics = self.metrics()
            if metrics:
                self.single = metrics.index(self.top_metric) if self.top_metric in metrics else 0
        elif k == "esc":
            self.single = None
        elif self.single is not None and k in ("left", "right", "h"):
            n = len(self.metrics())
            if n:
                self.single = (self.single + (1 if k == "right" else -1)) % n
        elif self.focus == "runs":
            self._runs_key(k)
        else:
            self._charts_key(k)
        return True

    def _runs_key(self, k):
        runs = self.visible()
        if k in ("up", "k"):
            self.cursor = max(0, self.cursor - 1)
        elif k in ("down", "j"):
            self.cursor = min(len(runs) - 1, self.cursor + 1)
        elif k == "home":
            self.cursor = 0
        elif k == "end":
            self.cursor = max(0, len(runs) - 1)
        elif k in (" ", "enter") and runs:
            run_key = key(runs[self.cursor])
            self.touched = True
            if run_key in self.selected:
                self._deselect(run_key)
            elif self._select(run_key):
                self.wants_refresh = True
            else:
                self.message = f"Up to {MAX_RUNS} runs at once; press space on a selected run to drop it"

    def _charts_key(self, k):
        moves = {"up": -3, "k": -3, "down": 3, "j": 3, "pgup": -self.page, "pgdn": self.page}
        if k in moves:
            self.scroll = max(0, min(self.max_scroll, self.scroll + moves[k]))
        elif k == "home":
            self.scroll = 0
        elif k == "end":
            self.scroll = self.max_scroll


def _center(text, width, style="dim"):
    pad = max(0, (width - len(text)) // 2)
    return Line(width).add(" " * pad).add(text, style).done()


def _header(model, width):
    source = getattr(model.store.source, "label", "")
    line = Line(width).add(" runboard", "bold").add("  ").add(model.project or "all projects", "accent")
    if model.error:
        status, style = f"● {model.error} ", "bad"
    elif model.updated_at:
        status, style = f"● live  updated {int(time.time() - model.updated_at)}s ago ", "good"
    else:
        status, style = "● connecting… ", "warn"
    room = width - line.length - len(status) - 4
    if room > 8:
        line.add("  ").add(source if len(source) <= room else "…" + source[-(room - 1):], "dim")
        line.add("  ")
    line.add(" " * max(0, width - line.length - len(status)))
    return line.add(status[: max(0, width - line.length)], style).done()


def _footer(model, width):
    line = Line(width)
    if model.editing is not None:
        return line.add(" filter metrics: ", "bold").add(model.editing).add("▏", "accent").add("   enter apply   esc cancel", "dim").done()
    if model.message:
        return line.add(" " + model.message, "warn").done()
    hints = [
        ("space", "select"), ("tab", "charts" if model.focus == "runs" else "runs"), ("/", f"filter {model.filter}" if model.filter else "filter"),
        ("s", f"smooth {model.opts['smoothing']:g}"), ("l", "log y" + (" on" if model.opts["logy"] else "")),
        ("o", "clip" + (" on" if model.opts["clip"] else "")), ("x", model.opts["xmode"]), ("[ ]", "project"), ("?", "help"), ("q", "quit"),
    ]
    line.add(" ")
    if model.max_scroll and model.single is None:
        line.add(f"{int(100 * model.scroll / model.max_scroll):>3}% ", "accent")
    for k, label in hints:
        if line.length + len(k) + len(label) + 4 > width:
            break
        line.add(k, "bold").add(" " + label + "   ", "dim")
    return line.done()


def _sidebar(model, width, rows):
    runs = model.visible()
    lines = [Line(width).add(" Runs ", "bold").add(f"{len(model.selected)} of {len(runs)} selected", "dim").done(), Line(width).add("─" * width, "dim").done()]
    room = rows - len(lines)
    if not runs:
        lines.append(Line(width).add(" No runs yet", "dim").done())
    first = max(0, min(model.cursor - room // 2, len(runs) - room))
    for i, run in enumerate(runs[first:first + room], start=first):
        run_key = key(run)
        on = run_key in model.selected
        status = status_of(run)
        tail = f" {STATUS_MARK.get(status, '·')} {_ago(run.get('updated')):>3} "
        name_w = max(4, width - 3 - len(tail))
        name = run.get("name") or run["run_id"]
        name = name if len(name) <= name_w else name[: name_w - 1] + "…"
        cursor = i == model.cursor
        line = Line(width).add(" ")
        line.add("■ " if on else "□ ", model.slots.get(run_key) if on else "dim")
        line.add(name.ljust(name_w), "rev" if cursor and model.focus == "runs" else ("bold" if cursor else None))
        line.add(tail, STATUS_STYLE.get(status) or "dim")
        lines.append(line.done())
    while len(lines) < rows:
        lines.append(Line(width).done())
    return lines[:rows]


def _charts(model, width, rows):
    if model.help:
        lines = [Line(width).done(), Line(width).add(" Keys", "bold").done(), Line(width).done()]
        for title, text in HELP:
            lines.append(Line(width).add(f" {title:<8}", "accent").add(text).done())
            lines.append(Line(width).done())
        lines.append(Line(width).add(" Press any key to close", "dim").done())
        return lines
    if not model.selected:
        return [Line(width).done()] * (rows // 2) + [_center("Select runs with space, or press a to select every run", width)]
    metrics = model.metrics()
    if not metrics:
        loaded = all(model.store.data.get(k) and model.store.data[k].seen for k in model.selected)
        message = f"No metrics match {model.filter!r}" if model.filter else ("The selected runs have not logged any metrics yet" if loaded else "Loading metrics…")
        return [Line(width).done()] * (rows // 2) + [_center(message, width)]
    keys = list(model.selected)
    if model.single is not None:
        model.single %= len(metrics)
        block = metric_block(model.store, keys, model.slots, metrics[model.single], width - 2, max(8, rows - 4), model.opts)
        head = Line(width).add(f" {model.single + 1} of {len(metrics)}", "dim").add("   ← → next chart   esc back", "dim").done()
        return [head] + [Line(width).add(" ").add_segments(line).done() for line in block]
    columns = max(1, min(3, (width - 2) // 56))
    height = max(8, min(16, (rows - 6) // 2))
    index = []
    lines = grid(model.store, keys, model.slots, metrics, width - 2, height, model.opts, columns, index=index)
    model.page = max(3, rows - 2)
    model.max_scroll = max(0, len(lines) - rows)
    model.scroll = max(0, min(model.scroll, model.max_scroll))
    model.top_metric = next((m for at, m in index if at >= model.scroll), index[-1][1] if index else None)
    view = lines[model.scroll:model.scroll + rows]
    return [Line(width).add(" ").add_segments(line).done() for line in view]


def screen(model, width, height):
    with model.lock:
        if width < MIN_W or height < MIN_H:
            lines = [Line(width).done()] * (height // 2)
            lines.append(_center(f"Make the terminal larger (at least {MIN_W}×{MIN_H})", width))
            return (lines + [Line(width).done()] * height)[:height]
        rows = height - 2
        side_w = min(36, max(24, width // 4))
        chart_w = width - side_w - 1
        side = _sidebar(model, side_w, rows)
        charts = _charts(model, chart_w, rows)
        charts = (charts + [Line(chart_w).done()] * rows)[:rows]
        body = []
        for left, right in zip(side, charts):
            line = Line(width).add_segments(left).add("│", "accent" if model.focus == "charts" else "dim").add_segments(right)
            body.append(line.done())
        return [_header(model, width)] + body + [_footer(model, width)]


KEYMAP = {
    "KEY_UP": "up", "KEY_DOWN": "down", "KEY_LEFT": "left", "KEY_RIGHT": "right", "KEY_PPAGE": "pgup", "KEY_NPAGE": "pgdn",
    "KEY_HOME": "home", "KEY_END": "end", "KEY_BACKSPACE": "backspace", "KEY_ENTER": "enter", "KEY_RESIZE": "resize",
}
CHARS = {"\n": "enter", "\r": "enter", "\t": "tab", "\x1b": "esc", "\x7f": "backspace", "\b": "backspace"}


def translate(curses, ch):
    if isinstance(ch, str):
        return CHARS.get(ch, ch if ch.isprintable() else None)
    for name, value in KEYMAP.items():
        if ch == getattr(curses, name, None):
            return value
    return None


def _attrs(curses):
    attrs = {None: 0, "bold": curses.A_BOLD, "dim": curses.A_DIM, "rev": curses.A_REVERSE}
    if not curses.has_colors():
        return attrs
    curses.start_color()
    try:
        curses.use_default_colors()
        background = -1
    except curses.error:
        background = curses.COLOR_BLACK
    basic = [curses.COLOR_BLUE, curses.COLOR_RED, curses.COLOR_GREEN, curses.COLOR_YELLOW, curses.COLOR_MAGENTA, curses.COLOR_GREEN, curses.COLOR_CYAN, curses.COLOR_RED]
    status_basic = {"good": curses.COLOR_GREEN, "bad": curses.COLOR_RED, "warn": curses.COLOR_YELLOW, "accent": curses.COLOR_CYAN}
    styles = [(i, PALETTE[i], basic[i]) for i in range(len(PALETTE))] + [(name, color, status_basic[name]) for name, color in STATUS.items()]
    for pair, (style, hex_color, fallback) in enumerate(styles, start=1):
        if pair >= curses.COLOR_PAIRS:
            break
        color = xterm256(hex_color) if curses.COLORS >= 256 else fallback
        try:
            curses.init_pair(pair, color, background)
            attrs[style] = curses.color_pair(pair)
        except curses.error:
            attrs[style] = 0
    return attrs


def _draw(curses, stdscr, model, attrs):
    height, width = stdscr.getmaxyx()
    if not model.lock.acquire(blocking=False):
        return False
    try:
        lines = screen(model, width, height)
    finally:
        model.lock.release()
    stdscr.erase()
    for y, line in enumerate(lines):
        x = 0
        for text, style in line:
            if x >= width:
                break
            try:
                stdscr.addstr(y, x, text[: width - x], attrs.get(style, 0))
            except curses.error:
                pass
            x += len(text)
    stdscr.noutrefresh()
    curses.doupdate()
    return True


def _loop(stdscr, model, interval):
    import curses

    try:
        curses.curs_set(0)
    except curses.error:
        pass
    stdscr.keypad(True)
    attrs = _attrs(curses)
    stop, wake = threading.Event(), threading.Event()

    def poll():
        while not stop.is_set():
            wake.wait(interval)
            wake.clear()
            if not stop.is_set():
                model.refresh()

    thread = threading.Thread(target=poll, name="runboard-top", daemon=True)
    thread.start()
    stdscr.timeout(250)
    last = 0.0
    dirty = True
    try:
        while True:
            if dirty or time.time() - last > 1.0:
                if _draw(curses, stdscr, model, attrs):
                    last, dirty = time.time(), False
            try:
                ch = stdscr.get_wch()
            except curses.error:
                continue
            k = translate(curses, ch)
            if k is None:
                continue
            dirty = True
            if k == "resize":
                continue
            with model.lock:
                keep = model.handle(k)
                refresh = model.wants_refresh
                model.wants_refresh = False
            if not keep:
                break
            if refresh:
                wake.set()
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        wake.set()
    return 0


def cmd_top(args):
    if not sys.stdout.isatty() or not sys.stdin.isatty():
        print("runboard: `runboard top` needs an interactive terminal; use `runboard plot` to print charts", file=sys.stderr)
        return 1
    try:
        import curses
    except ImportError:
        print("runboard: this Python has no curses module; use `runboard plot` instead", file=sys.stderr)
        return 1
    try:
        source = Source.resolve(args.server, args.token, args.dir)
        store = Store(source)
        runs = store.refresh_runs()
    except ViewerError as e:
        print(f"runboard: {e}", file=sys.stderr)
        return 1
    keys = []
    for wanted in args.runs:
        matches = pick_runs(runs, wanted)
        if not matches:
            print(f"runboard: no run matches {wanted!r}; `runboard ls` lists them", file=sys.stderr)
            return 1
        keys.extend(key(r) for r in matches if key(r) not in keys)
    opts = {"smoothing": args.smooth, "logy": args.logy, "clip": args.clip, "xmode": args.x}
    model = Model(store, project=args.project, metric=args.metric or "", keys=keys[:MAX_RUNS], opts=opts)
    model.refresh()
    os.environ.setdefault("ESCDELAY", "25")
    try:
        locale.setlocale(locale.LC_ALL, "")
    except locale.Error:
        pass
    return curses.wrapper(_loop, model, max(1.0, args.interval))
