# EnzyNotation Scientific Policy

## Purpose

EnzyNotation supports enzyme annotation by combining traceable observations.
It does not treat any single software output as biological truth. This policy
applies to local, containerized, and cluster execution and cannot be relaxed by
an execution backend.

## Non-negotiable inference rules

1. The first BLAST hit must never directly determine the final EC number.
2. Sequence similarity alone is not sufficient for fine-grained EC assignment.
3. CLEAN, BLASTp, domains, catalytic motifs, Foldseek, and TM-align are evidence
   providers. They produce observations and candidates, not final annotations.
4. The integration layer is the only component allowed to make a final EC
   prediction or confidence classification.
5. Correlated evidence must not be counted as independent confirmation.
6. Conflicting evidence must remain visible in normalized output and reports,
   including when a configured rule resolves the final decision.
7. Confidence values are heuristic until calibrated against a curated
   benchmark. Reports must identify the ruleset and calibration status.

InterProScan and HMMER/Pfam are likewise evidence providers under rule 3.

## Homology evidence

BLASTp results must retain all hits passing configured reporting thresholds,
not only the first hit. Identity, query coverage, subject coverage, E-value,
bit score, annotation source, curation status, database version, and alignment
context are separate facts. High identity cannot compensate silently for poor
coverage, and a descriptive protein name is not equivalent to a curated EC
mapping.

Multiple hits do not automatically constitute independent evidence. Paralogs,
duplicate database entries, and annotations copied from the same source may all
share one correlation group. Subject-to-EC associations should come from
explicit, versioned metadata rather than free-text description parsing whenever
possible.

## Learned sequence predictions

CLEAN predictions retain ranked alternatives, scores, model version, and model
data version. CLEAN is not a substitute for domain, catalytic, structural, or
curated evidence. A CLEAN and BLASTp agreement may still be correlated when
their labels derive from the same reference data; the configured correlation
policy decides whether they contribute independent confirmation.

## Domains and motifs

Domain architecture and catalytic motifs are evaluated through family
configuration. A motif match supports a biological statement only when its
pattern, context, coordinate constraint, and expected domain architecture are
appropriate. Short sequence patterns without context are weak evidence and may
occur by chance.

Required-feature absence becomes negative evidence only when the relevant test
completed successfully, had suitable coverage and sensitivity, and a versioned
rule defines the absence as informative. Otherwise it is missing or
inconclusive evidence.

## Structural evidence

Foldseek is used for structural-neighbor discovery and TM-align for configured
pairwise structural comparison. Results against the same reference structure
are correlated unless policy explicitly establishes a scientifically defensible
independent basis. TM-score direction, aligned length, query/reference coverage,
RMSD, structure origin, structure quality, and database version must be
preserved separately.

Predicted and experimentally determined structures must be distinguishable.
Structure-prediction confidence and model provenance must remain visible and
may constrain how structural evidence contributes to an EC assignment.

## Complete and partial EC predictions

Evidence for `1.2.3.-` is not evidence for every complete EC below that node.
Partial candidates remain partial. A complete EC prediction requires rules and
evidence that distinguish it at all four levels. When evidence supports only a
broader class, the pipeline reports that partial class or remains unresolved
according to the applicable EC rule.

Multifunctional proteins may legitimately retain multiple EC candidates. The
integration layer must not force a single winner when compatible multiple
activities remain supported.

## Independence and correlation

Independence is a scientific judgment encoded in versioned configuration, not a
count of tools. Evidence is presumed correlated when it shares decisive input,
reference labels, model training lineage, database annotation, or reference
structure. Distinct source IDs or executables are insufficient to establish
independence.

High confidence requires the configured number of independent evidence classes
and groups, not merely several records. Repeated observations within one
correlation group can improve robustness within that group but cannot satisfy a
cross-class independence gate.

## Conflict handling

The integration layer evaluates every retained candidate against supporting,
contradicting, required, and forbidden evidence. It emits typed conflicts with
the evidence and rules involved. Major unresolved conflicts produce the final
state `unresolved`; provider rank or arbitrary source priority cannot silently
break a tie.

If a configured rule resolves a conflict, the report records both the conflict
and the resolution rule. Resolution never deletes contrary evidence.

## Absence, failure, and negative evidence

These states are scientifically distinct:

- `missing`: the observation is unavailable because a provider was disabled,
  input or database was absent, or the stage was not applicable;
- `failed`: execution or parsing was attempted but produced no interpretable
  result;
- `negative`: a successfully completed, sufficiently sensitive test produced
  interpretable evidence against an explicit target.

Missing and failed evidence cannot be converted into negative evidence or used
to increase confidence. Tool failure must remain visible in reports. Optional
provider failure may allow a run to continue, but the confidence policy must
evaluate the resulting evidence gap explicitly.

## Confidence and calibration

`high`, `medium`, `low`, and `unresolved` are transparent rule outcomes. Each
report must expose the rules, thresholds, independent groups, conflicts, and
calibration status used. Provider-native scores are not probabilities unless
the provider documents and validates that interpretation.

Until the ruleset is evaluated against a versioned, curated benchmark, all
confidence categories are labeled heuristic. Calibration documentation must
describe benchmark composition, train/test leakage controls, family coverage,
EC resolution, metrics, error analysis, and ruleset version. Calibration for one
family or evidence regime must not be generalized silently to another.

## Reproducibility

Scientific outputs record normalized input digests, configuration digest,
parser version, tool and container versions, database releases, raw-artifact
checksums, and rule-set version. Unknown versions are explicit limitations.
Changing data, thresholds, models, parser behavior, or rules creates a new run
and never rewrites the provenance of a completed run.

