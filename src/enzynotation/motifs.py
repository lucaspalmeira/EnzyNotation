"""Generic, configuration-driven catalytic motif analysis."""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass

from enzynotation.family import (
    CatalyticResidue,
    FamilyProfile,
    MotifRelationship,
    MotifRule,
)
from enzynotation.models import ProteinRecord


@dataclass(frozen=True, slots=True)
class ResidueMatch:
    """Observed named catalytic residue inside one motif match."""

    name: str
    residue: str | None
    position: int | None
    allowed_residue: bool
    expected_position: int | None
    tolerance: int
    position_satisfied: bool


@dataclass(frozen=True, slots=True)
class MotifHit:
    """One literal or regular-expression motif match."""

    query_id: str
    rule_id: str
    start: int
    end: int
    matched_sequence: str
    occurrence: int
    coordinate_satisfied: bool
    position_satisfied: bool | None
    residues_satisfied: bool
    qualifying: bool
    residues: tuple[ResidueMatch, ...]


@dataclass(frozen=True, slots=True)
class MotifEvaluation:
    """Outcome of one motif rule for one query."""

    query_id: str
    sequence_length: int
    completeness: str
    analyzed_start: int
    analyzed_end: int
    rule: MotifRule
    raw_match_count: int
    qualifying_count: int
    satisfied: bool
    reason_code: str


@dataclass(frozen=True, slots=True)
class RelationshipEvaluation:
    """Ordering/distance evaluation between two motif rules."""

    query_id: str
    relationship: MotifRelationship
    satisfied: bool | None
    observed_distance: int | None
    reason_code: str


@dataclass(frozen=True, slots=True)
class MotifAnalysisResult:
    """All motif hits and rule evaluations for a family profile."""

    hits: tuple[MotifHit, ...]
    evaluations: tuple[MotifEvaluation, ...]
    relationships: tuple[RelationshipEvaluation, ...]


def _compiled(rule: MotifRule) -> re.Pattern[str]:
    pattern = (
        re.escape(rule.pattern) if rule.pattern_type == "literal" else rule.pattern
    )
    return re.compile(pattern)


def _iter_matches(expression: re.Pattern[str], sequence: str, *, overlapping: bool):
    if not overlapping:
        yield from expression.finditer(sequence)
        return
    for offset in range(len(sequence)):
        match = expression.match(sequence, offset)
        if match is not None:
            yield match


def _coordinate_satisfied(rule: MotifRule, start: int, end: int) -> bool:
    constraint = rule.coordinates
    if constraint is None:
        return True
    return all(
        (
            constraint.minimum_start is None or start >= constraint.minimum_start,
            constraint.maximum_start is None or start <= constraint.maximum_start,
            constraint.minimum_end is None or end >= constraint.minimum_end,
            constraint.maximum_end is None or end <= constraint.maximum_end,
        )
    )


def _position_satisfied(
    rule: MotifRule, *, start: int, sequence_length: int
) -> bool | None:
    constraint = rule.position
    if constraint is None:
        return True
    if constraint.relative_to == "sequence_start":
        offset = start - 1
    elif constraint.relative_to == "sequence_end":
        offset = start - sequence_length
    else:
        return None
    return constraint.min_offset <= offset <= constraint.max_offset


def _residue_match(
    constraint: CatalyticResidue,
    *,
    matched_sequence: str,
    motif_start: int,
) -> ResidueMatch:
    index = constraint.motif_offset
    if index >= len(matched_sequence):
        return ResidueMatch(
            name=constraint.name,
            residue=None,
            position=None,
            allowed_residue=False,
            expected_position=constraint.expected_position,
            tolerance=constraint.tolerance,
            position_satisfied=False,
        )
    residue = matched_sequence[index]
    position = motif_start + index
    position_satisfied = (
        constraint.expected_position is None
        or abs(position - constraint.expected_position) <= constraint.tolerance
    )
    return ResidueMatch(
        name=constraint.name,
        residue=residue,
        position=position,
        allowed_residue=residue in constraint.allowed_residues,
        expected_position=constraint.expected_position,
        tolerance=constraint.tolerance,
        position_satisfied=position_satisfied,
    )


def _expected_window_outside_region(
    rule: MotifRule,
    *,
    region_start: int,
    region_end: int,
    sequence_length: int,
) -> bool:
    coordinate = rule.coordinates
    if coordinate is not None:
        earliest = coordinate.minimum_start or 1
        latest = coordinate.maximum_start or sequence_length
        if latest < region_start or earliest > region_end:
            return True
    position = rule.position
    if position is None:
        return False
    if position.relative_to == "sequence_start":
        earliest = position.min_offset + 1
        latest = position.max_offset + 1
    elif position.relative_to == "sequence_end":
        earliest = sequence_length + position.min_offset
        latest = sequence_length + position.max_offset
    else:
        return False
    return latest < region_start or earliest > region_end


def _absence_reason(
    *,
    rule: MotifRule,
    completeness: str,
    region_start: int,
    region_end: int,
    sequence_length: int,
    raw_hits: tuple[MotifHit, ...],
) -> str:
    if not rule.enabled:
        return "motif_not_evaluated"
    if rule.position is not None and rule.position.relative_to.startswith("domain_"):
        return "domain_context_unavailable"
    if _expected_window_outside_region(
        rule,
        region_start=region_start,
        region_end=region_end,
        sequence_length=sequence_length,
    ):
        return "motif_outside_analyzed_region"
    if completeness == "truncated":
        return "sequence_truncated"
    if completeness == "unknown":
        return "sequence_completeness_unknown"
    if completeness == "outside_expected_length":
        return "sequence_length_outside_expected_range"
    if raw_hits and any(not hit.coordinate_satisfied for hit in raw_hits):
        return "motif_outside_expected_position"
    if raw_hits and any(not hit.residues_satisfied for hit in raw_hits):
        return "catalytic_residue_constraint_failed"
    return "motif_absent_complete_sequence"


def _rule_hits(
    record: ProteinRecord,
    rule: MotifRule,
    *,
    region_start: int,
    region_end: int,
) -> tuple[MotifHit, ...]:
    if not rule.enabled or region_start > region_end:
        return ()
    region = record.sequence[region_start - 1 : region_end]
    expression = _compiled(rule)
    hits: list[MotifHit] = []
    for occurrence, match in enumerate(
        _iter_matches(expression, region, overlapping=rule.overlapping), start=1
    ):
        if match.end() == match.start():
            continue
        start = region_start + match.start()
        end = region_start + match.end() - 1
        matched = match.group(0)
        coordinate_ok = _coordinate_satisfied(rule, start, end)
        position_ok = _position_satisfied(
            rule, start=start, sequence_length=len(record.sequence)
        )
        residues = tuple(
            _residue_match(
                residue,
                matched_sequence=matched,
                motif_start=start,
            )
            for residue in rule.catalytic_residues
        )
        residues_ok = all(
            residue.allowed_residue and residue.position_satisfied
            for residue in residues
        )
        hits.append(
            MotifHit(
                query_id=record.identifier,
                rule_id=rule.rule_id,
                start=start,
                end=end,
                matched_sequence=matched,
                occurrence=occurrence,
                coordinate_satisfied=coordinate_ok,
                position_satisfied=position_ok,
                residues_satisfied=residues_ok,
                qualifying=coordinate_ok and position_ok is True and residues_ok,
                residues=residues,
            )
        )
    return tuple(hits)


def _evaluate_rule(
    record: ProteinRecord,
    rule: MotifRule,
    hits: tuple[MotifHit, ...],
    *,
    profile: FamilyProfile,
    region_start: int,
    region_end: int,
) -> MotifEvaluation:
    qualifying = sum(hit.qualifying for hit in hits)
    completeness = profile.sequence_analysis.completeness(len(record.sequence))
    within_count = qualifying >= rule.min_occurrences and (
        rule.max_occurrences is None or qualifying <= rule.max_occurrences
    )
    if rule.requirement == "forbidden":
        satisfied = qualifying == 0
        reason = "forbidden_motif_absent" if satisfied else "forbidden_motif_present"
    elif qualifying == 0:
        satisfied = rule.min_occurrences == 0
        reason = _absence_reason(
            rule=rule,
            completeness=completeness,
            region_start=region_start,
            region_end=region_end,
            sequence_length=len(record.sequence),
            raw_hits=hits,
        )
    elif within_count:
        satisfied = True
        reason = "motif_present"
    else:
        satisfied = False
        reason = "motif_occurrence_constraint_failed"
    return MotifEvaluation(
        query_id=record.identifier,
        sequence_length=len(record.sequence),
        completeness=completeness,
        analyzed_start=region_start,
        analyzed_end=region_end,
        rule=rule,
        raw_match_count=len(hits),
        qualifying_count=qualifying,
        satisfied=satisfied,
        reason_code=reason,
    )


def _evaluate_relationship(
    query_id: str,
    relationship: MotifRelationship,
    hits_by_rule: dict[str, tuple[MotifHit, ...]],
) -> RelationshipEvaluation:
    upstream = tuple(
        hit
        for hit in hits_by_rule.get(relationship.upstream_motif, ())
        if hit.qualifying
    )
    downstream = tuple(
        hit
        for hit in hits_by_rule.get(relationship.downstream_motif, ())
        if hit.qualifying
    )
    if not upstream or not downstream:
        return RelationshipEvaluation(
            query_id,
            relationship,
            None,
            None,
            "relationship_not_evaluated_missing_motif",
        )
    distances = [
        right.start - left.end - 1
        for left in upstream
        for right in downstream
        if right.start > left.end
    ]
    if not distances:
        return RelationshipEvaluation(
            query_id,
            relationship,
            False,
            None,
            "motif_order_failed",
        )
    valid = [
        distance
        for distance in distances
        if (
            relationship.minimum_distance is None
            or distance >= relationship.minimum_distance
        )
        and (
            relationship.maximum_distance is None
            or distance <= relationship.maximum_distance
        )
    ]
    if valid:
        return RelationshipEvaluation(
            query_id,
            relationship,
            True,
            min(valid),
            "motif_relationship_satisfied",
        )
    return RelationshipEvaluation(
        query_id,
        relationship,
        False,
        min(distances),
        "motif_distance_failed",
    )


def analyze_motifs(
    records: tuple[ProteinRecord, ...], profile: FamilyProfile
) -> MotifAnalysisResult:
    """Analyze all configured motifs without making a functional prediction."""

    all_hits: list[MotifHit] = []
    evaluations: list[MotifEvaluation] = []
    relationships: list[RelationshipEvaluation] = []
    for record in records:
        region_start = profile.sequence_analysis.region_start
        configured_end = profile.sequence_analysis.region_end
        region_end = min(configured_end or len(record.sequence), len(record.sequence))
        hits_by_rule: dict[str, tuple[MotifHit, ...]] = defaultdict(tuple)
        for rule in profile.motifs:
            hits = _rule_hits(
                record,
                rule,
                region_start=region_start,
                region_end=region_end,
            )
            hits_by_rule[rule.rule_id] = hits
            all_hits.extend(hits)
            evaluations.append(
                _evaluate_rule(
                    record,
                    rule,
                    hits,
                    profile=profile,
                    region_start=region_start,
                    region_end=region_end,
                )
            )
        relationships.extend(
            _evaluate_relationship(record.identifier, relationship, hits_by_rule)
            for relationship in profile.motif_relationships
        )
    return MotifAnalysisResult(
        hits=tuple(all_hits),
        evaluations=tuple(evaluations),
        relationships=tuple(relationships),
    )
