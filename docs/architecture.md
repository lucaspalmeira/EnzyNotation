# EnzyNotation Architecture

## Scope

EnzyNotation is a reusable protein-enzyme annotation pipeline. It collects
multiple kinds of evidence, normalizes them into a common contract, and passes
them to a separate integration layer that may produce an EC-number prediction.
The architecture is family-agnostic: enzyme-family and EC-specific knowledge is
configuration, not Python control flow.

Milestones 0 through 2 are currently implemented. The local execution core can
run and resume the validation stage. Evidence providers, the integration
engine, Slurm execution, and final reports described here remain contracts for
later milestones and are not currently available commands.

## Architectural boundaries

The pipeline is divided into six layers:

1. **Input layer** validates FASTA, creates normalized sequences, and establishes
   unique query identifiers.
2. **Provider layer** invokes tools such as BLASTp, CLEAN, HMMER, InterProScan,
   Foldseek, and TM-align, or evaluates configured catalytic motifs.
3. **Parser layer** preserves raw output and converts provider-specific results
   into canonical evidence records.
4. **Integration layer** evaluates candidates, correlations, family rules,
   EC-specific rules, conflicts, and confidence. It is the only layer allowed
   to make a final EC prediction.
5. **Reporting layer** renders normalized evidence, candidate decisions,
   conflicts, provenance, and final annotations without changing decisions.
6. **Execution layer** runs the same stages locally or through Slurm. Execution
   backends may schedule work but may not alter scientific semantics.

Providers never call the integration layer internally and never emit a final
annotation. Parsers never discard a qualifying alternative solely because it
was not ranked first. The reporting layer never repairs or hides conflicts.

## Planned data flow

```text
input FASTA
    |
    v
validation and normalized FASTA
    |
    +--> BLASTp -------------------+
    +--> CLEAN --------------------+
    +--> HMMER / InterProScan -----+
    +--> catalytic motifs ---------+--> canonical evidence records
    +--> Foldseek -----------------+              |
    +--> TM-align -----------------+              v
                                             integration
                                                  |
                           +----------------------+-------------------+
                           v                      v                   v
                     candidate ECs           conflicts        confidence class
                           +----------------------+-------------------+
                                                  |
                                                  v
                                             final reports
```

Each provider stage writes raw artifacts before normalized evidence. A failed
normalization must not erase or overwrite the raw artifact.

## Stable identifiers

The canonical `query_id` is the exact first whitespace-delimited token from the
validated FASTA header, with case preserved. Milestone 1 already rejects
duplicates. Query IDs are opaque identifiers and must never be interpolated
directly into file paths, shell commands, or Slurm job names without safe
encoding. `original_id` may preserve a source-system identifier when an import
step changes the query ID in a future version.

Every canonical evidence record has a run-unique `evidence_id`. Evidence IDs are
opaque and stable within a completed run; downstream code must not infer
scientific meaning by splitting them. Sequences are additionally bound to their
lowercase SHA-256 digest and normalized length.

## Stage contract

A stage receives resolved configuration plus paths to immutable upstream
artifacts. It writes into its own stage directory and publishes a status record
only after all declared outputs are complete. A stage status will eventually
record:

- stage identifier and implementation version;
- input and configuration digests;
- start and finish timestamps;
- command arguments and exit status;
- software, container, and database versions;
- output paths and checksums;
- `completed`, `failed`, `skipped`, or `not_available` state.

Restart decisions must be based on these records rather than on the mere
existence of an output file.

## Run directory layout

The planned canonical layout is:

```text
results/<run_id>/
├── manifest.json
├── config/
│   ├── resolved-pipeline.yaml
│   ├── resolved-families.yaml
│   └── resolved-ec-rules.yaml
├── input/
│   ├── original.fasta
│   ├── normalized.fasta
│   └── validation.json
├── stages/
│   └── <stage_id>/
│       ├── status.json
│       ├── raw/
│       └── normalized/
├── evidence/
│   ├── evidence.jsonl
│   └── evidence.tsv
├── integration/
│   ├── candidates.tsv
│   ├── rule-evaluations.jsonl
│   └── conflicts.tsv
└── reports/
    ├── annotations.tsv
    ├── report.json
    └── report.html

logs/<run_id>/
└── <stage_id>/
    ├── stdout.log
    └── stderr.log
```

`results/<run_id>` contains scientific artifacts. `logs/<run_id>` contains
execution streams. Large shared databases remain under `databases/` or an
external configured mount and are never copied into the run directory.

Paths stored in manifests and evidence records should be relative to the run
root when they refer to run artifacts. External database paths may be absolute,
but reports must also record a stable database name, version, and checksum or
release identifier.

## Configuration model and precedence

Configuration is split into three versioned document types:

- pipeline configuration controls input, output, execution, paths, tools, and
  resources;
- family configuration expresses expected domains, catalytic motifs, and
  structural signatures;
- EC-rule configuration defines candidate-specific requirements, known
  correlations, conflict behavior, and confidence gates.

Pipeline overlays use deterministic deep-merge precedence, from lowest to
highest:

1. built-in defaults, mirrored by `configs/default.yaml`;
2. site or cluster profile;
3. project/run configuration files in the order supplied;
4. explicit command-line scalar overrides when those are introduced.

A later mapping value recursively overrides an earlier mapping value; a later
scalar or array replaces the earlier value. Arrays are never implicitly
concatenated. The resolved configuration and its digest must be preserved in
the run directory.

Family and EC-rule documents are loaded as independent, schema-validated
documents referenced by the pipeline configuration. They do not silently
override pipeline keys. Duplicate family IDs, rule IDs, or conflicting rule-set
versions are configuration errors.

Milestone 1 currently implements built-in defaults followed by `--config`
overlays in argument order. It does not yet load family or EC-rule documents.

## Evidence independence

An evidence source identifies a provider, while an evidence class identifies
the scientific basis of an observation. Built-in source identifiers and their
usual classes are:

| Source ID | Usual evidence class |
| --- | --- |
| `blastp` | `sequence_homology` |
| `clean` | `learned_sequence_model` |
| `hmmer` | `domain_architecture` |
| `interproscan` | `domain_architecture` |
| `catalytic_motif` | `catalytic_motif` |
| `foldseek` | `structure_homology` |
| `tmalign` | `structure_homology` |

Extensions use an `x_` prefix. Source and class alone do not prove
independence. Each record also carries a `correlation_group`, and EC-rule
configuration can declare broader correlations such as shared reference
annotations, databases, models, or structures. Confirmation is independent
only when the configured policy determines that the supporting observations
have distinct scientific bases and no applicable correlation relationship.

For example, Foldseek discovery and TM-align confirmation against the same
reference structure are normally correlated structural evidence, not two
independent confirmations. Multiple BLAST hits copied from the same underlying
annotation are also correlated even when their accessions differ.

## Failure and absence semantics

The canonical states are `observed`, `negative`, `missing`, and `failed`:

- `observed` means a provider completed and produced an interpretable result;
- `negative` means a valid, completed test produced scientifically meaningful
  evidence against an explicit target;
- `missing` means the provider was not run or lacked required input, database,
  configuration, or structure;
- `failed` means execution or parsing was attempted but did not yield a
  scientifically interpretable result.

Missing and failed evidence cannot support or contradict a candidate. An empty
search result is not automatically negative evidence: it becomes negative only
when a configured, validated rule says the completed test had sufficient power
to make absence informative.

## Extension points

A new provider must have a stable source ID, declare an evidence class and
correlation behavior, preserve its raw output, record versions, and emit records
valid against `configs/schema/evidence.schema.json`. A new family or EC number
must be addable through validated configuration. Adding either must not require
branches in the core runner or integration engine.

Contract-breaking changes require a new `schema_version`. Parsers may evolve
independently but must record their own name and version in provenance.
