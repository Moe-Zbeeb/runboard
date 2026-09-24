import math
import random
import sys
import time

import runboard

lr = float(sys.argv[1]) if len(sys.argv) > 1 else 3e-4
steps = int(sys.argv[2]) if len(sys.argv) > 2 else 500

runboard.init(project="demo", name=f"lr={lr}", config={"lr": lr, "batch_size": 64, "model": "mlp"})
for step in range(steps):
    loss = 2.5 * math.exp(-step * lr * 20) + 0.1 + random.gauss(0, 0.05)
    runboard.log({"train/loss": loss, "train/lr": lr * (1 - step / steps)}, step=step)
    if step % 50 == 0:
        runboard.log({"eval/accuracy": 1 - loss / 3 + random.gauss(0, 0.01), "eval/loss": loss * 1.1}, step=step)
    time.sleep(0.005)
runboard.finish()
