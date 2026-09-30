# EnzyNotation

EnzyNotation is a reusable pipeline for functional annotation of protein
sequences and Enzyme Commission (EC) number prediction. Its central design
principle is that an EC number must be supported by multiple, traceable sources
of evidence rather than assigned from the first BLAST hit.

## Project status

Milestones 0 through 2 are implemented. The repository currently provides:

- an installable Python package and command-line interface;
- layered YAML configuration;
- protein FASTA validation and normalization;
- EC-number parsing and normalization;
- machine-readable validation reports;
- versioned JSON Schema contracts for pipeline, family, EC-rule, and evidence
  documents;
- documented architecture and scientific inference policy;
- a validation-only local workflow with manifests and resumable stage state;
- content-based input/configuration invalidation and atomic stage completion;
- command, software-version, checksum, stdout, and stderr provenance;
- unit tests and linting configuration.

Evidence collection, evidence integration, EC prediction, confidence
classification, final annotation reports, containers, and Slurm execution are
planned but are **not implemented yet**.

## Requirements

- Python 3.11 or newer
- PyYAML 6.0 or newer
- jsonschema 4.21 or newer

Development checks additionally use pytest, pytest-cov, and Ruff.

## Installation

Create a virtual environment and install the package:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e .
```

For development tools:

```bash
python -m pip install -e '.[dev]'
```

## FASTA validation

Validate a protein FASTA file:

```bash
enzynotation validate proteins.fasta
```

Write normalized sequences and a JSON validation report:

```bash
enzynotation validate proteins.fasta \
  --output normalized.fasta \
  --report validation.json
```

The command exits with status `0` for valid input, `1` for validation errors,
and `2` for configuration, input/output, or command-line failures. A normalized
FASTA file is written only when validation succeeds.

Validation currently checks:

- presence of FASTA records and non-empty identifiers;
- duplicate sequence identifiers;
- empty sequences;
- sequence data before the first header;
- allowed amino-acid symbols;
- whitespace within sequence lines;
- misplaced stop markers;
- configured minimum and maximum sequence lengths.

Lowercase sequences are converted to uppercase by default. A single terminal
`*` is accepted, removed from normalized output, and reported as a warning.

## Local validation workflow

Run the Milestone 2 validation workflow with an explicit ID:

```bash
enzynotation run proteins.fasta --run-id example-run
```

Scientific artifacts are written under `results/example-run/` and stage logs
under `logs/example-run/`. Repeating the command with the same run ID resumes
the run. A completed validation stage is skipped only when its input,
validation-related configuration, implementation version, dependencies, and
recorded outputs are unchanged.

Use custom roots or disable resume explicitly:

```bash
enzynotation run proteins.fasta \
  --run-id example-run \
  --results-dir project-results \
  --logs-dir project-logs

enzynotation run proteins.fasta \
  --run-id new-run \
  --no-resume
```

When no run ID is supplied, EnzyNotation generates a timestamped unique ID.
Resuming therefore requires reusing an explicit ID or a configured
`pipeline.run_id`.

The validation-only workflow currently creates:

```text
results/<run_id>/
├── manifest.json
├── config/resolved-pipeline.yaml
├── input/
│   ├── original.fasta
│   ├── normalized.fasta
│   └── validation.json
├── stages/validate/
│   ├── status.json
│   ├── raw/
│   └── normalized/
├── evidence/
├── integration/
└── reports/

logs/<run_id>/validate/
├── stdout.log
└── stderr.log
```

Failed validation produces a failed stage status and manifest without a stale
normalized FASTA. Interrupted attempts are retried, previous attempt records
are retained under the stage history directory, and changed FASTA content
invalidates the completed validation stage.

## Configuration

Built-in defaults are mirrored in `configs/default.yaml`. One or more YAML
overlays can be applied in order; later files override earlier values:

```bash
enzynotation validate proteins.fasta \
  --config site.yaml \
  --config analysis.yaml
```

Milestone 1 supports these settings:

```yaml
schema_version: 1

pipeline:
  name: EnzyNotation

input:
  allowed_residues: ACDEFGHIKLMNPQRSTVWYBXZJUO
  allow_terminal_stop: true
  min_sequence_length: 1
  max_sequence_length: null
  uppercase: true

output:
  line_width: 60

logging:
  level: INFO
```

The versioned contracts for future pipeline, family, EC-rule, and evidence
documents are in `configs/schema/`, with valid examples in `examples/configs/`.
The current configuration loader validates the Milestone 1 fields shown above,
and the local runner additionally honors `pipeline.run_id` when present. It does
not yet load family or EC-rule documents or run evidence providers.

Configuration precedence is deterministic: built-in defaults are followed by
site/project overlays in supplied order, and a later value overrides an earlier
one. Mapping values merge recursively; arrays and scalar values are replaced.
Family and EC-rule documents are independent contracts rather than implicit
pipeline overlays.

Family-specific motifs, EC rules, tool paths, database locations, thresholds,
and compute resources remain configuration rather than hardcoded Python logic.

## EC normalization

The Python API accepts complete and hierarchical partial EC numbers:

```python
from enzynotation.ec import normalize_ec

normalize_ec("EC: 1.2.3.4")  # "1.2.3.4"
normalize_ec("1.2")          # "1.2.-.-"
```

Malformed identifiers, missing top-level classes, empty levels, non-positive
levels, and levels specified after an unknown level are rejected.

## Development

Run the tests:

```bash
python -m pytest
```

Run tests with coverage and check formatting and linting:

```bash
python -m pytest --cov=enzynotation --cov-report=term-missing
ruff check src tests
ruff format --check src tests
```

## Repository structure

```text
configs/             Defaults and JSON Schema contracts
docs/                Architecture, data contracts, and scientific policy
examples/configs/    Valid contract examples for future milestones
src/enzynotation/    Python package, stages, and local execution backend
tests/               Unit, CLI, and schema tests
```

The workflow creates `results/` and `logs/` when run. Later milestones will add
`scripts/`, `slurm/`, `docker/`, `data/`, and `databases/` as required. Large
biological databases will not be stored in the repository or embedded in
container images.

## Planned pipeline

Later milestones will add independent adapters for BLASTp, CLEAN,
HMMER/Pfam or InterProScan, catalytic motifs, Foldseek, and TM-align. Their raw
results will be preserved and normalized before a separate inference layer
evaluates candidate EC numbers, conflicting evidence, and transparent
confidence categories (`high`, `medium`, `low`, and `unresolved`).

Docker will support local development. Apptainer/Singularity and dependency-
aware Slurm jobs will support cluster execution. Until those milestones are
implemented, no container or Slurm commands are available.

## Architecture and data contracts

The planned pipeline separates input handling, evidence providers, parsers,
integration, reporting, and execution backends. Providers emit observations;
only the integration layer may produce a final EC prediction. Raw artifacts are
preserved separately from normalized canonical evidence.

The canonical evidence contract distinguishes `observed`, `negative`,
`missing`, and `failed` records and requires query identity, evidence source and
class, a correlation group, and complete provenance. EC candidates carry a
four-level normalized EC string together with matching depth and
complete/partial status. Correlated records cannot satisfy independent-evidence
requirements merely because they came from different executables.

See:

- `docs/architecture.md` for component boundaries, configuration precedence,
  execution semantics, and the planned run layout;
- `docs/data-contracts.md` for canonical identifiers, evidence, EC candidates,
  provenance, conflicts, family rules, and EC rules;
- `docs/scientific-policy.md` for non-negotiable inference and confidence
  policies.

The schemas are contracts for upcoming implementations. Their presence does not
make BLASTp, CLEAN, domain, motif, structure, integration, reporting, container,
or Slurm commands available yet.

## Scientific and architectural policy

- Homology detection is separate from functional inference.
- No EC number is assigned from the first BLAST hit alone.
- Family-specific and EC-specific rules belong in configuration files.
- Raw and normalized evidence must remain traceable.
- Major disagreements between evidence sources must be reported explicitly.
- Missing evidence and tool failures must not be presented as biological
  disagreement.
- Correlated evidence must not be counted as independent confirmation.
- Confidence categories remain heuristic until calibrated against a curated,
  versioned benchmark.
