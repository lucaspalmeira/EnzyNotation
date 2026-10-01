"""Tests for the generic configuration-driven motif engine."""

from __future__ import annotations

from pathlib import Path

import yaml

from enzynotation.family import load_family_profile
from enzynotation.models import ProteinRecord
from enzynotation.motifs import analyze_motifs

FIXTURES = Path("tests/fixtures/domains")


def _profile(tmp_path: Path, mutate=None):
    document = yaml.safe_load((FIXTURES / "family.yaml").read_text())
    if mutate is not None:
        mutate(document)
    path = tmp_path / "family.yaml"
    path.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")
    return load_family_profile(path)


def _evaluation(result, rule_id: str):
    return next(item for item in result.evaluations if item.rule.rule_id == rule_id)


def test_literal_regex_alternatives_and_named_residue(tmp_path: Path) -> None:
    profile = _profile(tmp_path)
    result = analyze_motifs(
        (ProteinRecord("q", "", "AAACATAAANDPAAAAAAAAAA"),), profile
    )

    literal = next(hit for hit in result.hits if hit.rule_id == "literal_catalytic")
    regex = next(hit for hit in result.hits if hit.rule_id == "regex_region")
    assert (literal.start, literal.end, literal.matched_sequence) == (4, 6, "CAT")
    assert literal.qualifying
    assert literal.residues[0].name == "catalytic_cysteine"
    assert literal.residues[0].position == 4
    assert regex.matched_sequence == "NDP"
    assert _evaluation(result, "literal_catalytic").satisfied
    assert _evaluation(result, "regex_region").satisfied

    alternative = analyze_motifs(
        (ProteinRecord("q", "", "AAACATAAANEPAAAAAAAAAA"),), profile
    )
    assert any(hit.matched_sequence == "NEP" for hit in alternative.hits)


def test_overlapping_motif_matches(tmp_path: Path) -> None:
    def mutate(document):
        motif = document["rules"]["motifs"][0]
        motif.update(pattern="AAA", catalytic_residues=[], coordinates={})
        document["rules"]["motifs"] = [motif]
        document["rules"]["motif_relationships"] = []

    result = analyze_motifs(
        (ProteinRecord("q", "", "AAAAA"),), _profile(tmp_path, mutate)
    )
    assert [(hit.start, hit.end) for hit in result.hits] == [(1, 3), (2, 4), (3, 5)]


def test_coordinate_and_expected_residue_constraints(tmp_path: Path) -> None:
    profile = _profile(tmp_path)
    pass_result = analyze_motifs(
        (ProteinRecord("q", "", "AAACATAAAAAAAAAAAAAA"),), profile
    )
    assert next(
        hit for hit in pass_result.hits if hit.rule_id == "literal_catalytic"
    ).qualifying

    fail_result = analyze_motifs(
        (ProteinRecord("q", "", "AAAAAAAAAAAAAAAAAAAACAT"),), profile
    )
    hit = next(hit for hit in fail_result.hits if hit.rule_id == "literal_catalytic")
    assert not hit.coordinate_satisfied
    assert not hit.residues[0].position_satisfied
    assert not hit.qualifying
    assert _evaluation(fail_result, "literal_catalytic").reason_code == (
        "motif_outside_expected_position"
    )


def test_forbidden_and_optional_motif_handling(tmp_path: Path) -> None:
    profile = _profile(tmp_path)
    present = analyze_motifs(
        (ProteinRecord("q", "", "AAACATBADNDPAAAAAAAAAAA"),), profile
    )
    forbidden = _evaluation(present, "forbidden_region")
    assert forbidden.qualifying_count == 1
    assert not forbidden.satisfied
    assert forbidden.reason_code == "forbidden_motif_present"

    absent = analyze_motifs(
        (ProteinRecord("q", "", "AAACATAAANDPAAAAAAAAAA"),), profile
    )
    assert _evaluation(absent, "forbidden_region").reason_code == (
        "forbidden_motif_absent"
    )
    assert _evaluation(absent, "optional_region").reason_code == (
        "motif_absent_complete_sequence"
    )


def test_order_and_distance_constraints(tmp_path: Path) -> None:
    profile = _profile(tmp_path)
    passing = analyze_motifs(
        (ProteinRecord("q", "", "AAACATAAANDPAAAAAAAAAA"),), profile
    )
    relation = passing.relationships[0]
    assert relation.satisfied is True
    assert relation.observed_distance == 3

    wrong_order = analyze_motifs(
        (ProteinRecord("q", "", "NDPAAACATAAAAAAAAAAAA"),), profile
    )
    assert wrong_order.relationships[0].satisfied is False
    assert wrong_order.relationships[0].reason_code == "motif_order_failed"

    too_far = analyze_motifs((ProteinRecord("q", "", f"CAT{'A' * 30}NDP"),), profile)
    assert too_far.relationships[0].satisfied is False
    assert too_far.relationships[0].reason_code == "motif_distance_failed"


def test_absence_distinguishes_complete_truncated_and_unknown(tmp_path: Path) -> None:
    profile = _profile(tmp_path)
    complete = analyze_motifs((ProteinRecord("complete", "", "A" * 25),), profile)
    assert _evaluation(complete, "literal_catalytic").reason_code == (
        "motif_absent_complete_sequence"
    )

    truncated = analyze_motifs((ProteinRecord("short", "", "A" * 10),), profile)
    assert _evaluation(truncated, "literal_catalytic").reason_code == (
        "sequence_truncated"
    )

    unknown_profile = _profile(
        tmp_path,
        lambda document: document["sequence_analysis"].update(
            minimum_complete_length=None
        ),
    )
    unknown = analyze_motifs((ProteinRecord("unknown", "", "A" * 25),), unknown_profile)
    assert _evaluation(unknown, "literal_catalytic").reason_code == (
        "sequence_completeness_unknown"
    )


def test_outside_analyzed_region_and_disabled_are_not_evaluated(
    tmp_path: Path,
) -> None:
    outside_profile = _profile(
        tmp_path,
        lambda document: document["sequence_analysis"].update(region_start=21),
    )
    outside = analyze_motifs(
        (ProteinRecord("q", "", "AAACATAAAAAAAAAAAAAAAAA"),), outside_profile
    )
    assert _evaluation(outside, "literal_catalytic").reason_code == (
        "motif_outside_analyzed_region"
    )

    disabled_profile = _profile(
        tmp_path,
        lambda document: document["rules"]["motifs"][0].update(enabled=False),
    )
    disabled = analyze_motifs(
        (ProteinRecord("q", "", "AAACATAAAAAAAAAAAAAAAAA"),), disabled_profile
    )
    assert _evaluation(disabled, "literal_catalytic").reason_code == (
        "motif_not_evaluated"
    )
