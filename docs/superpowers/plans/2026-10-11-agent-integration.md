# Zero-Code Agent Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let coding agents and researchers stream any training job's existing logs to Runboard with a one-line launch change (`runboard run --` / `runboard watch`), verify it with `runboard doctor`, and follow a packaged agent guide.

**Architecture:** Followers tail TensorBoard, JSONL, CSV, Hugging Face `trainer_state.json`, and text output, and a `Streamer` appends their records to an ordinary `Run` through a new private `Run._log_row()`. Every source is its own client session whose sequence numbers are record indexes, so the existing per-session deduplication in both backends makes resume and retry safe. No protocol, server, or Worker change.

**Tech Stack:** Python 3.8+ standard library only (`struct`, `csv`, `json`, `re`, `subprocess`, `signal`, `threading`, `urllib`), pytest.

**Spec:** `docs/superpowers/specs/2026-10-11-agent-integration-design.md`

## Global Constraints

- Runtime dependencies: none. Only the Python standard library.
- Python versions: 3.8 through 3.13. No syntax or APIs newer than 3.8 (no `match`, no `X | Y` types, no `str.removeprefix`, no `list[int]` annotations).
- Do not add comments to code (`AGENTS.md` working rule and the user's global instruction).
- No change to the REST protocol, `src/runboard/server.py` behavior, or `cloudflare/src/worker.js`.
- `runboard run` never prevents the command from starting, never changes its output, and never changes its exit code.
- Never print tokens. Use dummy values such as `secret` in tests.
- User agent string on HTTP requests: `runboard/0.2`.
- Commits are local only. **Never push** and never open pull requests; the user pushes.
- End every commit message with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- Test command: `python -m pytest -q` from the repository root, using an environment with `pip install -e ".[dev]"`.

## Review Focus

- A dataset or an earlier run's `*.jsonl` in the working directory must not be streamed by `runboard run`; only files modified since launch count (test in Task 7).
- TensorBoard files containing images or histograms (records over 1 MB) must not stall or slow the follower (test in Task 3).
- A log file that is truncated or rewritten must be read again from the start, not stall at a stale offset (test in Task 2).
- A mistyped command (`runboard run -- pyhton train.py`) must exit 127 with a clear message and mark the run `crashed` (test in Task 8).
- When the terminal side of the output closes (`runboard run -- ... | head`), the wrapper must keep draining the child's output so the child never blocks (test in Task 8).

## File Structure

| File | Responsibility |
|---|---|
| `src/runboard/client.py` (modify) | `Run._log_row`, shared `_append`, monotonic spool file stamps |
| `src/runboard/config.py` (modify) | Rename `_write_private` to public `write_private` |
| `src/runboard/follow/__init__.py` (create) | `Record`, `to_record`, `flatten`, `LineFollower` |
| `src/runboard/follow/jsonl.py` (create) | `JsonlFollower` |
| `src/runboard/follow/csvfile.py` (create) | `CsvFollower` |
| `src/runboard/follow/tensorboard.py` (create) | CRC32C, protobuf field reader, `parse_event`, `TensorBoardFollower` |
| `src/runboard/follow/hf_state.py` (create) | `newest_state`, `HfStateFollower` |
| `src/runboard/follow/text.py` (create) | `parse_line`, `TextFollower`, `StreamFollower` |
| `src/runboard/follow/discover.py` (create) | `classify`, `scan`, `best`, `PRIORITY` |
| `src/runboard/runner.py` (create) | `Streamer`, state files, `run_command`, `watch_command` |
| `src/runboard/doctor.py` (create) | `resolve`, `doctor` |
| `src/runboard/cli.py` (modify) | `run`, `watch`, `doctor`, `guide` subcommands; hosted `ls`; exit codes |
| `src/runboard/AGENT_GUIDE.md` (create) | Agent integration guide, packaged |
| `docs/integrating.md` (create) | Byte-identical copy of the guide |
| `pyproject.toml` (modify) | Package `AGENT_GUIDE.md` |
| `.github/workflows/ci.yml` (modify) | Wheel smoke test runs `runboard guide` |
| `README.md`, `AGENTS.md`, `docs/architecture.md`, `CHANGELOG.md` (modify) | Documentation |
| `tests/helpers.py` (modify) | Test-only TensorBoard writer |
| `tests/test_follow.py`, `tests/test_follow_tensorboard.py`, `tests/test_runner.py`, `tests/test_wrap.py`, `tests/test_doctor.py` (create) | Tests |
| `tests/test_client.py` (modify) | `Run._log_row`, spool order, hosted `ls`, guide tests |

---

### Task 0: Branch and commit the earlier bug fixes

The working tree holds the bug fixes from earlier in this session (client backoff, bounded `finish`, private secret files, server 413, Worker race, dashboard escaping). Commit them separately so this feature's history is clean.

**Files:** none created.

- [ ] **Step 1: Create a local branch**

```bash
git checkout -b agent-integration
```

- [ ] **Step 2: Confirm the full suite passes before committing**

Run: `python -m pytest -q && node tests/js/app.test.cjs && node tests/js/binned.test.cjs`
Expected: `36 passed`, `dashboard escaping checks passed`, `binned relationship checks passed`

- [ ] **Step 3: Commit the fixes (not the docs/superpowers files)**

```bash
git add CHANGELOG.md cloudflare/public/app.js cloudflare/src/worker.js cloudflare/test/worker.test.js \
  src/runboard/client.py src/runboard/config.py src/runboard/server.py src/runboard/static/app.js \
  tests/test_client.py tests/test_server.py tests/js/app.test.cjs
git commit -m "$(cat <<'EOF'
Fix retry spin, unbounded finish, spool overwrite, and duplicate rows

Back off after a failure that follows a spool upload, stop finish() at its
timeout, keep meta-only spool files from overwriting pending ones, create
secret files with mode 600, close the local server connection after 413,
make Worker deduplication atomic under concurrent retries, and escape run
status in the dashboard.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
)"
```

- [ ] **Step 4: Verify only the workflow docs remain uncommitted**

Run: `git status --short`
Expected: only `?? docs/superpowers/`

---

### Task 1: `Run._log_row` and creation-ordered spool files

**Files:**
- Modify: `src/runboard/client.py` (the `log` method near line 214, and `_spool` near line 390)
- Modify: `src/runboard/config.py` (rename helper)
- Test: `tests/test_client.py`

**Interfaces:**
- Produces: `Run._log_row(metrics: dict, step: int | None, when: float | None, sid: str, seq: int) -> None`. Appends one row with explicit `_sid`/`_seq`; `when=None` uses the current time; `step=None` uses the run's auto step. No-op after `finish()`.
- Produces: `config.write_private(path: Path, text: str) -> None` (renamed from `_write_private`, same behavior).
- Produces: spool files named `<project>__<run_id>__<stamp:020d>__<run sid>__<first_seq:020d>.jsonl` with a strictly increasing stamp.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_client.py`:

```python
def test_log_row_keeps_explicit_session_and_time(tmp_path):
    run = Run("p", dir=str(tmp_path / "x"), flush_interval=60)
    run._log_row({"loss": 0.5}, 7, 123.0, "src-a", 4)
    run._log_row({"loss": 0.25}, None, None, "src-a", 5)
    run.log({"loss": 0.1})
    run.finish()
    rows, _ = Storage(tmp_path / "x").read_rows("p", run.run_id)
    assert [(r["_sid"], r["_seq"], r["_step"]) for r in rows] == [("src-a", 4, 7), ("src-a", 5, 8), (run._sid, 0, 9)]
    assert rows[0]["_time"] == 123.0 and rows[1]["_time"] > 123.0


def test_sync_replays_spools_in_creation_order(tmp_path, server):
    run = Run("p", dir=str(tmp_path / "x"), flush_interval=60)
    first = run._spool({}, [{"v": 1.0, "_step": 0, "_seq": 50, "_sid": "b"}, {"v": 2.0, "_step": 1, "_seq": 0, "_sid": "a"}])
    second = run._spool({}, [{"v": 3.0, "_step": 2, "_seq": 1, "_sid": "a"}, {"v": 4.0, "_step": 3, "_seq": 51, "_sid": "b"}])
    run.finish()
    assert sorted([first.name, second.name]) == [first.name, second.name]
    sync(server=f"http://127.0.0.1:{server.port}", token="secret")
    rows, _ = server.storage.read_rows("p", run.run_id)
    assert [r["v"] for r in rows] == [1.0, 2.0, 3.0, 4.0]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest -q tests/test_client.py -k "log_row or creation_order"`
Expected: FAIL — `AttributeError: 'Run' object has no attribute '_log_row'`, and the sync test asserts `[3.0, 4.0] == [1.0, 2.0, 3.0, 4.0]` (old names sort by first sequence number, so the server skips the earlier file).

- [ ] **Step 3: Implement**

In `src/runboard/client.py`, after the `_sigterm_installed = False` line, add:

```python
_spool_lock = threading.Lock()
_spool_clock = 0


def _spool_stamp():
    global _spool_clock
    with _spool_lock:
        _spool_clock = max(time.time_ns(), _spool_clock + 1)
        return _spool_clock
```

Replace the whole `log` method with:

```python
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
```

In `_spool`, replace the filename line with:

```python
        p = d / f"{self.project[:48]}__{self.run_id[:48]}__{_spool_stamp():020d}__{self._sid}__{first_seq:020d}.jsonl"
```

In `src/runboard/config.py`, rename `_write_private` to `write_private` (the definition and both call sites in `get_or_create_token` and `write_server_info`).

- [ ] **Step 4: Run the full suite**

Run: `python -m pytest -q`
Expected: all tests pass (38 passed).

- [ ] **Step 5: Commit**

```bash
git add src/runboard/client.py src/runboard/config.py tests/test_client.py
git commit -m "$(cat <<'EOF'
Add Run._log_row and replay spool files in creation order

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: Record extraction, JSONL and CSV followers

**Files:**
- Create: `src/runboard/follow/__init__.py`, `src/runboard/follow/jsonl.py`, `src/runboard/follow/csvfile.py`
- Test: `tests/test_follow.py`

**Interfaces:**
- Produces: `Record = namedtuple("Record", "step time metrics")` — `step: int | None`, `time: float | None`, `metrics: dict[str, float | None]`.
- Produces: `STEP_KEYS`, `TIME_KEYS` tuples; `flatten(values: dict, prefix="") -> dict`; `to_record(values: dict) -> Record | None`.
- Produces: `LineFollower(path, state=None)` with `.offset`, `.state() -> dict`, `.reset()`, `.read_lines() -> list[str]` (complete lines only, without `\n`).
- Produces: `JsonlFollower(path, state=None).poll() -> list[Record]`, `CsvFollower(path, state=None).poll() -> list[Record]`; both `kind = "table"`. Follower state is JSON-serializable.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_follow.py`:

```python
import json

from runboard.follow import Record, to_record
from runboard.follow.csvfile import CsvFollower
from runboard.follow.jsonl import JsonlFollower


def test_to_record_reads_step_time_and_metrics():
    assert to_record({"global_step": 4, "wall_time": 10.5, "loss": "0.5", "name": "x", "_hidden": 1}) == Record(4, 10.5, {"loss": 0.5})
    assert to_record({"step": 1, "note": "text"}) is None


def test_jsonl_follower_flattens_and_waits_for_complete_lines(tmp_path):
    path = tmp_path / "metrics.jsonl"
    path.write_text(json.dumps({"step": 1, "train": {"loss": 0.5}}) + "\nnot json\n" + '{"step": 2, "train": {"loss": 0.4')
    follower = JsonlFollower(path)
    assert follower.poll() == [Record(1, None, {"train/loss": 0.5})]
    with open(path, "a") as f:
        f.write("}}\n")
    assert follower.poll() == [Record(2, None, {"train/loss": 0.4})]
    assert follower.poll() == []


def test_jsonl_follower_resumes_from_state(tmp_path):
    path = tmp_path / "metrics.jsonl"
    path.write_text('{"step": 1, "loss": 1}\n{"step": 2, "loss": 2}\n')
    first = JsonlFollower(path)
    first.poll()
    with open(path, "a") as f:
        f.write('{"step": 3, "loss": 3}\n')
    assert JsonlFollower(path, json.loads(json.dumps(first.state()))).poll() == [Record(3, None, {"loss": 3.0})]


def test_truncated_file_is_read_again_from_start(tmp_path):
    path = tmp_path / "metrics.jsonl"
    path.write_text('{"step": 1, "loss": 1}\n{"step": 2, "loss": 2}\n')
    follower = JsonlFollower(path)
    follower.poll()
    path.write_text('{"step": 1, "loss": 9}\n')
    assert follower.poll() == [Record(1, None, {"loss": 9.0})]


def test_csv_follower_skips_blank_cells(tmp_path):
    path = tmp_path / "metrics.csv"
    path.write_text("epoch,step,train_loss,val_acc\n0,10,0.5,\n0,10,,0.8\n")
    assert CsvFollower(path).poll() == [
        Record(10, None, {"epoch": 0.0, "train_loss": 0.5}),
        Record(10, None, {"epoch": 0.0, "val_acc": 0.8}),
    ]


def test_csv_follower_rereads_header_after_truncation(tmp_path):
    path = tmp_path / "metrics.csv"
    path.write_text("step,loss\n1,0.5\n2,0.4\n")
    follower = CsvFollower(path)
    follower.poll()
    path.write_text("step,acc\n1,0.9\n")
    assert follower.poll() == [Record(1, None, {"acc": 0.9})]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest -q tests/test_follow.py`
Expected: FAIL — `ModuleNotFoundError: No module named 'runboard.follow'`

- [ ] **Step 3: Implement**

Create `src/runboard/follow/__init__.py`:

```python
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
```

Create `src/runboard/follow/jsonl.py`:

```python
import json

from . import LineFollower, flatten, to_record


class JsonlFollower(LineFollower):
    kind = "table"

    def poll(self):
        records = []
        for line in self.read_lines():
            try:
                value = json.loads(line)
            except ValueError:
                continue
            if isinstance(value, dict):
                record = to_record(flatten(value))
                if record:
                    records.append(record)
        return records
```

Create `src/runboard/follow/csvfile.py`:

```python
import csv

from . import LineFollower, to_record


class CsvFollower(LineFollower):
    kind = "table"

    def __init__(self, path, state=None):
        super().__init__(path, state)
        self.header = (state or {}).get("header")

    def state(self):
        return {"offset": self.offset, "header": self.header}

    def reset(self):
        super().reset()
        self.header = None

    def poll(self):
        records = []
        for row in csv.reader(self.read_lines()):
            if self.header is None:
                self.header = row
                continue
            record = to_record({key: value for key, value in zip(self.header, row) if value.strip()})
            if record:
                records.append(record)
        return records
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest -q tests/test_follow.py`
Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
git add src/runboard/follow tests/test_follow.py
git commit -m "$(cat <<'EOF'
Add JSONL and CSV metric followers

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 3: TensorBoard follower

**Files:**
- Create: `src/runboard/follow/tensorboard.py`
- Modify: `tests/helpers.py` (test-only TensorBoard writer)
- Test: `tests/test_follow_tensorboard.py`

**Interfaces:**
- Consumes: `Record` from `runboard.follow`; `clean_value` from `runboard.storage`.
- Produces: `crc32c(data: bytes) -> int`, `masked_crc32c(data: bytes) -> int`, `parse_event(payload: bytes) -> Record | None`, `MAX_RECORD` (module attribute, 1 MB), `TensorBoardFollower(path, state=None)` with `kind = "tensorboard"`, `.poll() -> list[Record]`, `.state() -> {"offset": int}`.
- Produces (tests only): `helpers.tf_event(step, wall_time, scalars=(), tensors=()) -> bytes`, `helpers.tf_version_event() -> bytes`, `helpers.tf_record(payload) -> bytes`.

- [ ] **Step 1: Add the test writer**

Append to `tests/helpers.py`:

```python
import struct

from runboard.follow.tensorboard import masked_crc32c


def _varint(n):
    out = bytearray()
    while True:
        byte, n = n & 0x7F, n >> 7
        out.append(byte | 0x80 if n else byte)
        if not n:
            return bytes(out)


def _field(number, wire, payload):
    key = _varint(number << 3 | wire)
    return key + _varint(len(payload)) + payload if wire == 2 else key + payload


def tf_version_event():
    return _field(1, 1, struct.pack("<d", 0.0)) + _field(3, 2, b"brain.Event:2")


def tf_event(step, wall_time, scalars=(), tensors=()):
    values = b"".join(_field(1, 2, _field(1, 2, tag.encode()) + _field(2, 5, struct.pack("<f", value))) for tag, value in scalars)
    values += b"".join(
        _field(1, 2, _field(1, 2, tag.encode()) + _field(8, 2, _field(1, 0, _varint(2)) + _field(6, 2, struct.pack("<d", value))))
        for tag, value in tensors
    )
    body = _field(1, 1, struct.pack("<d", wall_time)) + _field(2, 0, _varint(step))
    return body + _field(5, 2, values) if values else body


def tf_record(payload):
    head = struct.pack("<Q", len(payload))
    return head + struct.pack("<I", masked_crc32c(head)) + payload + struct.pack("<I", masked_crc32c(payload))
```

- [ ] **Step 2: Write the failing tests**

Create `tests/test_follow_tensorboard.py`:

```python
from helpers import tf_event, tf_record, tf_version_event
from runboard.follow import Record
from runboard.follow import tensorboard
from runboard.follow.tensorboard import TensorBoardFollower, crc32c


def test_crc32c_known_vector():
    assert crc32c(b"123456789") == 0xE3069283


def test_reads_simple_and_tensor_scalars(tmp_path):
    path = tmp_path / "events.out.tfevents.1.host"
    path.write_bytes(
        tf_record(tf_version_event())
        + tf_record(tf_event(5, 100.0, scalars=[("train/loss", 0.5)]))
        + tf_record(tf_event(6, 101.0, tensors=[("eval/acc", 0.25)]))
    )
    assert TensorBoardFollower(path).poll() == [Record(5, 100.0, {"train/loss": 0.5}), Record(6, 101.0, {"eval/acc": 0.25})]


def test_waits_for_partial_record_and_skips_bad_checksum(tmp_path):
    path = tmp_path / "events.out.tfevents.2.host"
    good = tf_record(tf_event(1, 1.0, scalars=[("loss", 0.5)]))
    bad = bytearray(tf_record(tf_event(2, 2.0, scalars=[("loss", 0.25)])))
    bad[-1] ^= 0xFF
    last = tf_record(tf_event(3, 3.0, scalars=[("loss", 0.125)]))
    path.write_bytes(good + bytes(bad) + last[:10])
    follower = TensorBoardFollower(path)
    assert follower.poll() == [Record(1, 1.0, {"loss": 0.5})]
    with open(path, "ab") as f:
        f.write(last[10:])
    assert follower.poll() == [Record(3, 3.0, {"loss": 0.125})]


def test_skips_records_larger_than_the_limit(tmp_path, monkeypatch):
    monkeypatch.setattr(tensorboard, "MAX_RECORD", 64)
    path = tmp_path / "events.out.tfevents.3.host"
    path.write_bytes(tf_record(bytes(200)) + tf_record(tf_event(4, 4.0, scalars=[("loss", 0.5)])))
    assert TensorBoardFollower(path).poll() == [Record(4, 4.0, {"loss": 0.5})]


def test_resumes_from_saved_offset(tmp_path):
    path = tmp_path / "events.out.tfevents.4.host"
    path.write_bytes(tf_record(tf_event(1, 1.0, scalars=[("loss", 0.5)])))
    first = TensorBoardFollower(path)
    first.poll()
    with open(path, "ab") as f:
        f.write(tf_record(tf_event(2, 2.0, scalars=[("loss", 0.25)])))
    assert TensorBoardFollower(path, first.state()).poll() == [Record(2, 2.0, {"loss": 0.25})]
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `python -m pytest -q tests/test_follow_tensorboard.py`
Expected: FAIL — `ModuleNotFoundError: No module named 'runboard.follow.tensorboard'` (raised while importing `helpers`)

- [ ] **Step 4: Implement**

Create `src/runboard/follow/tensorboard.py`:

```python
import os
import struct
import sys
from pathlib import Path

from ..storage import clean_value
from . import Record

MAX_RECORD = 1024 * 1024
MAX_RECORDS_PER_POLL = 100000
DT_FLOAT = 1
DT_DOUBLE = 2

_TABLE = []
for _index in range(256):
    _crc = _index
    for _ in range(8):
        _crc = (_crc >> 1) ^ 0x82F63B78 if _crc & 1 else _crc >> 1
    _TABLE.append(_crc)


def crc32c(data):
    crc = 0xFFFFFFFF
    for byte in data:
        crc = _TABLE[(crc ^ byte) & 0xFF] ^ (crc >> 8)
    return crc ^ 0xFFFFFFFF


def masked_crc32c(data):
    crc = crc32c(data)
    return (((crc >> 15) | (crc << 17)) + 0xA282EAD8) & 0xFFFFFFFF


def _varint(buf, pos):
    result = shift = 0
    while True:
        byte = buf[pos]
        pos += 1
        result |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return result, pos
        shift += 7


def _fields(buf):
    pos = 0
    while pos < len(buf):
        key, pos = _varint(buf, pos)
        number, wire = key >> 3, key & 7
        if wire == 0:
            value, pos = _varint(buf, pos)
        elif wire == 1:
            value, pos = buf[pos : pos + 8], pos + 8
        elif wire == 2:
            size, pos = _varint(buf, pos)
            value, pos = buf[pos : pos + size], pos + size
        elif wire == 5:
            value, pos = buf[pos : pos + 4], pos + 4
        else:
            raise ValueError("unsupported protobuf wire type")
        if pos > len(buf):
            raise ValueError("truncated protobuf field")
        yield number, wire, value


def _tensor_scalar(buf):
    dtype, content, values = DT_FLOAT, b"", []
    for number, wire, value in _fields(buf):
        if number == 1 and wire == 0:
            dtype = value
        elif number == 4 and wire == 2:
            content = value
        elif number == 5 and wire in (2, 5):
            values.extend(item for (item,) in struct.iter_unpack("<f", value))
        elif number == 6 and wire in (1, 2):
            values.extend(item for (item,) in struct.iter_unpack("<d", value))
    if dtype not in (DT_FLOAT, DT_DOUBLE):
        return None
    if content:
        fmt = "<f" if dtype == DT_FLOAT else "<d"
        if len(content) != struct.calcsize(fmt):
            return None
        values = [struct.unpack(fmt, content)[0]]
    return values[0] if len(values) == 1 else None


def _scalars(summary):
    out = {}
    for number, wire, value in _fields(summary):
        if number != 1 or wire != 2:
            continue
        tag = scalar = None
        for inner, inner_wire, inner_value in _fields(value):
            if inner == 1 and inner_wire == 2:
                tag = inner_value.decode("utf-8", "replace")
            elif inner == 2 and inner_wire == 5:
                scalar = struct.unpack("<f", inner_value)[0]
            elif inner == 8 and inner_wire == 2:
                scalar = _tensor_scalar(inner_value)
        if tag is not None and scalar is not None:
            cleaned = clean_value(scalar)
            if cleaned is not ...:
                out[tag] = cleaned
    return out


def parse_event(payload):
    wall = step = None
    metrics = {}
    for number, wire, value in _fields(payload):
        if number == 1 and wire == 1:
            wall = struct.unpack("<d", value)[0]
        elif number == 2 and wire == 0:
            step = value - (1 << 64) if value >= 1 << 63 else value
        elif number == 5 and wire == 2:
            metrics.update(_scalars(value))
    return Record(step, wall, metrics) if metrics else None


class TensorBoardFollower:
    kind = "tensorboard"

    def __init__(self, path, state=None):
        self.path = Path(path)
        self.offset = int((state or {}).get("offset", 0))
        self.broken = False
        self.warned = False

    def state(self):
        return {"offset": self.offset}

    def _warn(self, message):
        if not self.warned:
            print(f"[runboard] {self.path}: {message}", file=sys.stderr, flush=True)
            self.warned = True

    def poll(self):
        if self.broken:
            return []
        records = []
        try:
            with open(self.path, "rb") as f:
                size = os.fstat(f.fileno()).st_size
                if size < self.offset:
                    self.offset = 0
                pos = self.offset
                for _ in range(MAX_RECORDS_PER_POLL):
                    if pos + 12 > size:
                        break
                    f.seek(pos)
                    head = f.read(12)
                    if len(head) < 12:
                        break
                    length, length_crc = struct.unpack("<QI", head)
                    if masked_crc32c(head[:8]) != length_crc:
                        self._warn("corrupt record framing; stopped reading this file")
                        self.broken = True
                        break
                    end = pos + 12 + length + 4
                    if end > size:
                        break
                    if length > MAX_RECORD:
                        pos = end
                        continue
                    payload = f.read(length)
                    tail = f.read(4)
                    pos = end
                    if len(tail) < 4 or masked_crc32c(payload) != struct.unpack("<I", tail)[0]:
                        self._warn("skipped a record with a bad checksum")
                        continue
                    try:
                        record = parse_event(payload)
                    except (ValueError, IndexError, struct.error):
                        continue
                    if record:
                        records.append(record)
                self.offset = pos
        except OSError:
            return records
        return records
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `python -m pytest -q tests/test_follow_tensorboard.py`
Expected: 5 passed

- [ ] **Step 6: Commit**

```bash
git add src/runboard/follow/tensorboard.py tests/helpers.py tests/test_follow_tensorboard.py
git commit -m "$(cat <<'EOF'
Add a dependency-free TensorBoard event follower

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 4: Hugging Face `trainer_state.json` follower

**Files:**
- Create: `src/runboard/follow/hf_state.py`
- Test: `tests/test_follow.py`

**Interfaces:**
- Consumes: `to_record`, `Record` from `runboard.follow`.
- Produces: `CHECKPOINT` (compiled regex `^checkpoint-(\d+)$`), `newest_state(root) -> Path | None`, `HfStateFollower(path, state=None)` with `kind = "hf"`, `.poll() -> list[Record]`, `.state() -> {"last_step": int, "at_last": int}`. `path` may be an output directory or a `trainer_state.json` file.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_follow.py`:

```python
import os
import time

from runboard.follow.hf_state import HfStateFollower


def _write_state(directory, history, mtime):
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "trainer_state.json"
    path.write_text(json.dumps({"global_step": history[-1]["step"], "log_history": history}))
    os.utime(path, (mtime, mtime))


def test_hf_state_follows_newest_checkpoint_without_repeats(tmp_path):
    first = [{"loss": 1.0, "learning_rate": 5e-05, "epoch": 0.5, "step": 10}, {"eval_loss": 0.9, "epoch": 0.5, "step": 10}]
    _write_state(tmp_path / "checkpoint-10", first, time.time() - 100)
    follower = HfStateFollower(tmp_path)
    assert follower.poll() == [
        Record(10, None, {"loss": 1.0, "learning_rate": 5e-05, "epoch": 0.5}),
        Record(10, None, {"eval_loss": 0.9, "epoch": 0.5}),
    ]
    assert follower.poll() == []
    _write_state(tmp_path / "checkpoint-20", first + [{"loss": 0.5, "epoch": 1.0, "step": 20}], time.time())
    assert follower.poll() == [Record(20, None, {"loss": 0.5, "epoch": 1.0})]
    assert HfStateFollower(tmp_path, follower.state()).poll() == []


def test_hf_state_reads_a_direct_file(tmp_path):
    _write_state(tmp_path, [{"loss": 2.0, "step": 1}], time.time())
    assert HfStateFollower(tmp_path / "trainer_state.json").poll() == [Record(1, None, {"loss": 2.0})]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest -q tests/test_follow.py -k hf_state`
Expected: FAIL — `ModuleNotFoundError: No module named 'runboard.follow.hf_state'`

- [ ] **Step 3: Implement**

Create `src/runboard/follow/hf_state.py`:

```python
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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest -q tests/test_follow.py`
Expected: 8 passed

- [ ] **Step 5: Commit**

```bash
git add src/runboard/follow/hf_state.py tests/test_follow.py
git commit -m "$(cat <<'EOF'
Add a Hugging Face trainer_state.json follower

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 5: Text and stdout parsing

**Files:**
- Create: `src/runboard/follow/text.py`
- Test: `tests/test_follow.py`

**Interfaces:**
- Consumes: `LineFollower`, `STEP_KEYS`, `to_record`, `Record` from `runboard.follow`.
- Produces: `parse_line(line: str) -> Record | None`; `TextFollower(path, state=None)` (`kind = "stdout"`, file-backed, resumable); `StreamFollower()` (`kind = "stdout"`) with `.feed(data: bytes)`, `.poll(final: bool = False) -> list[Record]`, `.discard()`, `.state() -> {}`. `StreamFollower.feed` and `.poll` are thread-safe.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_follow.py`:

```python
import pytest

from runboard.follow.text import StreamFollower, TextFollower, parse_line

METRIC_LINES = [
    ("{'loss': 0.42, 'grad_norm': 1.5, 'learning_rate': 5e-05, 'epoch': 0.1}", Record(None, None, {"loss": 0.42, "grad_norm": 1.5, "learning_rate": 5e-05, "epoch": 0.1})),
    ("step:12 - actor/loss:0.3 - critic/vf_loss:-0.1", Record(12, None, {"actor/loss": 0.3, "critic/vf_loss": -0.1})),
    ("\x1b[32mINFO\x1b[0m step=7 loss=0.5", Record(7, None, {"loss": 0.5})),
    (" 45%|####5     | 450/1000 [00:10<00:12, 43.2it/s, loss=0.3, lr=1e-4]", Record(None, None, {"loss": 0.3, "lr": 0.0001})),
]


@pytest.mark.parametrize("line,expected", METRIC_LINES)
def test_parse_line_extracts_metrics(line, expected):
    assert parse_line(line) == expected


@pytest.mark.parametrize("line", ["Downloading model: 50%", "x = 5", "Running on http://127.0.0.1:8080", "Saved checkpoint to /tmp/model-3.pt", "version 1.2.3"])
def test_parse_line_ignores_ordinary_output(line):
    assert parse_line(line) is None


def test_stream_follower_handles_partial_lines_and_carriage_returns():
    stream = StreamFollower()
    stream.feed(b"step=1 loss=0.5\rstep=2 lo")
    assert stream.poll() == [Record(1, None, {"loss": 0.5})]
    stream.feed(b"ss=0.25\nstep=3 loss=0.125")
    assert stream.poll() == [Record(2, None, {"loss": 0.25})]
    assert stream.poll(final=True) == [Record(3, None, {"loss": 0.125})]
    assert stream.poll(final=True) == []


def test_text_follower_reads_a_log_file(tmp_path):
    path = tmp_path / "slurm-1.out"
    path.write_text("step=1 loss=0.5\rstep=2 loss=0.25\nloading data\n")
    assert TextFollower(path).poll() == [Record(1, None, {"loss": 0.5}), Record(2, None, {"loss": 0.25})]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest -q tests/test_follow.py -k "parse_line or stream_follower or text_follower"`
Expected: FAIL — `ModuleNotFoundError: No module named 'runboard.follow.text'`

- [ ] **Step 3: Implement**

Create `src/runboard/follow/text.py`:

```python
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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest -q tests/test_follow.py`
Expected: 19 passed

- [ ] **Step 5: Commit**

```bash
git add src/runboard/follow/text.py tests/test_follow.py
git commit -m "$(cat <<'EOF'
Parse metrics from command output and text logs

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 6: Discovery

**Files:**
- Create: `src/runboard/follow/discover.py`
- Test: `tests/test_follow.py`

**Interfaces:**
- Produces: `PRIORITY = ("tensorboard", "table", "hf")`; `classify(path) -> "tensorboard" | "table" | "hf" | None`; `scan(roots, since=0.0, max_depth=4, max_files=200) -> dict[str, list[Path]]` (resolved paths, keys are every `PRIORITY` entry); `best(found) -> (kind | None, list[Path])`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_follow.py`:

```python
from runboard.follow.discover import best, classify, scan


def _touch(path, mtime=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("x")
    if mtime is not None:
        os.utime(path, (mtime, mtime))


def test_classify_known_files():
    assert classify("events.out.tfevents.1.host") == "tensorboard"
    assert classify("train.jsonl") == "table"
    assert classify("metrics.csv") == "table"
    assert classify("results.csv") is None
    assert classify("trainer_state.json") == "hf"
    assert classify("train.log") is None


def test_scan_prefers_tensorboard_and_skips_noise(tmp_path):
    _touch(tmp_path / "runs/exp/events.out.tfevents.1.host")
    _touch(tmp_path / "logs/metrics.jsonl")
    _touch(tmp_path / ".hidden/events.out.tfevents.2.host")
    _touch(tmp_path / "node_modules/x/metrics.jsonl")
    _touch(tmp_path / "a/b/c/d/e/events.out.tfevents.3.host")
    _touch(tmp_path / "results.csv")
    found = scan([tmp_path])
    assert found["tensorboard"] == [(tmp_path / "runs/exp/events.out.tfevents.1.host").resolve()]
    assert found["table"] == [(tmp_path / "logs/metrics.jsonl").resolve()]
    assert best(found) == ("tensorboard", found["tensorboard"])


def test_scan_ignores_files_older_than_since(tmp_path):
    _touch(tmp_path / "old/metrics.jsonl", mtime=1000)
    _touch(tmp_path / "new/metrics.jsonl")
    assert scan([tmp_path], since=2000)["table"] == [(tmp_path / "new/metrics.jsonl").resolve()]


def test_scan_stops_at_the_file_limit_and_deduplicates_roots(tmp_path):
    for index in range(3):
        _touch(tmp_path / f"logs/{index}.jsonl")
    assert len(scan([tmp_path], max_files=2)["table"]) == 2
    assert len(scan([tmp_path, tmp_path / "logs"])["table"]) == 3
    assert best(scan([tmp_path / "missing"])) == (None, [])
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest -q tests/test_follow.py -k "classify or scan"`
Expected: FAIL — `ModuleNotFoundError: No module named 'runboard.follow.discover'`

- [ ] **Step 3: Implement**

Create `src/runboard/follow/discover.py`:

```python
import os
from pathlib import Path

SKIP_DIRS = {"node_modules", ".git", "__pycache__", "venv", ".venv", "site-packages"}
MAX_DEPTH = 4
MAX_FILES = 200
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


def scan(roots, since=0.0, max_depth=MAX_DEPTH, max_files=MAX_FILES):
    found = {kind: [] for kind in PRIORITY}
    seen = set()
    for root in roots:
        root = Path(root)
        base = len(root.parts)
        for current, dirs, files in os.walk(root):
            depth = len(Path(current).parts) - base
            dirs[:] = sorted(d for d in dirs if depth < max_depth and not d.startswith(".") and d not in SKIP_DIRS)
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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest -q tests/test_follow.py`
Expected: 23 passed

- [ ] **Step 5: Commit**

```bash
git add src/runboard/follow/discover.py tests/test_follow.py
git commit -m "$(cat <<'EOF'
Discover metric files with format precedence and scan limits

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 7: Streamer (selection, stdout fallback, resumable state)

**Files:**
- Create: `src/runboard/runner.py`
- Test: `tests/test_runner.py`

**Interfaces:**
- Consumes: `Run._log_row`, `Run._send_lock`, `Run._is_empty`, `Run._set_meta` (Task 1); `config.write_private`, `config.home`; all followers (Tasks 2–5); `discover.scan`, `discover.best`, `discover.PRIORITY` (Task 6); `CHECKPOINT` (Task 4).
- Produces: `state_path(project, run_id) -> Path`; `load_state(path) -> dict`; `follower_for(kind, path, state=None)`; `LABELS` dict; `Streamer(run, roots=(), sources=(), since=0.0, stdout="off", state_file=None, config=None, clock=time.time)` with:
  - `.feed(name: "stdout" | "stderr", data: bytes)`
  - `.scan()`, `.pump(final=False) -> int`, `.step() -> int`, `.drain() -> int`
  - `.checkpoint() -> bool`, `.save()`, `.snapshot() -> dict`
  - attributes `.kind` (`None`, a `PRIORITY` kind, or `"explicit"`), `.rows` (records logged), `.created` (float or `None`), `.nonce`.
  - `sources` is a list of `(kind, path)` where kind is `"tensorboard"`, `"table"`, `"hf"`, or `"stdout"`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_runner.py`:

```python
import json
import os

from helpers import tf_event, tf_record
from runboard.client import Run
from runboard.runner import Streamer
from runboard.storage import Storage


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def _rows(tmp_path, run):
    return Storage(tmp_path / "store").read_rows(run.project, run.run_id)[0]


def _run(tmp_path, run_id=None):
    return Run("p", run_id=run_id, dir=str(tmp_path / "store"), flush_interval=60)


def test_streamer_follows_best_format_and_switches_up(tmp_path):
    work = tmp_path / "work"
    (work / "logs").mkdir(parents=True)
    (work / "logs/metrics.jsonl").write_text('{"step": 1, "loss": 0.5}\n')
    run = _run(tmp_path)
    clock = Clock()
    streamer = Streamer(run, roots=[work], config={"command": ["x"]}, clock=clock)
    assert streamer.step() == 1 and streamer.kind == "table"
    (work / "runs").mkdir()
    (work / "runs/events.out.tfevents.1.h").write_bytes(tf_record(tf_event(2, 5.0, scalars=[("loss", 0.25)])))
    clock.now += 6
    assert streamer.step() == 1 and streamer.kind == "tensorboard"
    run.finish()
    assert [(r["_step"], r["loss"]) for r in _rows(tmp_path, run)] == [(1, 0.5), (2, 0.25)]
    assert Storage(tmp_path / "store").read_meta("p", run.run_id)["config"] == {"command": ["x"], "source": "TensorBoard"}


def test_explicit_sources_disable_discovery(tmp_path):
    (tmp_path / "events.out.tfevents.1.h").write_bytes(tf_record(tf_event(1, 1.0, scalars=[("tb", 1.0)])))
    log = tmp_path / "train.log"
    log.write_text("step=1 loss=0.5\n")
    run = _run(tmp_path)
    streamer = Streamer(run, roots=[tmp_path], sources=[("stdout", log)])
    assert streamer.drain() == 1 and streamer.kind == "explicit"
    run.finish()
    assert [r.get("loss") for r in _rows(tmp_path, run)] == [0.5]


def test_streamer_ignores_files_older_than_since(tmp_path):
    work = tmp_path / "work"
    work.mkdir()
    old = work / "dataset.jsonl"
    old.write_text('{"step": 1, "label": 3}\n')
    os.utime(old, (1000, 1000))
    run = _run(tmp_path)
    streamer = Streamer(run, roots=[work], since=2000)
    assert streamer.drain() == 0 and streamer.kind is None
    run.finish()


def test_stdout_auto_waits_for_structured_files(tmp_path):
    (tmp_path / "empty").mkdir()
    run = _run(tmp_path)
    clock = Clock()
    streamer = Streamer(run, roots=[tmp_path / "empty"], stdout="auto", clock=clock)
    streamer.feed("stdout", b"step=1 loss=0.5\n")
    assert streamer.step() == 0
    clock.now += 61
    assert streamer.step() == 1
    run.finish()


def test_stdout_auto_parses_output_when_a_short_job_exits(tmp_path):
    (tmp_path / "empty").mkdir()
    run = _run(tmp_path)
    streamer = Streamer(run, roots=[tmp_path / "empty"], stdout="auto", clock=Clock())
    streamer.feed("stderr", b"step=1 loss=0.5")
    assert streamer.drain() == 1
    run.finish()


def test_stdout_is_discarded_once_files_are_found(tmp_path):
    (tmp_path / "metrics.jsonl").write_text('{"step": 1, "loss": 0.5}\n')
    run = _run(tmp_path)
    clock = Clock()
    streamer = Streamer(run, roots=[tmp_path], stdout="auto", clock=clock)
    streamer.feed("stdout", b"step=9 loss=9\n")
    clock.now += 61
    assert streamer.step() == 1
    assert streamer.drain() == 0
    run.finish()


def test_resumed_streamer_does_not_duplicate_or_drop_rows(tmp_path):
    work = tmp_path / "work"
    work.mkdir()
    log = work / "metrics.jsonl"
    state = tmp_path / "state.json"
    log.write_text('{"step": 1, "loss": 1}\n')
    first_run = _run(tmp_path, run_id="r")
    first = Streamer(first_run, roots=[work], state_file=state)
    first.step()
    first_run._flush_once()
    assert first.checkpoint()
    with open(log, "a") as f:
        f.write('{"step": 2, "loss": 2}\n')
    first.step()
    first_run._flush_once()
    first_run.finish("killed")
    with open(log, "a") as f:
        f.write('{"step": 3, "loss": 3}\n')
    second_run = _run(tmp_path, run_id="r")
    second = Streamer(second_run, roots=[work], state_file=state)
    second.drain()
    second_run.finish()
    second.save()
    assert [r["loss"] for r in _rows(tmp_path, second_run)] == [1.0, 2.0, 3.0]
    saved = json.loads(state.read_text())
    assert saved["nonce"] == first.nonce
    assert saved["sources"][str(log.resolve())]["count"] == 3
    assert state.stat().st_mode & 0o777 == 0o600
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest -q tests/test_runner.py`
Expected: FAIL — `ModuleNotFoundError: No module named 'runboard.runner'`

- [ ] **Step 3: Implement**

Create `src/runboard/runner.py`:

```python
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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest -q tests/test_runner.py`
Expected: 7 passed

- [ ] **Step 5: Run the full suite**

Run: `python -m pytest -q`
Expected: all pass

- [ ] **Step 6: Commit**

```bash
git add src/runboard/runner.py tests/test_runner.py
git commit -m "$(cat <<'EOF'
Stream follower records into a run with resumable sessions

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 8: `runboard run` wrapper

**Files:**
- Modify: `src/runboard/runner.py` (append wrapper code)
- Modify: `src/runboard/cli.py` (add `run` subcommand; `main` returns exit codes)
- Test: `tests/test_wrap.py`

**Interfaces:**
- Consumes: `Streamer`, `state_path`, `_say` (Task 7); `discover.classify` (Task 6); `runboard.client.Run`.
- Produces: `build_config(command: list, config_json: str | None, pairs: list[str]) -> dict`; `default_name(command: list) -> str`; `_pipe(source, sink, streamer, name)`; `run_command(args) -> int` where `args` has `command`, `project`, `name`, `run_id`, `watch` (list), `source` (list), `stdout`, `config_json`, `config` (list).
- Produces: `cli.main()` raises `SystemExit(code)` when a subcommand function returns a non-zero int.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_wrap.py`:

```python
import os
import signal
import subprocess
import sys

import pytest

from helpers import wait_for
from runboard.runner import _pipe, build_config, default_name

CHILD = """
import json, sys
with open("metrics.jsonl", "a") as f:
    for step in range(3):
        f.write(json.dumps({"step": step, "loss": 1.0 / (step + 1)}) + "\\n")
print("child output", flush=True)
sys.exit(int(sys.argv[1]))
"""

SLEEPER = """
import json, time
with open("metrics.jsonl", "a") as f:
    f.write(json.dumps({"step": 0, "loss": 1.0}) + "\\n")
open("ready", "w").close()
time.sleep(60)
"""

PRINTER = """
for step in range(3):
    print(f"step={step} loss={1.0 / (step + 1)}", flush=True)
"""


def _env(server):
    return dict(os.environ, RUNBOARD_SERVER=f"http://127.0.0.1:{server.port}", RUNBOARD_TOKEN="secret")


def _wrap_args(*args):
    return [sys.executable, "-m", "runboard.cli", "run", "--project", "p", "--run-id", "job", *args]


def _wrap(tmp_path, server, *args):
    (tmp_path / "child.py").write_text(CHILD)
    return subprocess.run(_wrap_args(*args), cwd=tmp_path, env=_env(server), capture_output=True, text=True, timeout=60)


@pytest.mark.parametrize("code,status", [(0, "finished"), (3, "crashed")])
def test_run_streams_files_and_keeps_exit_code(tmp_path, server, code, status):
    result = _wrap(tmp_path, server, "--", sys.executable, "child.py", str(code))
    assert result.returncode == code
    assert "child output" in result.stdout
    assert f"{status}: logged 3 rows to p/job" in result.stderr
    rows, _ = server.storage.read_rows("p", "job")
    assert [r["loss"] for r in rows] == [1.0, 0.5, pytest.approx(1 / 3)]
    meta = server.storage.read_meta("p", "job")
    assert meta["status"] == status and meta["config"]["source"] == "JSONL/CSV"


def test_run_forwards_sigterm_and_marks_killed(tmp_path, server):
    (tmp_path / "sleeper.py").write_text(SLEEPER)
    proc = subprocess.Popen(_wrap_args("--", sys.executable, "sleeper.py"), cwd=tmp_path, env=_env(server), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    assert wait_for(lambda: (tmp_path / "ready").exists(), timeout=30)
    proc.send_signal(signal.SIGTERM)
    proc.communicate(timeout=60)
    assert proc.returncode == 128 + signal.SIGTERM
    assert server.storage.read_meta("p", "job")["status"] == "killed"
    assert [r["loss"] for r in server.storage.read_rows("p", "job")[0]] == [1.0]


def test_run_reports_missing_command(tmp_path, server):
    result = _wrap(tmp_path, server, "--", "definitely-not-a-command-xyz")
    assert result.returncode == 127
    assert "cannot start definitely-not-a-command-xyz" in result.stderr
    assert server.storage.read_meta("p", "job")["status"] == "crashed"


def test_run_parses_output_when_no_files_are_written(tmp_path, server):
    (tmp_path / "printer.py").write_text(PRINTER)
    result = _wrap(tmp_path, server, "--", sys.executable, "printer.py")
    assert result.returncode == 0 and "step=2" in result.stdout
    assert [r["_step"] for r in server.storage.read_rows("p", "job")[0]] == [0, 1, 2]


def test_run_on_a_nonzero_rank_only_runs_the_command(tmp_path, server, monkeypatch):
    monkeypatch.setenv("SLURM_PROCID", "1")
    result = _wrap(tmp_path, server, "--", sys.executable, "child.py", "0")
    assert result.returncode == 0 and "child output" in result.stdout
    assert server.storage.list_runs() == []


def test_run_without_a_command_is_a_usage_error(tmp_path, server):
    result = _wrap(tmp_path, server)
    assert result.returncode == 2 and "nothing to run" in result.stderr


def test_pipe_keeps_draining_when_output_is_closed():
    class Closed:
        def write(self, data):
            raise BrokenPipeError()

        def flush(self):
            pass

    class Collector:
        def __init__(self):
            self.data = []

        def feed(self, name, data):
            self.data.append((name, data))

    read_end, write_end = os.pipe()
    os.write(write_end, b"step=1 loss=0.5\n")
    os.close(write_end)
    collector = Collector()
    _pipe(os.fdopen(read_end, "rb"), Closed(), collector, "stdout")
    assert collector.data == [("stdout", b"step=1 loss=0.5\n")]


def test_build_config_and_default_name(tmp_path):
    (tmp_path / "hp.json").write_text('{"lr": 0.1}')
    config = build_config(["python", "train.py"], str(tmp_path / "hp.json"), ["batch=32", "opt=adam", "bad"])
    assert config["lr"] == 0.1 and config["batch"] == 32 and config["opt"] == "adam" and "bad" not in config
    assert config["command"] == ["python", "train.py"]
    assert default_name(["torchrun", "--nproc_per_node", "8", "train.py"]).startswith("train-")
    assert default_name(["python", "-m", "pkg.trainer"]).startswith("trainer-")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest -q tests/test_wrap.py`
Expected: FAIL — `ImportError: cannot import name '_pipe' from 'runboard.runner'`

- [ ] **Step 3: Implement the wrapper**

In `src/runboard/runner.py`, extend the imports so the import block reads:

```python
import hashlib
import json
import os
import secrets
import signal
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
```

Append to `src/runboard/runner.py`:

```python
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
```

- [ ] **Step 4: Wire the CLI**

In `src/runboard/cli.py`, add to the imports:

```python
from .runner import run_command
```

In `main`, after the `url` subparser and before `configure`, add:

```python
    s = sub.add_parser("run", help="run a training command and stream the metrics it logs")
    s.add_argument("--project", help="dashboard project (default: current directory name)")
    s.add_argument("--name", help="display name (default: script name and timestamp)")
    s.add_argument("--run-id", help="run id; reuse one to continue a run")
    s.add_argument("--watch", action="append", default=[], metavar="DIR", help="also search this directory for metric files")
    s.add_argument("--source", action="append", default=[], metavar="FILE", help="follow only these files")
    s.add_argument("--stdout", choices=["auto", "on", "off"], default="auto", help="parse metrics from command output")
    s.add_argument("--config-json", metavar="FILE", help="record hyperparameters from a JSON file")
    s.add_argument("--config", action="append", default=[], metavar="KEY=VALUE", help="record a hyperparameter")
    s.add_argument("command", nargs=argparse.REMAINDER, help="the command to run, after --")
    s.set_defaults(func=run_command)
```

Replace the last line of `main`, `args.func(args)`, with:

```python
    code = args.func(args)
    if code:
        raise SystemExit(code)
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `python -m pytest -q tests/test_wrap.py`
Expected: 9 passed

- [ ] **Step 6: Run the full suite**

Run: `python -m pytest -q`
Expected: all pass

- [ ] **Step 7: Commit**

```bash
git add src/runboard/runner.py src/runboard/cli.py tests/test_wrap.py
git commit -m "$(cat <<'EOF'
Add runboard run to stream a command's metrics with no code changes

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 9: `runboard watch`

**Files:**
- Modify: `src/runboard/runner.py`, `src/runboard/cli.py`
- Test: `tests/test_wrap.py`

**Interfaces:**
- Consumes: `Streamer`, `state_path`, `build_config`, `_say` (Tasks 7–8); `discover.classify`.
- Produces: `default_watch_id(path) -> str`; `watch_command(args) -> int` where `args` has `paths` (list), `project`, `name`, `run_id`, `stdout_file`, `once`, `config_json`, `config` (list).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_wrap.py`:

```python
def test_watch_once_twice_continues_one_run_without_duplicates(tmp_path, server, monkeypatch):
    from runboard.cli import main

    monkeypatch.setenv("RUNBOARD_SERVER", f"http://127.0.0.1:{server.port}")
    monkeypatch.setenv("RUNBOARD_TOKEN", "secret")
    logs = tmp_path / "out"
    logs.mkdir()
    (logs / "metrics.jsonl").write_text('{"step": 1, "loss": 1}\n')
    main(["watch", str(logs), "--once", "--project", "p"])
    created = server.storage.list_runs()[0]["created"]
    with open(logs / "metrics.jsonl", "a") as f:
        f.write('{"step": 2, "loss": 2}\n')
    main(["watch", str(logs), "--once", "--project", "p"])
    runs = server.storage.list_runs()
    assert len(runs) == 1 and runs[0]["status"] == "finished" and runs[0]["created"] == created
    assert [r["loss"] for r in server.storage.read_rows("p", runs[0]["run_id"])[0]] == [1.0, 2.0]


def test_watch_reads_a_slurm_output_file(tmp_path, server, monkeypatch):
    from runboard.cli import main

    monkeypatch.setenv("RUNBOARD_SERVER", f"http://127.0.0.1:{server.port}")
    monkeypatch.setenv("RUNBOARD_TOKEN", "secret")
    (tmp_path / "slurm-1.out").write_text("loading\nstep=1 loss=0.5\n")
    main(["watch", "--stdout-file", str(tmp_path / "slurm-1.out"), "--once", "--project", "p", "--run-id", "slurm"])
    assert [r["loss"] for r in server.storage.read_rows("p", "slurm")[0]] == [0.5]


def test_watch_rejects_missing_paths(tmp_path):
    from runboard.cli import main

    with pytest.raises(SystemExit) as e:
        main(["watch", str(tmp_path / "missing")])
    assert e.value.code == 2
    with pytest.raises(SystemExit) as e:
        main(["watch"])
    assert e.value.code == 2
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest -q tests/test_wrap.py -k watch`
Expected: FAIL — `SystemExit: 2` from argparse (`invalid choice: 'watch'`)

- [ ] **Step 3: Implement**

In `src/runboard/runner.py`, add `import re` and `import socket` to the imports, then append:

```python
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
```

In `src/runboard/cli.py`, change the runner import to `from .runner import run_command, watch_command`, and after the `run` subparser add:

```python
    s = sub.add_parser("watch", help="stream metrics from existing log files or directories")
    s.add_argument("paths", nargs="*", metavar="PATH", help="metric files or directories to follow")
    s.add_argument("--project", help="dashboard project (default: current directory name)")
    s.add_argument("--name", help="display name (default: the run id)")
    s.add_argument("--run-id", help="run id (default: stable id derived from the first path)")
    s.add_argument("--stdout-file", metavar="FILE", help="read metrics from a text log such as a Slurm .out file")
    s.add_argument("--once", action="store_true", help="upload what exists and exit")
    s.add_argument("--config-json", metavar="FILE", help="record hyperparameters from a JSON file")
    s.add_argument("--config", action="append", default=[], metavar="KEY=VALUE", help="record a hyperparameter")
    s.set_defaults(func=watch_command)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest -q tests/test_wrap.py`
Expected: 12 passed

- [ ] **Step 5: Commit**

```bash
git add src/runboard/runner.py src/runboard/cli.py tests/test_wrap.py
git commit -m "$(cat <<'EOF'
Add runboard watch for running and finished jobs

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 10: `runboard doctor`

**Files:**
- Create: `src/runboard/doctor.py`
- Modify: `src/runboard/cli.py`
- Test: `tests/test_doctor.py`

**Interfaces:**
- Consumes: `config.read_server_info`, `config.home`; `Storage`.
- Produces: `resolve(server=None, token=None) -> dict` with `mode` (`"http"` or `"file"`) and, for http, `server`, `server_from`, `token`, `token_from`; for file, `dir`, `dir_from`, `partial` (bool). `doctor(server=None, token=None, out=None) -> int` (0 PASS, 1 FAIL).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_doctor.py`:

```python
from runboard.doctor import doctor


def test_doctor_passes_against_a_local_server(server, capsys):
    assert doctor(server=f"http://127.0.0.1:{server.port}", token="secret") == 0
    out = capsys.readouterr().out
    assert out.strip().endswith("PASS") and "secret" not in out and "from argument" in out
    assert len(server.storage.read_rows("runboard", "doctor")[0]) == 3


def test_doctor_reports_a_rejected_token(server, capsys):
    assert doctor(server=f"http://127.0.0.1:{server.port}", token="wrong-token") == 1
    out = capsys.readouterr().out
    assert "FAIL:" in out and "rejected the token" in out and "fix:" in out and "wrong-token" not in out


def test_doctor_reports_a_refused_connection(capsys):
    assert doctor(server="http://127.0.0.1:9", token="secret") == 1
    assert "refused" in capsys.readouterr().out


def test_doctor_checks_file_mode(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("RUNBOARD_DIR", str(tmp_path / "shared"))
    assert doctor() == 0
    out = capsys.readouterr().out
    assert "from RUNBOARD_DIR" in out and out.strip().endswith("PASS")


def test_doctor_notes_missing_configuration(capsys):
    assert doctor() == 0
    assert "no server is configured" in capsys.readouterr().out


def test_doctor_fails_on_half_configuration(monkeypatch, capsys):
    monkeypatch.setenv("RUNBOARD_SERVER", "http://example.invalid")
    assert doctor() == 1
    assert "not both" in capsys.readouterr().out
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest -q tests/test_doctor.py`
Expected: FAIL — `ModuleNotFoundError: No module named 'runboard.doctor'`

- [ ] **Step 3: Implement**

Create `src/runboard/doctor.py`:

```python
import json
import os
import secrets
import socket
import ssl
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from . import config as _config
from .storage import Storage

USER_AGENT = "runboard/0.2"
PROJECT = "runboard"
RUN_ID = "doctor"
OFFLINE_FIX = "this machine may not allow outbound HTTPS; set RUNBOARD_DIR to a shared directory and run `runboard serve` (see `runboard guide`)"


class Failure(Exception):
    def __init__(self, cause, fix):
        super().__init__(cause)
        self.cause = cause
        self.fix = fix


def _pick(options):
    for value, source in options:
        if value:
            return value, source
    return None, None


def resolve(server=None, token=None):
    info = _config.read_server_info()
    saved = str(_config.home() / "server.json")
    url, url_from = _pick([(server, "argument"), (os.environ.get("RUNBOARD_SERVER"), "RUNBOARD_SERVER"), (info.get("url"), saved)])
    secret, secret_from = _pick([(token, "argument"), (os.environ.get("RUNBOARD_TOKEN"), "RUNBOARD_TOKEN"), (info.get("token"), saved)])
    directory = os.environ.get("RUNBOARD_DIR")
    if directory and not server:
        return {"mode": "file", "dir": directory, "dir_from": "RUNBOARD_DIR", "partial": False}
    if url and secret:
        return {"mode": "http", "server": url.rstrip("/"), "server_from": url_from, "token": secret, "token_from": secret_from}
    return {"mode": "file", "dir": "./runboard-runs", "dir_from": "default", "partial": bool(url or secret)}


def _http_failure(server, code):
    if code == 401:
        return Failure(f"{server} rejected the token (HTTP 401)", "run `runboard configure <url>` with the RUNBOARD_TOKEN stored in the Worker, or correct RUNBOARD_TOKEN")
    if code == 403:
        return Failure(f"request to {server} was blocked before reaching Runboard (HTTP 403)", "a proxy or Cloudflare security rule is blocking this machine; check proxy settings or allow it in Cloudflare")
    if code == 404:
        return Failure(f"{server} has no Runboard API (HTTP 404)", "check the URL; it should be the Worker or `runboard serve` address")
    if code == 413:
        return Failure(f"{server} rejected a minimal request as too large (HTTP 413)", "a proxy between this machine and the server limits request size")
    return Failure(f"{server} returned HTTP {code}", "check the Worker logs with `npx wrangler tail`, or the `runboard serve` output")


def _network_failure(server, reason):
    if isinstance(reason, ssl.SSLError):
        return Failure(f"TLS handshake with {server} failed ({reason})", "set SSL_CERT_FILE to your cluster's CA bundle, or ask your admin about HTTPS interception")
    if isinstance(reason, socket.gaierror):
        return Failure(f"cannot resolve the host in {server}", "check the URL; if it is correct, " + OFFLINE_FIX)
    if isinstance(reason, ConnectionRefusedError):
        return Failure(f"connection to {server} was refused", "check the URL and port; for a local server, make sure `runboard serve` is running")
    if isinstance(reason, (socket.timeout, TimeoutError)):
        return Failure(f"timed out connecting to {server}", OFFLINE_FIX)
    return Failure(f"cannot reach {server} ({reason})", OFFLINE_FIX)


def _request(server, token, path, payload=None):
    data = None if payload is None else json.dumps(payload).encode()
    headers = {"Authorization": f"Bearer {token}", "User-Agent": USER_AGENT}
    if data is not None:
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(server + path, data=data, headers=headers, method="GET" if data is None else "POST")
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as e:
        raise _http_failure(server, e.code) from None
    except urllib.error.URLError as e:
        raise _network_failure(server, e.reason) from None
    except (socket.timeout, TimeoutError) as e:
        raise _network_failure(server, e) from None
    except ValueError as e:
        raise Failure(f"{server} is not a valid Runboard URL or did not return JSON ({e})", "use the full https:// URL of the Worker or `runboard serve`") from None
    except OSError as e:
        raise _network_failure(server, e) from None


def _rows(sid, count=3):
    now = time.time()
    return [{"doctor/check": float(index), "_step": index, "_time": now, "_seq": index, "_sid": sid} for index in range(count)]


def _check_http(server, token):
    status, _ = _request(server, token, "/api/health")
    yield "health", f"ok (HTTP {status})"
    sid = secrets.token_hex(8)
    _request(server, token, f"/api/runs/{PROJECT}/{RUN_ID}", {"meta": {"name": "doctor", "status": "finished"}, "rows": _rows(sid)})
    found, offset = 0, 0
    for _ in range(1000):
        _, page = _request(server, token, f"/api/metrics?project={PROJECT}&run={RUN_ID}&offset={offset}")
        rows = page.get("rows") or []
        found += sum(1 for row in rows if isinstance(row, dict) and row.get("_sid") == sid)
        if not rows or page.get("offset") == offset:
            break
        offset = page.get("offset", offset)
    if found != 3:
        raise Failure(f"wrote 3 rows to {PROJECT}/{RUN_ID} but read back {found}", "the server accepted the write but did not store it; check the Worker's D1 binding or the server's --dir")
    yield "write/read", f"ok (3 rows written to {PROJECT}/{RUN_ID} and read back)"


def _check_file(directory):
    root = Path(directory).expanduser().resolve()
    sid = secrets.token_hex(8)
    try:
        storage = Storage(root)
        storage.update_meta(PROJECT, RUN_ID, {"name": "doctor", "status": "finished"})
        storage.append_rows(PROJECT, RUN_ID, _rows(sid))
        found, offset = 0, 0
        while True:
            rows, new_offset = storage.read_rows(PROJECT, RUN_ID, offset)
            found += sum(1 for row in rows if row.get("_sid") == sid)
            if new_offset == offset:
                break
            offset = new_offset
    except OSError as e:
        raise Failure(f"cannot write to {root} ({e})", "set RUNBOARD_DIR to a writable directory, ideally on storage shared with the dashboard server") from None
    if found != 3:
        raise Failure(f"wrote 3 rows to {root} but read back {found}", "check that the directory is not on a read-only or eventually consistent filesystem")
    yield "write/read", f"ok (3 rows written to {root}/{PROJECT}/{RUN_ID} and read back)"


def doctor(server=None, token=None, out=None):
    out = out or sys.stdout

    def line(label, text):
        print(f"  {label + ':':12} {text}", file=out)

    print("runboard doctor", file=out)
    target = resolve(server, token)
    try:
        if target["mode"] == "http":
            line("destination", f"{target['server']} (from {target['server_from']})")
            line("token", f"set (from {target['token_from']})")
            checks = _check_http(target["server"], target["token"])
        else:
            if target["partial"]:
                raise Failure("a server URL or a token is configured, but not both", "set both RUNBOARD_SERVER and RUNBOARD_TOKEN, or run `runboard configure <url>`")
            line("destination", f"local files in {Path(target['dir']).expanduser().resolve()} (from {target['dir_from']})")
            checks = _check_file(target["dir"])
        for label, text in checks:
            line(label, text)
    except Failure as failure:
        print(f"FAIL: {failure.cause}", file=out)
        print(f"fix:  {failure.fix}", file=out)
        return 1
    if target["mode"] == "file" and target["dir_from"] == "default":
        print("note: no server is configured, so runs are written to ./runboard-runs; run `runboard configure <url>` to use Cloudflare", file=out)
    print("PASS", file=out)
    return 0
```

In `src/runboard/cli.py`, add `from .doctor import doctor`, add the function:

```python
def cmd_doctor(args):
    return doctor(args.server, args.token)
```

and the subparser after `watch`:

```python
    s = sub.add_parser("doctor", help="check where metrics go and send a test run")
    s.add_argument("--server", help="check this server instead of the configured one")
    s.add_argument("--token", help="token for --server")
    s.set_defaults(func=cmd_doctor)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest -q tests/test_doctor.py`
Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
git add src/runboard/doctor.py src/runboard/cli.py tests/test_doctor.py
git commit -m "$(cat <<'EOF'
Add runboard doctor with actionable PASS/FAIL output

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 11: Hosted `runboard ls`

**Files:**
- Modify: `src/runboard/cli.py` (`cmd_ls` and the `ls` subparser)
- Test: `tests/test_client.py`

**Interfaces:**
- Produces: `cmd_ls(args)` where `args` has `dir` (default `None`), `server`, `token`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_client.py`:

```python
def test_ls_lists_runs_from_the_configured_server(server, capsys):
    from runboard.cli import main

    config.write_server_info({"url": f"http://127.0.0.1:{server.port}", "token": "secret"})
    server.storage.update_meta("p", "remote-run", {"status": "finished"})
    main(["ls"])
    assert "remote-run" in capsys.readouterr().out


def test_ls_with_dir_reads_local_runs(tmp_path, capsys):
    from runboard.cli import main

    Storage(tmp_path / "runs").update_meta("p", "local-run", {"status": "running"})
    main(["ls", "--dir", str(tmp_path / "runs")])
    assert "local-run" in capsys.readouterr().out
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest -q tests/test_client.py -k "test_ls_"`
Expected: FAIL — the hosted test prints nothing because `ls` reads `./runboard-runs`; `assert 'remote-run' in ''`

- [ ] **Step 3: Implement**

In `src/runboard/cli.py`, add `import json` and `import os` to the imports, replace `cmd_ls` with:

```python
def _remote_runs(server, token):
    request = urllib.request.Request(
        f"{server.rstrip('/')}/api/runs",
        headers={"Authorization": f"Bearer {token}", "User-Agent": "runboard/0.2"},
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as e:
        raise SystemExit(f"runboard: {server} returned HTTP {e.code}; run `runboard doctor`") from e
    except (OSError, ValueError) as e:
        raise SystemExit(f"runboard: cannot list runs from {server} ({e}); run `runboard doctor`") from e


def cmd_ls(args):
    runs = None
    if args.dir is None and (args.server or not os.environ.get("RUNBOARD_DIR")):
        info = config.read_server_info()
        server = args.server or os.environ.get("RUNBOARD_SERVER") or info.get("url")
        token = args.token or os.environ.get("RUNBOARD_TOKEN") or info.get("token")
        if server and token:
            runs = _remote_runs(server, token)
    if runs is None:
        runs = Storage(args.dir or os.environ.get("RUNBOARD_DIR") or "./runboard-runs").list_runs()
    for r in runs:
        upd = datetime.fromtimestamp(r["updated"]).strftime("%Y-%m-%d %H:%M") if r.get("updated") else "-"
        print(f"{r['project']:20} {r['run_id']:28} {str(r.get('status', '')):10} {upd}  {r.get('name', '')}")
```

Replace the `ls` subparser with:

```python
    s = sub.add_parser("ls", help="list runs on the configured server or in a local directory")
    s.add_argument("--dir", default=None, help="list runs stored in this directory")
    s.add_argument("--server", help="list runs on this server")
    s.add_argument("--token", help="token for --server")
    s.set_defaults(func=cmd_ls)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest -q`
Expected: all pass

- [ ] **Step 5: Commit**

```bash
git add src/runboard/cli.py tests/test_client.py
git commit -m "$(cat <<'EOF'
List runs from the configured server in runboard ls

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 12: Agent guide and `runboard guide`

**Files:**
- Create: `src/runboard/AGENT_GUIDE.md`, `docs/integrating.md` (identical copy)
- Modify: `src/runboard/cli.py`, `pyproject.toml`, `.github/workflows/ci.yml`
- Test: `tests/test_client.py`

**Interfaces:**
- Produces: `cmd_guide(args)` printing the packaged guide.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_client.py`:

```python
def test_guide_prints_the_packaged_agent_guide(capsys):
    from runboard.cli import main

    main(["guide"])
    out = capsys.readouterr().out
    assert out.startswith("# Integrating Runboard") and "runboard doctor" in out and "runboard run --" in out


def test_agent_guide_matches_docs_copy():
    root = Path(__file__).parents[1]
    assert (root / "src" / "runboard" / "AGENT_GUIDE.md").read_bytes() == (root / "docs" / "integrating.md").read_bytes()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest -q tests/test_client.py -k "guide"`
Expected: FAIL — `SystemExit: 2` (`invalid choice: 'guide'`) and `FileNotFoundError` for `AGENT_GUIDE.md`

- [ ] **Step 3: Write the guide**

Create `src/runboard/AGENT_GUIDE.md` with exactly this content:

````markdown
# Integrating Runboard into a training codebase

These instructions are for coding agents and people adding Runboard to an existing training project.
Runboard streams a training job's metrics to the user's personal dashboard. It needs no changes to
training code and uses only the Python standard library.

## 1. Check the connection

Run:

```bash
runboard doctor
```

It prints where metrics will go and ends with `PASS` or `FAIL`. On `FAIL`, apply the printed `fix:`
line and run it again. If the fix needs a URL or token you do not have, stop and ask the user.

If the destination reads `local files ... (from default)`, ask the user whether they have a Cloudflare
deployment. If they do, they run `runboard configure <url>` themselves.

## 2. Find what the job already logs

Runboard reads these without code changes, in this order of preference:

| Format | Files | Typical sources |
|---|---|---|
| TensorBoard | `events.out.tfevents.*` | PyTorch `SummaryWriter`, Hugging Face Trainer and TRL with `report_to="tensorboard"`, Lightning `TensorBoardLogger` |
| JSONL or CSV | `*.jsonl`, `*metrics*.csv` | Custom metric files, Lightning `CSVLogger` |
| Hugging Face state | `trainer_state.json`, `checkpoint-*/trainer_state.json` | Any Hugging Face Trainer or TRL run; updates only when a checkpoint is saved |
| Command output | Lines with `key=value` or `key: value` pairs | Print-based logging, Hugging Face `{'loss': ...}` lines, verl console output |

Search the code near the training loop for `SummaryWriter`, `report_to`, `logger=`, `.jsonl`, and
`print(` to see which apply. Only the highest-priority format present is followed, so the same metric
is never sent twice.

## 3. Wrap the launch command

Put `runboard run --` in front of the command that starts training. Do not change the training code.

```bash
runboard run --project my-project -- python train.py --lr 3e-4
```

Wrap launchers once, not once per process:

```bash
runboard run --project my-project -- torchrun --nproc_per_node 8 train.py
runboard run --project my-project -- accelerate launch train.py
runboard run --project my-project -- deepspeed train.py --deepspeed ds_config.json
```

In a Slurm batch script, change only the launch line:

```bash
runboard run --project my-project --name "$SLURM_JOB_NAME-$SLURM_JOB_ID" -- python train.py
```

When `srun` starts one task per GPU, put the wrapper inside `srun`:
`srun runboard run --project my-project -- python train.py`. Only the task with rank 0 streams
metrics; the other tasks run unchanged.

| Option | Use |
|---|---|
| `--project NAME` | Groups runs in the dashboard. Defaults to the current directory name. |
| `--name NAME` | Display name. Defaults to the script name and a timestamp. |
| `--watch DIR` | Also search an output directory outside the current directory, such as `--watch /scratch/$USER/outputs`. |
| `--source FILE` | Follow only this file and turn off automatic discovery. |
| `--stdout on` | Always read metrics from command output. `--stdout off` never does. |
| `--config KEY=VALUE`, `--config-json FILE` | Record hyperparameters with the run. |

The wrapper never changes the command's output or exit code, and the command still runs if Runboard
cannot connect. Only files created or changed after launch are followed, so datasets and earlier runs
in the same directory are ignored.

To stream a job that is already running, or to upload logs from a finished run:

```bash
runboard watch ./outputs                            # follows until Ctrl-C
runboard watch ./outputs --once                     # uploads what exists and exits
runboard watch --stdout-file slurm-1234.out --once  # reads metrics printed to a Slurm log
```

Running `watch` again on the same path continues the same run without duplicating rows.

## 4. Verify

1. The wrapper's last line on stderr reads `runboard: finished: logged N rows to <project>/<run_id>`,
   and `N` is greater than 0. `no metrics found` means none of the formats above were written or
   printed; check where the job writes its logs and use `--watch DIR` or `--source FILE`.
2. `runboard ls` lists the run with its status and last update time.

Tell the user the project and run name. They open their dashboard as usual.

## 5. Custom metrics in code (optional)

Use the Python API only when the user wants metrics that are not already logged:

```python
import runboard

runboard.init(project="my-project", config={"lr": 3e-4})
for step in range(num_steps):
    loss = train_step()
    runboard.log({"train/loss": loss}, step=step)
runboard.finish()
```

- Call `runboard.init()` once per run in the training process. Under torchrun, DDP, and Accelerate,
  only rank 0 logs; calls on other ranks do nothing, so they can stay unconditional.
- Pass the global training step, not a per-epoch counter.
- Log already reduced scalars. Values that are not numbers are ignored.
- Name metrics `group/name` (`train/loss`, `eval/accuracy`) to group them in the dashboard.
- Do not combine `runboard.init()` with `runboard run` for the same job; that creates two runs.

## Rules for agents

- Do not change training logic, hyperparameters, or output paths to integrate Runboard.
- Never print, log, or commit `RUNBOARD_TOKEN`, `~/.runboard/server.json`, or URLs containing
  `?token=`.
- Do not start `runboard serve` or a tunnel when `runboard doctor` shows a hosted server.
- Do not install other packages for Runboard.

## Troubleshooting

| `runboard doctor` says | Do this |
|---|---|
| `rejected the token (HTTP 401)` | The token does not match the Worker secret. Ask the user to run `runboard configure <url>`. |
| `blocked before reaching Runboard (HTTP 403)` | A proxy or Cloudflare rule blocks this machine. Ask the user. |
| `cannot resolve the host` or `timed out` | The machine may lack outbound HTTPS. Set `RUNBOARD_DIR` to shared storage and run `runboard serve` where the user can reach it. |
| `connection ... was refused` | Check the URL and port. For a local server, start `runboard serve`. |
| `TLS handshake ... failed` | Set `SSL_CERT_FILE` to the cluster's CA bundle. |
| `configured, but not both` | Set both `RUNBOARD_SERVER` and `RUNBOARD_TOKEN`, or run `runboard configure <url>`. |
````

Then copy it:

```bash
mkdir -p docs && cp src/runboard/AGENT_GUIDE.md docs/integrating.md
```

- [ ] **Step 4: Wire `guide`, packaging, and CI**

In `src/runboard/cli.py`, add `from pathlib import Path` to the imports, add:

```python
def cmd_guide(args):
    sys.stdout.write((Path(__file__).parent / "AGENT_GUIDE.md").read_text())
```

and the subparser after `doctor`:

```python
    s = sub.add_parser("guide", help="print the integration guide for coding agents")
    s.set_defaults(func=cmd_guide)
```

In `pyproject.toml`, change the package-data line to:

```toml
runboard = ["static/*", "AGENT_GUIDE.md"]
```

In `.github/workflows/ci.yml`, in the "Smoke-test the wheel" step, add after the `runboard --version` line:

```yaml
          /tmp/smoke/bin/runboard guide | grep -q "runboard doctor"
```

- [ ] **Step 5: Run the tests and a wheel check**

Run: `python -m pytest -q`
Expected: all pass

Run: `python -m pip wheel --no-deps -w /tmp/rb-wheel . && python -m zipfile -l /tmp/rb-wheel/runboard-*.whl | grep AGENT_GUIDE.md`
Expected: a line listing `runboard/AGENT_GUIDE.md`

- [ ] **Step 6: Commit**

```bash
git add src/runboard/AGENT_GUIDE.md docs/integrating.md src/runboard/cli.py pyproject.toml .github/workflows/ci.yml tests/test_client.py
git commit -m "$(cat <<'EOF'
Ship an agent integration guide and runboard guide

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 13: Documentation

**Files:**
- Modify: `README.md`, `AGENTS.md`, `docs/architecture.md`, `CHANGELOG.md`

- [ ] **Step 1: Fix stale README facts**

Make these exact replacements in `README.md`:

| Old | New |
|---|---|
| `**4. Open the URL printed by \`runboard configure\`.** Charts update live every two seconds.` | `**4. Open the URL printed by \`runboard configure\`.** The dashboard refreshes every 5 seconds.` |
| `server once per second, in chunks of up to 5,000 rows.` | `server every 10 seconds (every second in local file mode), in chunks of up to 5,000 rows.` |
| `It polls every two seconds and fetches only batches it` | `It polls every 5 seconds and fetches only batches it` |

- [ ] **Step 2: Move "Binned relationship plots" under "The dashboard"**

Run:

```bash
python - <<'EOF'
from pathlib import Path
path = Path("README.md")
text = path.read_text()
start = text.index("### Binned relationship plots")
section = text[start:].rstrip() + "\n\n"
text = text[:start].rstrip() + "\n"
anchor = text.index("## Configuration")
path.write_text(text[:anchor] + section + text[anchor:])
EOF
```

Expected: `grep -n "^## \|^### Binned" README.md` shows `### Binned relationship plots` between `## The dashboard` and `## Configuration`, and `## License` is the last heading.

- [ ] **Step 3: Add the integration sections and contents entries**

In the Contents list, after `- [Quickstart](#quickstart)`, insert:

```markdown
- [Zero-code integration](#zero-code-integration)
- [For coding agents](#for-coding-agents)
```

Immediately before `## How it works`, insert:

````markdown
## Zero-code integration

Already logging to TensorBoard, a JSONL or CSV file, the Hugging Face Trainer, or just printing
`loss=...` lines? Put `runboard run --` in front of the launch command and leave the training code alone:

```bash
runboard run --project resnet -- python train.py --lr 3e-4
runboard run --project llm -- torchrun --nproc_per_node 8 train.py
```

The wrapper starts the command, finds the metric files it writes in the current directory (add more
with `--watch DIR`), and streams them to your dashboard. It prefers TensorBoard event files, then
`*.jsonl` and `*metrics*.csv`, then Hugging Face `trainer_state.json`, and reads printed metrics only
when none of those appear. Output and exit code pass through unchanged, and the run is marked
`finished`, `crashed`, or `killed` from the exit code.

To stream a job that is already running, or to upload a finished run's logs:

```bash
runboard watch ./outputs          # follow until Ctrl-C
runboard watch ./outputs --once   # upload what exists and exit
```

Running `watch` again on the same path continues the same run without duplicating rows.

## For coding agents

`runboard guide` prints step-by-step integration instructions for coding agents such as Claude Code and
Codex; the same text is in [docs/integrating.md](docs/integrating.md). In short: run `runboard doctor`
(it ends with `PASS` or `FAIL` and a fix), put `runboard run --` in front of the launch command, and
check that the wrapper's last line reports logged rows.

````

- [ ] **Step 4: Update the CLI reference table**

Replace the `runboard ls` row with:

```markdown
| `runboard ls [--dir D] [--server URL]` | List runs on the configured server, or in a local directory |
```

Insert these rows after the `runboard configure URL` row:

```markdown
| `runboard run [options] -- CMD` | Run a training command and stream the metrics it logs. Options: `--project`, `--name`, `--run-id`, `--watch DIR`, `--source FILE`, `--stdout auto\|on\|off`, `--config KEY=VALUE`, `--config-json FILE` |
| `runboard watch [PATH…]` | Stream metrics from existing log files or directories. `--once` uploads and exits; `--stdout-file FILE` reads a text log |
| `runboard doctor` | Show where metrics go, send a test run, and print `PASS` or `FAIL` with a fix |
| `runboard guide` | Print the integration guide for coding agents |
```

- [ ] **Step 5: Update `AGENTS.md`**

Replace the `cli.py` row in the repository map with:

```markdown
| `src/runboard/cli.py` | `configure`, `serve`, `ls`, `sync`, `url`, `run`, `watch`, `doctor`, `guide`, and version commands. |
```

Insert after it:

```markdown
| `src/runboard/runner.py` | `runboard run` wrapper, `runboard watch`, follower orchestration, and follower state. |
| `src/runboard/follow/` | Dependency-free followers for TensorBoard, JSONL, CSV, Hugging Face state, and text output, plus discovery. |
| `src/runboard/doctor.py` | `runboard doctor` configuration report and write/read check. |
| `src/runboard/AGENT_GUIDE.md` | Integration guide for coding agents, printed by `runboard guide`. |
| `docs/integrating.md` | Byte-identical copy of `AGENT_GUIDE.md`. |
```

Under `### Training must remain safe`, add as the last bullet:

```markdown
- `runboard run` must never change the wrapped command's output or exit code, and must still run the command when Runboard setup fails.
```

Under `### The dashboard has two synchronized copies`, add as the last bullet:

```markdown
- `src/runboard/AGENT_GUIDE.md` and `docs/integrating.md` must stay byte-identical; `test_agent_guide_matches_docs_copy` enforces this.
```

- [ ] **Step 6: Update `docs/architecture.md`**

Replace the `cli.py` row with:

```markdown
| `cli.py` | `runboard configure / serve / ls / sync / url / run / watch / doctor / guide` |
```

Insert after it:

```markdown
| `runner.py` | `Streamer`, the `run` wrapper, and `watch`; follower state in `~/.runboard/followers/` |
| `follow/` | TensorBoard, JSONL, CSV, Hugging Face state, and text followers; discovery |
| `doctor.py` | Configuration report and write/read check |
```

Append a new section at the end of the file:

```markdown
## Log followers

`runboard run` and `runboard watch` attach followers to an ordinary `Run`. A follower tails one source
by byte offset (or `log_history` position for `trainer_state.json`) and returns complete records only.
Discovery follows only the highest-priority format present: TensorBoard, then JSONL/CSV, then
`trainer_state.json`, then command output. `runboard run` ignores files not modified since launch.

Each source is its own client session. Its `_sid` is derived from a random nonce stored in
`~/.runboard/followers/<project>__<run_id>.json`, the source format, and its absolute path, and each
record's `_seq` is its index within that source. Re-reading a source reproduces the same records, so
resuming from any saved position is deduplicated by the existing per-session rule. The state file is
saved only when no send is in progress and the run's queue is empty.

Spool files are named `<project>__<run_id>__<stamp>__<sid>__<first_seq>.jsonl` with a strictly
increasing stamp, so `runboard sync` replays them in the order they were written.
```

- [ ] **Step 7: Update `CHANGELOG.md`**

Under `## [Unreleased]`, before `### Fixed`, insert:

```markdown
### Added

- `runboard run -- <command>` streams a training job's existing TensorBoard, JSONL, CSV, Hugging Face
  `trainer_state.json`, or printed metrics without code changes, and keeps the command's output and exit
  code.
- `runboard watch PATH` streams logs from a running job or uploads a finished run's logs; running it
  again continues the same run without duplicates.
- `runboard doctor` shows where metrics go, sends a test run, and prints `PASS` or `FAIL` with a fix.
- `runboard guide` prints an integration guide for coding agents, also in `docs/integrating.md`.
- `runboard ls` lists runs from the configured server; pass `--dir` for a local directory.
```

Under `### Fixed`, add:

```markdown
- `runboard sync` replays spool files in the order they were written. Spool file names now include a
  creation stamp.
- The README states the actual dashboard refresh and HTTP send intervals.
```

- [ ] **Step 8: Run the full suite**

Run: `python -m pytest -q`
Expected: all pass

- [ ] **Step 9: Commit**

```bash
git add README.md AGENTS.md docs/architecture.md CHANGELOG.md
git commit -m "$(cat <<'EOF'
Document zero-code integration, doctor, and the agent guide

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 14: End-to-end verification

**Files:** none.

- [ ] **Step 1: Full Python and JavaScript suites**

Run: `python -m pytest -q && node tests/js/app.test.cjs && node tests/js/binned.test.cjs`
Expected: all pass

- [ ] **Step 2: Python 3.8 syntax check**

Run: `python -c "import ast, pathlib; [ast.parse(p.read_text(), feature_version=(3, 8)) for p in pathlib.Path('src').rglob('*.py')]; print('ok')"`
Expected: `ok`

- [ ] **Step 3: Real end-to-end run against a local server**

```bash
export RUNBOARD_HOME="$(mktemp -d)"
runboard serve --dir "$RUNBOARD_HOME/runs" --port 8765 > "$RUNBOARD_HOME/serve.log" 2>&1 &
SERVE_PID=$!
sleep 2
runboard doctor
mkdir -p "$RUNBOARD_HOME/job" && cd "$RUNBOARD_HOME/job"
printf 'import json\nfor s in range(5):\n    open("metrics.jsonl","a").write(json.dumps({"step": s, "loss": 1/(s+1)})+"\\n")\n' > train.py
runboard run --project e2e -- python train.py
runboard ls
cd - && kill $SERVE_PID
```

Expected: doctor ends with `PASS`; the wrapper ends with `runboard: finished: logged 5 rows to e2e/<run_id>`; `runboard ls` lists the `e2e` run with status `finished`.

- [ ] **Step 4: Confirm the tree is clean except workflow docs**

Run: `git status --short`
Expected: only `?? docs/superpowers/`. Do not push.
