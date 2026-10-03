# CLEAN evidence provider

## Scope

The CLEAN stage runs an externally managed CLEAN runtime, preserves its raw
ranked EC candidates, and converts retained candidates into canonical
EnzyNotation evidence. It does not make a final EC assignment. CLEAN evidence
has source ID `clean`, evidence class `learned_sequence_model`, and a
correlation group shared by candidates from the same model, run, and query.

This provider is enzyme-family agnostic. It contains no GH32 mappings or
family-specific decision rules. The integration layer remains the only
component permitted to combine CLEAN with BLAST, domains, motifs, or structural
evidence and produce a final annotation.

Implementation behavior was checked against the upstream
[CLEAN repository](https://github.com/tttianhao/CLEAN), especially its FASTA
wrapper and inference code. The earlier
[GH32 workflow](https://github.com/lucaspalmeira/gh32) was consulted only for
the operational staging and container pattern.

## Runtime ownership

EnzyNotation orchestrates CLEAN and parses its output; it does not redistribute
CLEAN, pretrained models, or ESM weights. The preferred local strategy is the
published external image:

```text
moleculemaker/clean-image-amd64
```

In a future environment where runtime execution is authorized, an operator may
prepare the image explicitly:

```bash
docker pull moleculemaker/clean-image-amd64
```

An explicit tag or immutable repository digest should be configured for a
reproducible analysis. An untagged image is not reproducibly pinned. The stage
records the configured reference and digest, Docker and Compose versions, and
the result of a non-running `docker image inspect` query when those values are
available. Unknown versions remain `unknown`; EnzyNotation never invents them.

The optional `mounts.torch_cache` directory provides persistent storage at the
container's Torch checkpoint location. `mounts.external_model_data` can expose
externally managed model data read-only. Both are external runtime resources:
they must not be committed or copied into run results. The upstream Docker
workflow can require substantial RAM and large ESM weights; its current
requirements should be assessed for the target system rather than treated as
an EnzyNotation hardware guarantee.

The upstream repository includes a `NON-EXCLUSIVE RESEARCH USE LICENSE FOR
CLEAN SOFTWARE.pdf`, which limits the supplied software to qualifying research
use and sets other conditions. Users must review the current
[official CLEAN licensing terms](https://github.com/tttianhao/CLEAN/blob/main/NON-EXCLUSIVE%20RESEARCH%20USE%20LICENSE%20FOR%20CLEAN%20SOFTWARE.pdf)
before deployment, publication, redistribution, or commercial use. This
summary is not legal advice.

## Docker Compose mode

`docker/clean.compose.yml` is a provider-specific adapter, not the future
general EnzyNotation container image. It defines an ephemeral service with
explicit input and output bind mounts. The stage constructs a command of this
form without using a shell:

```bash
docker compose -f docker/clean.compose.yml run --rm clean \
  python /app/CLEAN_infer_fasta.py \
  --fasta_data enzynotation_clean_input
```

`CLEAN_IMAGE`, `CLEAN_INPUT_DIR`, and `CLEAN_OUTPUT_DIR` are supplied as the
command environment. Optional Torch and model-data mounts are appended as
`--volume` arguments. The Compose file has `pull_policy: never`, so a missing
image is reported instead of being downloaded implicitly.

The upstream wrapper accepts a basename rather than an arbitrary input path.
EnzyNotation therefore writes the validated FASTA as
`enzynotation_clean_input.fasta` in the configured input directory, asks CLEAN
to process `enzynotation_clean_input`, locates the configured result suffix,
and copies the result into the canonical run directory. FASTA query IDs are not
renamed.

Configure Docker mode in `configs/tools/clean.yaml` and opt in through the
public workflow:

```bash
enzynotation run proteins.fasta \
  --run-id clean-example \
  --clean-config configs/tools/clean.yaml
```

The checked-in image reference is deliberately overrideable and has no pinned
digest. Operators should make those choices before a production run.

## External command mode

Set `clean.execution.strategy: command` when CLEAN is supplied by Conda, a
manual installation, a wrapper, or a future Apptainer command. The executable
and every prefix token are passed as an argument vector. For example:

```yaml
execution:
  strategy: command
  docker_compose:
    docker_executable: docker
    compose_file: docker/clean.compose.yml
    service: clean
    image: moleculemaker/clean-image-amd64
    image_digest: null
  command:
    prefix: [conda, run, -n, clean]
    executable: python
    working_directory: /opt/CLEAN/app
```

For the upstream basename-oriented wrapper, `mounts.input_directory` and
`mounts.output_directory` must correspond to its expected input and results
directories. A custom wrapper may use the same `--fasta_data` contract while
managing those locations itself. Conda is an option, not a base EnzyNotation
dependency.

## Score semantics

The serialized number is interpreted only according to explicit configuration:

| Output variant | Metric | Direction | Interpretation |
| --- | --- | --- | --- |
| `maxsep_distance` | `cluster_center_pairwise_distance` | lower is better | Pairwise embedding distance from the query to the selected EC cluster center. It is not a confidence score. |
| `maxsep_gmm_confidence` | `gmm_confidence_estimate` | higher is better | CLEAN GMM-ensemble estimate derived from the selected distance. EnzyNotation does not treat it as a calibrated probability. |
| custom/opaque | `clean_model_score` | unknown | Raw numeric output whose scientific meaning and ordering are not established. |

The current upstream FASTA path supplies `gmm_ensumble.pkl` to maximum-
separation inference and can serialize the result of `infer_confidence_gmm`.
That implementation detail is why the default provider file declares the GMM
variant. Values in `[0, 1]` are not sufficient evidence that a value is a
calibrated probability.

For known directions, EnzyNotation computes an additional deterministic
normalized rank while preserving CLEAN's candidate order and original rank.
For unknown direction it does not numerically reorder candidates and leaves
`normalized_rank` null. EnzyNotation never replaces CLEAN's own maximum-
separation candidate-selection algorithm with a smallest-distance rule.

## Filtering

Filtering is operational and configuration-driven:

- `maximum_retained_candidates` limits retained candidates per query;
- `threshold` and `threshold_comparison` define an optional numeric test;
- `threshold_status` records whether a threshold is operational, externally
  documented, or empirically calibrated;
- `allowed_ec_depths` accepts normalized EC depths 1 through 4;
- `allow_partial_ec` controls retention of partial EC candidates.

A known lower-is-better metric only accepts `lte`; a known higher-is-better
metric only accepts `gte`. An unknown direction requires an explicit comparison
before a threshold can be used. These checks prevent a plausible-looking
number from silently acquiring the wrong meaning. The supplied configuration
has no numeric threshold and makes no claim of validated transfer performance.

Every criterion and rejection is retained. Malformed EC strings, malformed
tokens, non-finite/non-numeric values, empty candidate fields, and predictions
for unknown query IDs appear in `clean_rejections.jsonl` rather than
disappearing. Duplicate valid predictions remain separate candidates.

## Outputs

The stage uses the established run layout:

```text
results/<run_id>/
├── stages/clean/
│   ├── status.json
│   ├── raw/
│   │   └── clean_result.csv
│   └── normalized/
│       ├── clean_config.json
│       ├── clean_predictions.tsv
│       ├── clean_rejections.jsonl
│       └── clean_summary.json
└── evidence/
    └── clean_evidence.jsonl

logs/<run_id>/clean/
├── stdout.log
└── stderr.log
```

`clean_result.csv` is the unmodified retrieved result. The TSV preserves the
raw token, serialized value, numeric value, original CLEAN rank, optional
normalized rank, retention decision, and criteria. Each retained candidate
becomes one canonical evidence record. All candidates from the same CLEAN
model/run/query are correlated, so they cannot be counted as independent
confirmations.

`clean_summary.json` distinguishes an empty successful result from a run with
valid predictions that were all filtered. It always contains
`final_ec_prediction: null`.

## Provenance and caching

The stage status records start/end times, return code, exact command, stdout and
stderr paths, runtime versions, container image inspection data, and output
checksums. Canonical records add query checksum, resolved configuration digest,
parser version, model identifier/version/training split, metric semantics, raw
record locator, and optional external-resource checksum.

The stage cache signature includes the validated FASTA, full CLEAN provider
configuration, Compose file, configured resource fingerprint files, parser and
stage versions, model/image/runtime choices, score semantics, and filters.
Identical runs resume from the completed stage. Relevant FASTA, model,
resource, image/digest, metric, or threshold changes invalidate it.

`resource_fingerprints` should name small stable manifest or checksum files for
large external resources. Hashing model archives directly is supported but can
be expensive. A configured fingerprint that is absent is an explicit failure.

## Failure states

When `--clean-config` is absent, no CLEAN stage exists. A configured provider
with `enabled: false` completes with status `disabled` and executes nothing.
Other explicit reasons include unavailable Docker, Compose, image, command
runtime, mounts, model files or fingerprints; command failure; missing result;
and structurally malformed CSV. Per-candidate malformed predictions are
rejections in an otherwise successful parse.

Required-provider failure fails the run. Optional-provider failure produces a
completed run with optional failures. A successful empty file or query-only row
is not a provider failure, and valid candidates removed by configured filters
are reported as zero retained candidates.

An unavailable model version is not silently replaced or treated as an
execution failure: it is preserved as `unknown`/`unavailable`, with
`model_version_available: false` in normalized evidence and the summary.

## Limitations

CLEAN is one model-based source of EC evidence. Its predictions do not bypass
conflict handling, evidence correlation, the integration layer, or
future final-confidence classification. In particular, CLEAN agreement with
sequence homology may still reflect shared training/reference information and
must not automatically be treated as independent confirmation.

No real CLEAN executable, image, model, or ESM-weight smoke test is part of
Milestone 5. Parser, command, workflow, evidence, failure, and caching behavior
are tested with compact fixtures and mocked execution only.
