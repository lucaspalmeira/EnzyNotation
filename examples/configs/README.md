# Contract examples

These files demonstrate schema version 1 for the EnzyNotation architecture:

- `pipeline.example.yaml` is a future complete pipeline configuration;
- `family.example.yaml` defines generic family evidence rules;
- `gh32.example.yaml` is an uncalibrated, literature-linked example consumed
  by the implemented domain and motif stages;
- `clean.example.yaml` configures an externally managed CLEAN command and raw
  maximum-separation distance semantics;
- `ec-rules.example.yaml` defines EC integration and confidence rules;
- `integration.example.yaml` defines provider roles, evidence inputs, EC
  specificity, tie handling, and conflict policy;
- `evidence.example.json` is one canonical normalized evidence record.

All examples are validated by `tests/test_schemas.py`. The CLI consumes the
FASTA pipeline subset, separate provider configurations, family profiles, and
the implemented integration, confidence, and optional EC-rule policies.

Example thresholds and EC values are illustrative and are not validated
biological recommendations. In particular, the GH32 profile supports only
family/catalytic compatibility and cannot distinguish GH32 activities or assign
a specific EC number.
