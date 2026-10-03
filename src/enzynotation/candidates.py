"""EC hierarchy and correlation-aware candidate scorecards."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from enzynotation.ec import ECNumber, parse_ec
from enzynotation.evidence import EvidenceRecord


def ec_parents(value: str) -> tuple[str, ...]:
    """Return specified parents from broadest to immediate parent."""

    ec = parse_ec(value)
    return tuple(
        str(ECNumber(ec.parts[:depth] + ("-",) * (4 - depth)))
        for depth in range(1, ec.depth)
    )


def ec_is_ancestor(ancestor: str, descendant: str) -> bool:
    """Whether ``ancestor`` contains ``descendant`` in the EC hierarchy."""

    left, right = parse_ec(ancestor), parse_ec(descendant)
    return (
        left.depth <= right.depth
        and left.parts[: left.depth] == right.parts[: left.depth]
    )


def common_ec_parent(values: list[str] | tuple[str, ...]) -> str | None:
    """Return the deepest shared EC parent, or ``None`` across top-level classes."""

    if not values:
        return None
    parsed = [parse_ec(value) for value in values]
    depth = 0
    for index in range(4):
        parts = {ec.parts[index] for ec in parsed}
        if len(parts) != 1 or "-" in parts:
            break
        depth += 1
    if depth == 0:
        return None
    parts = parsed[0].parts[:depth] + ("-",) * (4 - depth)
    return str(ECNumber(parts))


def independent_class_matching(records: list[EvidenceRecord]) -> tuple[str, ...]:
    """Match classes to distinct correlation groups without double counting."""

    edges: dict[str, set[str]] = defaultdict(set)
    for record in records:
        edges[record.correlation_group].add(record.evidence_class)
    matched_group: dict[str, str] = {}

    def assign(group: str, visited: set[str]) -> bool:
        for evidence_class in sorted(edges[group]):
            if evidence_class in visited:
                continue
            visited.add(evidence_class)
            previous = matched_group.get(evidence_class)
            if previous is None or assign(previous, visited):
                matched_group[evidence_class] = group
                return True
        return False

    for group in sorted(edges):
        assign(group, set())
    return tuple(sorted(matched_group))


@dataclass(slots=True)
class CandidateScorecard:
    """Traceable, non-numeric summary for one explicit or inherited candidate."""

    candidate_ec: str
    depth: int
    completeness: str
    parent_hierarchy: tuple[str, ...]
    supporting_records: list[EvidenceRecord] = field(default_factory=list)
    contradicting_records: list[EvidenceRecord] = field(default_factory=list)
    rule_evaluations: list[dict[str, Any]] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)
    rejection_reasons: list[str] = field(default_factory=list)
    status: str = "insufficient_support"
    inherited: bool = False

    @property
    def supporting_groups(self) -> tuple[str, ...]:
        return tuple(
            sorted({item.correlation_group for item in self.supporting_records})
        )

    @property
    def supporting_classes(self) -> tuple[str, ...]:
        return tuple(sorted({item.evidence_class for item in self.supporting_records}))

    @property
    def independent_classes(self) -> tuple[str, ...]:
        return independent_class_matching(self.supporting_records)

    @property
    def providers(self) -> tuple[str, ...]:
        return tuple(sorted({item.source_id for item in self.supporting_records}))

    def provider_summary(self) -> dict[str, dict[str, Any]]:
        """Summarize records and groups per provider without assigning weights."""

        providers = sorted(
            {
                item.source_id
                for item in self.supporting_records + self.contradicting_records
            }
        )
        return {
            provider: {
                "supporting_evidence_ids": sorted(
                    item.evidence_id
                    for item in self.supporting_records
                    if item.source_id == provider
                ),
                "contradicting_evidence_ids": sorted(
                    item.evidence_id
                    for item in self.contradicting_records
                    if item.source_id == provider
                ),
                "correlation_groups": sorted(
                    {
                        item.correlation_group
                        for item in self.supporting_records + self.contradicting_records
                        if item.source_id == provider
                    }
                ),
            }
            for provider in providers
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_ec": self.candidate_ec,
            "depth": self.depth,
            "completeness": self.completeness,
            "parent_hierarchy": list(self.parent_hierarchy),
            "inherited_from_complete_candidate": self.inherited,
            "supporting_evidence_ids": sorted(
                item.evidence_id for item in self.supporting_records
            ),
            "contradicting_evidence_ids": sorted(
                item.evidence_id for item in self.contradicting_records
            ),
            "supporting_evidence_classes": list(self.supporting_classes),
            "independent_evidence_classes": list(self.independent_classes),
            "supporting_correlation_groups": list(self.supporting_groups),
            "contradicting_correlation_groups": sorted(
                {item.correlation_group for item in self.contradicting_records}
            ),
            "provider_summary": self.provider_summary(),
            "required_constraints": [
                value["rule_id"]
                for value in self.rule_evaluations
                if value.get("requirement") in {"required", "required_any"}
            ],
            "passed_constraints": [
                value["rule_id"]
                for value in self.rule_evaluations
                if value.get("outcome") == "pass"
            ],
            "failed_constraints": [
                value["rule_id"]
                for value in self.rule_evaluations
                if value.get("outcome") in {"fail", "unknown"}
            ],
            "required_rule_results": [
                value
                for value in self.rule_evaluations
                if value.get("requirement") in {"required", "required_any"}
            ],
            "optional_rule_results": [
                value
                for value in self.rule_evaluations
                if value.get("requirement") not in {"required", "required_any"}
            ],
            "conflicts": sorted(set(self.conflicts)),
            "rejection_reasons": sorted(set(self.rejection_reasons)),
            "confidence_features": {
                "supporting_record_count": len(self.supporting_records),
                "independent_class_count": len(self.independent_classes),
                "correlation_group_count": len(self.supporting_groups),
                "conflicting_class_count": len(
                    {item.evidence_class for item in self.contradicting_records}
                ),
            },
            "eligibility_status": self.status,
            "final_candidate_status": self.status,
        }


def build_candidate_scorecards(
    records: tuple[EvidenceRecord, ...], *, inherit_complete_to_parents: bool
) -> dict[str, CandidateScorecard]:
    """Build candidates only from explicit canonical EC assertions."""

    explicit = sorted(
        {
            record.candidate_ec
            for record in records
            if record.candidate_ec is not None
            and record.record_status in {"observed", "negative"}
        }
    )
    candidates = set(explicit)
    inherited: set[str] = set()
    if inherit_complete_to_parents:
        for candidate in explicit:
            if parse_ec(candidate).is_complete:
                parents = ec_parents(candidate)
                candidates.update(parents)
                inherited.update(parents)

    result: dict[str, CandidateScorecard] = {}
    for candidate in sorted(
        candidates, key=lambda value: (parse_ec(value).depth, value)
    ):
        parsed = parse_ec(candidate)
        supporting: list[EvidenceRecord] = []
        contradicting: list[EvidenceRecord] = []
        for record in records:
            asserted = record.candidate_ec
            if asserted is None:
                continue
            applies = asserted == candidate or (
                inherit_complete_to_parents
                and parse_ec(asserted).is_complete
                and ec_is_ancestor(candidate, asserted)
            )
            if not applies:
                continue
            if record.effect == "supports" and record.record_status == "observed":
                supporting.append(record)
            elif record.effect == "contradicts":
                contradicting.append(record)
        result[candidate] = CandidateScorecard(
            candidate_ec=candidate,
            depth=parsed.depth,
            completeness="complete" if parsed.is_complete else "partial",
            parent_hierarchy=ec_parents(candidate),
            supporting_records=supporting,
            contradicting_records=contradicting,
            inherited=candidate in inherited and candidate not in explicit,
        )
    return result
