#!/bin/bash
#SBATCH --job-name=logtool
#SBATCH --cpus-per-task=1
#SBATCH --mem=1G
#SBATCH --time=7-00:00:00
#SBATCH --output=%x-%j.out

logtool serve --dir "$HOME/logtool-runs" --port 8080 --tunnel ${LOGTOOL_NOTIFY:+--notify "$LOGTOOL_NOTIFY"}
