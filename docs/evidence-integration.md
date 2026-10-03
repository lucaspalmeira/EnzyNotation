# Evidence Integration

## Scope

Milestone 7 is the only EnzyNotation layer that produces a final EC prediction.
It consumes canonical JSONL records validated against
`configs/schema/evidence.schema.json`; it never reparses BLAST, CLEAN, HMMER,
InterProScan, motif, Foldseek, or TM-align raw output.

Integration is opt-in:

```bash
enzynotation run proteins.fasta \
  --run-id integrated-example \
  --integration-config configs/integration/default.yaml
```

Provider stages are still selected explicitly. Enabling integration does not
silently start an expensive search. A family profile and EC-specific rules may
be supplied when relevant:

```bash
enzynotation run proteins.fasta \
  --run-id integrated-example \
  --blast-config configs/tools/blast.yaml \
  --clean-config configs/tools/clean.yaml \
  --family-config examples/configs/family.example.yaml \
  --hmmer-config configs/tools/hmmer.yaml \
  --motifs \
  --integration-config configs/integration/default.yaml \
  --ec-rules configs/ec_rules/example.yaml
```

`--confidence-config` overrides the confidence document referenced by the
integration policy. Family profiles remain optional for integration itself and
are used to select family-scoped EC rules; they are mandatory only when domain
or motif providers require them.

## Candidate Boundary

Candidates come only from explicit canonical assertions whose target type is
`ec` and whose provider has the `candidate_source` role. Descriptions, protein
names, PDB titles, filenames, first-hit ranks, and arbitrary free text are never
scraped for EC numbers.

The default roles are conservative:

| Provider | Default roles |
| --- | --- |
| BLASTp | candidate source, supporting, contextual |
| CLEAN | candidate source, supporting |
| HMMER / InterProScan | supporting, constraint, contradiction capable |
| catalytic motifs | supporting, constraint, contradiction capable |
| Foldseek | candidate source, supporting, contextual |
| TM-align | supporting, contextual |

A provider without an EC assertion can still satisfy or fail a configured
domain, motif, family, or structural condition. Such a record is not discarded
and is not converted into an exact EC vote.

## EC Hierarchy

ECs form a hierarchy. With `inherit_complete_to_parents: true`, explicit
support for `3.2.1.26` is also recorded on `3.-.-.-`, `3.2.-.-`, and
`3.2.1.-`. This direction of inheritance is explicit in each scorecard.
Support for `3.2.1.-` never expands to every fourth-level child.

Exact and partial candidates have separate support gates. The shipped heuristic
policy requires more independent support for an exact EC than a partial EC.
When exact children remain tied, `tie_policy: common_parent` can retain their
supported common parent. `tie_policy: unresolved` keeps the alternatives and
returns no final EC. Alphabetical or input order never breaks a tie.

## Correlation And Independence

Every record retains its provider, evidence class, and correlation group.
Scorecards show total evidence IDs, unique groups, all observed classes, and a
conservative independent-class set. Independent classes are matched to distinct
correlation groups; several classes from one group contribute at most one
independent confirmation.

Consequences include:

- BLAST homology and curated annotation from one subject are correlated;
- Foldseek similarity, its reference annotation, and TM-align for one pair are
  correlated;
- several CLEAN alternatives from one model/run are correlated;
- HMMER and InterPro observations sharing a domain-signature group are
  correlated.

Raw record count is exposed for traceability but is not a confidence score.
There is no majority vote and the default policy contains no numerical provider
weights.

## Rules And Conflicts

`configs/schema/ec-rules.schema.json` defines the bounded rule language. An EC
rule has `all`, `any`, and `none` conditions. Conditions may select provider,
evidence class, effect, record state, normalized metric, minimum records, and
minimum distinct groups. Evaluations emit `pass`, `fail`, or `not_applicable`
plus the evidence IDs, groups, reason code, explanation, ruleset version, and
configuration source.

The engine preserves typed conflicts using the Milestone 0 vocabulary. Current
detection covers incompatible candidates, sequence/structure disagreement,
CLEAN/curated-reference disagreement, negative domain or motif constraints,
required-rule failure, required-provider failure, and insufficient specificity.
Configured severity and action remain visible. A major `force_unresolved`
conflict cannot be hidden by a better-ranked provider result.

Missing, failed, unavailable, disabled, not-run, and successful-zero providers
are summarized separately from evidence. A failed command is not a biological
contradiction. A successful negative domain or motif evaluation is.

## Confidence

The only categories are `high`, `medium`, `low`, and `unresolved`. The separate
confidence policy declares, per category:

- minimum independent evidence classes;
- minimum distinct correlation groups;
- required evidence classes and rule IDs;
- allowed EC completeness;
- maximum major and minor conflicts.

The engine checks gates in high-to-low order. It emits no probability. The
shipped categories and thresholds are heuristic operational defaults and are
uncalibrated until evaluated against a versioned curated benchmark.

## Outputs And Provenance

The integration stage writes:

```text
results/<run_id>/
├── final_annotation.json
└── stages/integrate/normalized/
    ├── candidate_scorecards.jsonl
    ├── confidence_policy.json
    ├── conflicts.jsonl
    ├── integration_config.json
    ├── rule_evaluations.jsonl
    └── integration_summary.json
```

Optional `ec_rules.json` and `family_profile.json` snapshots are added when
those documents participate in the decision.

`final_annotation.json` contains one compact annotation per query, including a
nullable predicted EC, depth/completeness, categorical confidence, alternatives,
major conflicts, support IDs, independent classes, provider availability,
policy identity, rule references, and reason codes. `unresolved` is a valid
successful scientific result.

Provenance records the integration implementation, policy ID/version and
calibration label, policy/rule/schema checksums, all canonical evidence
checksums, stage configuration digest, and timestamp. The stage cache also
includes canonical evidence, normalized FASTA, family/EC rules, provider state,
integration policy, confidence policy, and evidence schema. A relevant change
invalidates integration without requiring raw provider files to be hashed again.

## Synthetic Examples

These examples describe policy behavior, not benchmark performance.

### Strong exact agreement

BLAST curated annotation, CLEAN, and structural evidence support `1.1.1.1` in
three distinct classes and groups. The exact candidate passes its rules, has no
conflict, and reaches `high`. Its parent scorecards remain visible but are not
selected because the exact candidate satisfies the stronger exact gate.

### Lower-confidence exact result

BLAST curated annotation and CLEAN support `1.1.1.1` in two distinct groups and
classes. The exact candidate passes the minimum exact gate but not the high
gate, producing `medium`. No numeric probability is emitted.

### Partial result

Two sources support `3.2.1.26` and `3.2.1.80`; neither child satisfies the exact
gate, while inherited support converges on `3.2.1.-`. With partial output
enabled, the common parent is reported as partial. Neither child is silently
chosen.

### Unresolved sequence/structure conflict

Sequence evidence supports `1.1.1.1` while a structural reference supports
`2.2.2.2`. The scorecards preserve both candidates and the conflict contains
all involved evidence/groups. The default major conflict action yields
`predicted_ec: null`, `confidence: unresolved`, and the reason code
`major_unresolved_conflict`.

## Scientific Limits

The first BLAST or Foldseek hit never determines EC. Structural similarity and
high TM-score do not prove identical function or substrate specificity. Domain
or catalytic compatibility does not normally distinguish a fourth-level EC.
CLEAN metric semantics remain those recorded by the provider; opaque values are
not reordered or interpreted as probabilities. Curated reference metadata is
valuable but not infallible. Final confidence remains heuristic and
uncalibrated.
