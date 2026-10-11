import hashlib
import json
import os
import secrets
import sys
import time
from pathlib import Path

from . import config as _config
from .follow import discover
from .follow.csvfile import CsvFollower
from .follow.hf_state import CHECKPOINT, HfStateFollower
from .follow.jsonl import JsonlFollower
from .follow.tensorboard import TensorBoardFollower
from .follow.text import StreamFollower, TextFollower

SCAN_INTERVAL = 5.0
STDOUT_GRACE = 60.0
LABELS = {
    "tensorboard": "TensorBoard",
    "table": "JSONL/CSV",
    "hf": "Hugging Face trainer_state.json",
    "stdout": "text log",
    "explicit": "explicit",
}


def _say(message):
    print(f"runboard: {message}", file=sys.stderr, flush=True)


def follower_for(kind, path, state=None):
    if kind == "tensorboard":
        return TensorBoardFollower(path, state)
    if kind == "hf":
        return HfStateFollower(path, state)
    if kind == "stdout":
        return TextFollower(path, state)
    if str(path).endswith(".csv"):
        return CsvFollower(path, state)
    return JsonlFollower(path, state)


def _hf_root(path):
    path = Path(path)
    return path.parent.parent if CHECKPOINT.match(path.parent.name) else path.parent


def _relative(path):
    try:
        return os.path.relpath(path)
    except ValueError:
        return str(path)


def state_path(project, run_id):
    return _config.home() / "followers" / f"{project}__{run_id}.json"


def load_state(path):
    try:
        data = json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


class Streamer:
    def __init__(self, run, roots=(), sources=(), since=0.0, stdout="off", state_file=None, config=None, clock=time.time):
        self.run = run
        self.roots = [Path(root) for root in roots]
        self.sources = [(kind, Path(path).resolve()) for kind, path in sources]
        self.since = since
        self.stdout = stdout
        self.state_file = Path(state_file) if state_file else None
        self.config = config
        self.clock = clock
        self.started = clock()
        saved = load_state(self.state_file) if self.state_file else {}
        self.nonce = saved.get("nonce") or secrets.token_hex(8)
        self.created = saved.get("created")
        self.saved = saved.get("sources") if isinstance(saved.get("sources"), dict) else {}
        self.followers = {}
        self.kind = None
        self.last_scan = None
        self.rows = 0
        self.streams = {"stdout": StreamFollower(), "stderr": StreamFollower()}
        self.stream_sid = secrets.token_hex(8)
        self.stream_count = 0
        self.parsing_output = False

    def feed(self, name, data):
        self.streams[name].feed(data)

    def _sid(self, kind, key):
        return hashlib.sha256(f"{self.nonce}:{kind}:{key}".encode()).hexdigest()[:16]

    def _add(self, kind, path):
        key = str(path)
        if key in self.followers:
            return False
        saved = self.saved.get(key)
        saved = saved if isinstance(saved, dict) else {}
        same = saved.get("kind") == kind
        self.followers[key] = {
            "kind": kind,
            "follower": follower_for(kind, path, saved.get("state") if same else None),
            "sid": self._sid(kind, key),
            "count": int(saved.get("count", 0)) if same else 0,
        }
        return True

    def _set_kind(self, kind):
        self.kind = kind
        if self.config is not None:
            self.run._set_meta(config=dict(self.config, source=LABELS[kind]))

    def _announce(self):
        names = [_relative(key) for key in sorted(self.followers)]
        shown = ", ".join(names[:3]) + (f", and {len(names) - 3} more" if len(names) > 3 else "")
        noun = "source" if len(names) == 1 else "sources"
        _say(f"following {len(names)} {LABELS[self.kind]} {noun}: {shown}")

    def scan(self):
        self.last_scan = self.clock()
        if self.sources:
            if self.kind is None:
                self._set_kind("explicit")
            if any([self._add(kind, path) for kind, path in self.sources]):
                self._announce()
            return
        kind, paths = discover.best(discover.scan(self.roots, since=self.since))
        if kind is None:
            return
        if self.kind in discover.PRIORITY and discover.PRIORITY.index(kind) > discover.PRIORITY.index(self.kind):
            return
        if kind != self.kind:
            for key in [key for key, entry in self.followers.items() if entry["kind"] != kind]:
                del self.followers[key]
            self._set_kind(kind)
        if kind == "hf":
            paths = sorted({_hf_root(path) for path in paths})
        if any([self._add(kind, path) for path in paths]):
            self._announce()

    def _stdout_active(self, final):
        if self.stdout == "on":
            return True
        if self.stdout != "auto" or self.kind is not None:
            return False
        return final or self.clock() - self.started >= STDOUT_GRACE

    def _log(self, record, sid, seq):
        self.run._log_row(record.metrics, record.step, record.time, sid, seq)
        self.rows += 1

    def pump(self, final=False):
        logged = 0
        for key in sorted(self.followers):
            entry = self.followers[key]
            for record in entry["follower"].poll():
                self._log(record, entry["sid"], entry["count"])
                entry["count"] += 1
                logged += 1
        if self._stdout_active(final):
            if not self.parsing_output:
                self.parsing_output = True
                if self.stdout == "auto":
                    _say("no metric files found; reading metrics from command output")
            for name in ("stdout", "stderr"):
                for record in self.streams[name].poll(final=final):
                    self._log(record, self.stream_sid, self.stream_count)
                    self.stream_count += 1
                    logged += 1
        elif self.stdout == "off" or self.kind is not None:
            for stream in self.streams.values():
                stream.discard()
        return logged

    def step(self):
        if self.last_scan is None or self.clock() - self.last_scan >= SCAN_INTERVAL:
            self.scan()
        logged = self.pump()
        self.checkpoint()
        return logged

    def drain(self):
        self.scan()
        total = 0
        for _ in range(1000):
            logged = self.pump(final=True)
            total += logged
            if not logged:
                break
        return total

    def snapshot(self):
        sources = dict(self.saved)
        for key, entry in self.followers.items():
            sources[key] = {"kind": entry["kind"], "count": entry["count"], "state": entry["follower"].state()}
        return {"nonce": self.nonce, "created": self.created, "sources": sources}

    def save(self):
        if self.state_file is None:
            return
        tmp = self.state_file.with_name(self.state_file.name + ".tmp")
        try:
            _config.write_private(tmp, json.dumps(self.snapshot()))
            os.replace(tmp, self.state_file)
        except OSError as e:
            _say(f"could not save follower state ({e})")

    def checkpoint(self):
        lock = self.run._send_lock
        if not lock.acquire(blocking=False):
            return False
        try:
            if not self.run._is_empty():
                return False
            self.save()
            return True
        finally:
            lock.release()
