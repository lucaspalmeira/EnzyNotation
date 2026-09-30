# EnzyNotation Data Contracts

## Contract versions

All canonical JSON/YAML documents contain `schema_version: 1` and validate
against JSON Schema Draft 2020-12 files in `configs/schema/`. YAML documents are
validated after YAML parsing, so their in-memory representation must satisfy the
same JSON data model.

The schemas are authoritative for shape and primitive constraints. This
document defines semantics that cannot be fully expressed in JSON Schema, such
as identifier scope, correlation interpretation, and scientific meaning.

## Query and sequence identity

`query_id` is the unique identifier used to join all records within a run. It is
the first whitespace-delimited token of the validated FASTA header, is
case-sensitive, and is treated as opaque. It must be non-empty and unique after
normalization. It must not be used directly as a path component.

Every evidence record also carries:

- `sequence_sha256`: SHA-256 of the uppercase normalized sequence with no
  whitespace or terminal stop marker;
- `sequence_length`: length of that normalized sequence;
- optional `original_id`: upstream identifier when different from `query_id`.

Records with the same `query_id` but different sequence digests in one run are
invalid and must stop integration.

## EC representation

EC numbers always use a four-component canonical string with no `EC` prefix.
Each known component is a positive decimal integer without leading zeros.
Unknown trailing components are represented by `-`:

- complete: `1.2.3.4`;
- depth three: `1.2.3.-`;
- depth two: `1.2.-.-`;
- depth one: `1.-.-.-`.

A known component cannot follow `-`, and `-.-.-.-` is invalid. The canonical
candidate object contains:

```json
{
  "namespace": "EC",
  "ec": "1.2.3.-",
  "depth": 3,
  "completeness": "partial"
}
```

`depth` and `completeness` must agree with the EC string. Partial EC evidence is
preserved and may support a broader class, but it must not be silently promoted
to a complete EC. An EC-specific rule must explicitly permit a partial final
prediction. Competing complete ECs beneath the same partial parent remain
distinct candidates.

## Evidence-source identifiers

The stable built-in source IDs are `blastp`, `clean`, `hmmer`,
`interproscan`, `catalytic_motif`, `foldseek`, and `tmalign`. Future built-in
sources require a schema version change. Locally developed providers use a
lowercase `x_` identifier, for example `x_active_site_model`.

Source IDs name providers, not scientific independence. The canonical evidence
classes are:

- `sequence_homology`;
- `learned_sequence_model`;
- `domain_architecture`;
- `catalytic_motif`;
- `structure_homology`;
- `curated_annotation`;
- `user_assertion`.

Each record must include a non-empty `correlation_group`. The value represents
shared lineage such as the same reference annotation, reference structure,
database-derived label, or model family. It is opaque to consumers except for
equality and configured correlation rules.

## Canonical evidence record

The canonical record is one JSON object conforming to
`evidence.schema.json`. JSON Lines is the primary collection format; TSV is a
flattened reporting view and is not the lossless source of truth.

Required top-level fields are:

- `schema_version` and run-unique `evidence_id`;
- `query`, binding the record to a normalized sequence;
- `source`, identifying provider, evidence class, and correlation group;
- `record_status`;
- `provenance`.

An `observed` record requires an `assertion`. The assertion names an EC, family,
or feature target and states whether the observation `supports`, `contradicts`,
or is `neutral` toward that target. A `negative` record also requires an
assertion, whose effect must be `contradicts`, plus a reason code. `missing` and
`failed` records require a reason code and cannot contain an assertion.

Provider values belong in `metrics`. Threshold decisions belong in `criteria`,
where the record preserves criterion ID, metric, operator, configured threshold,
observed value, and pass/fail result. Provider scores are never assumed to be
comparable across tools merely because they are numeric.

## Raw and normalized evidence

Raw evidence is the immutable output emitted by a provider, including headers
and provider-specific metadata. Normalized evidence is a canonical record
created by a versioned parser. Normalization may rename fields, calculate
coverage, normalize EC syntax, or express configured criteria; it may not erase
the underlying raw artifact.

Every normalized record points to its raw artifact and, when possible, a record
locator such as line number, row key, or JSON path. Raw files receive checksums.
If a parser is rerun, the new normalized output records the new parser version
and does not overwrite a completed historical run.

## Provenance

Every record contains:

- `run_id`, `stage_id`, and UTC `generated_at` timestamp;
- parser name and version;
- tool name and version, using EnzyNotation itself for internal evaluators;
- zero or more database descriptors with stable name and version;
- raw artifact path, format, optional checksum, and optional record locator;
- configuration and normalized-input SHA-256 digests;
- optional command argument array and container image/digest.

An unknown external tool or database version must be written explicitly as
`unknown`; it must not be omitted or guessed. Such records remain traceable but
may be downgraded by scientific policy. Database paths do not replace database
versions. Secrets and credentials must never be recorded in command arguments.

## Conflict contract

The integration layer will classify conflicts using stable identifiers:

- `candidate_disagreement`: strong evidence supports incompatible ECs;
- `hierarchy_disagreement`: evidence supports incompatible EC hierarchy levels;
- `required_feature_absent`: a candidate lacks a required domain or motif;
- `forbidden_feature_present`: a configured exclusion is observed;
- `sequence_structure_disagreement`: sequence- and structure-derived evidence
  support incompatible functions;
- `curation_disagreement`: provider output conflicts with curated annotation;
- `rule_disagreement`: two applicable configuration rules produce incompatible
  decisions;
- `provenance_inadequate`: evidence needed for a decision lacks acceptable
  version or lineage information.

Conflicts carry severity (`minor` or `major`), involved evidence IDs, involved
candidates, and a human-readable explanation. Major conflicts force
`unresolved` unless an explicit, documented EC rule resolves that exact conflict
class. The original conflict still remains in output after resolution.

## Confidence contract

The final categories mean:

- `high`: the candidate passes all required rules, has the configured minimum
  number of genuinely independent supporting evidence classes, and has no
  unresolved major conflict;
- `medium`: the candidate has meaningful multi-source support but does not meet
  every high-confidence gate;
- `low`: the candidate has limited or weak support that is still sufficient to
  report as a hypothesis;
- `unresolved`: no candidate passes minimum rules, candidates remain tied, a
  major conflict remains, or available evidence cannot justify an assignment.

Confidence is a categorical integration result, not the average of provider
scores. `unresolved` is an outcome, not a confidence synonym for zero. All
thresholds and gates come from the versioned EC-rule document. These categories
are heuristic until calibrated against a curated benchmark; reports must expose
that calibration status.

## Family rules

Family documents define a stable family ID and version plus generic rule arrays:

- domain rules identify signatures, provider, required/supporting/forbidden
  role, occurrence bounds, and optional candidate ECs;
- motif rules define a regex, occurrence bounds, optional positional window,
  role, and optional candidate ECs;
- structural rules identify reference structures and configured TM-score and
  coverage thresholds.

The rule evaluator interprets these arrays generically. Family names, motif
patterns, domain accessions, reference structures, and EC lists never belong in
family-specific Python branches.

## EC-specific rules

An EC-rule document contains a versioned ruleset, declared source correlations,
confidence policy, unresolved policy, and candidate rules. Each candidate rule
specifies an EC, optional family applicability, whether partial prediction is
allowed, and `all`, `any`, and `none` evidence conditions.

Conditions filter by source, evidence class, effect, record status, counts,
distinct correlation groups, and optional metric comparison. Conditions and
confidence gates use stable `rule_id` or `condition_id` values so reports can
show exactly why a candidate passed or failed.

## Configuration and evidence formats

JSON is canonical for schemas and individual evidence records. YAML is used for
human-authored pipeline, family, and EC-rule configuration. Parsers must use
safe structured-data APIs. TSV exports use documented columns and JSON encoding
for nested values; they must not be reparsed as the lossless integration input.

