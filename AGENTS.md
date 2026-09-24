# Runboard agent guide

This file is the operating manual for coding agents working in this repository. Read it before making changes. It defines the product model, repository boundaries, required invariants, validation commands, and deployment rules.

## Working rules

- Do not add comments to code.
- Do not implement behavior when the requirement is unclear. Inspect the relevant code and documentation first, then ask for the missing product decision.
- When asked to brainstorm, assess technical feasibility and describe how the proposal fits the existing repository.
- Keep user-provided messages semantically unchanged when editing them; correct only grammar and syntax unless the user asks for a rewrite.
- Prefer the smallest coherent change that completes the requested behavior.
- Preserve Python 3.8 compatibility and the zero-runtime-dependency guarantee.
- Update documentation when user-facing behavior, setup, configuration, deployment, or limits change.
- Never commit credentials, access tokens, account-specific Cloudflare resource IDs, cluster usernames, or private deployment URLs.

## Product model

Runboard is a dependency-free Python experiment tracker with one dashboard and two interchangeable backends:

```text
training process
    -> Runboard in-memory queue
    -> background batching, retry, and disk spool
    -> HTTPS Worker + D1, or local Python server + JSONL
    -> browser dashboard
```

Cloudflare is the recommended backend. Every researcher deploys an independent Worker, D1 database, dashboard, and secret in their own account. Training jobs send metrics directly over outbound HTTPS. A cluster-hosted daemon, inbound cluster port, custom domain, and SSH tunnel are not required.

Local mode remains supported for offline clusters and development. It uses the same REST API and dashboard as the Cloudflare backend.

## Non-negotiable invariants

### Training must remain safe

- `Run.log()` must stay cheap. It may clean values and append to memory under a lock, but it must not perform network or disk I/O.
- Network, retry, batching, and spool work belong in the background sender thread.
- Logging and delivery failures must never raise into training code.
- `finish()` must attempt a bounded flush and spool any unsent data.
- Delivery must remain safe to retry. Local storage uses `_sid` and `_seq`; Cloudflare uses a deterministic SHA-256 batch key.

### Portability must remain intact

- The Python package must use only the standard library at runtime.
- Supported Python versions are 3.8 through 3.13.
- Avoid syntax and library APIs introduced after Python 3.8.
- The client must work on Linux and macOS without root access.

### Both backends must keep the same protocol

The Python server and Cloudflare Worker must expose compatible behavior:

| Method | Path | Behavior |
|---|---|---|
| `GET` | `/api/health` | Return backend health. |
| `GET` | `/api/runs` | Return run metadata ordered newest first. |
| `GET` | `/api/metrics?project=&run=&offset=` | Return the next metric page and new offset. |
| `POST` | `/api/runs/<project>/<run_id>` | Merge optional metadata and append metric rows. |
| `GET` | `/` | Serve the authenticated dashboard. |

Every endpoint requires the same bearer token, query token, or dashboard cookie.

### The dashboard has two synchronized copies

- Python package assets live in `src/runboard/static/`.
- Cloudflare assets live in `cloudflare/public/`.
- These directories must remain byte-equivalent.
- Update both copies in the same change.
- `test_cloudflare_dashboard_matches_python_package` enforces this requirement.
- Dashboard HTML requests `/static/*`. The Worker intentionally rewrites those paths to the root of the Cloudflare asset binding. Preserve that mapping.

### Cloudflare must stay free-tier compatible

- Use Workers, static assets, and D1.
- Do not introduce R2 or another paid resource without an explicit product decision.
- The Worker accepts at most 1.5 MB per request so a JSON batch stays below D1's 2 MB row limit.
- The Python HTTP sink must keep splitting batches after HTTP 413.
- D1 schema creation must remain idempotent and safe on the first authenticated request.
- Root `wrangler.jsonc` must keep account-specific resource IDs empty so the deploy button can provision resources for each user.

## Repository map

| Path | Responsibility |
|---|---|
| `src/runboard/__init__.py` | Public `init`, `log`, `finish`, `Run`, and `sync` API. |
| `src/runboard/client.py` | Run lifecycle, rank handling, buffering, retry, batching, spooling, and upload. |
| `src/runboard/config.py` | `RUNBOARD_HOME`, token, and `server.json` persistence. |
| `src/runboard/storage.py` | Local metadata and JSONL storage, validation, deduplication, and offsets. |
| `src/runboard/server.py` | Authenticated local HTTP API and dashboard server. |
| `src/runboard/tunnel.py` | Optional `cloudflared` quick-tunnel lifecycle. |
| `src/runboard/cli.py` | `configure`, `serve`, `ls`, `sync`, `url`, and version commands. |
| `src/runboard/static/` | Dashboard source shipped in the wheel. |
| `cloudflare/src/worker.js` | Hosted API, authentication, D1 access, and asset routing. |
| `cloudflare/public/` | Cloudflare copy of the dashboard assets. |
| `cloudflare/migrations/` | Inspectable D1 schema history. |
| `cloudflare/test/` | End-to-end Worker protocol tests. |
| `tests/` | Python client, local server, storage, delivery, and compatibility tests. |
| `examples/` | Minimal Python, PyTorch, and Slurm usage. |
| `docs/architecture.md` | Detailed design and delivery guarantees. |
| `wrangler.jsonc` | Deploy-button and repository-root Cloudflare configuration. |
| `cloudflare/wrangler.jsonc` | Isolated Cloudflare project configuration. |

## Python client behavior

`Run` resolves its destination in this order:

1. Explicit `server`, `token`, and `dir` arguments.
2. `RUNBOARD_SERVER`, `RUNBOARD_TOKEN`, and `RUNBOARD_DIR`.
3. `~/.runboard/server.json`, or the directory selected by `RUNBOARD_HOME`.
4. Local `./runboard-runs` file mode when no hosted server is configured.

Metric values are normalized to finite floats or `None`. Non-numeric values are ignored. `_step`, `_time`, `_seq`, and `_sid` are reserved internal keys. Project and run identifiers must match `[A-Za-z0-9._-]{1,128}`.

The default sender behavior is:

- Flush every second.
- Send chunks of up to 5,000 rows.
- Retry transient failures with exponential backoff up to 30 seconds.
- Treat 401, 408, and 429 as retryable client-side HTTP failures.
- Drop other permanent 4xx failures with a warning.
- Spill excess or final unsent rows under `~/.runboard/spool/`.
- Replay spool files with `runboard sync`.

Keep an explicit Runboard user agent on Cloudflare-bound requests. Cloudflare may reject Python's default `Python-urllib` signature before the request reaches the Worker.

## Distributed and PRIME integration

Runboard is framework-independent. Integrate it at the central trainer or existing experiment-logger boundary.

- Call `runboard.init()` once for the logical experiment.
- Send already-reduced scalar metrics from the trainer.
- Use a stable global training step.
- Put hyperparameters and immutable experiment settings in `config`.
- Call `runboard.finish()` on normal completion; the context-manager form is preferred when practical.
- Do not initialize a separate run in rollout workers, inference workers, or data-loader processes unless separate runs are explicitly desired.

`Run` automatically disables logging when `RANK` or `LOCAL_RANK` is non-zero. This makes unconditional calls safe under `torchrun`, DDP, and normal PRIME launches. Preserve this behavior. `all_ranks=True` is an explicit override and must not become the default.

For PRIME or another distributed RL system, the preferred metrics include:

- Policy, value, and total loss.
- Reward mean and distribution summaries.
- KL and clipping diagnostics.
- Rollout length and completion statistics.
- Tokens or samples per second.
- Learning rate and optimizer diagnostics.
- Evaluation metrics at their actual global step.

Runboard records hostname, command arguments, and `SLURM_JOB_ID` automatically. Do not duplicate these fields in every metric row.

## Slurm and cluster rules

- Install Runboard in the same Python or Conda environment as the training job.
- On a shared home, run `runboard configure` once and let compute nodes read `~/.runboard/server.json`.
- For containers or isolated homes, inject `RUNBOARD_SERVER` and `RUNBOARD_TOKEN` through the job environment or secret mechanism.
- Hosted mode requires outbound HTTPS from compute nodes.
- If outbound access is blocked, use `RUNBOARD_DIR` on shared storage and the local server mode.
- Do not start a Runboard daemon or tunnel on a login node when Cloudflare mode is configured.
- An end-to-end cluster check is complete only after the Slurm job exits successfully and the exact run plus all expected metric rows are readable from the configured backend.

## Cloudflare development and deployment

The normal public deployment path is the root README button:

```text
https://deploy.workers.cloudflare.com/?url=https://github.com/Moe-Zbeeb/runboard
```

The repository root is a complete Worker project. Cloudflare clones it, provisions D1, stores `RUNBOARD_TOKEN` as a Worker secret, connects GitHub builds, and deploys the Worker and dashboard.

For terminal deployment:

```bash
npm install
npx wrangler login
npx wrangler d1 create runboard
npm run deploy
npx wrangler secret put RUNBOARD_TOKEN
```

Copy the database ID returned by `wrangler d1 create` into the local Wrangler configuration before deploying. Do not commit that account-specific ID to the reusable source repository.

Treat the Wrangler configuration as the source of truth. A generated deployment repository may contain a real D1 database ID, but the reusable source repository must not.

The root and isolated Cloudflare configurations must stay semantically aligned:

- Same Worker entry point behavior.
- Same D1 binding name: `DB`.
- Same asset binding name: `ASSETS`.
- Same `RUNBOARD_TOKEN` secret contract.
- Same compatibility date unless a deliberate migration requires otherwise.

## Local development

Create the Python environment:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
```

Run the Python tests:

```bash
python -m pytest -q
```

Run a local Python backend:

```bash
runboard serve --dir /tmp/runboard-runs
python examples/quickstart.py
```

Validate the Cloudflare bundle:

```bash
npm ci
npm run check:cloud
```

Run the Worker locally:

```bash
cp .dev.vars.example .dev.vars
npm run dev -- --port 8787
```

Use a non-production token in `.dev.vars`, then run the protocol test from another terminal:

```bash
RUNBOARD_TEST_URL=http://127.0.0.1:8787 RUNBOARD_TEST_TOKEN=integration-token npm run test:cloud
```

Validate the package artifact when packaging changes:

```bash
python -m build
python -m twine check --strict dist/*
```

## Testing expectations

Choose checks that exercise the changed contract:

| Change | Required validation |
|---|---|
| Client buffering, retry, rank, or spool behavior | Python tests, including a focused failure-path test. |
| Local API or storage | Python server/storage tests and offset or deduplication coverage. |
| Dashboard HTML, CSS, or JavaScript | Python tests, mirrored-asset check, and a browser smoke test. |
| Worker routing, auth, D1, or limits | Wrangler dry run and local or deployed Worker protocol test. |
| Packaging or entry points | Build, Twine check, and wheel smoke test. |
| Deploy-button or Wrangler metadata | Root deploy form inspection and Wrangler dry run. |
| Cluster integration | Completed Slurm job plus backend verification of run metadata and row count. |

Do not call a deployment healthy because the page shell rendered. Confirm the JavaScript assets load, `/api/runs` returns data, metrics paginate, and a plotted run appears in the browser.

## Security

- `.dev.vars`, `~/.runboard/server.json`, dashboard token URLs, and `RUNBOARD_TOKEN` are secrets.
- Keep local secret files owner-readable only.
- Never print secrets in tests, logs, CI output, documentation, commits, issue bodies, or pull requests.
- Use dummy values such as `integration-token` in tests.
- Every API and dashboard request must remain authenticated.
- The `/?token=...` flow must set an `HttpOnly` cookie and redirect to a clean URL.
- Treat anyone with the endpoint and token as fully authorized to read and write experiment data.
- Token rotation must update the Worker secret and every configured client.

## Release process

For a release:

1. Update `version` in `pyproject.toml`.
2. Update `__version__` in `src/runboard/__init__.py`.
3. Update version-bearing HTTP user-agent strings.
4. Update `CHANGELOG.md`.
5. Run the full Python, package, and Cloudflare checks.
6. Commit, tag `vX.Y.Z`, push the tag, and create the GitHub release.

PyPI publishing uses trusted publishing. Do not add a PyPI token to the repository or workflow.

## Definition of done

A change is complete when:

- The requested behavior works in its real execution path.
- Relevant invariants above still hold.
- Both backends remain protocol-compatible when the change touches shared behavior.
- Dashboard copies are synchronized when UI assets change.
- Relevant tests pass locally.
- Documentation reflects the final behavior.
- No secret or account-specific deployment data appears in the diff.
- Git status contains only the intended files.
