"""EC hierarchy and correlation-aware candidate tests."""

from enzynotation.candidates import (
    build_candidate_scorecards,
    common_ec_parent,
    ec_is_ancestor,
    ec_parents,
    independent_class_matching,
)
from enzynotation.evidence import EvidenceRecord


def _records(*values):
    return tuple(EvidenceRecord(value) for value in values)


def test_ec_parent_hierarchy_is_explicit() -> None:
    assert ec_parents("3.2.1.26") == ("3.-.-.-", "3.2.-.-", "3.2.1.-")
    assert ec_is_ancestor("3.2.1.-", "3.2.1.26")
    assert not ec_is_ancestor("3.2.1.26", "3.2.1.-")


def test_common_parent_never_manufactures_exact_ec() -> None:
    assert common_ec_parent(["3.2.1.26", "3.2.1.80"]) == "3.2.1.-"
    assert common_ec_parent(["1.1.1.1", "2.1.1.1"]) is None


def test_complete_support_is_inherited_only_when_enabled(canonical_record) -> None:
    records = _records(canonical_record("one", ec="3.2.1.26"))
    inherited = build_candidate_scorecards(records, inherit_complete_to_parents=True)
    strict = build_candidate_scorecards(records, inherit_complete_to_parents=False)
    assert "3.2.1.-" in inherited
    assert "3.2.1.-" not in strict


def test_partial_support_does_not_expand_to_children(canonical_record) -> None:
    cards = build_candidate_scorecards(
        _records(canonical_record("partial", ec="3.2.1.-")),
        inherit_complete_to_parents=True,
    )
    assert set(cards) == {"3.2.1.-"}


def test_same_group_cannot_supply_two_independent_classes(canonical_record) -> None:
    records = list(
        _records(
            canonical_record("a", group="same", evidence_class="curated_annotation"),
            canonical_record("b", group="same", evidence_class="sequence_homology"),
        )
    )
    assert len(independent_class_matching(records)) == 1


def test_distinct_groups_and_classes_are_independent(canonical_record) -> None:
    records = list(
        _records(
            canonical_record("a", group="a", evidence_class="curated_annotation"),
            canonical_record(
                "b",
                source="clean",
                group="b",
                evidence_class="learned_sequence_model",
            ),
        )
    )
    assert independent_class_matching(records) == (
        "curated_annotation",
        "learned_sequence_model",
    )


def test_scorecard_preserves_ids_groups_and_providers(canonical_record) -> None:
    cards = build_candidate_scorecards(
        _records(canonical_record("one", group="blast-ref")),
        inherit_complete_to_parents=False,
    )
    value = cards["1.1.1.1"].to_dict()
    assert value["supporting_evidence_ids"] == ["one"]
    assert value["supporting_correlation_groups"] == ["blast-ref"]
    assert list(value["provider_summary"]) == ["blastp"]
