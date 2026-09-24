import json
import math
import os
import re
import threading
import time
from pathlib import Path

_NAME_RE = re.compile(r"^[A-Za-z0-9._-]{1,128}$")
_locks = {}
_locks_guard = threading.Lock()


def valid_name(name):
    return isinstance(name, str) and bool(_NAME_RE.match(name)) and name not in (".", "..")


def _lock_for(path):
    with _locks_guard:
        return _locks.setdefault(str(path), threading.Lock())


def clean_value(v):
    if isinstance(v, bool):
        return float(v)
    if isinstance(v, (int, float)):
        f = float(v)
        return f if math.isfinite(f) else None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return ...
    return f if math.isfinite(f) else None


def clean_row(row):
    out = {}
    for k, v in row.items():
        if not isinstance(k, str):
            continue
        if k in ("_step", "_time"):
            out[k] = v
            continue
        c = clean_value(v)
        if c is not ...:
            out[k] = c
    return out


class Storage:
    def __init__(self, root):
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def run_dir(self, project, run_id):
        if not (valid_name(project) and valid_name(run_id)):
            raise ValueError(f"invalid project/run name: {project!r}/{run_id!r}")
        return self.root / project / run_id

    def update_meta(self, project, run_id, meta):
        d = self.run_dir(project, run_id)
        d.mkdir(parents=True, exist_ok=True)
        p = d / "meta.json"
        with _lock_for(p):
            current = {}
            if p.exists():
                try:
                    current = json.loads(p.read_text())
                except ValueError:
                    current = {}
            current.update(meta)
            current.setdefault("project", project)
            current.setdefault("run_id", run_id)
            current.setdefault("name", run_id)
            current.setdefault("created", time.time())
            tmp = p.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(current, default=str))
            os.replace(tmp, p)
        return current

    def append_rows(self, project, run_id, rows):
        d = self.run_dir(project, run_id)
        d.mkdir(parents=True, exist_ok=True)
        if not (d / "meta.json").exists():
            self.update_meta(project, run_id, {})
        p = d / "metrics.jsonl"
        lines = "".join(json.dumps(clean_row(r)) + "\n" for r in rows if isinstance(r, dict))
        if not lines:
            return
        with _lock_for(p):
            with open(p, "a") as f:
                f.write(lines)

    def read_meta(self, project, run_id):
        p = self.run_dir(project, run_id) / "meta.json"
        try:
            return json.loads(p.read_text())
        except (OSError, ValueError):
            return None

    def read_rows(self, project, run_id, offset=0):
        p = self.run_dir(project, run_id) / "metrics.jsonl"
        if not p.exists():
            return [], 0
        with open(p, "rb") as f:
            f.seek(offset)
            data = f.read()
        end = data.rfind(b"\n")
        if end < 0:
            return [], offset
        rows = []
        for line in data[: end + 1].splitlines():
            if not line.strip():
                continue
            try:
                rows.append(json.loads(line))
            except ValueError:
                continue
        return rows, offset + end + 1

    def list_runs(self):
        runs = []
        if not self.root.exists():
            return runs
        for pdir in sorted(self.root.iterdir()):
            if not pdir.is_dir() or not valid_name(pdir.name):
                continue
            for rdir in pdir.iterdir():
                if not rdir.is_dir() or not valid_name(rdir.name):
                    continue
                meta = self.read_meta(pdir.name, rdir.name) or {
                    "project": pdir.name,
                    "run_id": rdir.name,
                    "name": rdir.name,
                }
                m = rdir / "metrics.jsonl"
                meta["updated"] = m.stat().st_mtime if m.exists() else meta.get("created")
                meta["project"] = pdir.name
                meta["run_id"] = rdir.name
                runs.append(meta)
        runs.sort(key=lambda r: r.get("created") or 0, reverse=True)
        return runs
