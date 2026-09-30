# EnzyNotation

EnzyNotation is a reusable protein enzyme annotation pipeline.

## Main goal

Build a modular pipeline for functional annotation of protein sequences
and EC number prediction by integrating multiple independent sources
of evidence.

The pipeline must not assign an EC number based only on the first BLAST hit.

## Main technologies

- Python
- Bash
- Docker
- Apptainer/Singularity
- Slurm

## Bioinformatics tools

The pipeline may integrate:

- BLASTp
- CLEAN
- Foldseek
- TM-align
- HMMER
- Pfam
- InterProScan
- protein structure prediction or user-provided structures
- catalytic motif analysis

Additional tools may be added when scientifically justified.

## Architecture

Prefer this general structure:

src/
scripts/
configs/
slurm/
docker/
data/
databases/
results/
logs/
tests/

## Design principles

1. Keep the pipeline generic.
2. Do not hardcode GH32-specific logic into the main pipeline.
3. Family-specific rules must live in configuration files.
4. EC-specific rules must live in configuration files.
5. Preserve intermediate results.
6. Prefer TSV, CSV, JSON, or YAML for machine-readable outputs.
7. Every annotation must be traceable to its supporting evidence.
8. Conflicting evidence must be explicitly reported.
9. Never silently choose one annotation when major tools disagree.
10. Separate homology detection from functional inference.

## Annotation evidence

Consider independently:

- sequence identity
- query coverage
- subject coverage
- E-value
- bit score
- curated annotation
- EC number
- CLEAN prediction
- CLEAN score/confidence
- protein domains
- catalytic motifs
- Foldseek results
- structural similarity
- TM-score

## Confidence

Final annotations should support categories such as:

- high
- medium
- low
- unresolved

The confidence system must be transparent and documented.

## Slurm

All expensive tasks should support execution through Slurm.

Use:

- sbatch
- job dependencies with afterok
- separate stdout/stderr logs
- configurable CPUs
- configurable memory
- configurable walltime

Do not assume Docker can run directly on compute nodes.

Prefer Apptainer/Singularity for cluster execution when containers are needed.

## Containers

Docker should be supported for local development.

Large databases must not be embedded in Docker images.

Use mounted volumes for:

- BLAST databases
- Foldseek databases
- Pfam/HMM databases
- protein structures
- CLEAN models/data

## Python

Write modular Python.

Prefer:

- pathlib
- argparse
- logging
- dataclasses when appropriate
- type hints

Avoid unnecessary dependencies.

Add docstrings to important public functions.

## Shell

Use Bash.

Use:

set -eo pipefail

Do not use:

set -u

because some cluster environments and third-party scripts may reference
unset environment variables.

Quote paths and variables properly.

## Configuration

Centralize user-configurable parameters in YAML files.

Do not hardcode:

- database locations
- CPU counts
- memory
- paths
- EC lists
- family-specific motifs
- thresholds

## Tests

Add tests for important Python modules.

At minimum test:

- FASTA parsing
- result parsers
- EC normalization
- evidence integration
- confidence calculation

## Documentation

Keep README.md updated when functionality changes.

README.md should explain:

- purpose
- installation
- architecture
- dependencies
- database setup
- Docker usage
- Apptainer usage
- local execution
- Slurm execution
- input files
- output files
- configuration
- confidence interpretation
- adding new enzyme families
- adding new EC numbers
- troubleshooting

## Development workflow

Before making major changes:

1. inspect the repository;
2. understand the existing architecture;
3. reuse existing components when possible;
4. avoid duplicating functionality.

After implementing changes:

1. run relevant tests;
2. run linting if configured;
3. inspect generated outputs;
4. summarize what changed;
5. mention anything that could not be tested.

Do not leave placeholder implementations unless explicitly requested.
