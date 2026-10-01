# BLASTp Evidence Provider

## Scope and scientific role

The Milestone 3 BLASTp stage is an evidence provider. It detects sequence
homology, joins hits to an explicit accession metadata file, and emits
canonical evidence records. It does not choose an EC number. Hit rank is only
an inspection order, and neither the first hit nor sequence similarity alone
is a final annotation.

Each retained hit produces `sequence_homology` evidence. When its accession has
explicitly supplied EC metadata, each EC also produces a separate
`curated_annotation` candidate record. These records share a correlation group
for that reference hit, so they cannot be counted as independent confirmation.
Unmapped hits remain valid homology evidence and are marked
`metadata_mapped: false`; EnzyNotation does not invent metadata or scrape BLAST
description text.

## Requirements and database setup

Install NCBI BLAST+ so `blastp` and `makeblastdb` are available, or configure an
absolute `blast.executable` path. Databases remain outside the repository and
containers. Build a protein database from an already curated FASTA with:

```bash
scripts/setup_blast_database.sh \
  /external/data/curated-proteins.fasta \
  /external/blast/curated-proteins \
  "Curated proteins 2026-03"
```

Set `MAKEBLASTDB_BIN` when `makeblastdb` is not on `PATH`. The helper uses
`-dbtype prot -parse_seqids`; it does not download data or create functional
metadata.

For a Swiss-Prot-derived database, obtain a release from the official UniProt
distribution, retain the desired reviewed entries, build the FASTA with stable
accessions, and export a separate release-matched metadata file. Record the
actual UniProt release in both `database.version` and each metadata row. The
same interface also supports custom curated enzyme sets and local databases.

## Functional metadata

Metadata is independent from the BLAST database and may be TSV, CSV, or JSON.
Delimited files require these headers:

```text
accession  protein_name  ec_numbers  annotation_status  source_database  database_version  curation_status
```

`curation_status` is optional. In TSV/CSV, multiple EC values are separated by
semicolon or comma. In JSON, use a list of records or `{"records": [...]}` and
represent `ec_numbers` as a string or list. Empty EC values, one EC, multiple
ECs, and hierarchical partial ECs such as `2.3.-.-` are supported. EC values
are normalized by the common EnzyNotation EC parser.

Common identifiers such as `sp|P12345|NAME`, `tr|Q12345|NAME`,
`ref|NP_123.1|`, and `UniProtKB:P12345` are matched to their accession. A
versionless metadata accession may match a versioned subject accession. The
original BLAST subject ID is always preserved.

## Configuration

Copy and edit `configs/tools/blast.yaml`. All paths and execution/filter values
are explicit:

```yaml
schema_version: 1
blast:
  required: true
  thresholds_validated: false
  executable: blastp
  database:
    path: /external/blast/curated-proteins
    name: swissprot-curated
    version: 2026_03
  metadata:
    path: /external/metadata/swissprot-2026_03.tsv
  execution:
    cpus: 8
    evalue: 1.0e-5
    max_target_sequences: 500
  filters:
    maximum_evalue: 1.0e-5
    minimum_query_coverage: 0.0
    minimum_subject_coverage: 0.0
    minimum_percent_identity: 0.0
    minimum_aligned_length: 1
    maximum_retained_hits_per_query: null
```

Coverage thresholds are fractions from `0` to `1`; percent identity is from
`0` to `100`. The permissive repository values are operational reporting
defaults, not validated EC-transfer thresholds. Set
`thresholds_validated: true` only when the configured thresholds have actually
been validated for the intended scientific use.

Run validation followed by BLAST evidence collection:

```bash
enzynotation run proteins.fasta \
  --run-id blast-example \
  --blast-config configs/tools/blast.yaml
```

Without `--blast-config`, `enzynotation run` remains validation-only. The
`required` flag selects the existing required/optional stage failure behavior.

## Raw fields and aggregation

BLASTp is invoked without a shell using explicit outfmt 6 fields:

```text
qseqid sseqid pident length mismatch gapopen qstart qend sstart send evalue bitscore qlen slen
```

All raw HSP rows are preserved. HSPs for one query/subject pair are aggregated
for reporting as follows:

- query and subject coverage are the unions of their inclusive HSP coordinate
  intervals divided by `qlen` and `slen` respectively;
- overlapping HSPs therefore cannot inflate coverage beyond 100%;
- percent identity is the alignment-length-weighted HSP mean;
- aligned length is the query-coordinate union length;
- ranking uses the minimum E-value and maximum bit score;
- HSP count, raw line numbers, summed HSP alignment length, and summed bit score
  remain available for future alternative aggregation policies.

After aggregation, hits are deterministically ranked per query by E-value
ascending, bit score descending, query coverage descending, percent identity
descending, and subject ID ascending. All configured thresholds and the
per-query retention limit are recorded as pass/fail criteria. Rank does not
imply functional assignment.

## Outputs and provenance

The stage writes:

```text
results/<run_id>/stages/blast/raw/blast_hits.tsv
results/<run_id>/stages/blast/normalized/blast_hits.tsv
results/<run_id>/stages/blast/normalized/blast_summary.json
results/<run_id>/stages/blast/normalized/blast_config.json
results/<run_id>/evidence/blast_evidence.jsonl
logs/<run_id>/blast/stdout.log
logs/<run_id>/blast/stderr.log
```

The parsed TSV contains both retained and filtered aggregated hits with filter
reasons. JSONL contains only retained evidence and validates against
`configs/schema/evidence.schema.json`. Provenance includes the exact BLAST
command, BLAST+ version, normalized-input checksum, database identity/version
and checksum, metadata identity/version and checksum when mapped, raw artifact
checksum, and raw line locator.

Database artifacts, metadata, normalized FASTA, and provider configuration all
participate in the stage signature. An unchanged completed stage is reused;
changing any of them reruns BLAST deterministically and archives prior outputs.

An empty successful search is recorded as a completed empty result, not as
negative biological evidence. Missing executables and command errors, malformed
databases, malformed raw output, and malformed metadata are failed execution or
normalization states. Missing database or metadata inputs fail during stage
preparation. No condition in this provider produces a final EC prediction.
