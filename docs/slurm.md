# Slurm Execution

The Slurm backend translates the existing `Workflow` DAG into one job per
stage. It does not contain BLAST, CLEAN, domain, motif, structural, integration,
or report logic. Each job calls the normal local stage runner and therefore
uses the same cache signatures, state files, parsers, and scientific policy.

## Dry-run

Review a pipeline without contacting Slurm:

```bash
enzynotation run input.fasta \
  --run-id cluster-review \
  --backend slurm \
  --slurm-config configs/slurm/default.yaml \
  --integration-config configs/integration/default.yaml \
  --report \
  --dry-run
```

The JSON output lists stage order, dependencies, requested resources, separate
stdout/stderr patterns, worker argv, and complete `sbatch` argv. Dry-run does
not call `sbatch` or create a run directory.

## Submission

Remove `--dry-run` on the target cluster. Required scientific dependencies use
`afterok`. Integration waits for optional independent providers with `afterany`
so their failures remain visible without making optional evidence mandatory.
Direct stage dependencies, such as `structures -> foldseek -> tmalign`, remain
`afterok` relationships.

Example dependency syntax:

```text
--dependency afterok:1201:1202,afterany:1203
```

Arguments are passed as an argv sequence. `shell=True` is never used. The
generic `slurm/run_stage.sbatch` script contains no provider science.

## Resources and logs

`configs/slurm/default.yaml` supplies operational examples, not universal
requirements. Supported fields include partition, account, qos, CPUs, memory,
time, GPUs, generic resources, constraints, nodes, and tasks.

```text
logs/<run_id>/slurm/<stage>/stdout-%j.log
logs/<run_id>/slurm/<stage>/stderr-%j.log
```

The run keeps `slurm/plan.json`; a worker with Slurm environment variables also
writes `stages/<stage>/scheduler.json`. Scheduler state and job IDs are
operational provenance and never biological evidence.

## Shared storage and restart

Input, results, logs, configuration, databases, models, and container images
must be readable from worker nodes at the same configured paths. Databases are
not copied per job. Stage state remains atomic, while manifest updates use a
small file lock to merge concurrent branch results.

Resubmit the same run ID after correcting a failure. Completed stages with
unchanged inputs and outputs are skipped. Changed stage inputs/configuration
invalidate that stage and its downstream cache as in local execution.

Real Slurm submission must be validated by the target-site administrator.

