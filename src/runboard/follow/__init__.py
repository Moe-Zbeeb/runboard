import math
import os
from collections import namedtuple
from pathlib import Path

from ..storage import clean_value

Record = namedtuple("Record", "step time metrics")

STEP_KEYS = ("step", "global_step", "_step", "iteration", "iter")
TIME_KEYS = ("time", "timestamp", "_time", "wall_time")
MAX_READ = 8 * 1024 * 1024


def _number(value):
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def _first(values, keys):
    for key in keys:
        if key in values:
            number = _number(values[key])
            if number is not None:
                return number
    return None


def flatten(values, prefix=""):
    out = {}
    for key, value in values.items():
        name = f"{prefix}{key}"
        if isinstance(value, dict):
            out.update(flatten(value, name + "/"))
        else:
            out[name] = value
    return out


def to_record(values):
    step = _first(values, STEP_KEYS)
    when = _first(values, TIME_KEYS)
    metrics = {}
    for key, value in values.items():
        if not isinstance(key, str) or key in STEP_KEYS or key in TIME_KEYS or key.startswith("_"):
            continue
        cleaned = clean_value(value)
        if cleaned is not ...:
            metrics[key] = cleaned
    if not metrics:
        return None
    return Record(None if step is None else int(step), when, metrics)


class LineFollower:
    def __init__(self, path, state=None):
        self.path = Path(path)
        self.offset = int((state or {}).get("offset", 0))

    def state(self):
        return {"offset": self.offset}

    def reset(self):
        self.offset = 0

    def read_lines(self):
        try:
            with open(self.path, "rb") as f:
                if os.fstat(f.fileno()).st_size < self.offset:
                    self.reset()
                f.seek(self.offset)
                data = f.read(MAX_READ)
        except OSError:
            return []
        end = data.rfind(b"\n")
        if end < 0:
            if len(data) == MAX_READ:
                self.offset += len(data)
            return []
        self.offset += end + 1
        return data[:end].decode("utf-8", "replace").split("\n")
