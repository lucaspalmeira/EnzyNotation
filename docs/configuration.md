# Configuration

EnzyNotation separates scientific policy, provider runtime settings, site
resources, and presentation. Configuration files are versioned and validated
against JSON Schema where a contract exists.

## Layers

1. `configs/default.yaml` supplies FASTA, output, and logging defaults.
2. Repeated `--config` files are deep-merged from left to right. Later scalar
   and array values replace earlier values.
3. Provider documents are selected explicitly with options such as
   `--blast-config`, `--clean-config`, and `--hmmer-config`.
4. A family profile supplies domains and motifs; EC-rule files supply
   candidate-specific declarative constraints.
5. Integration and confidence documents control candidate resolution and
   categorical confidence. Reporting configuration affects presentation only.
6. `configs/databases.yaml` inventories externally managed scientific
   resources without overriding provider science.
7. `configs/slurm/default.yaml` controls scheduling resources only.

Slurm resources resolve in this order:

```text
slurm.defaults < slurm.stages.<stage> < slurm.run_overrides.<stage>
```

No scheduler setting changes evidence filtering, EC rules, correlation, or
confidence. The resolved pipeline configuration, source checksums, provider
configuration, and stage-relevant inputs participate in cache signatures.

## Major CLI documents

| Layer | CLI option | Contract |
| --- | --- | --- |
| Pipeline overlays | `--config` | `pipeline.schema.json` |
| BLAST | `--blast-config` | `blast.schema.json` |
| CLEAN | `--clean-config` | `clean.schema.json` |
| HMMER | `--hmmer-config` | `hmmer.schema.json` |
| InterProScan | `--interpro-config` | `interpro.schema.json` |
| Family | `--family-config` | `family.schema.json` |
| Structures | `--structures-config` | `structures.schema.json` |
| Foldseek | `--foldseek-config` | `foldseek.schema.json` |
| TM-align | `--tmalign-config` | `tmalign.schema.json` |
| EC rules | `--ec-rules` | `ec-rules.schema.json` |
| Integration | `--integration-config` | `integration.schema.json` |
| Confidence | `--confidence-config` | `confidence.schema.json` |
| Reports | `--report-config` | `report-config.schema.json` |
| Resources | database CLI `--config` | `databases.schema.json` |
| Slurm | `--slurm-config` | `slurm.schema.json` |

Paths must be valid from the runtime that executes the stage. Shared HPC paths
should be visible from submission and worker nodes. Container paths should use
the documented `/work`, `/databases`, `/models`, and `/cache` contract.

