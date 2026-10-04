#!/usr/bin/env bash
# Thin convenience wrapper; Python owns DAG planning and dependency submission.
set -eo pipefail

exec enzynotation run "$@" --backend slurm
