import os
from pathlib import Path

SKIP_DIRS = {"node_modules", ".git", "__pycache__", "venv", ".venv", "site-packages", "runboard-runs"}
MAX_DEPTH = 4
MAX_FILES = 200
MAX_DIRS = 5000
PRIORITY = ("tensorboard", "table", "hf")


def classify(path):
    name = Path(path).name
    if name.startswith("events.out.tfevents."):
        return "tensorboard"
    if name.endswith(".jsonl") or (name.endswith(".csv") and "metrics" in name):
        return "table"
    if name == "trainer_state.json":
        return "hf"
    return None


def _excluded(path, exclude):
    try:
        return path.resolve() in exclude
    except OSError:
        return True


def scan(roots, since=0.0, max_depth=MAX_DEPTH, max_files=MAX_FILES, max_dirs=MAX_DIRS, exclude=()):
    found = {kind: [] for kind in PRIORITY}
    seen = set()
    exclude = {Path(path).resolve() for path in exclude}
    visited = 0
    for root in roots:
        root = Path(root)
        base = len(root.parts)
        for current, dirs, files in os.walk(root):
            visited += 1
            if visited > max_dirs:
                return found
            depth = len(Path(current).parts) - base
            dirs[:] = sorted(
                d
                for d in dirs
                if depth < max_depth and not d.startswith(".") and d not in SKIP_DIRS and not (exclude and _excluded(Path(current, d), exclude))
            )
            for name in sorted(files):
                kind = classify(name)
                if kind is None:
                    continue
                path = Path(current, name)
                try:
                    if path.stat().st_mtime < since:
                        continue
                    path = path.resolve()
                except OSError:
                    continue
                if path in seen:
                    continue
                seen.add(path)
                found[kind].append(path)
                if len(seen) >= max_files:
                    return found
    return found


def best(found):
    for kind in PRIORITY:
        if found.get(kind):
            return kind, found[kind]
    return None, []
