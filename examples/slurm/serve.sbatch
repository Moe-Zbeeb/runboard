#!/bin/bash
# Long-running runboard server as a Slurm job (for clusters that kill long processes on login nodes).
#   sbatch examples/slurm/serve.sbatch
#   grep public: runboard-*.out        # the public dashboard URL
# Optional: export RUNBOARD_NOTIFY=https://ntfy.sh/<private-topic> before sbatch to get the URL pushed to your phone.
#SBATCH --job-name=runboard
#SBATCH --cpus-per-task=1
#SBATCH --mem=1G
#SBATCH --time=7-00:00:00
#SBATCH --output=%x-%j.out

runboard serve --dir "$HOME/runboard-runs" --tunnel ${RUNBOARD_NOTIFY:+--notify "$RUNBOARD_NOTIFY"}
