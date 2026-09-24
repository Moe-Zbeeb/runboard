"""A real PyTorch training loop logged with runboard.

    pip install torch runboard
    python examples/pytorch_train.py --lr 1e-3

Works unchanged under torchrun: only rank 0 logs.
"""

import argparse
import time

import torch
import torch.nn as nn

import runboard

parser = argparse.ArgumentParser()
parser.add_argument("--lr", type=float, default=1e-3)
parser.add_argument("--epochs", type=int, default=20)
parser.add_argument("--batch-size", type=int, default=128)
parser.add_argument("--hidden", type=int, default=256)
args = parser.parse_args()

device = "cuda" if torch.cuda.is_available() else "cpu"
torch.manual_seed(0)

x = torch.randn(20000, 32)
w = torch.randn(32, 10)
y = (x @ w + 0.5 * torch.randn(20000, 10)).argmax(dim=1)
x_train, y_train, x_val, y_val = x[:16000], y[:16000], x[16000:], y[16000:]

model = nn.Sequential(nn.Linear(32, args.hidden), nn.ReLU(), nn.Linear(args.hidden, 10)).to(device)
opt = torch.optim.AdamW(model.parameters(), lr=args.lr)
sched = torch.optim.lr_scheduler.CosineAnnealingLR(opt, T_max=args.epochs)
loss_fn = nn.CrossEntropyLoss()

runboard.init(project="pytorch-example", name=f"mlp-h{args.hidden}-lr{args.lr:g}", config={**vars(args), "device": device})

step = 0
for epoch in range(args.epochs):
    model.train()
    t0 = time.time()
    perm = torch.randperm(len(x_train))
    for i in range(0, len(x_train), args.batch_size):
        idx = perm[i : i + args.batch_size]
        xb, yb = x_train[idx].to(device), y_train[idx].to(device)
        loss = loss_fn(model(xb), yb)
        opt.zero_grad()
        loss.backward()
        grad_norm = nn.utils.clip_grad_norm_(model.parameters(), 1.0)
        opt.step()
        runboard.log({"train/loss": loss.item(), "train/grad_norm": grad_norm.item(), "train/lr": sched.get_last_lr()[0]}, step=step)
        step += 1
    sched.step()

    model.eval()
    with torch.no_grad():
        logits = model(x_val.to(device))
        val_loss = loss_fn(logits, y_val.to(device)).item()
        val_acc = (logits.argmax(1).cpu() == y_val).float().mean().item()
    runboard.log({"eval/loss": val_loss, "eval/accuracy": val_acc, "perf/epoch_seconds": time.time() - t0}, step=step)
    print(f"epoch {epoch}: val_loss={val_loss:.4f} val_acc={val_acc:.3f}")

runboard.finish()
