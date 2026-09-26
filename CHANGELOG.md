# Changelog

All notable changes to this project are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[Semantic Versioning](https://semver.org/).

## [Unreleased]

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

### Changed

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
