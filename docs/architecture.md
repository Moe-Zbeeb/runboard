# Architecture

runboard has a dependency-free Python client, two interchangeable backends, and one static dashboard.

| Module | Responsibility |
|---|---|
| `client.py` | `Run`: buffers rows, ships them from a background thread, spools to disk on failure; `sync()` |
| `storage.py` | On-disk layout, append with de-duplication, incremental reads by byte offset |
| `server.py` | `ThreadingHTTPServer` exposing the REST API and the static dashboard, token auth |
| `tunnel.py` | Finds or downloads `cloudflared`, runs a quick tunnel, reports the public URL |
| `cli.py` | `runboard configure / serve / ls / sync / url` |
| `static/` | The dashboard: `index.html`, `app.js`, `style.css`, bundled uPlot |
| `cloudflare/src/worker.js` | Hosted REST API, authentication, D1 index, R2 metric storage, static assets |

## Design constraints

- **Portable.** It must run on any Linux or macOS machine the user can log into: no root, no inbound
  ports, no cluster-specific networking.
- **Zero runtime dependencies.** Only the Python standard library, so `pip install` works behind proxies
  and on old Python versions (3.8+).
- **Never hurt training.** `log()` must be cheap and must never raise because of the network.
- **No cluster daemon required.** The default backend runs in each user's Cloudflare account and has a
  stable URL.
- **Same protocol everywhere.** The Python server and Cloudflare Worker expose the same API.

## Cloudflare storage

D1 contains run metadata and an ordered index of metric batches. R2 contains the JSON payload for each
batch. Keeping metric arrays in R2 avoids creating one database write per training step and avoids
database row-size pressure for wide experiments.

Each metric batch receives a SHA-256 key derived from its project, run, and rows. A retry writes the
same R2 object and `INSERT OR IGNORE` keeps one D1 index record. The browser treats the D1 batch ID as
its incremental offset and downloads one new batch at a time.

The Worker creates the schema with idempotent DDL when its first authenticated request arrives. D1,
R2, and static assets are declared without account-specific IDs in `wrangler.jsonc`, allowing Wrangler
and the Cloudflare deploy button to provision separate resources for every user.

## Local storage layout

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

1. `log()` appends to an in-memory list under a lock. Network and disk work run outside the training call.
   When the queue exceeds `max_buffer`, the background thread writes the oldest rows to spool files.
2. A daemon thread flushes every `flush_interval` (1 s), sending the pending meta and rows in chunks
   of `chunk_size` (5,000) rows.
3. On any exception the unsent remainder goes back to the front of the buffer, and the thread backs off
   exponentially (up to 30 s). A 4xx response other than 401, 408 or 429 is permanent, so those rows
   are dropped with a warning instead of being retried forever.
4. The server tracks the highest stored sequence number per client session. It rebuilds that state
   from the JSONL file on its first write after startup. Rows already stored for that session are
   skipped, including delayed retries from a previous session.
5. `finish()`, which is also registered with `atexit`, stops the thread, retries for up to 10 s, then
   writes whatever is left to an atomic spool file in `~/.runboard/spool/`. `runboard sync` replays
   spool files; session sequence numbers make replaying safe.

The tests cover retry after a lost connection, replay from spool, resumed runs, and duplicate batches.

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

## Local tunnel

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
