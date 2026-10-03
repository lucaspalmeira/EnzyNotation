"""Small declarative evaluator for EC-specific evidence rules."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from jsonschema import Draft202012Validator, FormatChecker

from enzynotation.candidates import ec_is_ancestor
from enzynotation.ec import normalize_ec
from enzynotation.evidence import EvidenceRecord
from enzynotation.exceptions import IntegrationError


@dataclass(frozen=True, slots=True)
class ECRules:
    """Validated EC-specific rules and their source document."""

    document: Mapping[str, Any]
    source_path: Path

    @property
    def policy_id(self) -> str:
        return str(self.document["ruleset"]["id"])

    @property
    def policy_version(self) -> str:
        return str(self.document["ruleset"]["version"])

    def rule_for(
        self, candidate_ec: str, family_id: str | None
    ) -> Mapping[str, Any] | None:
        for rule in self.document["ec_rules"]:
            if normalize_ec(str(rule["candidate_ec"])) != candidate_ec:
                continue
            families = rule.get("applies_to_families")
            if families and family_id not in families:
                continue
            return rule
        return None


def _load_document(path: Path) -> dict[str, Any]:
    try:
        value = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise IntegrationError(f"Cannot load configuration {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise IntegrationError(f"Configuration {path} must contain a mapping")
    return value


def _validate(document: Mapping[str, Any], schema_path: Path, label: str) -> None:
    try:
        schema = json.loads(Path(schema_path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise IntegrationError(
            f"Cannot load {label} schema {schema_path}: {exc}"
        ) from exc
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    errors = sorted(validator.iter_errors(document), key=lambda error: list(error.path))
    if errors:
        path = ".".join(str(part) for part in errors[0].absolute_path)
        raise IntegrationError(
            f"Invalid {label} configuration at {path}: {errors[0].message}"
        )


def load_ec_rules(path: Path, *, schema_path: Path) -> ECRules:
    """Load EC rules after validating the Milestone 0 contract."""

    document = _load_document(path)
    _validate(document, schema_path, "EC rules")
    return ECRules(document, Path(path).resolve())


def _metric_matches(observed: Any, operator: str, expected: Any) -> bool:
    try:
        if operator == "eq":
            return observed == expected
        if operator == "ne":
            return observed != expected
        if operator == "lt":
            return observed < expected
        if operator == "lte":
            return observed <= expected
        if operator == "gt":
            return observed > expected
        if operator == "gte":
            return observed >= expected
        if operator == "in":
            return observed in expected
    except TypeError:
        return False
    return False


def _record_applies(record: EvidenceRecord, candidate_ec: str) -> bool:
    asserted = record.candidate_ec
    if asserted is None:
        return True
    return asserted == candidate_ec or ec_is_ancestor(candidate_ec, asserted)


def evaluate_condition(
    condition: Mapping[str, Any],
    records: tuple[EvidenceRecord, ...],
    candidate_ec: str,
    *,
    invert: bool = False,
    requirement: str = "required",
) -> dict[str, Any]:
    """Evaluate one bounded evidence condition with a full trace."""

    matches: list[EvidenceRecord] = []
    metric_unavailable = False
    for record in records:
        if not _record_applies(record, candidate_ec):
            continue
        if record.record_status not in condition.get(
            "record_statuses", ("observed", "negative")
        ):
            continue
        if record.effect not in condition["effects"]:
            continue
        if (
            condition.get("source_ids")
            and record.source_id not in condition["source_ids"]
        ):
            continue
        if (
            condition.get("evidence_classes")
            and record.evidence_class not in condition["evidence_classes"]
        ):
            continue
        metric = condition.get("metric")
        if metric:
            observed = record.metrics.get(metric["name"])
            if observed is None:
                metric_unavailable = True
                continue
            if not _metric_matches(observed, metric["operator"], metric["value"]):
                continue
        matches.append(record)
    groups = sorted({record.correlation_group for record in matches})
    threshold_met = len(matches) >= int(condition["minimum_records"]) and len(
        groups
    ) >= int(condition["minimum_independent_groups"])
    unknown = metric_unavailable and not matches
    passed = (not threshold_met if invert else threshold_met) and not unknown
    reason = "forbidden_evidence_absent" if invert and passed else "condition_satisfied"
    if unknown:
        reason = "required_metric_unavailable"
    elif not passed:
        reason = (
            "forbidden_evidence_present" if invert else "insufficient_matching_evidence"
        )
    return {
        "rule_id": str(condition["condition_id"]),
        "requirement": requirement,
        "candidate_ec": candidate_ec,
        "outcome": "unknown" if unknown else ("pass" if passed else "fail"),
        "evidence_ids": sorted(record.evidence_id for record in matches),
        "correlation_groups": groups,
        "reason_code": reason,
        "explanation": (
            f"matched {len(matches)} record(s) in {len(groups)} correlation group(s)"
        ),
    }


def evaluate_ec_rule(
    rules: ECRules | None,
    candidate_ec: str,
    records: tuple[EvidenceRecord, ...],
    *,
    family_id: str | None = None,
) -> list[dict[str, Any]]:
    """Evaluate the configured rule for one candidate without hidden inference."""

    if rules is None:
        return []
    rule = rules.rule_for(candidate_ec, family_id)
    if rule is None:
        return [
            {
                "rule_id": "ec_specific_rule",
                "requirement": "optional",
                "candidate_ec": candidate_ec,
                "outcome": "not_applicable",
                "evidence_ids": [],
                "correlation_groups": [],
                "reason_code": "no_matching_ec_rule",
                "explanation": "no EC-specific rule applies to this candidate",
                "config_source": str(rules.source_path),
                "ruleset_id": rules.policy_id,
                "ruleset_version": rules.policy_version,
            }
        ]
    evaluations: list[dict[str, Any]] = []
    requirements = rule["requirements"]
    for condition in requirements["all"]:
        evaluations.append(evaluate_condition(condition, records, candidate_ec))
    for condition in requirements["any"]:
        evaluations.append(
            evaluate_condition(
                condition, records, candidate_ec, requirement="required_any"
            )
        )
    for condition in requirements["none"]:
        evaluations.append(
            evaluate_condition(condition, records, candidate_ec, invert=True)
        )
    for evaluation in evaluations:
        evaluation.update(
            config_source=str(rules.source_path),
            ruleset_id=rules.policy_id,
            ruleset_version=rules.policy_version,
        )
    return evaluations


def required_rules_passed(evaluations: list[dict[str, Any]]) -> bool:
    """Return whether all/all-none and the any-set satisfy their requirements."""

    required = [item for item in evaluations if item["requirement"] == "required"]
    any_items = [item for item in evaluations if item["requirement"] == "required_any"]
    return all(item["outcome"] == "pass" for item in required) and (
        not any_items or any(item["outcome"] == "pass" for item in any_items)
    )
