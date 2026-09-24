# Changelog

All notable changes to this project are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[Semantic Versioning](https://semver.org/).

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
