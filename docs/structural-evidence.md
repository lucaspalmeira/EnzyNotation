# Structural Evidence

Milestone 6 adds structural observations without making final functional
annotations. The implemented flow is:

```text
validated FASTA
      |
      v
supplied structure mapping
      |
      v
Foldseek broad search
      |
      v
retained structural candidates
      |
      v
selective TM-align
      |
      v
canonical evidence
```

No structure predictor is included. PDB and mmCIF structures created by an
external predictor can be supplied in exactly the same way as experimental or
other user-managed structures.

## Query structure manifest

Enable supplied structures with:

```bash
enzynotation run proteins.fasta \
  --run-id structural-example \
  --structures-config examples/configs/structures.example.yaml
```

The configuration points to a tab-separated manifest. Required columns are:

| Column | Meaning |
| --- | --- |
| `query_id` | Identifier in the validated FASTA |
| `structure_id` | Stable identifier for this supplied structure |
| `structure_path` | PDB or mmCIF path |
| `structure_format` | `pdb`, `cif`, or `mmcif` |
| `structure_source` | `user_supplied`, `experimentally_determined`, or `externally_predicted` |

Optional columns select `chain_id` and the one-based `model_index`, and may
record source/model versions, prediction method, expected sequence checksum,
and opaque quality metadata. See
`examples/configs/structure-manifest.example.tsv` for the complete header.

The structure stage checks that the file is readable, that its format matches
the suffix, and that the selected chain and model exist. It extracts a
CA-residue sequence where practical and reports one of `exact`,
`compatible_partial`, `compatible_extension`, `mismatch`, or `unavailable`.
A declared sequence checksum mismatch is also explicit. A mismatch is retained
as traceable quality information because unresolved residues, tags, terminal
losses, and modeled substitutions can be legitimate. EnzyNotation does not
repair residues, loops, protonation, or coordinates.

The normalized mapping is written to:

```text
stages/structures/normalized/structure_manifest.tsv
stages/structures/normalized/query/
stages/structures/normalized/structure_summary.json
```

## Foldseek runtime

For local/container execution, Foldseek uses the provider-specific Compose
adapter in `docker/foldseek.compose.yml`. The default image is the official
published release:

```text
ghcr.io/steineggerlab/foldseek:10-941cd33
```

The adapter uses `pull_policy: never`; normal execution never downloads an
image implicitly. Override the effective image in the provider YAML or with
`FOLDSEEK_IMAGE`. An environment value may include a digest:

```bash
export FOLDSEEK_IMAGE='ghcr.io/steineggerlab/foldseek:10-941cd33@sha256:...'
```

The effective image and digest are resolved before stage-signature generation,
so an override invalidates the cache and is preserved in provenance. The image
is not installed into EnzyNotation's Python environment. See the
[official Foldseek packages](https://github.com/steineggerlab/foldseek/pkgs/container/foldseek)
and [release history](https://github.com/steineggerlab/foldseek/releases).

Run the structural search with:

```bash
enzynotation run proteins.fasta \
  --run-id structural-example \
  --structures-config examples/configs/structures.example.yaml \
  --foldseek-config configs/tools/foldseek.yaml
```

The generated invocation is equivalent to:

```bash
docker compose -f docker/foldseek.compose.yml run --rm foldseek \
  easy-search /work/query /database/<database-name> \
  /work/output/foldseek.tsv /work/tmp \
  --format-output query,target,fident,alnlen,qstart,qend,tstart,tend,evalue,bits,qlen,tlen,alntmscore,qtmscore,ttmscore,lddt \
  --threads <cpus> -e <evalue> --max-seqs <max-seqs>
```

The query directory and database directory are mounted read-only. Output and
temporary directories are writable. Host and container paths remain execution
details and are never used as scientific identifiers.

On HPC, the same provider also accepts `execution.strategy: command`. Its
`prefix` may be empty for a native executable or contain an administrator
wrapper such as `apptainer exec /containers/foldseek.sif`. Command execution
uses the same output fields, parser, filters, evidence schema, correlation
groups, cache inputs, and scientific interpretation as the Compose strategy.

## External database and metadata

The Foldseek database remains external. Configuration records its directory,
database prefix/name, release/version, an optional fingerprint file, and a
separate metadata TSV. Large databases are neither downloaded nor committed.

Reference metadata supports:

```text
structure_id, chain_id, protein_accession, protein_name, ec_numbers,
annotation_status, source_database, database_version, structure_method,
sequence_accession, structure_path, structure_format, model_index
```

The actual file is tab-separated. EC values use semicolons and pass through the
same EC normalizer as every other provider, including multiple and partial ECs.
`structure_path` is optional for Foldseek evidence but required if that
reference is to be scheduled for TM-align. No EC is extracted from filenames,
PDB titles, target descriptions, or other free text. An unmapped hit remains a
valid structural-similarity observation.

## Foldseek parsing and filtering

The stage requests an explicit tabular field list documented by the
[official Foldseek output guide](https://github.com/steineggerlab/foldseek/blob/master/README.md?plain=1).
Raw `fident` is a 0-1 fraction and is normalized to `percent_identity` on a
0-100 scale. TM-like and lDDT metrics remain on their reported 0-1 scale.

Repeated rows for the same query/reference pair are retained through raw line
locators and aggregated with interval-union semantics:

```text
query_coverage  = union(query coordinate intervals)  / query_length
target_coverage = union(target coordinate intervals) / target_length
```

Both values are capped at 1.0. Overlapping alignments are never naively summed.
Ranking is deterministic by E-value, bit score, query coverage, query TM-like
score, and target identifier. Ranking is only for inspection and selective
TM-align scheduling.

Filters cover maximum E-value, minimum query/target coverage, minimum aligned
length, and maximum retained hits per query. Every criterion and rejection is
written to normalized output. `threshold_status` must be `operational`,
`externally_documented`, or `empirically_calibrated`. Shipped values are
operational defaults, not validated EC-transfer thresholds.

Outputs are:

```text
stages/foldseek/raw/foldseek.tsv
stages/foldseek/normalized/foldseek_hits.tsv
stages/foldseek/normalized/foldseek_config.json
stages/foldseek/normalized/foldseek_summary.json
evidence/foldseek_evidence.jsonl
```

## Selective TM-align

TM-align is an external executable, configurable from the default name
`TMalign` to an arbitrary path. It is never compiled, vendored, or put into a
new container by this milestone.

```bash
enzynotation run proteins.fasta \
  --run-id structural-example \
  --structures-config examples/configs/structures.example.yaml \
  --foldseek-config configs/tools/foldseek.yaml \
  --tmalign-config configs/tools/tmalign.yaml
```

Only retained Foldseek pairs satisfying configured query and target coverage
are scheduled. `top_hits_per_query` can limit the number or be `null`. TM-align
is never run as a query-by-database search.

Commands always orient the pair as:

```text
TMalign <EnzyNotation-query-structure> <reference-structure>
         Structure 1                    Structure 2
```

The parser uses TM-align's normalization labels rather than relying only on
line order. It preserves:

- `tm_score_normalized_by_query` (Structure 1 length);
- `tm_score_normalized_by_target` (Structure 2 length);
- aligned length;
- RMSD;
- aligned-sequence identity;
- query and target lengths and coverages.

This orientation follows the
[official TM-align help](https://www.zhanggroup.org/TM-align/help.html).
Outputs are under `stages/tmalign/{raw,normalized}/`, with canonical records at
`evidence/tmalign_evidence.jsonl`.

## Evidence and correlation

Each retained Foldseek pair emits a `structure_homology` observation. Explicit
reference EC metadata emits separate `curated_annotation` candidate records.
TM-align emits another `structure_homology` observation. For the same
query/reference/chain, every record shares a group such as:

```text
structural:q1:reference_001:A
```

Provider identities remain `foldseek` and `tmalign`, but these records describe
the same underlying relationship and are correlated. They are not independent
biological confirmations.

Provenance includes the raw artifact and checksum, parser version, command,
database identity/version/fingerprint, metadata checksum, configured Foldseek
release and image/digest, Docker/Compose/image inspection results, and TM-align
banner/version when obtainable. Structure, configuration, executable, database,
metadata, and selection changes participate in stage invalidation.

## Failure semantics and limitations

Missing structures, malformed files, missing chain/model selections, Docker or
image unavailability, missing databases/metadata, tool failures, malformed
outputs, unavailable references, and TM-align failures are distinguishable in
stage summaries. A successful Foldseek search with zero hits and a TM-align
stage with zero selected pairs are successful empty results, not failures.
Required and optional stages retain the shared workflow semantics.

The following scientific limits are non-negotiable:

- the first Foldseek hit never determines EC;
- structural similarity alone is insufficient for fine-grained EC assignment;
- a high TM-score does not imply identical enzyme function;
- the lowest RMSD is not automatically the correct function;
- Foldseek and TM-align for the same pair are correlated;
- reference ECs come only from explicit curated metadata;
- predicted-structure quality is not functional confidence;
- every structural summary keeps `final_ec_prediction` as `null`.

Only the future Milestone 7 integration layer may produce a final EC
annotation or confidence class.
