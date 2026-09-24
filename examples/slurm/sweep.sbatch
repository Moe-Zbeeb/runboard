#!/bin/bash
# A learning-rate sweep as a job array; every task shows up as its own run on the dashboard.
#   sbatch examples/slurm/sweep.sbatch
#SBATCH --job-name=sweep
#SBATCH --array=0-3
#SBATCH --gres=gpu:1
#SBATCH --cpus-per-task=4
#SBATCH --mem=16G
#SBATCH --time=04:00:00
#SBATCH --output=%x-%A_%a.out

LRS=(3e-4 1e-3 3e-3 1e-2)
python examples/pytorch_train.py --lr "${LRS[$SLURM_ARRAY_TASK_ID]}"
