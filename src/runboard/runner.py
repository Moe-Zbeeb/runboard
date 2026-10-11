import hashlib
import json
import os
import re
import secrets
import signal
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

from . import config as _config
from .client import Run
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


def _primary():
    return all(os.environ.get(name) in (None, "", "0") for name in ("RANK", "LOCAL_RANK", "SLURM_PROCID"))


def build_config(command, config_json=None, pairs=()):
    config = {"command": list(command), "cwd": os.getcwd()}
    if config_json:
        try:
            loaded = json.loads(Path(config_json).read_text())
        except (OSError, ValueError) as e:
            _say(f"ignoring --config-json {config_json} ({e})")
            loaded = None
        if isinstance(loaded, dict):
            config.update(loaded)
    for pair in pairs:
        key, sep, value = pair.partition("=")
        if not sep or not key:
            _say(f"ignoring --config {pair!r}; expected KEY=VALUE")
            continue
        try:
            config[key] = json.loads(value)
        except ValueError:
            config[key] = value
    return config


def default_name(command):
    script = Path(command[0]).name
    for index, part in enumerate(command[1:], 1):
        if part == "-m" and index + 1 < len(command):
            script = command[index + 1].rsplit(".", 1)[-1]
            break
        if part.endswith(".py"):
            script = Path(part).stem
            break
    return f"{script}-{time.strftime('%Y%m%d-%H%M%S')}"


def _pipe(source, sink, streamer, name):
    fd = source.fileno()
    while True:
        try:
            chunk = os.read(fd, 65536)
        except OSError:
            break
        if not chunk:
            break
        try:
            sink.write(chunk)
            sink.flush()
        except (OSError, ValueError, TypeError):
            pass
        if streamer is not None:
            streamer.feed(name, chunk)
    try:
        source.close()
    except OSError:
        pass


def _status(code, received):
    if code == 0:
        return "finished"
    if code < 0 or code in (128 + signal.SIGINT, 128 + signal.SIGTERM) or signal.SIGTERM in received:
        return "killed"
    return "crashed"


def _summary(run, streamer, status):
    if streamer.rows:
        _say(f"{status}: logged {streamer.rows} rows to {run.project}/{run.run_id}")
    else:
        _say(
            f"{status}: no metrics found for {run.project}/{run.run_id}; write TensorBoard, JSONL, or CSV logs, "
            "or print lines like 'step=1 loss=0.5' (see `runboard guide`)"
        )


def run_command(args):
    command = list(args.command)
    if command and command[0] == "--":
        command = command[1:]
    if not command:
        _say("nothing to run; usage: runboard run [options] -- <command ...>")
        return 2
    started = time.time()
    run = streamer = None
    if _primary():
        try:
            config = build_config(command, args.config_json, args.config)
            run = Run(project=args.project or Path.cwd().name, name=args.name or default_name(command), run_id=args.run_id, config=config)
            sources = [(discover.classify(path) or "stdout", Path(path)) for path in args.source]
            streamer = Streamer(
                run,
                roots=[Path.cwd()] + [Path(path) for path in args.watch],
                sources=sources,
                since=started - 2,
                stdout=args.stdout,
                state_file=state_path(run.project, run.run_id),
                config=config,
            )
            _say(f"logging {run.project}/{run.run_id} ({run.mode} mode)")
        except Exception as e:
            _say(f"setup failed ({e}); running the command without Runboard")
            if run is not None:
                run.finish("crashed")
            run = streamer = None
    pipe = streamer is not None and args.stdout != "off"
    env = dict(os.environ)
    if pipe:
        env.setdefault("PYTHONUNBUFFERED", "1")
    received = []
    children = []

    def on_term(signum, frame):
        received.append(signum)
        for child in children:
            try:
                child.send_signal(signum)
            except OSError:
                pass

    previous_term = signal.signal(signal.SIGTERM, on_term)
    previous_int = signal.signal(signal.SIGINT, lambda signum, frame: received.append(signum))
    try:
        child = subprocess.Popen(command, env=env, stdout=subprocess.PIPE if pipe else None, stderr=subprocess.PIPE if pipe else None)
    except OSError as e:
        signal.signal(signal.SIGTERM, previous_term)
        signal.signal(signal.SIGINT, previous_int)
        _say(f"cannot start {command[0]}: {e}")
        if run is not None:
            run.finish("crashed")
        return 127
    children.append(child)
    if signal.SIGTERM in received:
        child.send_signal(signal.SIGTERM)
    readers = []
    if pipe:
        outputs = ((child.stdout, getattr(sys.stdout, "buffer", sys.stdout), "stdout"), (child.stderr, getattr(sys.stderr, "buffer", sys.stderr), "stderr"))
        for source, sink, name in outputs:
            reader = threading.Thread(target=_pipe, args=(source, sink, streamer, name), daemon=True)
            reader.start()
            readers.append(reader)
    reported = False
    while True:
        try:
            code = child.wait(timeout=1.0)
            break
        except subprocess.TimeoutExpired:
            pass
        if streamer is not None:
            try:
                streamer.step()
            except Exception as e:
                if not reported:
                    _say(f"metric collection error ({e}); the command keeps running")
                    reported = True
    for reader in readers:
        reader.join(timeout=5)
    signal.signal(signal.SIGTERM, previous_term)
    signal.signal(signal.SIGINT, previous_int)
    if run is not None:
        status = _status(code, received)
        try:
            streamer.drain()
        except Exception as e:
            _say(f"metric collection error ({e})")
        run.finish(status)
        streamer.save()
        _summary(run, streamer, status)
    return 128 - code if code < 0 else code


def default_watch_id(path):
    path = Path(path).resolve()
    digest = hashlib.sha256(f"{socket.gethostname()}{path}".encode()).hexdigest()[:8]
    base = re.sub(r"[^A-Za-z0-9._-]", "-", path.name).strip(".")[:110] or "watch"
    return f"{base}-{digest}"


def watch_command(args):
    paths = [Path(path) for path in args.paths]
    extra = [Path(args.stdout_file)] if args.stdout_file else []
    if not paths and not extra:
        _say("nothing to watch; give a file or directory, or --stdout-file FILE")
        return 2
    missing = [str(path) for path in paths + extra if not path.exists()]
    if missing:
        _say(f"no such file or directory: {', '.join(missing)}")
        return 2
    roots = [path for path in paths if path.is_dir()]
    sources = [(discover.classify(path) or "stdout", path) for path in paths if path.is_file()]
    sources += [("stdout", path) for path in extra]
    run_id = args.run_id or default_watch_id((paths + extra)[0])
    config = build_config(["runboard", "watch"] + [str(path) for path in paths + extra], args.config_json, args.config)
    try:
        run = Run(project=args.project or Path.cwd().name, name=args.name or run_id, run_id=run_id, config=config)
    except Exception as e:
        _say(f"setup failed ({e})")
        return 1
    streamer = Streamer(run, roots=roots, sources=sources, state_file=state_path(run.project, run.run_id), config=config)
    streamer.created = streamer.created or time.time()
    run._set_meta(created=streamer.created)
    stop = threading.Event()
    previous = signal.signal(signal.SIGTERM, lambda signum, frame: stop.set())
    status = "finished"
    try:
        if args.once:
            streamer.drain()
        else:
            _say(f"watching {run.project}/{run.run_id}; press Ctrl-C to stop")
            while not stop.is_set():
                streamer.step()
                stop.wait(1.0)
            status = "killed"
    except KeyboardInterrupt:
        status = "killed"
    finally:
        signal.signal(signal.SIGTERM, previous)
    try:
        streamer.drain()
    except Exception as e:
        _say(f"metric collection error ({e})")
    run.finish(status)
    streamer.save()
    _say(f"{status}: logged {streamer.rows} rows to {run.project}/{run.run_id}")
    return 0
