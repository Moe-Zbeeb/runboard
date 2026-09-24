# Architecture

runboard has five small parts, each in its own module under `src/runboard/`.

| Module | Responsibility |
|---|---|
| `client.py` | `Run`: buffers rows, ships them from a background thread, spools to disk on failure; `sync()` |
| `storage.py` | On-disk layout, append with de-duplication, incremental reads by byte offset |
| `server.py` | `ThreadingHTTPServer` exposing the REST API and the static dashboard, token auth |
| `tunnel.py` | Finds or downloads `cloudflared`, runs a quick tunnel, reports the public URL |
| `cli.py` | `runboard serve / ls / sync / url` |
| `static/` | The dashboard: `index.html`, `app.js`, `style.css`, bundled uPlot |

## Design constraints

- **Portable.** It must run on any Linux or macOS machine the user can log into: no root, no inbound
  ports, no cluster-specific networking.
- **Zero runtime dependencies.** Only the Python standard library, so `pip install` works behind proxies
  and on old Python versions (3.8+).
- **Never hurt training.** `log()` must be cheap and must never raise because of the network.

## Storage layout

```text
<root>/
  <project>/
    <run_id>/
      meta.json        # name, config, tags, status, host, argv, slurm_job_id, created/finished
      metrics.jsonl    # one JSON object per log() call
```

A metrics row looks like this:

```json
{"train/loss": 0.4213, "_step": 1200, "_time": 1790257189.2, "_seq": 1200, "_sid": "9f2c01aa"}
```

Keys starting with `_` are reserved. `_seq` is a per-client-session counter and `_sid` identifies the
session. `meta.json` is replaced atomically (write to a temp file, then `os.replace`).

Project and run names must match `[A-Za-z0-9._-]{1,128}`, which rules out path traversal.

## Delivery guarantees

1. `log()` appends to an in-memory list under a lock. Nothing blocks on I/O.
2. A daemon thread flushes every `flush_interval` (1 s), sending the pending meta and rows in chunks
   of `chunk_size` (5,000) rows.
3. On any exception the unsent remainder goes back to the front of the buffer, and the thread backs off
   exponentially (up to 30 s). A 4xx response other than 401, 408 or 429 is permanent, so those rows
   are dropped with a warning instead of being retried forever.
4. The server keeps the last `(_sid, _seq)` it stored per run, initialized from the file's tail.
   Rows with the same `_sid` and a `_seq` it has already seen are skipped. That makes a batch retried
   after a lost response idempotent, while a resumed run (new `_sid`) still appends normally.
5. `finish()`, which is also registered with `atexit`, stops the thread, retries for up to 10 s, then
   writes whatever is left to `~/.runboard/spool/<project>__<run_id>.jsonl`. `runboard sync` replays
   spool files; the sequence numbers make replaying safe.

This was validated by killing the server with `SIGKILL` twice during 8 concurrent 50k-step jobs, three
times over: every run arrived complete with no duplicates.

## HTTP API

Every endpoint needs the token, sent as `Authorization: Bearer <token>`, a `?token=` query parameter,
or the `runboard_token` cookie.

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/runs/<project>/<run_id>` | Body `{"meta": {...}?, "rows": [...]}`. Merges meta and appends rows. |
| `GET` | `/api/runs` | All runs with meta and `updated` (mtime of `metrics.jsonl`). |
| `GET` | `/api/metrics?project=&run=&offset=` | Rows after byte `offset` (up to 8 MB per call) plus the new `offset`. |
| `GET` | `/api/health` | `{"ok": true}` |
| `GET` | `/` | Dashboard. `/?token=` sets the cookie and redirects to `/`. |

The dashboard polls every 2 s rather than using server-sent events, because long-lived streams are
unreliable through tunnels and proxies. Byte offsets keep each poll incremental.

## Tunnel

`runboard serve --tunnel` resolves `cloudflared` from `PATH`, then from `~/.cache/runboard/`. If neither
exists, it downloads the release binary for the platform. It runs
`cloudflared tunnel --no-autoupdate --url http://127.0.0.1:<port>`, parses the `trycloudflare.com` URL
from its log, and waits for `Registered tunnel connection` before printing it. If `cloudflared` exits,
the serve loop restarts it and prints (and optionally `--notify`s) the new URL. `SIGTERM` is turned into
a clean shutdown so the tunnel process never outlives the server (Slurm sends `SIGTERM` on job end).

## Server discovery

`runboard serve` writes `~/.runboard/server.json` (mode `600`):

```json
{"url": "http://login-node-3:8080", "token": "...", "dir": "/home/you/runboard-runs", "public_url": "https://..."}
```

`init()` resolves settings in this order: explicit arguments, then environment variables
(`RUNBOARD_SERVER`, `RUNBOARD_TOKEN`, `RUNBOARD_DIR`), then `server.json`. If no server is found, it
falls back to writing files under `./runboard-runs`. The advertised host is the machine's hostname if it
resolves, otherwise its primary IP. Override it with `--advertise`.
