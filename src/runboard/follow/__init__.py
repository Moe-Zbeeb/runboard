import math
import os
from collections import namedtuple
from pathlib import Path

from ..storage import clean_value

Record = namedtuple("Record", "step time metrics")

STEP_KEYS = ("step", "global_step", "_step", "iteration", "iter")
TIME_KEYS = ("time", "timestamp", "_time", "wall_time")
MAX_READ = 8 * 1024 * 1024
HEAD = 64
WALL_TIME = (946684800.0, 4102444800.0)


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


def _wall_time(value):
    number = _number(value)
    return number is not None and WALL_TIME[0] <= number <= WALL_TIME[1]


def to_record(values):
    step = _first(values, STEP_KEYS)
    times = [key for key in TIME_KEYS if key in values and _wall_time(values[key])]
    when = float(values[times[0]]) if times else None
    metrics = {}
    for key, value in values.items():
        if not isinstance(key, str) or key in STEP_KEYS or key in times or key.startswith("_"):
            continue
        cleaned = clean_value(value)
        if cleaned is not ...:
            metrics[key] = cleaned
    if not metrics:
        return None
    return Record(None if step is None else int(step), when, metrics)


class LineFollower:
    def __init__(self, path, state=None):
        state = state or {}
        self.path = Path(path)
        self.offset = int(state.get("offset", 0))
        self.head = bytes.fromhex(state.get("head", ""))
        self.rewound = False

    def state(self):
        return {"offset": self.offset, "head": self.head.hex()}

    def reset(self):
        self.offset = 0

    def read_lines(self):
        try:
            with open(self.path, "rb") as f:
                size = os.fstat(f.fileno()).st_size
                head = f.read(min(HEAD, size))
                if size < self.offset:
                    self.reset()
                elif head[: len(self.head)] != self.head:
                    self.reset()
                    self.rewound = True
                self.head = head
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
