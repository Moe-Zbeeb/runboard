import os
import shutil
import sys
import time

from .termplot import Line, fmt, render_chart, render_legend, tick, to_ansi
from .viewer import Source, Store, ViewerError, binned_prefixes, default_selection, group_of, latest, metric_names, series_for

MAX_RUNS = 8
STATUS_STYLE = {"running": "good", "finished": None, "crashed": "bad", "killed": "bad", "stale": "warn"}
STATUS_MARK = {"running": "●", "finished": "✓", "crashed": "✕", "killed": "✕", "stale": "!"}
STALE_S = 600
X_FORMATS = {
    "step": tick,
    "minutes": lambda v: tick(v) + "m",
    "time": lambda v: time.strftime("%H:%M", time.localtime(v)),
}


def status_of(run, now=None):
    status = run.get("status") or "running"
    now = time.time() if now is None else now
    if status == "running" and run.get("updated") and now - run["updated"] > STALE_S:
        return "stale"
    return status


def metric_block(store, keys, slots, metric, width, height, opts):
    series, items, last_step = [], [], None
    for k in keys:
        data = store.data.get(k)
        xs, ys = series_for(data, metric, opts.get("xmode", "step"), opts.get("smoothing", 0))
        if not xs:
            continue
        series.append({"xs": xs, "ys": ys, "color": slots[k]})
        last = latest(data, metric)
        if last:
            last_step = last[0] if last_step is None or (last[0] or 0) > last_step else last_step
        items.append((store.runs[k].get("name") or k, fmt(last[1]) if last else "–", slots[k]))
    right = f"step {last_step:,}" if isinstance(last_step, int) and opts.get("xmode", "step") == "step" else ""
    lines = render_chart(
        series, width, height, title=metric, right=right,
        logy=opts.get("logy", False), clip=opts.get("clip", False), xfmt=X_FORMATS.get(opts.get("xmode", "step"), tick),
        xrange=opts.get("xrange"),
    )
    return lines + render_legend(items, width)


def section_line(name, count, width):
    label = f" {name or 'metrics'} "
    line = Line(width).add("──", "dim").add(label, "bold").add(f"{count} chart{'s' if count != 1 else ''} ", "dim")
    return line.add("─" * width, "dim").done()


def grid(store, keys, slots, metrics, width, height, opts, columns, gap=3, index=None):
    lines = []
    groups = {}
    for m in metrics:
        groups.setdefault(group_of(m), []).append(m)
    for g in sorted(groups, key=lambda name: (name != "", name)):
        names = groups[g]
        per_row = max(1, min(columns, len(names)))
        col_w = max(30, (width - gap * (per_row - 1)) // per_row)
        lines.append(Line(width).done())
        lines.append(section_line(g, len(names), width))
        for start in range(0, len(names), per_row):
            blocks = [metric_block(store, keys, slots, m, col_w, height, opts) for m in names[start:start + per_row]]
            tall = max(len(b) for b in blocks)
            blank = Line(col_w).done()
            for b in blocks:
                b.extend([blank] * (tall - len(b)))
            lines.append(Line(width).done())
            if index is not None:
                index.extend((len(lines), m) for m in names[start:start + per_row])
            for row in range(tall):
                merged = Line(width)
                for i, b in enumerate(blocks):
                    if i:
                        merged.add(" " * gap)
                    for text, style in b[row]:
                        merged.add(text, style)
                lines.append(merged.done())
    return lines


def run_header(store, keys, slots, width, title):
    line = Line(width).add("runboard", "bold").add("  ").add(title, "dim")
    lines = [line.done()]
    current = Line(width)
    for k in keys:
        run = store.runs[k]
        status = status_of(run)
        piece = f"{run.get('name') or k}  {STATUS_MARK.get(status, '·')} {status}"
        if current.length and current.length + len(piece) + 5 > width:
            lines.append(current.done())
            current = Line(width)
        if current.length:
            current.add("   ")
        current.add("■ ", slots[k]).add(run.get("name") or k).add("  ").add(f"{STATUS_MARK.get(status, '·')} {status}", STATUS_STYLE.get(status) or "dim")
    lines.append(current.done())
    return lines


def color_mode(choice, stream=None):
    stream = stream or sys.stdout
    if choice == "never":
        return None
    if choice == "auto" and (not stream.isatty() or os.environ.get("NO_COLOR") or os.environ.get("TERM") == "dumb"):
        return None
    return "truecolor" if os.environ.get("COLORTERM", "").lower() in ("truecolor", "24bit") else "256"


def pick_runs(runs, wanted):
    exact = [r for r in runs if wanted in (r["run_id"], r.get("name"), f"{r['project']}/{r['run_id']}")]
    if exact:
        return exact
    needle = wanted.lower()
    return [r for r in runs if needle in r["run_id"].lower() or needle in (r.get("name") or "").lower()]


def _fail(message):
    print(f"runboard: {message}", file=sys.stderr)
    return 1


def cmd_plot(args):
    try:
        source = Source.resolve(args.server, args.token, args.dir)
        store = Store(source)
        runs = store.refresh_runs()
    except ViewerError as e:
        return _fail(str(e))
    if args.project:
        runs = [r for r in runs if r["project"] == args.project]
    if not runs:
        where = f" for project {args.project}" if args.project else ""
        return _fail(f"no runs found in {source.label}{where}")
    if args.runs:
        keys = []
        for wanted in args.runs:
            matches = pick_runs(runs, wanted)
            if not matches:
                return _fail(f"no run matches {wanted!r}; `runboard ls` lists them")
            keys.extend(f"{r['project']}/{r['run_id']}" for r in matches if f"{r['project']}/{r['run_id']}" not in keys)
    else:
        keys = default_selection(runs, args.project, 3)
    keys = keys[:MAX_RUNS]
    try:
        for k in keys:
            store.load(k)
    except ViewerError as e:
        return _fail(str(e))
    hidden = [p for k in keys for p in binned_prefixes(store.runs[k])]
    metrics = metric_names([store.data.get(k) for k in keys], args.metric or "", hidden)
    if not metrics:
        return _fail(f"no metrics match {args.metric!r}" if args.metric else "the selected runs have not logged any metrics yet")
    width = args.width or shutil.get_terminal_size((100, 40)).columns
    columns = args.columns or max(1, min(3, width // 56))
    slots = {k: i for i, k in enumerate(keys)}
    projects = sorted({store.runs[k]["project"] for k in keys})
    opts = {"smoothing": args.smooth, "logy": args.logy, "clip": args.clip, "xmode": args.x}
    lines = run_header(store, keys, slots, width, f"{', '.join(projects)}  {source.label}")
    lines += grid(store, keys, slots, metrics, width, args.height, opts, columns)
    print(to_ansi(lines, color_mode(args.color)))
    return 0
