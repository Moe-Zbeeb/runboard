#!/bin/bash
# A training job that logs to runboard. Nothing runboard-specific is needed here:
# runboard.init() finds the server through ~/.runboard/server.json on the shared home directory.
#   sbatch examples/slurm/train.sbatch
#SBATCH --job-name=train
#SBATCH --gres=gpu:1
#SBATCH --cpus-per-task=4
#SBATCH --mem=16G
#SBATCH --time=04:00:00
#SBATCH --output=%x-%j.out

python examples/pytorch_train.py --lr 1e-3
