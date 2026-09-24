<h1 align="center">runboard</h1>

<p align="center">
  <b>Live training dashboards for any cluster or VM, open in your browser from anywhere, no SSH tunnel needed.</b>
</p>

<p align="center">
  <a href="https://github.com/Moe-Zbeeb/runboard/actions/workflows/ci.yml"><img src="https://github.com/Moe-Zbeeb/runboard/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
  <a href="https://pypi.org/project/runboard/"><img src="https://img.shields.io/pypi/v/runboard" alt="PyPI"></a>
  <a href="https://pypi.org/project/runboard/"><img src="https://img.shields.io/pypi/pyversions/runboard" alt="Python versions"></a>
  <img src="https://img.shields.io/badge/dependencies-0-brightgreen" alt="Zero dependencies">
  <a href="https://github.com/Moe-Zbeeb/runboard/blob/main/LICENSE"><img src="https://img.shields.io/github/license/Moe-Zbeeb/runboard" alt="License"></a>
</p>

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/Moe-Zbeeb/runboard/main/docs/assets/dashboard-dark.png">
  <img alt="runboard dashboard comparing three training runs" src="https://raw.githubusercontent.com/Moe-Zbeeb/runboard/main/docs/assets/dashboard-light.png">
</picture>

runboard is a self-hosted, W&B-style experiment dashboard. It is built for the setup most researchers
actually have: jobs running on a Slurm cluster, a lab server, or a cloud VM that you can SSH into but
that the internet can't reach.

- **Two lines in your training code.** `runboard.init(...)` and `runboard.log({...})`.
- **Viewable from anywhere.** `runboard serve --tunnel` prints a public HTTPS URL, created by an
  *outbound* Cloudflare quick tunnel. You don't need root, open ports, an account, or `ssh -L`.
- **Zero dependencies.** The client, server, and dashboard use only the Python standard library.
  `pip install runboard` works on any cluster, even behind a restrictive proxy.
- **Keeps network and disk work out of training.** `log()` appends metrics in memory; a background
  thread sends batches, retries outages, and spills excess backlog to disk. Remaining metrics are
  saved when the run finishes.
- **Your data, plain files.** Runs are stored as `<project>/<run_id>/metrics.jsonl`, so you can
  `grep`, `rsync`, or load them into pandas.

## Contents

- [Quickstart](#quickstart)
- [How it works](#how-it-works)
- [Logging API](#logging-api)
- [Running on a cluster](#running-on-a-cluster)
- [The dashboard](#the-dashboard)
- [Configuration](#configuration)
- [CLI reference](#cli-reference)
- [Security](#security)
- [FAQ](#faq)
- [Development](#development)

## Quickstart

```bash
pip install runboard
```

**1. Start the server** on the cluster login node or VM:

```bash
runboard serve --tunnel
```

```text
runboard serving /home/you/runboard-runs
  local:   http://127.0.0.1:8080/?token=Xk3...
  cluster: http://login-node-3:8080/?token=Xk3...
  public:  https://calm-river-demo.trycloudflare.com/?token=Xk3...
```

**2. Log from your training script:**

```python
import runboard

runboard.init(project="resnet", config={"lr": 3e-4, "batch_size": 256})

for step in range(num_steps):
    loss = train_step()
    runboard.log({"train/loss": loss}, step=step)

    if step % 500 == 0:
        runboard.log({"eval/accuracy": evaluate()}, step=step)

runboard.finish()
```

**3. Open the `public:` URL** on your laptop or phone. Charts update live every two seconds.

`runboard.init()` finds the server on its own. `runboard serve` writes its address and token to
`~/.runboard/server.json`, and on most clusters your home directory is shared by every node.

## How it works

```text
  compute nodes                      login node / VM                           anywhere
 ┌──────────────────┐   HTTP    ┌───────────────────────────────┐          ┌───────────┐
 │ train.py         │ ────────► │ runboard serve                │          │  browser  │
 │   runboard.log() │           │   ├── REST API                │ ◄──────► │ dashboard │
 │   (background    │  or files │   ├── runs/*/metrics.jsonl    │  HTTPS   │           │
 │    batching)     │ ─ ─ ─ ─ ► │   ├── dashboard (static)      │          └───────────┘
 └──────────────────┘ shared FS │   └── cloudflared ────────────┼──outbound──► trycloudflare.com
                                └───────────────────────────────┘
```

1. Your script appends metrics to an in-memory buffer. A background thread sends them to the
   server once per second, in chunks of up to 5,000 rows.
2. The server appends them to one JSONL file per run. Every row carries a sequence number, so a
   batch that is retried after a dropped connection is never stored twice.
3. With `--tunnel`, the server starts [`cloudflared`](https://github.com/cloudflare/cloudflared)
   (downloaded automatically into `~/.cache/runboard`, no root needed). It opens an **outbound**
   connection to Cloudflare, which gives you a public HTTPS URL. Any machine that can reach the
   internet over HTTPS can do this. No inbound ports are opened.
4. The dashboard is one static page ([uPlot](https://github.com/leeoniya/uPlot) charts, bundled).
   It polls the server and fetches only the rows it hasn't seen yet.

More detail is in [docs/architecture.md](docs/architecture.md).

## Logging API

```python
import runboard

run = runboard.init(
    project="my-project",      # groups runs in the dashboard
    name="baseline-lr3e-4",    # display name (defaults to the run id)
    config={"lr": 3e-4},       # hyperparameters, shown in run details
    tags=["baseline"],         # searchable in the sidebar
    run_id=None,               # pass an existing id to resume a run
    server=None, token=None,   # override server discovery
    dir=None,                  # write to a shared directory instead of HTTP
)

runboard.log({"train/loss": 0.42, "train/lr": 1e-4}, step=100)   # step is optional; auto-increments
runboard.finish()                                                # also runs automatically at exit
```

| Behavior | Details |
|---|---|
| Values | Python numbers, NumPy scalars, and 0-d torch tensors all work. NaN and inf become gaps; non-numeric values are ignored. |
| Sections | Metrics named `group/name` are grouped into dashboard sections (`train/…`, `eval/…`). |
| Status | Runs are marked `running`, `finished`, `crashed` (uncaught exception), or `killed` (Ctrl-C). A run that stops logging for 10 minutes is shown as `stale`. |
| Metadata | Hostname, command line, and `SLURM_JOB_ID` are recorded automatically. |
| Multi-GPU | Under `torchrun` or DDP, only rank 0 logs. `init()` and `log()` are no-ops where `RANK` or `LOCAL_RANK` is non-zero, so you can call them unconditionally. Pass `all_ranks=True` to log from every rank. |
| Context manager | `with runboard.Run(project="p") as run: run.log(...)` marks the run `crashed` if the block raises. |

### When the server is unreachable

Network failures never run inside `log()`. If the server is down, rows are kept in memory and retried
with backoff; excess backlog is saved in the background. Rows still unsent when the job exits are written to
`~/.runboard/spool/`. Upload them later with:

```bash
runboard sync
```

## Running on a cluster

### Keep the server alive

Run it in `tmux` or with `nohup` on the login node:

```bash
nohup runboard serve --tunnel > ~/runboard.log 2>&1 &
grep public: ~/runboard.log
```

If your cluster kills long processes on login nodes, run the server as a low-resource Slurm job
([examples/slurm/serve.sbatch](examples/slurm/serve.sbatch)):

```bash
sbatch examples/slurm/serve.sbatch
grep public: runboard-*.out
```

### Getting the URL without SSH

The quick-tunnel URL changes whenever the server restarts. Use `--notify` to have each new URL pushed
to your phone through [ntfy](https://ntfy.sh). Install the app and subscribe to a private,
hard-to-guess topic:

```bash
runboard serve --tunnel --notify https://ntfy.sh/my-private-topic-8f3k2
```

### Training jobs

Nothing extra is needed. Jobs discover the server through `~/.runboard/server.json`. See
[examples/slurm/train.sbatch](examples/slurm/train.sbatch) and
[examples/slurm/sweep.sbatch](examples/slurm/sweep.sbatch) (a job-array learning-rate sweep).

### Compute nodes can't reach the login node?

Some clusters block traffic between nodes. If they share a filesystem, skip HTTP and write straight
into the server's run directory:

```python
runboard.init(project="resnet", dir="~/runboard-runs")
```

or set `export RUNBOARD_DIR=~/runboard-runs` in your job script. The server picks the files up.

## The dashboard

- **Runs sidebar.** Search, project filter, and status badges. Click a run's name to see its config and metadata.
- **Compare runs.** Select up to 8 runs. Each run keeps its color while it stays selected.
- **One chart per metric.** Hover for exact values, drag to zoom, double-click to reset. Legends show each run's latest value.
- **Controls.** EMA smoothing, log-scale y, x axis as step, relative time, or wall clock, a regex metric filter, and system/light/dark themes.
- **Long runs.** Charts retain peaks and dips while reducing very large series to a display-sized sample. Original metrics remain in the JSONL files.
- **Summary table.** Latest value of every metric for the selected runs.
- A responsive layout for phones and desktops.

## Configuration

| Environment variable | Default | Purpose |
|---|---|---|
| `RUNBOARD_SERVER` | from `~/.runboard/server.json` | Server URL used by `init()` |
| `RUNBOARD_TOKEN` | from `~/.runboard/server.json` | Access token used by `init()` |
| `RUNBOARD_DIR` | unset | If set, `init()` writes files to this directory instead of using HTTP |
| `RUNBOARD_HOME` | `~/.runboard` | Where the token, server info, and spool files live |

## CLI reference

| Command | Description |
|---|---|
| `runboard serve` | Start the server. Options: `--dir` (default `./runboard-runs`), `--port` (default 8080, falls back to a free port if taken), `--host`, `--tunnel`, `--notify URL`, `--advertise URL` (address jobs should use) |
| `runboard ls [--dir D]` | List runs with status and last update |
| `runboard sync [files…]` | Upload spooled offline metrics |
| `runboard url` | Print the current dashboard URL |
| `runboard --version` | Print the version |

## Security

- Every request, including the dashboard itself, needs the access token. It is generated once, stored
  in `~/.runboard/token` (mode `600`), and compared in constant time.
- Opening `/?token=…` stores the token in an `HttpOnly` cookie and removes it from the address bar.
- The public URL is HTTPS end to end. Cloudflare relays the traffic and does not store it.
- **Anyone with the URL and the token can read your metrics.** Treat the full URL like a password. To
  rotate the token, delete `~/.runboard/token` and restart the server.
- Without `--tunnel`, nothing is exposed outside your network.

## FAQ

**How is this different from W&B or TensorBoard?**
W&B is a hosted service, so your metrics leave your infrastructure and you need an account.
TensorBoard needs port forwarding or a tunnel you set up yourself, and it reads event files.
runboard is a single `pip install` with no dependencies that you run yourself, and it gets you a
public URL without SSH.

**Does it work on clusters with no internet access?**
Logging and the dashboard work on the internal network. The public URL needs outbound HTTPS from the
server's machine. If that is blocked, use `ssh -L 8080:localhost:8080` as a fallback.

**Can I log from any experiment?**
Yes. Log numeric metrics with any names, group them with `/`, and include any JSON-compatible
hyperparameters in the run config. The tracker has no framework dependency, so it can live in a plain
Python loop or a PyTorch, JAX, or other training script. Each run's full metric history stays in JSONL.

**Can I analyze the data myself?**
Yes. Every run is `<dir>/<project>/<run_id>/metrics.jsonl`:

```python
import pandas as pd
df = pd.read_json("runboard-runs/resnet/20260924-101500-a1b2c3/metrics.jsonl", lines=True)
```

## Development

```bash
git clone https://github.com/Moe-Zbeeb/runboard && cd runboard
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest
```

Try it end to end with `runboard serve` in one terminal and `python examples/quickstart.py` in another.
See [CONTRIBUTING.md](CONTRIBUTING.md) for details.

## License

[MIT](LICENSE). Bundles [uPlot](https://github.com/leeoniya/uPlot) (MIT).
