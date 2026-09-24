#!/bin/bash
#SBATCH --job-name=runboard
#SBATCH --cpus-per-task=1
#SBATCH --mem=1G
#SBATCH --time=7-00:00:00
#SBATCH --output=%x-%j.out

runboard serve --dir "$HOME/runboard-runs" --port 8080 --tunnel ${RUNBOARD_NOTIFY:+--notify "$RUNBOARD_NOTIFY"}
