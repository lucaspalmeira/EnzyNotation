# Release Readiness

The package remains pre-release at version `0.1.0`. A release candidate must
pass every applicable gate below without changing scientific policy merely to
make an operational test pass.

## Gates

- complete pytest suite and compact E2E pass;
- total application coverage remains at least 90 percent;
- Ruff lint and formatting checks pass;
- all JSON Schemas and valid/invalid examples pass;
- exact, partial, conflict, cache, and local/scheduled parity cases pass;
- `git diff --check` passes and generated outputs remain ignored;
- no credentials, private `.env`, large biological resources, or runtime
  results are committed;
- CLI help and version agree with package metadata;
- documentation covers native, Compose, Slurm, and Apptainer operation;
- package build succeeds when the `build` frontend is available;
- Compose is syntax-validated without requiring provider execution;
- real target-site Slurm and Apptainer smoke tests are recorded separately.

Synthetic E2E tests establish software behavior only. They do not establish
accuracy, sensitivity, specificity, precision, recall, or calibrated
confidence.

## Post-release scientific validation

```text
curated benchmark
  -> threshold evaluation
  -> confidence calibration
  -> sensitivity/specificity analysis
```

The benchmark must be versioned, family-diverse, leakage-aware, and independent
from provider training/reference choices. Until then, confidence categories
remain documented heuristics.

