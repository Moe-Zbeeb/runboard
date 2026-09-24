# logtool

Self-hosted, W&B-style live training dashboards for **any cluster or VM** you can log into.
You view them in your browser from anywhere, with no SSH connection or port forwarding.

- Zero dependencies: client and server use only the Python standard library.
- No root, no open ports: the public URL comes from an *outbound* Cloudflare quick tunnel (no account).
- Logging never blocks or crashes training: metrics are batched in the background and buffered if the server is down.
- Your data stays in plain files: `runs/<project>/<run_id>/{meta.json, metrics.jsonl}`.

## Install (on the cluster / VM)

```bash
pip install git+<this repo>        # or: pip install -e .
```

## 1. Start the server (once)

```bash
logtool serve --dir ~/logtool-runs --tunnel
```

```
logtool serving /home/you/logtool-runs
  local:   http://127.0.0.1:8080/?token=...
  cluster: http://login-node-3:8080/?token=...
  public:  https://some-words.trycloudflare.com/?token=...
```

Open the **public** URL in your browser and bookmark it. The token is stored in a cookie, so later visits work too.

Keep the server running with `tmux`/`nohup` on a login node, or as a Slurm job (`sbatch examples/serve.sbatch`).

**Get the URL on your phone without SSH:** the tunnel URL changes whenever the server restarts.
Add `--notify https://ntfy.sh/<some-private-topic>` and every new URL is pushed to the ntfy app.
`logtool url` also prints the current URL.

## 2. Log from your training code

```python
import logtool

logtool.init(project="resnet", name="lr3e-4", config={"lr": 3e-4, "bs": 256})
for step in range(num_steps):
    ...
    logtool.log({"train/loss": loss, "train/lr": lr}, step=step)
    if step % 500 == 0:
        logtool.log({"eval/acc": acc}, step=step)
logtool.finish()
```

`logtool.init()` finds the server automatically through `~/.logtool/server.json`, which `logtool serve` writes.
Home directories are shared across nodes on most clusters. Otherwise set:

```bash
export LOGTOOL_SERVER=http://login-node-3:8080
export LOGTOOL_TOKEN=$(cat ~/.logtool/token)
```

Metrics whose names contain `/` are grouped into dashboard sections (`train/…`, `eval/…`).
Runs record host, argv, `SLURM_JOB_ID`, and status (`running` / `finished` / `crashed`).

### Shared-filesystem mode (most robust on clusters)

If compute nodes can't reach the server's host over the network, write to a shared directory instead.
The server reads it directly:

```python
logtool.init(project="resnet", dir="~/logtool-runs")   # or export LOGTOOL_DIR=~/logtool-runs
```

### Offline / server down

If the server is unreachable, rows are buffered in memory and retried. Rows still unsent when the job exits are
saved to `~/.logtool/spool/`. Upload them later with:

```bash
logtool sync
```

## Dashboard

- Runs sidebar with search and project filter; compare up to 8 runs, each with a stable color.
- One chart per metric; hover shows values, drag to zoom, double-click to reset.
- EMA smoothing, log-scale y, x axis = step / relative time / wall clock, regex metric filter.
- Summary table of the latest values; click a run name to see its config and metadata.
- Updates every 2 s; light and dark mode; works on phones.

## CLI

| command | |
|---|---|
| `logtool serve [--dir D] [--port P] [--tunnel] [--notify URL] [--advertise URL]` | run the server |
| `logtool ls [--dir D]` | list runs |
| `logtool sync [files…]` | upload spooled metrics |
| `logtool url` | print the dashboard URL |

## Security

Anyone with the URL **and** the token can view your metrics. The token lives in `~/.logtool/token` (mode 600).
Delete that file and restart the server to rotate it. Traffic through the tunnel is HTTPS, and Cloudflare relays it
without storing it.
