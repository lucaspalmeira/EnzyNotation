"""Explicit, traceable conflict detection for evidence integration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from enzynotation.candidates import CandidateScorecard, ec_is_ancestor
from enzynotation.evidence import EvidenceRecord, ProviderAvailability, ProviderState

_SEQUENCE_SOURCES = {"blastp", "clean"}
_STRUCTURE_SOURCES = {"foldseek", "tmalign"}


@dataclass(frozen=True, slots=True)
class Conflict:
    """One configured conflict with its complete evidence trace."""

    conflict_id: str
    conflict_type: str
    reason_code: str
    candidates: tuple[str, ...]
    evidence_ids: tuple[str, ...]
    correlation_groups: tuple[str, ...]
    severity: str
    action: str
    explanation: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "conflict_id": self.conflict_id,
            "conflict_type": self.conflict_type,
            "reason_code": self.reason_code,
            "candidate_ecs": list(self.candidates),
            "evidence_ids": list(self.evidence_ids),
            "correlation_groups": list(self.correlation_groups),
            "severity": self.severity,
            "action": self.action,
            "explanation": self.explanation,
        }


def _handling(policy: dict[str, dict[str, str]], conflict_type: str) -> tuple[str, str]:
    value = policy[conflict_type]
    return value["severity"], value["action"]


def _incompatible(left: str, right: str) -> bool:
    return not ec_is_ancestor(left, right) and not ec_is_ancestor(right, left)


def detect_conflicts(
    records: tuple[EvidenceRecord, ...],
    candidates: dict[str, CandidateScorecard],
    availability: tuple[ProviderAvailability, ...],
    policy: dict[str, dict[str, str]],
) -> list[Conflict]:
    """Detect candidate, biological, cross-modality, and availability conflicts."""

    result: list[Conflict] = []
    direct = [
        card
        for card in candidates.values()
        if not card.inherited and card.supporting_records
    ]
    incompatible = sorted(
        {
            value
            for index, left in enumerate(direct)
            for right in direct[index + 1 :]
            if _incompatible(left.candidate_ec, right.candidate_ec)
            for value in (left.candidate_ec, right.candidate_ec)
        }
    )
    if incompatible:
        severity, action = _handling(policy, "candidate_disagreement")
        involved = [
            record
            for card in direct
            if card.candidate_ec in incompatible
            for record in card.supporting_records
        ]
        result.append(
            Conflict(
                "candidate_disagreement",
                "candidate_disagreement",
                "incompatible_ec_candidates",
                tuple(incompatible),
                tuple(sorted({item.evidence_id for item in involved})),
                tuple(sorted({item.correlation_group for item in involved})),
                severity,
                action,
                "independent evidence asserts incompatible EC candidates",
            )
        )

    sequence = {
        record.candidate_ec
        for record in records
        if record.source_id in _SEQUENCE_SOURCES
        and record.effect == "supports"
        and record.candidate_ec
    }
    structure = {
        record.candidate_ec
        for record in records
        if record.source_id in _STRUCTURE_SOURCES
        and record.effect == "supports"
        and record.candidate_ec
    }
    if (
        sequence
        and structure
        and all(_incompatible(left, right) for left in sequence for right in structure)
    ):
        severity, action = _handling(policy, "sequence_structure_disagreement")
        involved = [
            item
            for item in records
            if item.candidate_ec in sequence | structure
            and item.source_id in _SEQUENCE_SOURCES | _STRUCTURE_SOURCES
        ]
        result.append(
            Conflict(
                "sequence_structure_disagreement",
                "sequence_structure_disagreement",
                "sequence_structure_conflict",
                tuple(sorted(sequence | structure)),
                tuple(sorted(item.evidence_id for item in involved)),
                tuple(sorted({item.correlation_group for item in involved})),
                severity,
                action,
                "sequence-derived and structure-derived EC assertions disagree",
            )
        )

    model = {
        record.candidate_ec
        for record in records
        if record.source_id == "clean"
        and record.effect == "supports"
        and record.candidate_ec
    }
    curated = {
        record.candidate_ec
        for record in records
        if record.evidence_class == "curated_annotation"
        and record.effect == "supports"
        and record.candidate_ec
    }
    if (
        model
        and curated
        and all(_incompatible(left, right) for left in model for right in curated)
    ):
        severity, action = _handling(policy, "curation_disagreement")
        involved = [
            item
            for item in records
            if item.candidate_ec in model | curated
            and (
                item.source_id == "clean" or item.evidence_class == "curated_annotation"
            )
        ]
        result.append(
            Conflict(
                "model_curated_disagreement",
                "curation_disagreement",
                "model_curated_reference_conflict",
                tuple(sorted(model | curated)),
                tuple(sorted(item.evidence_id for item in involved)),
                tuple(sorted({item.correlation_group for item in involved})),
                severity,
                action,
                "learned-model and curated-reference EC assertions disagree",
            )
        )

    for record in records:
        if record.record_status != "negative" or record.effect != "contradicts":
            continue
        conflict_type = "required_feature_absent"
        reason = "biological_constraint_failed"
        if record.evidence_class == "domain_architecture":
            reason = "domain_contradiction"
        elif record.evidence_class == "catalytic_motif":
            reason = "motif_contradiction"
        severity, action = _handling(policy, conflict_type)
        result.append(
            Conflict(
                f"{conflict_type}:{record.evidence_id}",
                conflict_type,
                reason,
                tuple(sorted(candidates)),
                (record.evidence_id,),
                (record.correlation_group,),
                severity,
                action,
                "successful biological analysis contradicted a required feature",
            )
        )

    for provider in availability:
        if provider.required and provider.state in {
            ProviderState.FAILED,
            ProviderState.UNAVAILABLE,
            ProviderState.NOT_RUN,
            ProviderState.NOT_CONFIGURED,
            ProviderState.DISABLED,
        }:
            severity, action = _handling(policy, "provenance_inadequate")
            result.append(
                Conflict(
                    f"required_provider:{provider.source_id}",
                    "provenance_inadequate",
                    "required_provider_failed",
                    tuple(sorted(candidates)),
                    (),
                    (),
                    severity,
                    action,
                    f"required provider {provider.source_id} is {provider.state.value}",
                )
            )
    return sorted(result, key=lambda item: item.conflict_id)
