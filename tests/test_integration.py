"""End-to-end scientific integration tests using canonical evidence only."""

from copy import deepcopy
from pathlib import Path

from enzynotation.evidence import (
    EvidenceCollection,
    EvidenceRecord,
    ProviderAvailability,
    ProviderState,
)
from enzynotation.integration import (
    IntegrationConfig,
    integrate_collection,
    integrate_query,
    load_integration_config,
)


def _config() -> IntegrationConfig:
    return load_integration_config(Path("configs/integration/default.yaml"))


def _integrate(canonical_record, *specs, config=None, availability=()):
    records = tuple(
        EvidenceRecord(canonical_record(*spec[:1], **spec[1])) for spec in specs
    )
    return integrate_query(
        "q1", records, config or _config(), availability=availability
    )


def test_exact_high_confidence_requires_three_independent_classes(
    canonical_record,
) -> None:
    result = _integrate(
        canonical_record,
        ("blast", {}),
        (
            "clean",
            {
                "source": "clean",
                "evidence_class": "learned_sequence_model",
                "group": "clean",
            },
        ),
        (
            "structure",
            {
                "source": "foldseek",
                "evidence_class": "structure_homology",
                "group": "structure",
            },
        ),
    )
    assert (result.predicted_ec, result.confidence) == ("1.1.1.1", "high")


def test_exact_two_class_support_is_medium(canonical_record) -> None:
    result = _integrate(
        canonical_record,
        ("blast", {}),
        (
            "clean",
            {
                "source": "clean",
                "evidence_class": "learned_sequence_model",
                "group": "clean",
            },
        ),
    )
    assert (result.predicted_ec, result.confidence) == ("1.1.1.1", "medium")


def test_direct_partial_candidate_is_valid_low_result(canonical_record) -> None:
    result = _integrate(canonical_record, ("partial", {"ec": "3.2.1.-"}))
    assert (result.predicted_ec, result.completeness, result.confidence) == (
        "3.2.1.-",
        "partial",
        "low",
    )


def test_no_candidate_is_successful_unresolved_outcome(canonical_record) -> None:
    result = _integrate(
        canonical_record,
        (
            "domain",
            {"source": "hmmer", "evidence_class": "domain_architecture", "ec": None},
        ),
    )
    assert result.predicted_ec is None
    assert result.confidence == "unresolved"
    assert "no_ec_candidates" in result.reason_codes


def test_common_parent_is_preferred_over_forced_exact_ec(canonical_record) -> None:
    result = _integrate(
        canonical_record,
        ("one", {"ec": "3.2.1.26", "group": "one"}),
        (
            "two",
            {
                "source": "blastp",
                "evidence_class": "sequence_homology",
                "ec": "3.2.1.80",
                "group": "two",
            },
        ),
    )
    assert result.predicted_ec == "3.2.1.-"
    assert result.completeness == "partial"


def test_tied_exact_candidates_use_configured_common_parent(canonical_record) -> None:
    base = _config()
    document = deepcopy(base.document)
    document["integration"]["ec_specificity"]["exact_ec_requires"] = {
        "minimum_independent_classes": 1,
        "minimum_correlation_groups": 1,
        "required_evidence_classes": [],
    }
    config = IntegrationConfig(
        document,
        base.source_path,
        base.confidence_document,
        base.confidence_path,
        base.providers,
    )
    result = _integrate(
        canonical_record,
        ("one", {"ec": "3.2.1.26", "group": "one"}),
        ("two", {"ec": "3.2.1.80", "group": "two"}),
        config=config,
    )
    assert result.predicted_ec == "3.2.1.-"
    assert "common_parent_fallback" in result.reason_codes


def test_specificity_preference_can_select_broadest_supported_parent(
    canonical_record,
) -> None:
    base = _config()
    document = deepcopy(base.document)
    document["integration"]["ec_specificity"]["prefer_most_specific_supported"] = False
    config = IntegrationConfig(
        document,
        base.source_path,
        base.confidence_document,
        base.confidence_path,
        base.providers,
    )
    result = _integrate(
        canonical_record,
        ("blast", {}),
        (
            "clean",
            {
                "source": "clean",
                "evidence_class": "learned_sequence_model",
                "group": "clean",
            },
        ),
        config=config,
    )
    assert result.predicted_ec == "1.-.-.-"
    assert "broadest_supported_candidate" in result.reason_codes


def test_sequence_structure_conflict_remains_unresolved(canonical_record) -> None:
    result = _integrate(
        canonical_record,
        ("sequence", {"ec": "1.1.1.1"}),
        ("structure", {"source": "foldseek", "group": "structure", "ec": "2.2.2.2"}),
    )
    assert result.predicted_ec is None
    assert "major_unresolved_conflict" in result.reason_codes
    assert any(
        item.reason_code == "sequence_structure_conflict" for item in result.conflicts
    )


def test_correlated_blast_records_do_not_inflate_support(canonical_record) -> None:
    result = _integrate(
        canonical_record,
        ("homology", {"evidence_class": "sequence_homology", "group": "same"}),
        ("annotation", {"evidence_class": "curated_annotation", "group": "same"}),
    )
    exact = next(
        card for card in result.scorecards if card["candidate_ec"] == "1.1.1.1"
    )
    assert exact["confidence_features"]["independent_class_count"] == 1
    assert "tmalign" not in exact["supporting_evidence_ids"]
    assert exact["eligibility_status"] == "insufficient_support"


def test_correlated_structural_records_do_not_inflate_support(canonical_record) -> None:
    result = _integrate(
        canonical_record,
        (
            "foldseek",
            {
                "source": "foldseek",
                "evidence_class": "structure_homology",
                "group": "pair",
            },
        ),
        (
            "annotation",
            {
                "source": "foldseek",
                "evidence_class": "curated_annotation",
                "group": "pair",
            },
        ),
        (
            "tmalign",
            {
                "source": "tmalign",
                "evidence_class": "structure_homology",
                "group": "pair",
            },
        ),
    )
    exact = next(
        card for card in result.scorecards if card["candidate_ec"] == "1.1.1.1"
    )
    assert exact["confidence_features"]["independent_class_count"] == 1


def test_multiple_clean_candidates_share_one_model_group(canonical_record) -> None:
    result = _integrate(
        canonical_record,
        (
            "clean-a",
            {
                "source": "clean",
                "evidence_class": "learned_sequence_model",
                "group": "model",
                "ec": "1.1.1.1",
            },
        ),
        (
            "clean-b",
            {
                "source": "clean",
                "evidence_class": "learned_sequence_model",
                "group": "model",
                "ec": "1.1.1.2",
            },
        ),
    )
    exact = [card for card in result.scorecards if card["depth"] == 4]
    assert all(
        card["confidence_features"]["correlation_group_count"] == 1 for card in exact
    )
    parent = next(
        card for card in result.scorecards if card["candidate_ec"] == "1.1.1.-"
    )
    assert parent["supporting_correlation_groups"] == ["model"]


def test_required_provider_failure_differs_from_negative_evidence(
    canonical_record,
) -> None:
    availability = (
        ProviderAvailability("clean", ProviderState.FAILED, required=True),
        ProviderAvailability("hmmer", ProviderState.SUCCESSFUL_ZERO),
        ProviderAvailability("interproscan", ProviderState.DISABLED),
    )
    result = _integrate(
        canonical_record,
        ("blast", {}),
        availability=availability,
    )
    assert result.predicted_ec is None
    assert any(
        item.reason_code == "required_provider_failed" for item in result.conflicts
    )
    assert [item.state.value for item in result.availability] == [
        "failed",
        "successful_zero",
        "disabled",
    ]


def test_collection_order_and_decisions_are_deterministic(canonical_record) -> None:
    records = tuple(
        EvidenceRecord(canonical_record(query, query_id=query))
        for query in ("q2", "q1")
    )
    results = integrate_collection(EvidenceCollection(records), _config())
    assert [item.query_id for item in results] == ["q1", "q2"]
    assert results[0].annotation_dict(policy_id="p", policy_version="1") == (
        integrate_query("q1", (records[1],), _config()).annotation_dict(
            policy_id="p", policy_version="1"
        )
    )
    assert "probability" not in results[0].annotation_dict(
        policy_id="p", policy_version="1"
    )
