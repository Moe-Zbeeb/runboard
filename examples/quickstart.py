"""Simulated training run. No dependencies beyond runboard.

    runboard serve              # in one terminal
    python examples/quickstart.py --lr 3e-4
"""

import argparse
import math
import random
import time

import runboard

parser = argparse.ArgumentParser()
parser.add_argument("--lr", type=float, default=3e-4)
parser.add_argument("--steps", type=int, default=2000)
parser.add_argument("--project", default="quickstart")
args = parser.parse_args()

runboard.init(project=args.project, name=f"lr={args.lr:g}", config=vars(args))

for step in range(args.steps):
    progress = 1 - math.exp(-step * args.lr * 10)
    loss = 2.3 * (1 - progress) + 0.15 + random.gauss(0, 0.04)
    lr = args.lr * 0.5 * (1 + math.cos(math.pi * step / args.steps))
    runboard.log({"train/loss": loss, "train/lr": lr}, step=step)

    if step % 100 == 0:
        runboard.log(
            {"eval/loss": loss * 1.08 + random.gauss(0, 0.02), "eval/accuracy": 0.1 + 0.85 * progress},
            step=step,
        )
    time.sleep(0.01)

runboard.finish()
