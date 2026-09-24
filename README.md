<h1 align="center">runboard</h1>

<p align="center">
  <b>Your own live experiment dashboard on Cloudflare, from any cluster or VM.</b>
</p>

<p align="center">
  <a href="https://github.com/Moe-Zbeeb/runboard/actions/workflows/ci.yml"><img src="https://github.com/Moe-Zbeeb/runboard/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
  <a href="https://pypi.org/project/runboard/"><img src="https://img.shields.io/pypi/v/runboard" alt="PyPI"></a>
  <a href="https://pypi.org/project/runboard/"><img src="https://img.shields.io/pypi/pyversions/runboard" alt="Python versions"></a>
  <img src="https://img.shields.io/badge/dependencies-0-brightgreen" alt="Zero dependencies">
  <a href="https://github.com/Moe-Zbeeb/runboard/blob/main/LICENSE"><img src="https://img.shields.io/github/license/Moe-Zbeeb/runboard" alt="License"></a>
</p>

<p align="center">
  <a href="https://deploy.workers.cloudflare.com/?url=https://github.com/Moe-Zbeeb/runboard"><img src="https://deploy.workers.cloudflare.com/button" alt="Deploy to Cloudflare"></a>
</p>

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/Moe-Zbeeb/runboard/main/docs/assets/dashboard-dark.png">
  <img alt="runboard dashboard comparing three training runs" src="https://raw.githubusercontent.com/Moe-Zbeeb/runboard/main/docs/assets/dashboard-light.png">
</picture>

runboard is a personal, W&B-style experiment dashboard. Each researcher deploys one small backend to
their own Cloudflare account. Training jobs send metrics directly over HTTPS, so the cluster does not
need to host a server and the dashboard keeps the same URL when jobs, login nodes, or laptops restart.

- **Two lines in your training code.** `runboard.init(...)` and `runboard.log({...})`.
- **Bring your own Cloudflare.** One click creates a Worker, D1 database, R2 bucket, and static dashboard
  in your account. No domain or always-on cluster process is required.
- **One stable URL.** Jobs and browsers connect to the same `workers.dev` HTTPS endpoint from anywhere.
- **Zero dependencies.** The client, server, and dashboard use only the Python standard library.
  `pip install runboard` works on any cluster, even behind a restrictive proxy.
- **Keeps network and disk work out of training.** `log()` appends metrics in memory; a background
  thread sends batches, retries outages, and spills excess backlog to disk. Remaining metrics are
  saved when the run finishes.
- **A local mode when you need it.** The same package can store plain JSONL files and serve them from a
  cluster or laptop without Cloudflare storage.

## Contents

- [Quickstart](#quickstart)
- [How it works](#how-it-works)
- [Logging API](#logging-api)
- [Cloudflare deployment](#cloudflare-deployment)
- [Running on a cluster](#running-on-a-cluster)
- [The dashboard](#the-dashboard)
- [Configuration](#configuration)
- [CLI reference](#cli-reference)
- [Security](#security)
- [FAQ](#faq)
- [Development](#development)

## Quickstart

**1. Deploy your personal backend.** Click the button, sign in to Cloudflare, and choose a long random
`RUNBOARD_TOKEN` when prompted. Cloudflare creates the Worker, database, metric bucket, and dashboard.

[![Deploy to Cloudflare](https://deploy.workers.cloudflare.com/button)](https://deploy.workers.cloudflare.com/?url=https://github.com/Moe-Zbeeb/runboard)

Cloudflare gives you a URL such as `https://runboard.<account>.workers.dev`.

**2. Install and connect the Python client** on each machine or shared cluster home:

```bash
pip install runboard
runboard configure https://runboard.<account>.workers.dev
```

The command asks for the same token, verifies the deployment, and saves both values in
`~/.runboard/server.json` with mode `600`.

**3. Log from your training script:**

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

**4. Open the URL printed by `runboard configure`.** Charts update live every two seconds.

`runboard.init()` reads the saved endpoint automatically. On clusters with a shared home directory,
configure it once and every compute node uses it. For containers or separate machines, set
`RUNBOARD_SERVER` and `RUNBOARD_TOKEN` as environment variables.

## How it works

```text
  cluster / VM                         your Cloudflare account                 anywhere
 ┌──────────────────┐    HTTPS    ┌───────────────────────────────┐        ┌───────────┐
 │ train.py         │ ──────────► │ Worker: auth + REST API       │ ◄────► │  browser  │
 │   runboard.log() │             │ D1: runs + batch index        │ HTTPS  │ dashboard │
 │   retry + spool  │             │ R2: metric batches            │        └───────────┘
 └──────────────────┘             │ Assets: dashboard             │
                                  └───────────────────────────────┘
```

1. Your script appends metrics to an in-memory buffer. A background thread sends them to the
   server once per second, in chunks of up to 5,000 rows.
2. The Cloudflare Worker authenticates the request, stores run metadata and its metric-batch index in
   D1, and stores metric payloads in R2. Retried batches have deterministic IDs and are stored once.
3. The dashboard is served by the same Worker. It polls every two seconds and fetches only batches it
   has not seen.
4. If Cloudflare or the network is unavailable, the client retries with backoff and writes remaining
   data to `~/.runboard/spool/`. `runboard sync` sends it later.

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

## Cloudflare deployment

The deploy button is the normal path. It uses [Cloudflare's automatic resource provisioning](https://developers.cloudflare.com/workers/platform/deploy-buttons/),
so every user receives isolated resources in their own account. A custom domain is optional; the
generated `workers.dev` address is stable and sufficient.

To deploy from a terminal instead:

```bash
git clone https://github.com/Moe-Zbeeb/runboard && cd runboard
npm install
npx wrangler login
npm run deploy
```

Generate a token, then store it as a Worker secret when Wrangler prompts for its value:

```bash
python -c 'import secrets; print(secrets.token_urlsafe(32))'
npx wrangler secret put RUNBOARD_TOKEN
```

The Worker creates its schema on the first authenticated request. The SQL migration is also kept in
`cloudflare/migrations/` for inspection and future upgrades.

For local Worker development, copy `.dev.vars.example` to `.dev.vars`, replace its value, and run
`npm run dev`. Wrangler keeps local D1 and R2 data under `.wrangler/`.

Cloudflare's free plan is enough for personal use and normal research runs, subject to its current
[Workers](https://developers.cloudflare.com/workers/platform/pricing/),
[D1](https://developers.cloudflare.com/d1/platform/pricing/), and
[R2](https://developers.cloudflare.com/r2/pricing/) quotas. Large sweeps or very frequent logging can
exceed those quotas. Runboard batches writes, but log at a useful interval instead of every inner-loop
operation.

## Running on a cluster

### Recommended: send directly to Cloudflare

If the cluster home is shared, run `runboard configure` once on a login node. Otherwise inject the
settings into the job:

```bash
export RUNBOARD_SERVER="https://runboard.<account>.workers.dev"
export RUNBOARD_TOKEN="your-token"
python train.py
```

Only outbound HTTPS is required. There is no Runboard daemon on the login node and no tunnel to keep
alive.

### Local server mode

When experiments must remain inside the cluster, run the included local server:

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

### Quick-tunnel URL notifications

The quick-tunnel URL changes whenever the server restarts. Use `--notify` to have each new URL pushed
to your phone through [ntfy](https://ntfy.sh). Install the app and subscribe to a private,
hard-to-guess topic:

```bash
runboard serve --tunnel --notify https://ntfy.sh/my-private-topic-8f3k2
```

### Local server discovery

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
| `runboard configure URL` | Verify and save a personal Cloudflare or other hosted endpoint. Prompts securely for its token; `--token` is available for automation. |
| `runboard serve` | Start the server. Options: `--dir` (default `./runboard-runs`), `--port` (default 8080, falls back to a free port if taken), `--host`, `--tunnel`, `--notify URL`, `--advertise URL` (address jobs should use) |
| `runboard ls [--dir D]` | List runs with status and last update |
| `runboard sync [files…]` | Upload spooled offline metrics |
| `runboard url` | Print the current dashboard URL |
| `runboard --version` | Print the version |

## Security

- Every request, including the dashboard itself, needs the access token. For Cloudflare, each user
  chooses it during deployment and stores it as a Worker secret. The local server generates one.
- Opening `/?token=…` stores the token in an `HttpOnly` cookie and removes it from the address bar.
- The hosted endpoint uses HTTPS. Run metadata is stored in D1 and metric batches in R2 inside the
  user's Cloudflare account.
- **Anyone with the URL and the token can read your metrics.** Treat the full URL like a password. To
  rotate a Cloudflare token, run `npx wrangler secret put RUNBOARD_TOKEN` from the repository and
  re-run `runboard configure`. For a local server, delete `~/.runboard/token` and restart it.

## FAQ

**How is this different from W&B or TensorBoard?**
W&B is a hosted service, so your metrics leave your infrastructure and you need an account.
TensorBoard needs port forwarding or a tunnel you set up yourself, and it reads event files.
runboard gives every user their own small Cloudflare backend and keeps the Python client dependency-free.

**Does it work on clusters with no internet access?**
Cloudflare mode needs outbound HTTPS from compute nodes. If that is blocked, use local file mode with
`RUNBOARD_DIR` and `runboard serve`, then access it over the internal network or an SSH tunnel.

**Can I log from any experiment?**
Yes. Log numeric metrics with any names, group them with `/`, and include any JSON-compatible
hyperparameters in the run config. The tracker has no framework dependency, so it can live in a plain
Python loop or a PyTorch, JAX, or other training script. Each backend retains the run's full metric
history.

**Can I analyze the data myself?**
Local mode stores every run as `<dir>/<project>/<run_id>/metrics.jsonl`:

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
npm install
npm run check:cloud
```

Try it end to end with `runboard serve` in one terminal and `python examples/quickstart.py` in another.
See [CONTRIBUTING.md](CONTRIBUTING.md) for details.

## License

[MIT](LICENSE). Bundles [uPlot](https://github.com/leeoniya/uPlot) (MIT).
