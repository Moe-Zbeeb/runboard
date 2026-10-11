import atexit
import json
import os
import secrets
import signal
import socket
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import quote

from . import config as _config
from .storage import Storage, clean_meta, clean_row, valid_name


def _warn(msg):
    print(f"[runboard] {msg}", file=sys.stderr, flush=True)


def _sanitize(name):
    s = "".join(c if c.isalnum() or c in "._-" else "-" for c in str(name))[:128].strip(".")
    return s or "default"


def post_json(server, token, project, run_id, payload, timeout=10):
    url = f"{server.rstrip('/')}/api/runs/{quote(project)}/{quote(run_id)}"
    req = urllib.request.Request(
        url,
        data=json.dumps(payload, default=str).encode(),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}", "User-Agent": "runboard/0.3"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read() or b"{}")


class _HttpSink:
    def __init__(self, server, token):
        self.server = server
        self.token = token

    def send(self, project, run_id, meta, rows):
        pending = [(meta, rows)]
        while pending:
            batch_meta, batch = pending.pop()
            payload = {"rows": batch}
            if batch_meta:
                payload["meta"] = clean_meta(batch_meta)
            try:
                post_json(self.server, self.token, project, run_id, payload)
            except urllib.error.HTTPError as e:
                if e.code != 413 or len(batch) < 2:
                    raise
                middle = len(batch) // 2
                pending.append(({}, batch[middle:]))
                pending.append((batch_meta, batch[:middle]))


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


_live_runs = []
_previous_sigterm = None
_sigterm_installed = False

_spool_lock = threading.Lock()
_spool_clock = 0


def _spool_stamp():
    global _spool_clock
    with _spool_lock:
        _spool_clock = max(time.time_ns(), _spool_clock + 1)
        return _spool_clock


def _on_sigterm(signum, frame):
    for run in list(_live_runs):
        run.finish("killed")
    if callable(_previous_sigterm):
        _previous_sigterm(signum, frame)
        return
    signal.signal(signal.SIGTERM, signal.SIG_DFL)
    os.kill(os.getpid(), signal.SIGTERM)


def _install_sigterm_handler():
    global _previous_sigterm, _sigterm_installed
    if _sigterm_installed or threading.current_thread() is not threading.main_thread():
        return
    try:
        previous = signal.getsignal(signal.SIGTERM)
        if previous is None or previous is signal.SIG_IGN:
            return
        signal.signal(signal.SIGTERM, _on_sigterm)
    except (ValueError, OSError):
        return
    _previous_sigterm = previous
    _sigterm_installed = True


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
        flush_interval=None,
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
        self.max_buffer = max_buffer
        self.chunk_size = chunk_size
        self._send_lock = threading.Lock()
        self._step = 0
        self._seq = 0
        self._sid = secrets.token_hex(8)
        self._rows = []
        self._overflow = []
        self._pending_spools = []
        self._meta = {}
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._finished = False
        self._delivered = False
        self._warned = False
        if _is_nonzero_rank() and not all_ranks:
            self._sink, self.mode = _NullSink(), "disabled"
        else:
            self._sink, self.mode = self._resolve_sink(server, token, dir, mode)
        if flush_interval is None:
            flush_interval = 10.0 if self.mode == "http" else 1.0
        self.flush_interval = flush_interval
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
        if self.mode != "disabled":
            _live_runs.append(self)
            _install_sigterm_handler()

    def _resolve_sink(self, server, token, dir, mode):
        dir = dir or os.environ.get("RUNBOARD_DIR")
        if mode == "file" or (mode is None and dir):
            root = dir or "./runboard-runs"
            return _FileSink(root), "file"
        info = _config.read_server_info()
        server = server or os.environ.get("RUNBOARD_SERVER") or info.get("url")
        token = token or os.environ.get("RUNBOARD_TOKEN") or info.get("token")
        if server and token:
            server = _config.normalize_url(server)
            try:
                urllib.request.urlopen(
                    urllib.request.Request(
                        f"{server.rstrip('/')}/api/health",
                        headers={"Authorization": f"Bearer {token}", "User-Agent": "runboard/0.3"},
                    ),
                    timeout=3,
                ).close()
            except urllib.error.HTTPError as e:
                if e.code == 401:
                    _warn(f"server {server} rejected the token; metrics will be buffered (check RUNBOARD_TOKEN)")
            except OSError as e:
                _warn(f"server {server} not reachable yet ({e}); metrics will be buffered and retried")
            except ValueError as e:
                _warn(f"server URL {server!r} is invalid ({e}); metrics will be buffered (check RUNBOARD_SERVER)")
            return _HttpSink(server, token), "http"
        if mode == "http":
            raise ValueError("http mode needs a server and token (args, RUNBOARD_SERVER/RUNBOARD_TOKEN, or `runboard serve`)")
        _warn("no server found; writing to ./runboard-runs (run `runboard serve --dir ./runboard-runs` to view)")
        return _FileSink("./runboard-runs"), "file"

    def _set_meta(self, **kw):
        with self._lock:
            self._meta.update(clean_meta(kw))

    def log(self, data, step=None):
        if self._finished:
            return
        row = clean_row(dict(data))
        with self._lock:
            self._append(row, step, time.time(), self._sid, self._seq)
            self._seq += 1

    def _log_row(self, metrics, step, when, sid, seq):
        if self._finished:
            return
        row = clean_row(dict(metrics))
        with self._lock:
            self._append(row, step, time.time() if when is None else when, sid, seq)

    def _append(self, row, step, when, sid, seq):
        if step is None:
            step = self._step
        self._step = max(self._step, int(step) + 1)
        row["_step"] = int(step)
        row["_time"] = when
        row["_seq"] = seq
        row["_sid"] = sid
        self._rows.append(row)
        if len(self._rows) > self.max_buffer:
            overflow = self._rows[: len(self._rows) - self.max_buffer]
            del self._rows[: len(overflow)]
            self._overflow.extend(overflow)

    def _take(self):
        with self._lock:
            rows, meta = self._overflow, self._meta
            rows.extend(self._rows)
            self._rows, self._meta = [], {}
            self._overflow = []
        return meta, rows

    def _restore(self, meta, rows):
        with self._lock:
            pending = rows + self._overflow + self._rows
            cut = max(0, len(pending) - self.max_buffer)
            self._overflow, self._rows = pending[:cut], pending[cut:]
            self._meta = {**meta, **self._meta}

    def _is_empty(self):
        with self._lock:
            return not (self._pending_spools or self._overflow or self._rows or self._meta)

    def _out_of_time(self, deadline):
        if deadline is None:
            return self._stop.is_set()
        return time.time() >= deadline

    def _flush_once(self, deadline=None):
        with self._send_lock:
            with self._lock:
                pending_spool = self._pending_spools[0] if self._pending_spools else None
            if pending_spool is not None:
                return self._flush_spool(pending_spool, deadline)
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
                    if self._out_of_time(deadline):
                        self._restore(meta, rows[sent:])
                        return True
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

    def _forget_spool(self, path):
        with self._lock:
            if path in self._pending_spools:
                self._pending_spools.remove(path)

    def _flush_spool(self, path, deadline=None):
        try:
            rec = json.loads(path.read_text())
            if not isinstance(rec, dict) or not isinstance(rec.get("rows", []), list):
                raise ValueError("not a spool record")
        except FileNotFoundError:
            _warn(f"spool file {path} disappeared (uploaded by `runboard sync`?); skipping it")
            self._forget_spool(path)
            return True
        except ValueError as e:
            bad = path.with_suffix(".jsonl.corrupt")
            _warn(f"spool file {path} is unreadable ({e}); moved to {bad}")
            try:
                os.replace(path, bad)
            except OSError:
                pass
            self._forget_spool(path)
            return True
        except OSError as e:
            if not self._warned:
                _warn(f"could not read spool file {path} ({e}); retrying")
                self._warned = True
            return False
        try:
            rows = rec.get("rows", [])
            meta = rec.get("meta") or {}
            for i in range(0, max(len(rows), 1), self.chunk_size):
                self._sink.send(self.project, self.run_id, meta, rows[i : i + self.chunk_size])
                meta = {}
                if i + self.chunk_size < len(rows) and self._out_of_time(deadline):
                    return True
            try:
                path.unlink()
            except FileNotFoundError:
                pass
            self._forget_spool(path)
            if self._warned:
                _warn("connection restored")
                self._warned = False
            return True
        except urllib.error.HTTPError as e:
            if 400 <= e.code < 500 and e.code not in (401, 408, 429):
                _warn(f"server rejected spooled metrics ({e.code}: {e.reason}); dropping {path}")
                try:
                    path.unlink()
                except OSError:
                    pass
                self._forget_spool(path)
                return True
            if not self._warned:
                _warn(f"could not send metrics ({e}); buffering and retrying")
                self._warned = True
            return False
        except Exception as e:
            if not self._warned:
                _warn(f"could not send metrics ({e}); buffering and retrying")
                self._warned = True
            return False

    def _spill_overflow(self):
        with self._lock:
            rows, self._overflow = self._overflow, []
        if not rows:
            return
        try:
            paths = [self._spool({}, rows[i : i + self.chunk_size * 10]) for i in range(0, len(rows), self.chunk_size * 10)]
        except OSError as e:
            with self._lock:
                self._overflow[:0] = rows
            if not self._warned:
                _warn(f"could not spool metrics ({e}); retaining them in memory")
                self._warned = True
            return
        with self._lock:
            self._pending_spools.extend(paths)

    def _loop(self):
        delay = self.flush_interval
        while not self._stop.wait(delay):
            try:
                self._spill_overflow()
                ok = self._flush_once()
            except Exception:
                ok = False
            if not ok:
                delay = min(max(delay * 2, self.flush_interval), 30.0)
            else:
                with self._lock:
                    delay = 0 if self._pending_spools else self.flush_interval

    def _spool(self, meta, rows):
        d = spool_dir()
        d.mkdir(parents=True, exist_ok=True)
        first_seq = rows[0].get("_seq", 0) if rows else self._seq
        p = d / f"{self.project[:48]}__{self.run_id[:48]}__{_spool_stamp():020d}__{self._sid}__{first_seq:020d}.jsonl"
        tmp = p.with_suffix(".jsonl.tmp")
        tmp.write_text(json.dumps({"project": self.project, "run_id": self.run_id, "meta": meta, "rows": rows}, default=str) + "\n")
        with open(tmp, "rb") as f:
            os.fsync(f.fileno())
        os.replace(tmp, p)
        fd = os.open(d, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
        return p

    def finish(self, status="finished", timeout=10.0):
        if self._finished:
            return
        self._finished = True
        if self in _live_runs:
            _live_runs.remove(self)
        self._stop.set()
        self._thread.join(timeout=5)
        self._set_meta(status=status, finished=time.time())
        deadline = time.time() + timeout
        while True:
            ok = self._flush_once(deadline)
            if ok and self._is_empty():
                self._delivered = True
                return
            if time.time() >= deadline:
                break
            if not ok:
                time.sleep(1.0)
        with self._send_lock:
            meta, rows = self._take()
        if rows or meta:
            try:
                p = self._spool(meta, rows)
                _warn(f"server unreachable; {len(rows)} rows saved to {p}. Upload later with `runboard sync`.")
            except OSError as e:
                self._restore(meta, rows)
                _warn(f"server unreachable and could not write spool file ({e}); metrics remain in memory")
        with self._lock:
            pending_spools = list(self._pending_spools)
        if pending_spools:
            _warn(f"server unreachable; {len(pending_spools)} metric batches saved to {spool_dir()}. Upload later with `runboard sync`.")

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
    sink = _HttpSink(_config.normalize_url(server), token)
    files = [Path(p) for p in paths] if paths else sorted(spool_dir().glob("*.jsonl"))
    total = 0
    for p in files:
        try:
            text = p.read_text()
        except FileNotFoundError:
            continue
        try:
            records = [json.loads(line) for line in text.splitlines() if line.strip()]
            for rec in records:
                if not isinstance(rec, dict) or not isinstance(rec.get("rows"), list) or not (rec.get("project") and rec.get("run_id")):
                    raise ValueError("not a spool record")
        except ValueError as e:
            bad = p.with_suffix(".jsonl.corrupt")
            _warn(f"spool file {p} is unreadable ({e}); moved to {bad}")
            try:
                os.replace(p, bad)
            except OSError:
                pass
            continue
        for rec in records:
            rows = rec["rows"]
            meta = rec.get("meta") or None
            for i in range(0, max(len(rows), 1), 5000):
                sink.send(rec["project"], rec["run_id"], meta, rows[i : i + 5000])
                meta = None
            total += len(rows)
        try:
            p.unlink()
        except FileNotFoundError:
            pass
    return total
