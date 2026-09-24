# logtool — design

## Goal
A self-hosted, W&B-like live metrics dashboard that works on any cluster or VM the user can log into.
Training code logs scalars with a tiny Python API; the user views live plots in a browser from anywhere,
without an SSH connection or port forwarding.

## Constraints
- Portable: no root, no open inbound ports, no cluster-specific networking.
- Zero runtime dependencies (Python standard library only) for both client and server.
- Logging must never block or crash training.
- Only external service: Cloudflare quick tunnel (optional, `--tunnel`), no account needed.

## Architecture
```
train.py --(HTTP on cluster LAN, or shared FS)--> logtool serve --(outbound cloudflared)--> browser
```

Units:
1. `logtool.storage` — on-disk layout `<root>/<project>/<run_id>/{meta.json, metrics.jsonl}`.
   Append-only JSONL rows `{"_step", "_time", <metric>: <float>}`. Incremental reads by byte offset.
2. `logtool.client` — `init(project, name, config)`, `log(dict, step)`, `finish()`.
   Background thread batches rows every ~1s. Modes:
   - `http`: POST to server; on failure keeps rows in memory and retries with backoff; unsent rows
     at exit are written to a spool file, uploadable later with `logtool sync`.
   - `file`: writes directly to a shared log directory via `storage`.
   Server discovery: args → env (`LOGTOOL_SERVER`, `LOGTOOL_TOKEN`, `LOGTOOL_DIR`) → `~/.logtool/server.json`
   (written by `logtool serve`; home dirs are usually shared across cluster nodes).
3. `logtool.server` — `ThreadingHTTPServer` bound to `0.0.0.0`.
   - `POST /api/runs/<project>/<run_id>` body `{"meta": {...}?, "rows": [...]}`
   - `GET /api/runs` → list of runs with meta and last-update time
   - `GET /api/metrics?project=&run=&offset=` → `{"rows": [...], "offset": n}`
   - `GET /` and `/static/*` → dashboard
   Auth: persistent random token (`~/.logtool/token`), accepted as `?token=`, `Authorization: Bearer`, or cookie.
   `GET /?token=` sets an HttpOnly cookie and redirects.
4. `logtool.tunnel` — finds or downloads `cloudflared` into `~/.cache/logtool/`, runs a quick tunnel,
   parses and prints the public `https://*.trycloudflare.com/?token=...` URL.
5. Dashboard — static HTML/JS with vendored uPlot. Runs sidebar grouped by project with checkboxes,
   one chart per metric overlaying selected runs, EMA smoothing, log-y toggle, x = step or wall time.
   Polls every 2s using byte offsets (polling instead of SSE: robust through tunnels/proxies).
6. CLI — `logtool serve [--dir] [--port] [--host] [--tunnel]`, `logtool ls`, `logtool sync <spool>`.

## Error handling
- Client network failures: buffered, retried, never raised into user code; warnings printed once.
- Non-finite / non-numeric values: numeric values coerced to float, NaN/inf stored as null, others dropped.
- Partial trailing JSONL lines are ignored until complete.
- Path components validated (`[A-Za-z0-9._-]`) to prevent traversal.

## Testing
pytest: storage round-trip and offsets, server API + auth, client http mode, file mode, offline buffering + spool.
