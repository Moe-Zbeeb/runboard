import atexit
import json
import os
import secrets
import socket
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import quote

from . import config as _config
from .storage import Storage, clean_row, valid_name


def _warn(msg):
    print(f"[runboard] {msg}", file=sys.stderr, flush=True)


def _sanitize(name):
    s = "".join(c if c.isalnum() or c in "._-" else "-" for c in str(name))[:128].strip(".")
    return s or "default"


def post_json(server, token, project, run_id, payload, timeout=10):
    url = f"{server.rstrip('/')}/api/runs/{quote(project)}/{quote(run_id)}"
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read() or b"{}")


class _HttpSink:
    def __init__(self, server, token):
        self.server = server
        self.token = token

    def send(self, project, run_id, meta, rows):
        payload = {"rows": rows}
        if meta:
            payload["meta"] = meta
        post_json(self.server, self.token, project, run_id, payload)


class _FileSink:
    def __init__(self, root):
        self.storage = Storage(root)

    def send(self, project, run_id, meta, rows):
        if meta:
            self.storage.update_meta(project, run_id, meta)
        if rows:
            self.storage.append_rows(project, run_id, rows)


class _NullSink:
    def send(self, project, run_id, meta, rows):
        pass


def _is_nonzero_rank():
    for var in ("RANK", "LOCAL_RANK"):
        v = os.environ.get(var)
        if v not in (None, "", "0"):
            return True
    return False


def spool_dir():
    return _config.home() / "spool"


class Run:
    def __init__(
        self,
        project="default",
        name=None,
        config=None,
        run_id=None,
        server=None,
        token=None,
        dir=None,
        mode=None,
        tags=None,
        flush_interval=1.0,
        max_buffer=1_000_000,
        chunk_size=5000,
        all_ranks=False,
    ):
        self.project = _sanitize(project)
        self.run_id = run_id or time.strftime("%Y%m%d-%H%M%S-") + secrets.token_hex(3)
        if not valid_name(self.run_id):
            raise ValueError(f"invalid run_id {self.run_id!r}")
        self.name = name or self.run_id
        self.config = dict(config or {})
        self.flush_interval = flush_interval
        self.max_buffer = max_buffer
        self.chunk_size = chunk_size
        self._send_lock = threading.Lock()
        self._step = 0
        self._seq = 0
        self._sid = secrets.token_hex(4)
        self._rows = []
        self._meta = {}
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._finished = False
        self._warned = False
        if _is_nonzero_rank() and not all_ranks:
            self._sink, self.mode = _NullSink(), "disabled"
        else:
            self._sink, self.mode = self._resolve_sink(server, token, dir, mode)
        self._set_meta(
            name=self.name,
            config=self.config,
            tags=list(tags or []),
            status="running",
            created=time.time(),
            host=socket.gethostname(),
            argv=sys.argv,
            slurm_job_id=os.environ.get("SLURM_JOB_ID"),
            mode=self.mode,
        )
        self._thread = threading.Thread(target=self._loop, name="runboard-sender", daemon=True)
        self._thread.start()
        atexit.register(self.finish)

    def _resolve_sink(self, server, token, dir, mode):
        dir = dir or os.environ.get("RUNBOARD_DIR")
        if mode == "file" or (mode is None and dir):
            root = dir or "./runboard-runs"
            return _FileSink(root), "file"
        info = _config.read_server_info()
        server = server or os.environ.get("RUNBOARD_SERVER") or info.get("url")
        token = token or os.environ.get("RUNBOARD_TOKEN") or info.get("token")
        if server and token:
            try:
                urllib.request.urlopen(
                    urllib.request.Request(f"{server.rstrip('/')}/api/health", headers={"Authorization": f"Bearer {token}"}),
                    timeout=3,
                ).close()
            except urllib.error.HTTPError as e:
                if e.code == 401:
                    _warn(f"server {server} rejected the token; metrics will be buffered (check RUNBOARD_TOKEN)")
            except OSError as e:
                _warn(f"server {server} not reachable yet ({e}); metrics will be buffered and retried")
            return _HttpSink(server, token), "http"
        if mode == "http":
            raise ValueError("http mode needs a server and token (args, RUNBOARD_SERVER/RUNBOARD_TOKEN, or `runboard serve`)")
        _warn("no server found; writing to ./runboard-runs (run `runboard serve --dir ./runboard-runs` to view)")
        return _FileSink("./runboard-runs"), "file"

    def _set_meta(self, **kw):
        with self._lock:
            self._meta.update(kw)

    def log(self, data, step=None):
        if self._finished:
            return
        if step is None:
            step = self._step
        self._step = max(self._step, int(step) + 1)
        row = clean_row(dict(data))
        row["_step"] = int(step)
        row["_time"] = time.time()
        with self._lock:
            row["_seq"] = self._seq
            row["_sid"] = self._sid
            self._seq += 1
            self._rows.append(row)
            if len(self._rows) > self.max_buffer:
                overflow = self._rows[: len(self._rows) - self.max_buffer]
                del self._rows[: len(overflow)]
                self._spool({}, overflow)

    def _take(self):
        with self._lock:
            rows, meta = self._rows, self._meta
            self._rows, self._meta = [], {}
        return meta, rows

    def _restore(self, meta, rows):
        with self._lock:
            self._rows[:0] = rows
            self._meta = {**meta, **self._meta}

    def _flush_once(self):
        with self._send_lock:
            meta, rows = self._take()
            if not meta and not rows:
                return True
            sent = 0
            try:
                while True:
                    chunk = rows[sent : sent + self.chunk_size]
                    try:
                        self._sink.send(self.project, self.run_id, meta, chunk)
                    except urllib.error.HTTPError as e:
                        if 400 <= e.code < 500 and e.code not in (401, 408, 429):
                            _warn(f"server rejected {len(chunk)} rows ({e.code}: {e.reason}); dropping them")
                        else:
                            raise
                    meta = {}
                    sent += len(chunk)
                    if sent >= len(rows):
                        break
                if self._warned:
                    _warn("connection restored")
                    self._warned = False
                return True
            except Exception as e:
                self._restore(meta, rows[sent:])
                if not self._warned:
                    _warn(f"could not send metrics ({e}); buffering and retrying")
                    self._warned = True
                return False

    def _loop(self):
        delay = self.flush_interval
        while not self._stop.wait(delay):
            try:
                ok = self._flush_once()
            except Exception:
                ok = False
            delay = self.flush_interval if ok else min(delay * 2, 30.0)

    def _spool(self, meta, rows):
        d = spool_dir()
        d.mkdir(parents=True, exist_ok=True)
        p = d / f"{self.project}__{self.run_id}.jsonl"
        with open(p, "a") as f:
            f.write(json.dumps({"project": self.project, "run_id": self.run_id, "meta": meta, "rows": rows}) + "\n")
        return p

    def finish(self, status="finished", timeout=10.0):
        if self._finished:
            return
        self._finished = True
        self._stop.set()
        self._thread.join(timeout=5)
        self._set_meta(status=status, finished=time.time())
        deadline = time.time() + timeout
        while True:
            if self._flush_once():
                return
            if time.time() >= deadline:
                break
            time.sleep(1.0)
        with self._send_lock:
            meta, rows = self._take()
        try:
            p = self._spool(meta, rows)
            _warn(f"server unreachable; {len(rows)} rows saved to {p}. Upload later with `runboard sync`.")
        except OSError as e:
            _warn(f"server unreachable and could not write spool file ({e}); {len(rows)} rows lost")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.finish("crashed" if exc_type else "finished")


def sync(server=None, token=None, paths=None):
    info = _config.read_server_info()
    server = server or os.environ.get("RUNBOARD_SERVER") or info.get("url")
    token = token or os.environ.get("RUNBOARD_TOKEN") or info.get("token")
    if not (server and token):
        raise ValueError("no server/token configured")
    files = [Path(p) for p in paths] if paths else sorted(spool_dir().glob("*.jsonl"))
    total = 0
    for p in files:
        for line in p.read_text().splitlines():
            if not line.strip():
                continue
            rec = json.loads(line)
            rows = rec["rows"]
            meta = rec.get("meta") or None
            for i in range(0, max(len(rows), 1), 5000):
                post_json(server, token, rec["project"], rec["run_id"], {"meta": meta, "rows": rows[i : i + 5000]})
                meta = None
            total += len(rows)
        p.unlink()
    return total
