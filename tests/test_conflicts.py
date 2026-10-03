"""Conflict detection tests."""

from pathlib import Path

from enzynotation.candidates import build_candidate_scorecards
from enzynotation.conflicts import detect_conflicts
from enzynotation.evidence import EvidenceRecord, ProviderAvailability, ProviderState
from enzynotation.integration import load_integration_config


def _policy():
    config = load_integration_config(Path("configs/integration/default.yaml"))
    return dict(config.integration["conflict_policy"])


def _detect(records, availability=()):
    values = tuple(EvidenceRecord(value) for value in records)
    cards = build_candidate_scorecards(values, inherit_complete_to_parents=True)
    return detect_conflicts(values, cards, tuple(availability), _policy())


def test_incompatible_ec_candidates_are_visible(canonical_record) -> None:
    conflicts = _detect(
        [canonical_record("a", ec="1.1.1.1"), canonical_record("b", ec="2.1.1.1")]
    )
    assert conflicts[0].reason_code == "incompatible_ec_candidates"


def test_sequence_structure_disagreement_is_major(canonical_record) -> None:
    conflicts = _detect(
        [
            canonical_record("seq", ec="1.1.1.1"),
            canonical_record(
                "struct",
                source="foldseek",
                evidence_class="curated_annotation",
                group="structure",
                ec="2.1.1.1",
            ),
        ]
    )
    conflict = next(
        item
        for item in conflicts
        if item.conflict_type == "sequence_structure_disagreement"
    )
    assert conflict.severity == "major"
    assert conflict.action == "force_unresolved"


def test_clean_curated_disagreement_is_explicit(canonical_record) -> None:
    conflicts = _detect(
        [
            canonical_record("curated", ec="1.1.1.1"),
            canonical_record(
                "model",
                source="clean",
                evidence_class="learned_sequence_model",
                group="model",
                ec="2.1.1.1",
            ),
        ]
    )
    assert any(item.conflict_type == "curation_disagreement" for item in conflicts)


def test_domain_negative_is_biological_conflict(canonical_record) -> None:
    conflicts = _detect(
        [
            canonical_record(
                "domain-negative",
                source="hmmer",
                evidence_class="domain_architecture",
                ec=None,
                effect="contradicts",
                status="negative",
            )
        ]
    )
    assert conflicts[0].reason_code == "domain_contradiction"


def test_motif_negative_is_biological_conflict(canonical_record) -> None:
    conflicts = _detect(
        [
            canonical_record(
                "motif-negative",
                source="catalytic_motif",
                evidence_class="catalytic_motif",
                ec=None,
                effect="contradicts",
                status="negative",
            )
        ]
    )
    assert conflicts[0].reason_code == "motif_contradiction"


def test_missing_record_is_not_a_biological_conflict(canonical_record) -> None:
    conflicts = _detect([canonical_record("missing", status="missing")])
    assert conflicts == []


def test_required_provider_failure_is_not_biological_negative() -> None:
    conflicts = _detect(
        [],
        [ProviderAvailability("clean", ProviderState.FAILED, required=True)],
    )
    assert conflicts[0].reason_code == "required_provider_failed"
    assert conflicts[0].evidence_ids == ()
