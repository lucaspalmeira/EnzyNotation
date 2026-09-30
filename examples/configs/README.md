# Contract examples

These files demonstrate schema version 1 for the EnzyNotation architecture:

- `pipeline.example.yaml` is a future complete pipeline configuration;
- `family.example.yaml` defines generic family evidence rules;
- `ec-rules.example.yaml` defines EC integration and confidence rules;
- `evidence.example.json` is one canonical normalized evidence record.

All examples are validated by `tests/test_schemas.py`. They document contracts
for later milestones; only the Milestone 1 FASTA-related subset of the pipeline
configuration is currently consumed by the CLI.

The family and EC values are illustrative and are not validated biological
recommendations.

