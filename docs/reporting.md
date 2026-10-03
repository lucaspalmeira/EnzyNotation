# Final Reporting

## Scope

Milestone 8 presents completed Milestone 7 decisions. It does not infer EC
numbers, change candidate eligibility, resolve conflicts, or classify
confidence. The integration stage remains the only component that may make
those scientific decisions.

Reporting is opt-in and requires integration:

```bash
enzynotation run proteins.fasta \
  --run-id reported-example \
  --integration-config configs/integration/default.yaml \
  --report
```

Use `--report-config PATH` to change presentation-only settings. The current
configuration supports a title, detailed evidence in HTML, and run provenance
in HTML. It cannot alter any annotation.

## Inputs And Cache

The `report` stage depends on `integrate` and consumes:

- `final_annotation.json`;
- candidate scorecards, conflicts, and rule evaluations;
- the integration summary and stage status;
- canonical `evidence/*_evidence.jsonl` files;
- the run manifest and resolved configuration provenance;
- the report configuration, JSON Schema, and HTML template.

All direct inputs participate in the existing content-based stage signature.
An identical resumed run reuses the report. Changes to an integration artifact,
canonical evidence file, report configuration, schema, or template invalidate
the report. Presentation-only changes do not invalidate validation, providers,
or integration.

The optional standalone `enzynotation report RUN_ID` command is not implemented.
Regeneration currently uses `enzynotation run` with the original run ID and
`--report`; unchanged upstream stages are restored from the normal cache.

## Outputs

Outputs use the existing canonical directory:

```text
results/<run_id>/reports/
├── annotations.tsv
├── evidence.tsv
├── candidates.tsv
├── conflicts.tsv
├── rule_evaluations.tsv
├── provider_status.tsv
├── report.json
├── report.html
└── run_summary.json
```

The stage snapshot is stored at
`stages/report/normalized/report_config.json`. Original canonical JSONL and raw
provider artifacts remain the scientific source of truth and are referenced,
not duplicated into the report directory.

## TSV Conventions

All TSV files are UTF-8 with a tab delimiter, LF newlines, and fixed documented
header order. Null is an empty cell. Booleans are lower-case `true` or `false`.
Lists and mappings use compact, key-sorted JSON in one cell. CSV-compatible
quoting protects embedded tabs, newlines, quotation marks, and Unicode text.

`annotations.tsv` has one row per query and contains the final EC, depth,
complete/partial state, categorical confidence, exact/partial/unresolved
display status, reasons, alternatives, conflict counts, independence counts,
and integration policy identity. An unresolved EC is an empty cell, not an
error.

`evidence.tsv` is an inspection-oriented flattening of stable canonical fields.
It preserves evidence IDs, providers, classes, correlation groups, state,
effect, optional candidate EC, metrics as deterministic JSON, curated reference,
tool/database identity and versions, and the raw artifact path. It does not
replace canonical evidence JSONL.

`candidates.tsv` preserves Milestone 7 order and status, supporting and
contradicting evidence IDs, independent classes, correlation groups, failed
constraints, conflicts, rejection reasons, and confidence features. Reporting
does not compute a score or rerank candidates.

`conflicts.tsv`, `rule_evaluations.tsv`, and `provider_status.tsv` expose the
corresponding integration traces. Provider failure, unavailability, disabled
state, and a successful zero-evidence result are operational states. They are
not biological contradictions.

## Structured JSON

`report.json` validates against `configs/schema/report.schema.json`. It records
the report and EnzyNotation versions, run ID, integration timestamp, input and
configuration provenance, policy identity, provider availability, per-query
annotations, candidates, conflicts, rule traces, evidence summaries, evidence
file checksums, and tool/database/model provenance.

`run_summary.json` contains descriptive counts only: exact, partial, unresolved,
confidence categories, conflict severities, and failed/unavailable providers.
It is not an accuracy estimate and creates no global scientific ranking.

## Static HTML

`report.html` is self-contained semantic HTML and CSS and can be opened from a
`file://` URL. Exact, partial, and unresolved outcomes have distinct labels and
visual treatment. Major conflicts are prominent. Alternatives, correlated
evidence, provider states, rule evaluations, and provenance remain inspectable
with native `<details>` elements.

All query identifiers, descriptions, reference metadata, explanations, and
other external strings are HTML-escaped. The template uses Python's standard
library substitution and adds no frontend framework, JavaScript runtime, or
server dependency.

## Interpretation

The only confidence categories are `high`, `medium`, `low`, and `unresolved`.
EnzyNotation confidence categories are heuristic and are not calibrated
probabilities. Reports do not convert them to percentages, numerical scores,
stars, or progress bars.

Complete `EC 3.2.1.26` and partial `EC 3.2.1.-` assignments are visibly
different. `unresolved` is a valid scientific outcome and retains its reasons
and competing candidates.

Correlation groups remain visible because record count is not independence.
BLAST similarity and annotation from one reference remain correlated. Foldseek,
reference annotation, and TM-align for one structural pair likewise do not
become multiple independent confirmations in the report.

Final reporting does not perform EC inference. It displays decisions produced
by the integration layer. Provider metrics are shown as normalized observations
without conversion into new confidence scores.

## Limitations

The report is a per-run static artifact, not a web dashboard or API. It does not
measure accuracy without benchmark ground truth, calibrate confidence, download
databases, or execute providers. Absolute external resource paths may remain in
the run manifest or canonical evidence when required for reproducibility. The
portable report uses input/configuration names and checksums plus relative
references back to those source artifacts.
