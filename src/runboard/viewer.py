import json
import os
import re
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import quote, urlencode

from . import config
from .storage import Storage
from .termplot import ema

USER_AGENT = "runboard/0.3"


class ViewerError(Exception):
    pass


def key(run):
    return f"{run['project']}/{run['run_id']}"


def short_path(path):
    path = Path(path)
    for base, prefix in ((Path.cwd(), "."), (Path.home(), "~")):
        try:
            return prefix + "/" + str(path.relative_to(base)) if path != base else prefix
        except ValueError:
            continue
    return str(path)


class LocalSource:
    def __init__(self, root):
        self.root = Path(root).expanduser().resolve()
        self.label = short_path(self.root)

    def _storage(self):
        return Storage(self.root) if self.root.is_dir() else None

    def runs(self):
        storage = self._storage()
        return storage.list_runs() if storage else []

    def page(self, project, run_id, offset):
        storage = self._storage()
        return storage.read_rows(project, run_id, offset) if storage else ([], offset)


class HttpSource:
    def __init__(self, server, token):
        self.server = config.normalize_url(server)
        self.token = token
        self.label = self.server

    def _get(self, path):
        request = urllib.request.Request(
            self.server + path,
            headers={"Authorization": f"Bearer {self.token}", "User-Agent": USER_AGENT},
        )
        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                return json.loads(response.read() or b"null")
        except urllib.error.HTTPError as e:
            if e.code == 401:
                raise ViewerError(f"{self.server} rejected the token; run `runboard configure {self.server}` or pass --token") from None
            raise ViewerError(f"{self.server} returned HTTP {e.code}; run `runboard doctor`") from None
        except urllib.error.URLError as e:
            raise ViewerError(f"cannot reach {self.server} ({e.reason})") from None
        except (OSError, ValueError) as e:
            raise ViewerError(f"cannot read from {self.server} ({e})") from None

    def runs(self):
        runs = self._get("/api/runs")
        return runs if isinstance(runs, list) else []

    def page(self, project, run_id, offset):
        query = urlencode({"project": project, "run": run_id, "offset": offset}, quote_via=quote)
        page = self._get(f"/api/metrics?{query}") or {}
        return page.get("rows") or [], page.get("offset", offset)


class Source:
    @staticmethod
    def resolve(server=None, token=None, dir=None):
        if dir is None and (server or not os.environ.get("RUNBOARD_DIR")):
            if server:
                secret = token or config.paired_token(server)[0]
                if not secret:
                    raise ViewerError(f"no token for {server}; pass --token")
                return HttpSource(server, secret)
            info = config.read_server_info()
            url = os.environ.get("RUNBOARD_SERVER") or info.get("url")
            secret = token or os.environ.get("RUNBOARD_TOKEN") or info.get("token")
            if url and secret:
                return HttpSource(url, secret)
        return LocalSource(dir or os.environ.get("RUNBOARD_DIR") or "./runboard-runs")


class RunData:
    def __init__(self):
        self.offset = 0
        self.t0 = None
        self.seen = None
        self.series = {}

    def ingest(self, rows):
        for row in rows:
            step, t = row.get("_step"), row.get("_time")
            if self.t0 is None and t is not None:
                self.t0 = t
            for name, value in row.items():
                if name.startswith("_"):
                    continue
                s = self.series.get(name)
                if s is None:
                    s = self.series[name] = {"step": [], "t": [], "y": []}
                s["step"].append(step)
                s["t"].append(t)
                s["y"].append(value)


class Store:
    def __init__(self, source):
        self.source = source
        self.runs = {}
        self.data = {}

    def refresh_runs(self):
        runs = self.source.runs()
        self.runs = {key(r): r for r in runs}
        for k in [k for k in self.data if k not in self.runs]:
            del self.data[k]
        return sorted(runs, key=lambda r: r.get("created") or 0, reverse=True)

    def load(self, k):
        run = self.runs.get(k)
        if run is None:
            return False
        data = self.data.get(k)
        if data is None:
            data = self.data[k] = RunData()
        seen = f"{run.get('status')}|{run.get('updated')}"
        if data.seen == seen:
            return False
        changed = False
        for _ in range(1000):
            rows, offset = self.source.page(run["project"], run["run_id"], data.offset)
            if rows:
                data.ingest(rows)
                changed = True
            done = offset == data.offset or not rows
            data.offset = offset
            if done:
                break
        data.seen = seen
        return changed


def default_selection(runs, project, n):
    if not runs:
        return []
    recent = sorted(runs, key=lambda r: r.get("updated") or 0, reverse=True)
    target = project or recent[0]["project"]
    return [key(r) for r in recent if r["project"] == target][:n]


def series_for(data, metric, xmode, smoothing):
    s = data.series.get(metric) if data else None
    if not s:
        return [], []
    if xmode == "minutes":
        t0 = data.t0 or 0
        xs = [None if t is None else (t - t0) / 60 for t in s["t"]]
    elif xmode == "time":
        xs = list(s["t"])
    else:
        xs = list(s["step"])
    ys = list(s["y"])
    pairs = [(x, y) for x, y in zip(xs, ys) if x is not None]
    if any(pairs[i][0] < pairs[i - 1][0] for i in range(1, len(pairs))):
        pairs.sort(key=lambda p: p[0])
    xs, ys = [p[0] for p in pairs], [p[1] for p in pairs]
    return xs, ema(ys, smoothing)


def binned_prefixes(run):
    items = ((run or {}).get("config") or {}).get("runboard_binned_charts")
    if not isinstance(items, list):
        return []
    return [item["prefix"] for item in items if isinstance(item, dict) and isinstance(item.get("prefix"), str)]


def matcher(text):
    if not text:
        return lambda s: True
    try:
        pattern = re.compile(text, re.IGNORECASE)
        return lambda s: bool(pattern.search(s))
    except re.error:
        needle = text.lower()
        return lambda s: needle in s.lower()


def metric_names(datas, filter_text="", hidden=()):
    names = set()
    for data in datas:
        if data:
            names.update(data.series)
    match = matcher(filter_text)
    hide = tuple(prefix + "/bin_" for prefix in hidden)
    return sorted(m for m in names if match(m) and (filter_text or not (hide and m.startswith(hide))))


def group_of(metric):
    i = metric.find("/")
    return metric[:i] if i > 0 else ""


def latest(data, metric):
    s = data.series.get(metric) if data else None
    if not s:
        return None
    for step, y in zip(reversed(s["step"]), reversed(s["y"])):
        if y is not None:
            return step, y
    return None
