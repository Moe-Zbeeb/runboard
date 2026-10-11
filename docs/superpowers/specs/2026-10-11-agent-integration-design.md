# Zero-code, agent-friendly integration

Status: approved design, revised during planning
Date: 2026-10-11
Sub-project: B of 2 (A, the dashboard redesign with focus mode, follows separately)

## Goal

A coding agent (Claude Code, Codex, or similar) or a researcher can connect any training codebase to
Runboard without editing training code, verify that the connection works, and get it right the first
time. Runboard streams the log files and output a job already produces.

Success criteria:

- Adding Runboard to a job is a one-line change to its launch command or sbatch script.
- TensorBoard, JSONL, CSV, Hugging Face `trainer_state.json`, and plain stdout metrics all reach the
  dashboard without training-code changes.
- An agent can prove the integration works with commands that end in an unambiguous result, and a
  non-zero exit code on failure.
- The documentation an agent reads is accurate and ships inside the package.

## Constraints

These are unchanged from `AGENTS.md`:

- Zero runtime dependencies and Python 3.8 through 3.13 compatibility.
- No protocol, Python server, or Cloudflare Worker change. Both backends stay interchangeable.
- Runboard never prevents a training job from starting, never changes its output, and never changes
  its exit code.
- Secrets are never printed.

## Architecture

New commands create an ordinary `runboard.Run` and attach followers whose records are appended with
`Run._log_row()`. Batching, retry, spooling, deduplication, bounded `finish()`, and both backends are
reused unchanged.

```text
training command ──► log files + stdout
                         │
             followers (one per source, byte-offset tailing)
                         │  (step, time, metrics)
                         ▼
          Streamer ──► Run._log_row(sid per source, seq = record index)
                         │
                  existing sender ──► Worker/D1 or local server
```

Code layout:

| Path | Responsibility |
|---|---|
| `src/runboard/follow/__init__.py` | `Record`, record extraction, and the byte-offset `LineFollower` |
| `src/runboard/follow/tensorboard.py` | Pure-Python TFRecord and Event/Summary scalar reader with CRC32C checks |
| `src/runboard/follow/jsonl.py` | JSON Lines metrics |
| `src/runboard/follow/csvfile.py` | CSV metrics |
| `src/runboard/follow/hf_state.py` | Hugging Face `trainer_state.json` `log_history` |
| `src/runboard/follow/text.py` | Metric extraction from stdout or text log lines |
| `src/runboard/follow/discover.py` | Directory scanning, format precedence, and limits |
| `src/runboard/runner.py` | `Streamer`, follower state, and the `run` and `watch` commands |
| `src/runboard/doctor.py` | Configuration report and end-to-end write/read check |
| `src/runboard/AGENT_GUIDE.md` | Agent integration guide shipped in the wheel |
| `src/runboard/cli.py` | Wires `run`, `watch`, `doctor`, `guide`, and hosted `ls` |

## Commands

```bash
runboard run [--project P] [--name N] [--run-id ID] [--watch DIR]... [--source FILE]...
             [--stdout auto|on|off] [--config-json FILE] [--config KEY=VALUE]... -- <command...>
runboard watch [PATH...] [--project P] [--name N] [--run-id ID] [--stdout-file FILE]
             [--once] [--config-json FILE] [--config KEY=VALUE]...
runboard doctor [--server URL] [--token TOKEN]
runboard guide
runboard ls [--dir D] [--server URL] [--token TOKEN]
```

### `runboard run`

- Starts the command as a child process. When command output is parsed, child stdout and stderr are
  piped through to the wrapper's stdout and stderr byte for byte, and the child receives
  `PYTHONUNBUFFERED=1` unless already set. With `--stdout off`, the child inherits the wrapper's
  output directly.
- Watches the current working directory and each `--watch DIR` using discovery, unless `--source`
  files are given, in which case only those files are followed. Discovery for `run` ignores files not
  modified since two seconds before launch, so datasets and earlier runs in the same directory are not
  picked up.
- Defaults: project is `--project` or the sanitized current directory name; name is `--name` or the
  command's script or module name plus a timestamp; run ID is new per launch unless `--run-id` is
  given.
- `--watch` and `--source` paths that do not exist, and `--source` directories, produce a warning.
- When the wrapper's own output is closed (for example `| head`), it keeps draining the child's output
  and still exits with the child's exit code.
- Signals: `SIGTERM` is forwarded to the child once. `SIGINT` is not forwarded because a terminal
  delivers it to the whole foreground process group, so the child already receives it; the wrapper
  ignores it while the child runs. Signals that arrive before the child starts are forwarded right
  after it starts.
- When the child exits, followers drain remaining complete records. Status is `finished` for exit
  code 0; `killed` for a signal exit (negative return code, 130, or 143) or when the wrapper received
  `SIGTERM`; and `crashed` otherwise. The wrapper exits with the child's exit code, or 128 plus the
  signal number for a signal exit.
- A command that cannot be started exits 127 with a message, and the run is marked `crashed`.
- The last stderr line is a summary: `runboard: <status>: logged N rows to <project>/<run_id>`, or a
  `no metrics found` message with a pointer to `runboard guide` when N is 0.
- Wraps whole launchers (`torchrun`, `accelerate launch`, `deepspeed`) so there is one wrapper per
  job.

### `runboard watch`

- Follows the given files and directories; directories are discovered even when files are also given.
  Without `--once` it runs until interrupted by `SIGINT` or `SIGTERM`, which stop it cleanly after the
  current poll; with `--once` it reads all complete records, delivers them, and exits.
- At least one path or `--stdout-file` is required.
- File arguments with an unrecognized name are read as text logs. `--stdout-file FILE` always reads
  `FILE` as a text log, such as a Slurm `.out` file.
- The default run ID is stable: the sanitized basename of the first path plus the first 8 hex digits
  of SHA-256 of `hostname + absolute path`. Running `watch` again on the same path continues the same
  run, and the run keeps its original creation time.
- `--once` finishes the run as `finished`; an interrupted watch finishes it as `killed`.

### `runboard doctor`

Prints, without ever printing the token:

1. The resolved destination and the source of each value (`argument`, `RUNBOARD_SERVER`,
   `RUNBOARD_TOKEN`, `RUNBOARD_DIR`, `~/.runboard/server.json`, or the local file-mode default).
2. Health check result and HTTP status.
3. A write of three rows to the fixed run `runboard/doctor` with a fresh session, then a read back
   through `/api/metrics` (or local storage in file mode) that confirms the rows arrived.
4. `PASS`, or `FAIL: <cause>` followed by `fix: <action>`, and exit code 1 on failure.

Mapped failures: token rejected (401), request blocked (403), no Runboard API (404), 413 on a minimal
write, other HTTP statuses, DNS failure, connection refused, TLS failure, timeout, an invalid URL or
non-JSON response, rows not readable after a write, an unwritable file-mode directory, and a server URL
without a token or a token without a server URL.

With no configuration at all, doctor checks the local file-mode default `./runboard-runs`, passes if it
works, and prints a note explaining that no server is configured.

With `--server` and no `--token`, doctor and `ls` use a saved token only when it was saved for that same
server (`RUNBOARD_SERVER` or `~/.runboard/server.json`); otherwise they fail with `no token`. A reply
that is not HTTP is reported as a failure.

### `runboard guide`

Prints `AGENT_GUIDE.md` from the installed package.

### `runboard ls`

With `--dir`, or with `RUNBOARD_DIR` set and no `--server`, it lists local runs as before. Otherwise,
with a server and token from `--server`/`--token`, the environment, or `~/.runboard/server.json`, it
lists runs from `/api/runs` with the same columns. With nothing configured it lists `./runboard-runs`.

## Followers

A follower owns one source and keeps a byte offset (or log-history position for
`trainer_state.json`). Each poll returns complete records as `Record(step, time, metrics)` and never
consumes a partial trailing record. Metric values pass through the existing `clean_value` rules.
Records with no metrics are ignored. If a file becomes shorter than the saved offset, it is read again
from the start as new content. If its first 64 bytes change while it does not shrink, it was rewritten
in place (Lightning `CSVLogger` does this when a new metric appears); it is read again from the start
and its record count restarts at 0, so rows already stored are deduplicated and only new rows are added.

Step keys, in priority order: `step`, `global_step`, `_step`, `iteration`, `iter`. Time keys:
`time`, `timestamp`, `_time`, `wall_time`, accepted when numeric and between 2000-01-01 and 2100-01-01
in Unix seconds. Other values, such as mmengine's per-iteration `time` duration, are kept as metrics. A record without a step uses the
run's auto-incrementing step; a record without a time uses the current time.

Record boundaries never depend on when a file was read, so re-reading a file always reproduces the
same records in the same order.

### TensorBoard

- Files: `events.out.tfevents.*`.
- Reads TFRecord framing: 8-byte length, 4-byte masked CRC32C of the length, data, 4-byte masked
  CRC32C of the data. CRC32C is implemented in pure Python.
- A truncated trailing record is left for the next poll. A record with a bad data CRC is skipped, and a
  warning is printed once per file. A bad length CRC stops reading that file with a warning.
- Records larger than 1 MB, such as images and histograms, are skipped without being read.
- Decodes `Event.wall_time` (field 1), `Event.step` (field 2), and `Event.summary` (field 5). In each
  `Summary.Value`, reads `tag` (field 1) and either `simple_value` (field 2) or a scalar `tensor`
  (field 8) with `DT_FLOAT` or `DT_DOUBLE` from `float_val`, `double_val`, or `tensor_content`.
- Each event with at least one scalar becomes one record. The data CRC is verified only for events that
  contain scalars, and a poll reads at most 64 MB.
- `add_scalars` series are named from their directory: PyTorch writes `<tag>_<name>/` and tensorboardX
  writes `<tag>/<name>/`, and both become `<tag>/<name>`.

### JSONL

- Files: `*.jsonl`. One JSON object per line; nested dictionaries are flattened with `/`.
- Invalid complete lines are skipped.

### CSV

- Files: `*metrics*.csv`. The first row is the header. Empty cells are skipped, which supports
  Lightning `CSVLogger` output.

### Hugging Face trainer state

- Source: an output directory, or a directly given `trainer_state.json`. In a directory, the most
  recently modified of `trainer_state.json` and `checkpoint-*/trainer_state.json` is read.
- Re-read when its path, modification time, or size changes. Entries are emitted in `log_history`
  order after the last emitted step, counting entries that share a step, so newer checkpoints continue
  without repeats.

### Text and stdout

- Input: the wrapped process's stdout and stderr, a text file given to `watch`, or `--stdout-file`.
- Splits on `\n` and `\r` so progress-bar redraws do not merge lines. ANSI escape sequences are removed.
- Extracts pairs of an optionally quoted key, `:` or `=`, and a number. Keys start with a letter or
  `_` and may contain letters, digits, `_`, `.`, `/`, and `-`. This covers
  `{'loss': 0.42, 'epoch': 0.1}`, `step:12 - actor/loss:0.3`, and `loss=0.42 lr=3e-4`.
- A line is accepted only when it contains a step key or at least two numeric pairs.
- Reader threads copy output to the terminal and hand bytes to the parser; parsing happens in the main
  loop, so pass-through never waits on parsing. Unparsed output is capped at 16 MB per stream.

## Discovery and precedence

1. Files given explicitly with `--source` or as `watch` file arguments are used as given. With
   `--source`, no other files are discovered.
2. Otherwise, watched directories are scanned every 5 seconds. Only the highest-priority format present
   is followed: TensorBoard, then JSONL/CSV, then `trainer_state.json`, then stdout. All files of that
   format are followed, including files that appear later. A higher-priority format that appears later
   replaces the current one; rows already logged are kept.
3. With `--stdout auto`, stdout parsing starts only if no structured format is found within 60 seconds,
   or when the command exits without any structured format having been found. Once a structured format
   is selected, stdout is no longer parsed. `--stdout on` and `--stdout off` force the behavior.
4. When the selected source changes, one line is printed, such as
   `runboard: following 2 TensorBoard sources: runs/a/events.out.tfevents.1, runs/b/events.out.tfevents.2`.
5. Scanning skips hidden directories, `node_modules`, `.git`, `__pycache__`, `venv`, `.venv`,
   `site-packages`, `runboard-runs`, the run's own storage directory, `RUNBOARD_DIR`, and `RUNBOARD_HOME`,
   stops at depth 4, visits at most 5,000 directories per scan, and follows at most 200 files.

Followers only read files. They never modify, move, or delete them.

## Run identity and resume

Each source has its own client session. Its session ID is derived from a random nonce stored in the
run's follower state, the source's format, and its absolute path. Each record's sequence number is its
index among the records read from that source. Because records are reproducible, re-reading a source
from any saved position assigns the same sequence numbers again, and both backends' existing
per-session deduplication skips rows already stored. Command output uses a random session per launch.

Follower state is stored at `~/.runboard/followers/<project>__<run_id>.json` with owner-only
permissions and replaced atomically. It contains the nonce, the run creation time, and each source's
format, record count, and follower position.

- The nonce is saved as soon as the follower state is created.
- State is saved at the start of each loop, before new records are read, when no send is in progress and
  the run's queue is empty, meaning everything logged so far was delivered; and after `finish()` only if
  `finish()` delivered everything. When `finish()` spools rows, the last delivered checkpoint is kept,
  so a resume re-reads and re-sends them and deduplication makes that safe.
- After a crash, a resumed `watch` re-reads from the last saved state. Nothing is duplicated or lost.
- If the state file is missing or unreadable, a new nonce is used, so rows may be sent again but are
  never dropped.
- `Run` gains a private method, `_log_row(metrics, step, time, sid, seq)`, that appends a row with an
  explicit session and sequence number. `Run`'s public API is unchanged.
- Spool file names start with a creation stamp, so `runboard sync` replays spool files in the order
  they were written. This keeps rows of every session in sequence order during replay.

## Lifecycle and safety

- A server URL without a scheme gets `https://`, in the client, `configure`, `sync`, `ls`, and `doctor`.

- The wrapper installs its signal handlers before starting the child and after creating the `Run`.
- If Runboard setup fails, the wrapper prints one warning and still runs the command, preserving its
  exit code.
- The wrapper creates a run only when `RANK`, `LOCAL_RANK`, and `SLURM_PROCID` are each unset or `0`.
  Other ranks run the command with no Runboard involvement.
- Follower errors never stop the wrapper or the child; they are reported once.
- `config` contains the command, working directory, and selected source format, plus values from
  `--config-json` and `--config`. Host, `argv`, and `SLURM_JOB_ID` are still recorded by `Run`.

## Agent guide and documentation

`AGENT_GUIDE.md` is packaged with the wheel, printed by `runboard guide`, and mirrored to
`docs/integrating.md` (a test keeps them byte-identical). Contents:

1. Run `runboard doctor`. On failure, apply the printed fix, or stop and ask the user.
2. Identify existing logging: TensorBoard, JSONL/CSV, Hugging Face, or print statements only.
3. Prefer `runboard run --` before the launch command. Include examples for plain Python, sbatch,
   `srun`, `torchrun`, `accelerate launch`, and `deepspeed`, and the `watch` command.
4. Verify with the wrapper's final summary line (N greater than 0) and `runboard ls`.
5. Use `runboard.init()` and `runboard.log()` only when the user wants custom metrics, with placement
   rules: once per run, a global step, and the rank-0 behavior. Do not combine it with `runboard run`
   for the same job.
6. Rules: do not change training logic, never print or commit the token or token URLs, do not start a
   server or tunnel when a hosted server is configured, and do not install other packages.
7. Troubleshooting that mirrors `doctor` messages.

README and docs changes:

- Correct the dashboard refresh interval to 5 seconds and the HTTP flush interval to 10 seconds.
- Move "Binned relationship plots" above the License section, under "The dashboard".
- Add "Zero-code integration" and "For coding agents" sections, and add `run`, `watch`, `doctor`,
  `guide`, and hosted `ls` to the CLI reference.
- Update the `AGENTS.md` repository map and invariants, `docs/architecture.md`, `CHANGELOG.md`, and the
  CI wheel smoke test.

## Testing

Tests are written first for every unit.

- `Run._log_row` and spool replay order across sessions.
- TensorBoard: a test-only writer produces files with valid CRCs for `simple_value` and scalar tensors,
  a truncated trailing record, a corrupted record, and an oversized record.
- JSONL and CSV: nested keys, step and time aliases, partial trailing lines, truncated files, and
  Lightning-style blank cells.
- Hugging Face: `log_history` growth across `checkpoint-10` and `checkpoint-20`.
- Text: Hugging Face dictionary lines, verl lines, ANSI codes, tqdm lines, carriage-return redraws, and
  lines that must not match.
- Discovery: precedence, skip lists, depth limit, file limit, and the modification-time filter.
- Streamer: switching to a higher-priority format, explicit sources, `--stdout auto` timing, and a
  resume after an interrupted run with no duplicates or gaps.
- Wrapper: exit codes 0 and 3, `SIGTERM`, command not found, stdout fallback, non-zero rank, and output
  that keeps draining when the terminal side is closed.
- Watch: `--once` twice produces one run with no duplicates; missing paths fail.
- Doctor: pass on the local server and in file mode; failure messages and exit codes for a wrong token,
  a refused connection, and a half configuration.
- Hosted `ls`, `runboard guide`, and the guide's docs mirror.

## Out of scope

- Dashboard redesign and focus mode (sub-project A).
- Framework-specific in-process hooks such as Trainer callbacks and Lightning loggers.
- Weights & Biases offline files and other formats not listed here.
- YAML configuration parsing.
- Deleting runs, including the `runboard/doctor` run.
