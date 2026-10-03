"""Categorical confidence gate tests."""

from pathlib import Path

from enzynotation.candidates import build_candidate_scorecards
from enzynotation.confidence import classify_confidence
from enzynotation.evidence import EvidenceRecord
from enzynotation.integration import load_integration_config


def _card(canonical_record, count: int, *, ec: str = "1.1.1.1"):
    classes = ["curated_annotation", "learned_sequence_model", "structure_homology"]
    sources = ["blastp", "clean", "foldseek"]
    records = tuple(
        EvidenceRecord(
            canonical_record(
                f"r{index}",
                source=sources[index],
                evidence_class=classes[index],
                group=f"g{index}",
                ec=ec,
            )
        )
        for index in range(count)
    )
    card = build_candidate_scorecards(records, inherit_complete_to_parents=False)[ec]
    card.status = "eligible"
    return card


def _policy():
    return load_integration_config(Path("configs/integration/default.yaml")).confidence


def test_high_requires_three_independent_classes(canonical_record) -> None:
    assert classify_confidence(_card(canonical_record, 3), _policy())[0] == "high"


def test_two_independent_classes_are_medium(canonical_record) -> None:
    assert classify_confidence(_card(canonical_record, 2), _policy())[0] == "medium"


def test_one_independent_class_is_low(canonical_record) -> None:
    assert classify_confidence(_card(canonical_record, 1), _policy())[0] == "low"


def test_partial_candidate_cannot_be_high(canonical_record) -> None:
    assert (
        classify_confidence(_card(canonical_record, 3, ec="1.1.1.-"), _policy())[0]
        == "medium"
    )


def test_major_conflict_forces_unresolved(canonical_record) -> None:
    category, reasons = classify_confidence(
        _card(canonical_record, 3), _policy(), major_conflicts=1
    )
    assert category == "unresolved"
    assert reasons == ["insufficient_independent_support"]


def test_no_scorecard_is_unresolved() -> None:
    assert classify_confidence(None, _policy()) == (
        "unresolved",
        ["no_eligible_candidate"],
    )
