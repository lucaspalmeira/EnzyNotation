# Adding Enzyme Families

New families and EC rules are configuration additions. They must not require a
Python branch in the core pipeline.

## Family profile

Start from `examples/configs/family.example.yaml` and choose a stable family
identifier. Declare only literature-supported, reviewable features:

```yaml
schema_version: 1
family:
  id: synthetic_hydrolase_example
  name: Synthetic hydrolase example
  domains:
    - id: catalytic_core
      accessions: [PF00000]
      requirement: required
      minimum_count: 1
      maximum_count: 1
  motifs:
    - id: catalytic_region
      requirement: required
      pattern:
        type: regex
        value: "D[A-Z]E"
      residues:
        - {name: catalytic_aspartate, offset: 1, amino_acids: [D]}
```

Use required, expected, optional, and forbidden domain rules carefully. Motif
coordinates, ordering, distances, residue alternatives, truncation behavior,
and tolerances belong in the profile rather than code.

## EC rules

Copy `examples/configs/ec-rules.example.yaml`. Each rule should name its
candidate EC, required/forbidden features, failure behavior, provenance, and
ruleset version. Partial ECs are valid when only a hierarchy level is supported.

## Validation

1. Validate the family and EC-rule documents against their schemas.
2. Add compact positive, absent, forbidden, truncated, and conflict fixtures.
3. Test domain/motif coordinates and named residues.
4. Test integration with correlated records so one biological relationship is
   not counted twice.
5. Include an unresolved case and document known limitations.

Family compatibility does not by itself establish substrate specificity or a
fine-grained EC. Confidence remains heuristic until curated benchmark
calibration is performed.

