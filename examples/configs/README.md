# Contract examples

These files demonstrate schema version 1 for the EnzyNotation architecture:

- `pipeline.example.yaml` is a future complete pipeline configuration;
- `family.example.yaml` defines generic family evidence rules;
- `gh32.example.yaml` is an uncalibrated, literature-linked example consumed
  by the implemented domain and motif stages;
- `ec-rules.example.yaml` defines EC integration and confidence rules;
- `evidence.example.json` is one canonical normalized evidence record.

All examples are validated by `tests/test_schemas.py`. Family and EC-rule files
EC-rule files still document contracts for later milestones. The CLI consumes
the Milestone 1 FASTA-related pipeline subset, separate BLAST/HMMER/InterProScan
provider configurations, and family profiles for implemented domain and motif
evidence.

Example thresholds and EC values are illustrative and are not validated
biological recommendations. In particular, the GH32 profile supports only
family/catalytic compatibility and cannot distinguish GH32 activities or assign
a specific EC number.
