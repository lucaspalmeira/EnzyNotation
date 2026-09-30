# EnzyNotation

EnzyNotation is a reusable pipeline for functional annotation of protein
sequences and Enzyme Commission (EC) number prediction. Its central design
principle is that an EC number must be supported by multiple, traceable sources
of evidence rather than assigned from the first BLAST hit.

## Project status

Milestone 1 is implemented. The repository currently provides:

- an installable Python package and command-line interface;
- layered YAML configuration;
- protein FASTA validation and normalization;
- EC-number parsing and normalization;
- machine-readable validation reports;
- unit tests and linting configuration.

Evidence collection, evidence integration, EC prediction, confidence
classification, final annotation reports, containers, and Slurm execution are
planned but are **not implemented yet**.

## Requirements

- Python 3.11 or newer
- PyYAML 6.0 or newer

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

Family-specific motifs, EC rules, tool paths, database locations, thresholds,
and compute resources will be introduced in later milestones and will remain in
configuration rather than being hardcoded in Python.

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
configs/             Default and, later, family/EC/tool configuration
src/enzynotation/    Python package
tests/               Unit and CLI tests
```

The planned pipeline will add `scripts/`, `slurm/`, `docker/`, `data/`,
`databases/`, `results/`, and `logs/` as their corresponding milestones are
implemented. Large biological databases will not be stored in the repository
or embedded in container images.

## Planned pipeline

Later milestones will add independent adapters for BLASTp, CLEAN,
HMMER/Pfam or InterProScan, catalytic motifs, Foldseek, and TM-align. Their raw
results will be preserved and normalized before a separate inference layer
evaluates candidate EC numbers, conflicting evidence, and transparent
confidence categories (`high`, `medium`, `low`, and `unresolved`).

Docker will support local development. Apptainer/Singularity and dependency-
aware Slurm jobs will support cluster execution. Until those milestones are
implemented, no container or Slurm commands are available.

## Scientific and architectural policy

- Homology detection is separate from functional inference.
- No EC number is assigned from the first BLAST hit alone.
- Family-specific and EC-specific rules belong in configuration files.
- Raw and normalized evidence must remain traceable.
- Major disagreements between evidence sources must be reported explicitly.
- Missing evidence and tool failures must not be presented as biological
  disagreement.

