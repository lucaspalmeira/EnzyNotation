# External Scientific Resources

## Registry

`configs/databases.yaml` is an independent, versioned registry of databases,
metadata, structure collections, and model resources. It does not download,
update, modify, or package any resource. Its contract is
`configs/schema/databases.schema.json`.

Resources are family-agnostic and include:

- a stable `resource_id` and resource `type`;
- applicable providers;
- a named root plus relative or explicit absolute path;
- release/version and fingerprint strategy;
- required or optional status;
- optional functional metadata path;
- read-only expectation;
- source, license, and citation information.

Supported types are `blast_database`, `annotation_metadata`, `hmmer_database`,
`interpro_resource`, `foldseek_database`,
`structural_reference_directory`, and `model_resource`.

The shipped roots resolve from `ENZYNOTATION_DB_ROOT` and
`ENZYNOTATION_MODEL_ROOT`. In the Compose deployment they correspond to
`/databases` and `/models`. A relative path cannot escape its root. Explicit
absolute paths remain supported for native administrator-managed deployments.

## Example

```yaml
schema_version: 1
registry:
  roots:
    databases:
      environment_variable: ENZYNOTATION_DB_ROOT
      container_path: /databases
  resources:
    - resource_id: curated_blast_proteins
      type: blast_database
      providers: [blastp]
      root: databases
      path: blast/curated_proteins
      version: "2026_01"
      fingerprint:
        strategy: selected_file_checksums
        value: sha256:<administrator-computed-digest>
        selected_files: [curated_proteins.pin, curated_proteins.psq]
      required: true
      metadata_path: blast/curated_proteins.tsv
      read_only: true
      source: {name: local curated release, url: null}
      license: {name: source-dependent, url: null}
      citation: null
```

The registry is separate from provider configuration. It inventories resources;
provider YAML still defines the database path and scientific execution/filter
parameters. Registry verification never changes those parameters.

## Fingerprint Strategies

Verification is deliberately bounded:

- `explicit_version` compares the declared version with the configured value;
- `administrator_supplied_fingerprint` trusts a supplied stable identifier;
- `manifest_checksum` hashes one explicit release manifest;
- `selected_file_checksums` hashes only a configured file list and then hashes
  that deterministic mapping;
- `full_sha256_for_small_file` hashes one file only when it does not exceed the
  configured byte limit.

The verifier never recursively hashes a database directory. Missing fingerprint
manifests/files, fingerprint mismatches, and size-limit violations remain
explicit reasons in the manifest. Placeholder values `unknown` and
`replace-on-server` are never reported as verified fingerprints.

## Verification CLI

Print a deterministic manifest without writing into a database directory:

```bash
enzynotation databases verify --config configs/databases.yaml
```

Override host roots for one invocation:

```bash
enzynotation databases verify \
  --config configs/databases.yaml \
  --database-root /srv/enzynotation/databases \
  --model-root /srv/enzynotation/models
```

Write a manifest to an explicit destination:

```bash
enzynotation databases manifest \
  --config configs/databases.yaml \
  --output /srv/enzynotation/manifests/databases-manifest.json
```

The command exits with `1` when a required resource is invalid or missing. An
absent optional resource is `missing_optional`; a present resource with invalid
metadata or fingerprint is `invalid`. Neither makes verification fail when the
resource is optional. Configuration/schema errors exit with `2` through the
standard CLI error handling.

## Manifest

`databases-manifest.json` is deterministic and contains registry/schema
checksums, resolved roots, and one result per resource:

```json
{
  "resource_id": "curated_blast_proteins",
  "expected_path": "blast/curated_proteins",
  "resolved_path": "/srv/enzynotation/databases/blast/curated_proteins",
  "type": "blast_database",
  "required": true,
  "exists": true,
  "configured_version": "2026_01",
  "fingerprint": {
    "strategy": "selected_file_checksums",
    "configured": "sha256:...",
    "observed": "sha256:..."
  },
  "validation_status": "valid",
  "reason": null
}
```

The manifest may be stored with run provenance later, but it need not live
inside a read-only database directory.

## Resource Preparation

Reuse `scripts/setup_blast_database.sh` for explicitly supplied BLAST FASTA
input. Milestone 9 adds no automatic downloads or background updater. Swiss-
Prot, Pfam, InterPro, PDB, AlphaFold, Foldseek databases, CLEAN models, and ESM
weights must be acquired deliberately by the server administrator under their
respective licenses and citation requirements.
