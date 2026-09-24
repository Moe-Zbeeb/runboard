# runboard

Self-hosted, W&B-style live training dashboards for **any cluster or VM** you can log into.
You view them in your browser from anywhere, with no SSH connection or port forwarding.

- Zero dependencies: client and server use only the Python standard library.
- No root, no open ports: the public URL comes from an *outbound* Cloudflare quick tunnel (no account).
- Logging never blocks or crashes training: metrics are batched in the background and buffered if the server is down.
- Your data stays in plain files: `runs/<project>/<run_id>/{meta.json, metrics.jsonl}`.

## Install (on the cluster / VM)

```bash
pip install runboard
```

## 1. Start the server (once)

```bash
runboard serve --dir ~/runboard-runs --tunnel
```

```
runboard serving /home/you/runboard-runs
  local:   http://127.0.0.1:8080/?token=...
  cluster: http://login-node-3:8080/?token=...
  public:  https://some-words.trycloudflare.com/?token=...
```

Open the **public** URL in your browser and bookmark it. The token is stored in a cookie, so later visits work too.

Keep the server running with `tmux`/`nohup` on a login node, or as a long Slurm job:

```bash
#!/bin/bash
#SBATCH --job-name=runboard --cpus-per-task=1 --mem=1G --time=7-00:00:00
runboard serve --dir "$HOME/runboard-runs" --tunnel --notify https://ntfy.sh/<your-private-topic>
```

**Get the URL on your phone without SSH:** the tunnel URL changes whenever the server restarts.
Add `--notify https://ntfy.sh/<some-private-topic>` and every new URL is pushed to the ntfy app.
`runboard url` also prints the current URL.

## 2. Log from your training code

```python
import runboard

runboard.init(project="resnet", name="lr3e-4", config={"lr": 3e-4, "bs": 256})
for step in range(num_steps):
    ...
    runboard.log({"train/loss": loss, "train/lr": lr}, step=step)
    if step % 500 == 0:
        runboard.log({"eval/acc": acc}, step=step)
runboard.finish()
```

`runboard.init()` finds the server automatically through `~/.runboard/server.json`, which `runboard serve` writes.
Home directories are shared across nodes on most clusters. Otherwise set:

```bash
export RUNBOARD_SERVER=http://login-node-3:8080
export RUNBOARD_TOKEN=$(cat ~/.runboard/token)
```

Metrics whose names contain `/` are grouped into dashboard sections (`train/…`, `eval/…`).

**Multi-GPU (DDP / torchrun):** only rank 0 logs. On processes where `RANK` or `LOCAL_RANK` is non-zero,
`init()` and `log()` do nothing, so you can call them everywhere. Pass `all_ranks=True` to override.

**Resuming a run:** pass the same `run_id` to `runboard.init(..., run_id="my-run")` to keep appending to it.
Runs record host, argv, `SLURM_JOB_ID`, and status (`running` / `finished` / `crashed`).

### Shared-filesystem mode (most robust on clusters)

If compute nodes can't reach the server's host over the network, write to a shared directory instead.
The server reads it directly:

```python
runboard.init(project="resnet", dir="~/runboard-runs")   # or export RUNBOARD_DIR=~/runboard-runs
```

### Offline / server down

Logging is fire-and-forget: `log()` only appends to memory (~15 µs), and a background thread sends batches.
Delivery is exactly-once: rows carry sequence numbers, so a batch resent after a dropped connection is not duplicated.
If the server is unreachable, rows are buffered in memory and retried. Rows still unsent when the job exits are
saved to `~/.runboard/spool/`. Upload them later with:

```bash
runboard sync
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
| `runboard serve [--dir D] [--port P] [--tunnel] [--notify URL] [--advertise URL]` | run the server |
| `runboard ls [--dir D]` | list runs |
| `runboard sync [files…]` | upload spooled metrics |
| `runboard url` | print the dashboard URL |

## Security

Anyone with the URL **and** the token can view your metrics. The token lives in `~/.runboard/token` (mode 600).
Delete that file and restart the server to rotate it. Traffic through the tunnel is HTTPS, and Cloudflare relays it
without storing it.

## License

MIT. Bundles [uPlot](https://github.com/leeoniya/uPlot) (MIT).
