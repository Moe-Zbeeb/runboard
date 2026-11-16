# Changelog

All notable changes to this project are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

- `runboard top` shows live, full-screen charts in the terminal, over SSH or in tmux: a run list,
  a chart grid grouped by metric prefix, a full-screen chart view, a metric filter, smoothing, log y,
  outlier clipping, and step, minute, or clock-time x axes. It refreshes every 5 seconds.
- `runboard plot` prints the same charts once, with color only when writing to a terminal. Both read
  the configured server or a runs directory and need only the Python standard library.
- Hovering a chart moves a crosshair across every chart and shows a tooltip with each run's value at
  that step. Dragging to zoom applies the same x range to every chart; a bar at the bottom of the page
  shows the range, and **Reset zoom**, a double-click, or <kbd>Esc</kbd> clears it.
- Each chart shows every run's latest value below the plot, and the values at the cursor while you hover.
- Hovering a run in the sidebar, or its value below a chart, highlights that run in the charts.
- **Clip outliers** scales the y axis to the 1st to 99th percentile of the visible data, so a rare loss
  spike no longer flattens the curve.
- With smoothing on, the raw values stay visible as a faint line behind each smoothed line.
- Metric sections collapse and expand, and remember their state.
- **Compare runs** lists the selected runs side by side: status, duration, latest step, the config keys
  that differ between them, and the latest value of every metric.
- Run details show the start and finish times, duration, host, Slurm job, command, tags, and a config
  table, with the raw metadata one click away.
- On phones, the run list folds into a **Runs** button so the charts are on the first screen.
- <kbd>/</kbd> focuses the metric filter.
- Opening the dashboard without its token shows a page that explains how to get the full URL.

### Changed

- The series palette has eight hues that stay distinguishable for colorblind viewers in both themes.
- Axis ticks use short forms such as `20k` and `1.5M`; large values read as `104,858` instead of
  `1.049e+5`.
- Theme is a **Light**, **Dark**, or **System** menu instead of a cycling button.
- Point markers appear only where points are far enough apart to see, instead of on every series with
  fewer than 60 points.

### Fixed

- The page no longer jumps back toward the top every 5 seconds while a run is logging.
- Chart legends show each run's latest value; they previously showed `–` for finished runs and for any
  run that ended before the longest one.
- Choosing a project now plots only that project's runs. Runs selected in another project were still
  plotted, but hidden from the sidebar.
- A saved project that no longer exists no longer hides every run.
- The first visit selects the most recent runs of the most recently active project, instead of the
  three newest runs from any project.
- A metric filter that is not a valid regular expression is matched as plain text and marked, instead
  of silently showing every metric.
- Changing the theme recolors the sidebar immediately.
- The top bar can wrap onto two lines without the sidebar sliding under it.
- Smoothing is applied after sorting by the x axis, so wall-clock and relative-time views of resumed runs
  smooth in the right order.

## [0.3.0] - 2026-10-12

### Added

- `runboard run -- <command>` streams a training job's existing TensorBoard, JSONL, CSV, Hugging Face
  `trainer_state.json`, or printed metrics without code changes, and keeps the command's output and exit
  code.
- `runboard watch PATH` streams logs from a running job or uploads a finished run's logs; running it
  again continues the same run without duplicates.
- `runboard doctor` shows where metrics go, sends a test run, and prints `PASS` or `FAIL` with a fix.
- `runboard guide` prints an integration guide for coding agents, also in `docs/integrating.md`.
- `runboard ls` lists runs from the configured server; pass `--dir` for a local directory.

### Fixed

- A spool file that disappeared or became unreadable no longer stops all later metric delivery; corrupt
  files are moved aside as `*.jsonl.corrupt`, and `runboard sync` skips files a running job already sent.
- Non-finite values in run metadata, such as `max_grad_norm=float("inf")`, are stored as the strings
  `"inf"`, `"-inf"`, and `"nan"`, so the Cloudflare backend no longer rejects the first batch and the local
  dashboard no longer fails to parse `/api/runs`.
- The Cloudflare Worker skips rows already stored for a client session, so a retry after a timeout no
  longer duplicates metrics. Existing deployments create the new `run_sessions` table automatically.
- `SIGTERM`, sent by Slurm on `scancel` and time limits, now finishes live runs with status `killed`
  and flushes buffered rows before chaining to the previous handler.
- `runboard serve` no longer replaces a hosted server saved by `runboard configure`.
- The dashboard no longer plots duplicate points when a run is selected during a poll.
- A transient send failure keeps rows in memory instead of writing them to spool files below
  `max_buffer`, and pending spool files are uploaded back to back.
- The local server returns 400 for a negative or malformed `Content-Length`.
- A send failure right after a spool file uploads now backs off instead of retrying in a tight loop that
  used a full CPU core and flooded the server with requests.
- `finish()` stops sending at its `timeout` and spools the remaining rows, so a slow connection and a
  large backlog no longer stretch shutdown past Slurm's kill grace period.
- A status-only spool file written by `finish()` no longer overwrites a pending spool file whose first row
  had sequence number 0.
- `~/.runboard/token` and `~/.runboard/server.json` are created with mode `600` instead of being
  briefly readable by other users before `chmod`.
- The Cloudflare Worker no longer stores duplicate rows when a timed-out request and its retry are
  processed concurrently.
- The dashboard escapes run status values.
- The local server closes the connection after rejecting an oversized request body.
- A server URL without `https://`, such as `runboard.<account>.workers.dev`, no longer makes
  `runboard.init()` raise into training code or `runboard configure` fail with a traceback; `https://` is
  added automatically.
- Run configs with non-string keys no longer block delivery and make `finish()` raise at exit.
- `runboard sync` moves an unreadable spool file aside as `*.jsonl.corrupt` and continues, and reports an
  unreachable server without a traceback.
- The Cloudflare Worker accepts rows without `_sid`/`_seq` again and handles batches from many client
  sessions within D1's 100-parameter limit.
- `runboard sync` replays spool files in the order they were written. Spool file names now include a
  creation stamp.
- The README states the actual dashboard refresh and HTTP send intervals.

### Changed

- The dashboard has a new look: cool drafting-paper surfaces, the bundled Recursive typeface (SIL Open
  Font License), and a colorblind-validated run palette in light and dark themes. Each run in the sidebar
  shows a heartbeat trace of its metric writes over the last 15 minutes, so a stalled run visibly flatlines.
- Charts draw point markers when a run has fewer than 60 values, so sparse metrics such as checkpoint
  counters stay visible.
- HTTP mode sends every 10 seconds by default instead of every second; file mode still flushes every
  second. Pass `flush_interval` to override.
- The Worker returns up to 64 metric batches (about 4 MB) per `/api/metrics` request.
- The dashboard polls every 5 seconds, pauses in hidden tabs, and only downloads metrics for runs that
  changed.

## [0.2.0] - 2026-09-25

### Added

- Personal Cloudflare backend with a Worker API, D1 run and metric storage, and bundled dashboard.
- One-click deploy configuration with automatic per-account resource provisioning.
- `runboard configure` to verify and save a hosted endpoint securely.

### Changed

- Cloudflare hosting is now the recommended setup, so clusters no longer need an always-on Runboard
  server or a changing quick-tunnel URL.
- Local server, shared-filesystem, and quick-tunnel modes remain available for offline clusters.

## [0.1.0] - 2026-09-24

First release.

### Added

- `runboard.init()`, `log()`, `finish()` client API with background batching, offline buffering,
  disk spooling, and `runboard sync`.
- Exactly-once delivery through per-session sequence numbers.
- Standard-library HTTP server with token auth and file-based storage.
- `--tunnel`: public HTTPS URL through a Cloudflare quick tunnel, with automatic `cloudflared` download,
  restart on failure, and `--notify` webhooks.
- Dashboard: run comparison, per-metric charts, smoothing, log-y, x-axis modes, metric filter,
  summary table, run details, light and dark themes, mobile layout.
- Shared-filesystem mode (`dir=` / `RUNBOARD_DIR`).
- Rank-0-only logging under DDP and `torchrun`.
- CLI: `serve`, `ls`, `sync`, `url`.

[0.1.0]: https://github.com/Moe-Zbeeb/runboard/releases/tag/v0.1.0
[0.2.0]: https://github.com/Moe-Zbeeb/runboard/releases/tag/v0.2.0
[0.3.0]: https://github.com/Moe-Zbeeb/runboard/releases/tag/v0.3.0
