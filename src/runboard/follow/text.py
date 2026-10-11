import re
import threading

from . import STEP_KEYS, LineFollower, to_record

ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
PAIR = re.compile(r"""(?<![\w./-])['"]?([A-Za-z_][\w./-]*)['"]?\s*[:=]\s*([-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?)(?![\w.])""")
SPLIT = re.compile(r"[\r\n]")
MAX_PENDING = 16 * 1024 * 1024


def parse_line(line):
    values = dict(PAIR.findall(ANSI.sub("", line)))
    if len(values) < 2 and not any(key in values for key in STEP_KEYS):
        return None
    return to_record(values)


def _parse_text(text):
    records = []
    for part in SPLIT.split(text):
        record = parse_line(part)
        if record:
            records.append(record)
    return records


class TextFollower(LineFollower):
    kind = "stdout"

    def poll(self):
        return _parse_text("\n".join(self.read_lines()))


class StreamFollower:
    kind = "stdout"

    def __init__(self):
        self.lock = threading.Lock()
        self.pending = b""

    def state(self):
        return {}

    def feed(self, data):
        with self.lock:
            self.pending += data
            if len(self.pending) > MAX_PENDING:
                self.pending = self.pending[-MAX_PENDING:]

    def discard(self):
        with self.lock:
            self.pending = b""

    def poll(self, final=False):
        with self.lock:
            if final:
                data, self.pending = self.pending, b""
            else:
                cut = max(self.pending.rfind(b"\n"), self.pending.rfind(b"\r"))
                if cut < 0:
                    return []
                data, self.pending = self.pending[:cut], self.pending[cut + 1 :]
        return _parse_text(data.decode("utf-8", "replace"))
