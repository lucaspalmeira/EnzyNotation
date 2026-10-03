"""Declarative EC-rule tests."""

from pathlib import Path

from enzynotation.evidence import EvidenceRecord
from enzynotation.rules import (
    evaluate_condition,
    evaluate_ec_rule,
    load_ec_rules,
    required_rules_passed,
)


def _rules():
    return load_ec_rules(
        Path("configs/ec_rules/example.yaml"),
        schema_path=Path("configs/schema/ec-rules.schema.json"),
    )


def test_required_domain_rule_passes_with_matching_feature(canonical_record) -> None:
    records = (
        EvidenceRecord(
            canonical_record(
                "domain",
                source="hmmer",
                evidence_class="domain_architecture",
                ec=None,
            )
        ),
    )
    evaluations = evaluate_ec_rule(_rules(), "1.1.1.1", records)
    assert evaluations[0]["outcome"] == "pass"
    assert required_rules_passed(evaluations)


def test_required_domain_rule_failure_is_traceable() -> None:
    evaluations = evaluate_ec_rule(_rules(), "1.1.1.1", ())
    assert evaluations[0]["outcome"] == "fail"
    assert evaluations[0]["reason_code"] == "insufficient_matching_evidence"
    assert not required_rules_passed(evaluations)


def test_nonmatching_ec_rule_is_not_applicable() -> None:
    evaluations = evaluate_ec_rule(_rules(), "2.2.2.2", ())
    assert evaluations[0]["outcome"] == "not_applicable"
    assert required_rules_passed(evaluations)


def test_metric_condition_uses_normalized_metric(canonical_record) -> None:
    condition = {
        "condition_id": "coverage",
        "source_ids": ["blastp"],
        "effects": ["supports"],
        "record_statuses": ["observed"],
        "minimum_records": 1,
        "minimum_independent_groups": 1,
        "metric": {"name": "query_coverage", "operator": "gte", "value": 0.8},
    }
    record = EvidenceRecord(canonical_record("blast", metrics={"query_coverage": 0.9}))
    assert evaluate_condition(condition, (record,), "1.1.1.1")["outcome"] == "pass"


def test_missing_normalized_metric_is_unknown(canonical_record) -> None:
    condition = {
        "condition_id": "coverage",
        "source_ids": ["blastp"],
        "effects": ["supports"],
        "record_statuses": ["observed"],
        "minimum_records": 1,
        "minimum_independent_groups": 1,
        "metric": {"name": "query_coverage", "operator": "gte", "value": 0.8},
    }
    result = evaluate_condition(
        condition,
        (EvidenceRecord(canonical_record("blast")),),
        "1.1.1.1",
    )
    assert result["outcome"] == "unknown"
    assert result["reason_code"] == "required_metric_unavailable"


def test_none_condition_detects_forbidden_evidence(canonical_record) -> None:
    condition = {
        "condition_id": "forbidden",
        "source_ids": ["catalytic_motif"],
        "effects": ["supports"],
        "record_statuses": ["observed"],
        "minimum_records": 1,
        "minimum_independent_groups": 1,
    }
    record = EvidenceRecord(
        canonical_record("motif", source="catalytic_motif", ec=None)
    )
    result = evaluate_condition(condition, (record,), "1.1.1.1", invert=True)
    assert result["outcome"] == "fail"
    assert result["reason_code"] == "forbidden_evidence_present"
