"""Heuristic categorical confidence gates for integrated EC candidates."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from enzynotation.candidates import CandidateScorecard


def gate_passes(
    scorecard: CandidateScorecard,
    gate: Mapping[str, Any],
    *,
    major_conflicts: int,
    minor_conflicts: int,
) -> bool:
    """Evaluate an explicit confidence gate without numerical weighting."""

    if scorecard.completeness not in gate["allowed_completeness"]:
        return False
    if len(scorecard.independent_classes) < gate["minimum_independent_classes"]:
        return False
    if len(scorecard.supporting_groups) < gate["minimum_correlation_groups"]:
        return False
    if len(scorecard.supporting_records) < gate.get("minimum_supporting_records", 1):
        return False
    if not set(gate["required_evidence_classes"]).issubset(
        scorecard.supporting_classes
    ):
        return False
    passed_rules = {
        item["rule_id"]
        for item in scorecard.rule_evaluations
        if item["outcome"] == "pass"
    }
    if not set(gate.get("required_rule_ids", ())).issubset(passed_rules):
        return False
    if major_conflicts > gate["maximum_major_conflicts"]:
        return False
    if minor_conflicts > gate["maximum_minor_conflicts"]:
        return False
    conflicting_classes = {
        item.evidence_class for item in scorecard.contradicting_records
    }
    if len(conflicting_classes) > gate.get("maximum_conflicting_classes", 0):
        return False
    return True


def classify_confidence(
    scorecard: CandidateScorecard | None,
    policy: Mapping[str, Any],
    *,
    major_conflicts: int = 0,
    minor_conflicts: int = 0,
) -> tuple[str, list[str]]:
    """Return high/medium/low/unresolved plus stable explanatory reasons."""

    if scorecard is None:
        return "unresolved", ["no_eligible_candidate"]
    if scorecard.status not in {"eligible", "conflicted"}:
        return "unresolved", ["candidate_not_eligible"]
    for category in ("high", "medium", "low"):
        if gate_passes(
            scorecard,
            policy[category],
            major_conflicts=major_conflicts,
            minor_conflicts=minor_conflicts,
        ):
            return category, [f"{category}_confidence_gate_satisfied"]
    return "unresolved", ["insufficient_independent_support"]
