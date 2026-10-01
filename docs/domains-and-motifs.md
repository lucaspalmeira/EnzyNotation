# Domain and Catalytic-Motif Evidence

EnzyNotation can collect domain evidence with HMMER, optionally supplement it
with InterProScan, and evaluate catalytic motifs from a versioned family
profile. These providers emit evidence only. They do not choose a final EC
number, and agreement between a domain and a motif is not by itself a
fine-grained functional assignment.

## HMMER and Pfam-compatible databases

The HMMER provider runs `hmmscan` against an external HMM library. Set the
executable, database path and identity, CPU count, search limits, and retained-
hit filters in a separate provider document:

```yaml
schema_version: 1
hmmer:
  enabled: true
  required: true
  executable: hmmscan
  database:
    path: /srv/databases/pfam/Pfam-A.hmm
    name: Pfam-A
    version: "36.0"
    kind: pfam
  execution:
    cpus: 4
    sequence_evalue: 10.0
    domain_evalue: 10.0
  filters:
    maximum_sequence_evalue: 1.0e-5
    maximum_domain_i_evalue: 1.0e-5
    minimum_bit_score: 20.0
    minimum_query_coverage: 0.05
```

Prepare a multi-model library with the `hmmpress` command supplied by HMMER:

```bash
hmmpress /srv/databases/pfam/Pfam-A.hmm
```

The database is not downloaded, copied into a run, or embedded in a container.
Its main file and available `.h3f`, `.h3i`, `.h3m`, and `.h3p` files are cache
inputs. Changing one invalidates a completed domain stage.

`execution` values control the external search. `filters` control which parsed
hits become canonical evidence. They are operational settings, not validated
EC-transfer thresholds. Each retained HMMER observation preserves the sequence
E-value, independent domain E-value, bit score, aligned query coordinates, and
query coverage `(end - start + 1) / query_length`.

## Optional InterProScan

`configs/tools/interpro.yaml` is a versioned InterProScan example. It controls
the executable, application list, CPU count, GO-term and pathway output, and
database release identity. `required: false` makes a missing or failed
InterProScan provider visible in `domain_summary.json` without failing a
successful required HMMER provider. `enabled: false` records the provider as
disabled and does not invoke it.

InterProScan TSV parsing preserves the member database, member signature,
InterPro accession, signature and InterPro descriptions, coordinates, score,
GO terms, and pathway annotations. These annotations are not converted into EC
predictions.

HMMER and InterProScan observations of the same underlying member signature at
the same query coordinates receive the same correlation group. They are
therefore not automatically independent biological confirmation merely because
two executables reported them.

## Family profiles

The `configs/schema/family.schema.json` contract contains all family-specific
biology. A domain rule declares:

- a provider (`pfam`, `hmmer`, or `interproscan`) and signature accession;
- `required`, `expected`, `supporting`, `optional`, or `forbidden` status;
- minimum and maximum occurrence counts;
- optional query-coverage and E-value limits;
- an optional integer `architecture_order`;
- optional candidate EC values for a future integration layer.

The Python evaluator contains no branches for named enzyme families. To add a
family, copy a valid family document, assign stable rule IDs and a version,
provide curated signatures and motif constraints, validate it against the
schema, and run it with `--family-config`.

## Catalytic motif syntax

A motif rule supports `pattern_type: literal` or `pattern_type: regex`.
Regular-expression character classes such as `[DE]` express amino-acid
alternatives. Matches may be overlapping. All reported coordinates are
one-based and inclusive.

```yaml
- rule_id: catalytic_region
  pattern_type: regex
  pattern: "N[DE]P"
  requirement: required
  min_occurrences: 1
  max_occurrences: 1
  overlapping: false
  coordinates:
    minimum_start: 40
    maximum_start: 80
  catalytic_residues:
    - name: catalytic_acid
      motif_offset: 1
      allowed_residues: DE
      expected_position: 62
      tolerance: 5
```

`motif_offset` is zero-based within the matched string; reported residue
positions are one-based sequence coordinates. Absolute coordinate ranges and
expected residue positions with a tolerance are supported. Sequence-start and
sequence-end relative positions are evaluated directly. Domain-relative motif
positions are represented by the contract but remain `domain_context_unavailable`
until a domain-to-motif context is supplied; they are never silently treated as
failed biological evidence.

`motif_relationships` express ordering and the minimum or maximum number of
residues between an upstream motif end and downstream motif start. Missing
required motifs in a sequence classified as complete can be negative evidence.
The same absence in a truncated sequence, outside the configured analyzed
region, for a disabled motif, or when required context is unavailable is
recorded as missing/not evaluated instead.

## Running the providers

Run HMMER domain evidence and motif analysis together:

```bash
enzynotation run proteins.fasta \
  --run-id domain-motif-example \
  --family-config examples/configs/gh32.example.yaml \
  --hmmer-config configs/tools/hmmer.yaml \
  --motifs
```

Add optional InterProScan evidence with:

```bash
  --interpro-config configs/tools/interpro.yaml
```

The family profile is required whenever a domain or motif provider is selected.
Provider configuration and family-profile content participate in the stage
cache signature.

## Outputs and failure states

The domain stage writes:

```text
stages/domains/raw/hmmer.domtblout
stages/domains/raw/interpro.tsv
stages/domains/normalized/domain_hits.tsv
stages/domains/normalized/domain_summary.json
evidence/domain_evidence.jsonl
```

The motif stage writes:

```text
stages/motifs/raw/motif_matches.jsonl
stages/motifs/normalized/motif_hits.tsv
stages/motifs/normalized/motif_summary.json
evidence/motif_evidence.jsonl
```

Summaries distinguish `not_configured`, `disabled`, `failed`, `empty`, and
`completed` provider states. A valid zero-hit search is `empty`, not failed.
Raw records remain separate from normalized TSV and canonical JSONL evidence.
Commands, tool versions, database name/version/checksums, input and
configuration checksums, raw-record locators, stdout, and stderr remain
traceable through stage state and evidence provenance.

## GH32 example limitations

`examples/configs/gh32.example.yaml` demonstrates Pfam domain rules plus the
nucleophile-containing `[WF]MNDPNG`, RDP, and acid/base glutamate-containing EC
regions described for GH32 proteins. The profile is deliberately uncalibrated.
These conserved regions may support GH32 family and catalytic compatibility,
but they do not by themselves distinguish invertase, endo-inulinase,
exo-inulinase, or levanase and do not establish a specific EC number.

The example cites its scientific sources directly in the YAML file. Thresholds,
sequence-completeness limits, and occurrence rules must be reviewed against a
curated benchmark before production interpretation.
