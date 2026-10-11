# Integrating Runboard into a training codebase

These instructions are for coding agents and people adding Runboard to an existing training project.
Runboard streams a training job's metrics to the user's personal dashboard. It needs no changes to
training code and uses only the Python standard library.

## 1. Check the connection

Run:

```bash
runboard doctor
```

It prints where metrics will go and ends with `PASS` or `FAIL`. On `FAIL`, apply the printed `fix:`
line and run it again. If the fix needs a URL or token you do not have, stop and ask the user.

If the destination reads `local files ... (from default)`, ask the user whether they have a Cloudflare
deployment. If they do, they run `runboard configure <url>` themselves.

## 2. Find what the job already logs

Runboard reads these without code changes, in this order of preference:

| Format | Files | Typical sources |
|---|---|---|
| TensorBoard | `events.out.tfevents.*` | PyTorch `SummaryWriter`, Hugging Face Trainer and TRL with `report_to="tensorboard"`, Lightning `TensorBoardLogger` |
| JSONL or CSV | `*.jsonl`, `*metrics*.csv` | Custom metric files, Lightning `CSVLogger` |
| Hugging Face state | `trainer_state.json`, `checkpoint-*/trainer_state.json` | Any Hugging Face Trainer or TRL run; updates only when a checkpoint is saved |
| Command output | Lines with `key=value` or `key: value` pairs | Print-based logging, Hugging Face `{'loss': ...}` lines, verl console output |

Search the code near the training loop for `SummaryWriter`, `report_to`, `logger=`, `.jsonl`, and
`print(` to see which apply. Only the highest-priority format present is followed, so the same metric
is never sent twice.

## 3. Wrap the launch command

Put `runboard run --` in front of the command that starts training. Do not change the training code.

```bash
runboard run --project my-project -- python train.py --lr 3e-4
```

Wrap launchers once, not once per process:

```bash
runboard run --project my-project -- torchrun --nproc_per_node 8 train.py
runboard run --project my-project -- accelerate launch train.py
runboard run --project my-project -- deepspeed train.py --deepspeed ds_config.json
```

In a Slurm batch script, change only the launch line:

```bash
runboard run --project my-project --name "$SLURM_JOB_NAME-$SLURM_JOB_ID" -- python train.py
```

When `srun` starts one task per GPU, put the wrapper inside `srun`:
`srun runboard run --project my-project -- python train.py`. Only the task with rank 0 streams
metrics; the other tasks run unchanged.

| Option | Use |
|---|---|
| `--project NAME` | Groups runs in the dashboard. Defaults to the current directory name. |
| `--name NAME` | Display name. Defaults to the script name and a timestamp. |
| `--watch DIR` | Also search an output directory outside the current directory, such as `--watch /scratch/$USER/outputs`. |
| `--source FILE` | Follow only this file and turn off automatic discovery. |
| `--stdout on` | Always read metrics from command output. `--stdout off` never does. |
| `--config KEY=VALUE`, `--config-json FILE` | Record hyperparameters with the run. |

The wrapper never changes the command's output or exit code, and the command still runs if Runboard
cannot connect. Only files created or changed after launch are followed, so datasets and earlier runs
in the same directory are ignored.

To stream a job that is already running, or to upload logs from a finished run:

```bash
runboard watch ./outputs                            # follows until Ctrl-C
runboard watch ./outputs --once                     # uploads what exists and exits
runboard watch --stdout-file slurm-1234.out --once  # reads metrics printed to a Slurm log
```

Running `watch` again on the same path continues the same run without duplicating rows.

## 4. Verify

1. The wrapper's last line on stderr reads `runboard: finished: logged N rows to <project>/<run_id>`,
   and `N` is greater than 0. `no metrics found` means none of the formats above were written or
   printed; check where the job writes its logs and use `--watch DIR` or `--source FILE`.
2. `runboard ls` lists the run with its status and last update time.

Tell the user the project and run name. They open their dashboard as usual.

## 5. Custom metrics in code (optional)

Use the Python API only when the user wants metrics that are not already logged:

```python
import runboard

runboard.init(project="my-project", config={"lr": 3e-4})
for step in range(num_steps):
    loss = train_step()
    runboard.log({"train/loss": loss}, step=step)
runboard.finish()
```

- Call `runboard.init()` once per run in the training process. Under torchrun, DDP, and Accelerate,
  only rank 0 logs; calls on other ranks do nothing, so they can stay unconditional.
- Pass the global training step, not a per-epoch counter.
- Log already reduced scalars. Values that are not numbers are ignored.
- Name metrics `group/name` (`train/loss`, `eval/accuracy`) to group them in the dashboard.
- Do not combine `runboard.init()` with `runboard run` for the same job; that creates two runs.

## Rules for agents

- Do not change training logic, hyperparameters, or output paths to integrate Runboard.
- Never print, log, or commit `RUNBOARD_TOKEN`, `~/.runboard/server.json`, or URLs containing
  `?token=`.
- Do not start `runboard serve` or a tunnel when `runboard doctor` shows a hosted server.
- Do not install other packages for Runboard.

## Troubleshooting

| `runboard doctor` says | Do this |
|---|---|
| `rejected the token (HTTP 401)` | The token does not match the Worker secret. Ask the user to run `runboard configure <url>`. |
| `blocked before reaching Runboard (HTTP 403)` | A proxy or Cloudflare rule blocks this machine. Ask the user. |
| `cannot resolve the host` or `timed out` | The machine may lack outbound HTTPS. Set `RUNBOARD_DIR` to shared storage and run `runboard serve` where the user can reach it. |
| `connection ... was refused` | Check the URL and port. For a local server, start `runboard serve`. |
| `TLS handshake ... failed` | Set `SSL_CERT_FILE` to the cluster's CA bundle. |
| `configured, but not both` | Set both `RUNBOARD_SERVER` and `RUNBOARD_TOKEN`, or run `runboard configure <url>`. |
