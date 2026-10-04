# Troubleshooting

| Symptom | Check |
| --- | --- |
| Malformed FASTA | Run `enzynotation validate`; inspect line/record diagnostics. |
| BLAST database missing | Verify provider path and BLAST index files; run the resource registry check. |
| Pfam/HMM missing | Verify the HMM path, pressed database files, release, and fingerprint. |
| CLEAN unavailable | Check command/container strategy, model/cache mounts, and provider stderr. |
| Foldseek database missing | Verify database name inside its configured directory and metadata separately. |
| TM-align unavailable | Set an executable or site-wrapper path and inspect version provenance. |
| Malformed metadata | Validate required columns, EC syntax, encoding, and duplicate accessions. |
| Integration unresolved | Inspect conflicts, provider availability, candidate scorecards, and failed rules. |
| Resource invalid | Inspect `enzynotation databases verify` reason and configured fingerprint strategy. |
| Compose mount failure | Expand `docker compose config`; compare host roots with `/work`, `/databases`, `/models`, and `/cache`. |
| Apptainer bind failure | Confirm shared paths exist on the worker and bind read/write flags are correct. |
| Slurm dependency failure | Inspect the upstream `%j` logs, `slurm/plan.json`, and required versus optional edges. |
| Permission failure | Confirm results/log/cache are writable and scientific resources are readable on every node. |
| Unexpected cache reuse/rerun | Inspect stage signature inputs, status history, checksums, and configuration snapshot. |

An empty valid provider result differs from a failed command. Missing evidence
and provider failure are not negative biological evidence. Use the run manifest,
stage `status.json`, provider summary, stdout, and stderr together.

For cluster review, run with `--backend slurm --dry-run`. It is safe on a
non-cluster machine because it does not execute `sbatch`.

