import json
import re
from pathlib import Path

from . import to_record

CHECKPOINT = re.compile(r"^checkpoint-(\d+)$")


def _mtime(path):
    try:
        return path.stat().st_mtime
    except OSError:
        return -1.0


def newest_state(root):
    root = Path(root)
    if root.is_file():
        return root
    candidates = []
    if (root / "trainer_state.json").is_file():
        candidates.append(root / "trainer_state.json")
    try:
        for child in root.iterdir():
            if CHECKPOINT.match(child.name) and (child / "trainer_state.json").is_file():
                candidates.append(child / "trainer_state.json")
    except OSError:
        pass
    return max(candidates, key=_mtime) if candidates else None


class HfStateFollower:
    kind = "hf"

    def __init__(self, path, state=None):
        state = state or {}
        self.path = Path(path)
        self.last_step = int(state.get("last_step", -1))
        self.at_last = int(state.get("at_last", 0))
        self.signature = None

    def state(self):
        return {"last_step": self.last_step, "at_last": self.at_last}

    def poll(self):
        target = newest_state(self.path)
        if target is None:
            return []
        try:
            stat = target.stat()
            signature = (str(target), stat.st_mtime_ns, stat.st_size)
            if signature == self.signature:
                return []
            history = json.loads(target.read_text()).get("log_history", [])
        except (OSError, ValueError, AttributeError):
            return []
        self.signature = signature
        records = []
        seen = 0
        for entry in history if isinstance(history, list) else []:
            if not isinstance(entry, dict):
                continue
            step = entry.get("step")
            if isinstance(step, bool) or not isinstance(step, (int, float)):
                continue
            step = int(step)
            if step < self.last_step:
                continue
            if step == self.last_step:
                seen += 1
                if seen <= self.at_last:
                    continue
                self.at_last = seen
            else:
                self.last_step, self.at_last, seen = step, 1, 1
            record = to_record(entry)
            if record:
                records.append(record)
        return records
