#!/usr/bin/env bash
# Generic EnzyNotation stage job. Scientific behavior remains in Python stages.
set -eo pipefail

stage_id="$1"
shift

"$@" --stage-id "$stage_id" --slurm-worker
