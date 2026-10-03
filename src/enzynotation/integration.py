"""Deterministic, correlation-aware EC evidence integration."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from jsonschema import Draft202012Validator, FormatChecker

from enzynotation.candidates import (
    CandidateScorecard,
    build_candidate_scorecards,
    common_ec_parent,
)
from enzynotation.confidence import classify_confidence
from enzynotation.conflicts import Conflict, detect_conflicts
from enzynotation.evidence import (
    EvidenceCollection,
    EvidenceRecord,
    ProviderAvailability,
)
from enzynotation.exceptions import IntegrationError
from enzynotation.rules import ECRules, evaluate_ec_rule, required_rules_passed


@dataclass(frozen=True, slots=True)
class ProviderPolicy:
    """Configured source role and availability lookup information."""

    source_id: str
    stage_id: str
    required: bool
    roles: tuple[str, ...]
    evidence_path: str | None = None
    summary_path: str | None = None
    summary_status_key: str | None = None


@dataclass(frozen=True, slots=True)
class IntegrationConfig:
    """Validated, path-resolved integration policy."""

    document: Mapping[str, Any]
    source_path: Path
    confidence_document: Mapping[str, Any]
    confidence_path: Path
    providers: tuple[ProviderPolicy, ...]

    @property
    def integration(self) -> Mapping[str, Any]:
        return self.document["integration"]

    @property
    def required(self) -> bool:
        return bool(self.integration["required"])

    @property
    def evidence_paths(self) -> tuple[str, ...]:
        return tuple(str(path) for path in self.integration["evidence_files"])

    @property
    def confidence(self) -> Mapping[str, Any]:
        return self.confidence_document["confidence"]

    def to_dict(self) -> dict[str, Any]:
        return dict(self.document)


@dataclass(frozen=True, slots=True)
class QueryIntegration:
    """Complete scientific integration result for one query."""

    query_id: str
    predicted_ec: str | None
    completeness: str | None
    depth: int | None
    confidence: str
    reason_codes: tuple[str, ...]
    scorecards: tuple[dict[str, Any], ...]
    conflicts: tuple[Conflict, ...]
    rule_evaluations: tuple[dict[str, Any], ...]
    supporting_evidence_ids: tuple[str, ...]
    independent_evidence_classes: tuple[str, ...]
    availability: tuple[ProviderAvailability, ...]

    def annotation_dict(
        self,
        *,
        policy_id: str,
        policy_version: str,
        provenance: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        alternatives = [
            {
                "candidate_ec": card["candidate_ec"],
                "status": card["final_candidate_status"],
            }
            for card in self.scorecards
            if card["candidate_ec"] != self.predicted_ec
        ]
        result: dict[str, Any] = {
            "query_id": self.query_id,
            "predicted_ec": self.predicted_ec,
            "ec_completeness": self.completeness,
            "ec_depth": self.depth,
            "confidence": self.confidence,
            "alternative_candidates": alternatives,
            "major_conflicts": [
                item.conflict_id for item in self.conflicts if item.severity == "major"
            ],
            "supporting_evidence_ids": list(self.supporting_evidence_ids),
            "independent_evidence_classes": list(self.independent_evidence_classes),
            "provider_availability": [item.to_dict() for item in self.availability],
            "policy": {"id": policy_id, "version": policy_version},
            "rule_evaluation_ids": [
                item["evaluation_id"] for item in self.rule_evaluations
            ],
            "reason_codes": list(self.reason_codes),
        }
        if provenance is not None:
            result["provenance"] = dict(provenance)
        return result


def _load_yaml(path: Path, label: str) -> dict[str, Any]:
    try:
        value = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise IntegrationError(f"Cannot load {label} {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise IntegrationError(f"{label} {path} must contain a mapping")
    return value


def _validate(document: Mapping[str, Any], schema_path: Path, label: str) -> None:
    try:
        schema = json.loads(Path(schema_path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise IntegrationError(
            f"Cannot load {label} schema {schema_path}: {exc}"
        ) from exc
    errors = sorted(
        Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(
            document
        ),
        key=lambda error: list(error.path),
    )
    if errors:
        location = ".".join(str(value) for value in errors[0].absolute_path)
        raise IntegrationError(f"Invalid {label} at {location}: {errors[0].message}")


def load_integration_config(
    path: Path,
    *,
    schema_path: Path = Path("configs/schema/integration.schema.json"),
    confidence_schema_path: Path = Path("configs/schema/confidence.schema.json"),
    confidence_path: Path | None = None,
) -> IntegrationConfig:
    """Load integration and confidence policies with paths relative to the policy."""

    source = Path(path).resolve()
    document = _load_yaml(source, "integration configuration")
    _validate(document, schema_path, "integration configuration")
    configured_confidence = Path(document["integration"]["confidence_policy"])
    selected_confidence = (
        Path(confidence_path).resolve()
        if confidence_path
        else (source.parent / configured_confidence).resolve()
    )
    confidence = _load_yaml(selected_confidence, "confidence policy")
    _validate(confidence, confidence_schema_path, "confidence policy")
    providers = tuple(
        ProviderPolicy(
            source_id=str(value["source_id"]),
            stage_id=str(value["stage_id"]),
            required=bool(value["required"]),
            roles=tuple(str(role) for role in value["roles"]),
            evidence_path=value.get("evidence_path"),
            summary_path=value.get("summary_path"),
            summary_status_key=value.get("summary_status_key"),
        )
        for value in document["integration"]["providers"]
    )
    sources = [provider.source_id for provider in providers]
    if len(set(sources)) != len(sources):
        raise IntegrationError(
            "integration.providers contains duplicate source_id values"
        )
    return IntegrationConfig(
        document=document,
        source_path=source,
        confidence_document=confidence,
        confidence_path=selected_confidence,
        providers=providers,
    )


def _support_gate(card: CandidateScorecard, gate: Mapping[str, Any]) -> bool:
    return (
        len(card.independent_classes) >= gate["minimum_independent_classes"]
        and len(card.supporting_groups) >= gate["minimum_correlation_groups"]
        and set(gate["required_evidence_classes"]).issubset(card.supporting_classes)
    )


def _candidate_records(
    records: tuple[EvidenceRecord, ...], config: IntegrationConfig
) -> tuple[EvidenceRecord, ...]:
    sources = {
        provider.source_id
        for provider in config.providers
        if "candidate_source" in provider.roles
    }
    return tuple(record for record in records if record.source_id in sources)


def integrate_query(
    query_id: str,
    records: tuple[EvidenceRecord, ...],
    config: IntegrationConfig,
    *,
    availability: Sequence[ProviderAvailability] = (),
    ec_rules: ECRules | None = None,
    family_id: str | None = None,
) -> QueryIntegration:
    """Integrate one query using explicit gates, constraints, and conflict policy."""

    policy = config.integration
    specificity = policy["ec_specificity"]
    cards = build_candidate_scorecards(
        _candidate_records(records, config),
        inherit_complete_to_parents=bool(specificity["inherit_complete_to_parents"]),
    )
    all_evaluations: list[dict[str, Any]] = []
    for card in cards.values():
        candidate_rule = (
            ec_rules.rule_for(card.candidate_ec, family_id) if ec_rules else None
        )
        evaluations = evaluate_ec_rule(
            ec_rules, card.candidate_ec, records, family_id=family_id
        )
        for index, evaluation in enumerate(evaluations, start=1):
            evaluation["query_id"] = query_id
            evaluation["evaluation_id"] = (
                f"{query_id}:{card.candidate_ec}:{evaluation['rule_id']}:{index}"
            )
        card.rule_evaluations.extend(evaluations)
        all_evaluations.extend(evaluations)
        gate_name = (
            "exact_ec_requires"
            if card.completeness == "complete"
            else "partial_ec_requires"
        )
        if card.completeness == "partial" and not specificity["allow_partial"]:
            card.rejection_reasons.append("partial_ec_not_allowed")
            card.status = "rejected"
        elif (
            card.completeness == "partial"
            and candidate_rule is not None
            and not candidate_rule["allow_partial_prediction"]
        ):
            card.rejection_reasons.append("ec_rule_disallows_partial_prediction")
            card.status = "rejected"
        elif not _support_gate(card, specificity[gate_name]):
            card.rejection_reasons.append("insufficient_independent_support")
            card.status = "insufficient_support"
        elif not required_rules_passed(evaluations):
            card.rejection_reasons.append("required_constraint_failed")
            card.status = "rejected"
        elif card.contradicting_records:
            card.rejection_reasons.append("candidate_contradicted")
            card.status = "rejected"
        else:
            card.status = "eligible"

    available = tuple(sorted(availability, key=lambda item: item.source_id))
    conflict_policy = dict(policy["conflict_policy"])
    if ec_rules is not None:
        conflict_policy.update(ec_rules.document["conflict_policy"])
    conflicts = detect_conflicts(records, cards, available, conflict_policy)
    for card in cards.values():
        if "required_constraint_failed" not in card.rejection_reasons:
            continue
        failed = [
            item
            for item in card.rule_evaluations
            if item["requirement"] in {"required", "required_any"}
            and item["outcome"] == "fail"
        ]
        if not failed:
            continue
        handling = conflict_policy["rule_disagreement"]
        conflicts.append(
            Conflict(
                f"required_rule_failure:{card.candidate_ec}",
                "rule_disagreement",
                "required_biological_constraint_failed",
                (card.candidate_ec,),
                tuple(
                    sorted(
                        {
                            evidence_id
                            for item in failed
                            for evidence_id in item["evidence_ids"]
                        }
                    )
                ),
                tuple(
                    sorted(
                        {
                            group
                            for item in failed
                            for group in item["correlation_groups"]
                        }
                    )
                ),
                handling["severity"],
                handling["action"],
                "a configured required biological constraint failed",
            )
        )
    conflicts.sort(key=lambda item: item.conflict_id)
    for conflict in conflicts:
        for candidate in conflict.candidates:
            if candidate in cards:
                cards[candidate].conflicts.append(conflict.conflict_id)

    passed_rule_ids = {
        item["rule_id"] for item in all_evaluations if item["outcome"] == "pass"
    }

    def conflict_blocks(conflict: Conflict) -> bool:
        if conflict.severity != "major":
            return False
        if conflict.action == "force_unresolved":
            return True
        if conflict.action != "resolve_by_rule":
            return False
        required = set(
            conflict_policy[conflict.conflict_type].get("resolution_rule_ids", ())
        )
        return not required.issubset(passed_rule_ids)

    major_block = any(conflict_blocks(conflict) for conflict in conflicts)
    eligible = [card for card in cards.values() if card.status == "eligible"]
    selected: CandidateScorecard | None = None
    reasons: list[str] = []
    if not cards:
        reasons.append("no_ec_candidates")
    elif not eligible:
        reasons.append("no_eligible_candidate")
    elif major_block:
        for card in eligible:
            card.status = "conflicted"
        reasons.append("major_unresolved_conflict")
    else:
        selected_depth = (
            max(card.depth for card in eligible)
            if specificity["prefer_most_specific_supported"]
            else min(card.depth for card in eligible)
        )
        finalists = [card for card in eligible if card.depth == selected_depth]
        if len(finalists) == 1:
            selected = finalists[0]
            reasons.append(
                "most_specific_supported_candidate"
                if specificity["prefer_most_specific_supported"]
                else "broadest_supported_candidate"
            )
        elif (
            specificity["tie_policy"] == "common_parent"
            and specificity["allow_partial"]
        ):
            parent = common_ec_parent(tuple(card.candidate_ec for card in finalists))
            parent_card = cards.get(parent) if parent else None
            if parent_card is not None and parent_card.status == "eligible":
                selected = parent_card
                reasons.append("common_parent_fallback")
            else:
                reasons.extend(["tied_candidates", "insufficient_ec_specificity"])
        else:
            reasons.append("tied_candidates")

        if selected is None and len(finalists) > 1:
            handling = conflict_policy["hierarchy_disagreement"]
            conflicts.append(
                Conflict(
                    "insufficient_specificity",
                    "hierarchy_disagreement",
                    "insufficient_ec_specificity",
                    tuple(sorted(card.candidate_ec for card in finalists)),
                    tuple(
                        sorted(
                            {
                                record.evidence_id
                                for card in finalists
                                for record in card.supporting_records
                            }
                        )
                    ),
                    tuple(
                        sorted(
                            {
                                record.correlation_group
                                for card in finalists
                                for record in card.supporting_records
                            }
                        )
                    ),
                    handling["severity"],
                    handling["action"],
                    "tied candidates could not be resolved at an allowed EC depth",
                )
            )

    major_count = sum(item.severity == "major" for item in conflicts)
    minor_count = sum(item.severity == "minor" for item in conflicts)
    confidence_policy = deepcopy(config.confidence)
    if ec_rules is not None:
        for category in ("high", "medium", "low"):
            override = ec_rules.document["confidence_policy"][category]
            gate = confidence_policy[category]
            gate["minimum_independent_classes"] = override[
                "minimum_independent_classes"
            ]
            gate["required_rule_ids"] = list(override["required_rule_ids"])
            gate["minimum_supporting_records"] = override["minimum_supporting_records"]
            gate["maximum_conflicting_classes"] = override[
                "maximum_conflicting_classes"
            ]
            if override["require_complete_ec"]:
                gate["allowed_completeness"] = ["complete"]
    confidence, confidence_reasons = classify_confidence(
        selected,
        confidence_policy,
        major_conflicts=major_count,
        minor_conflicts=minor_count,
    )
    reasons.extend(confidence_reasons)
    if confidence == "unresolved":
        selected = None

    ordered_cards = sorted(
        cards.values(),
        key=lambda card: (
            -card.depth,
            -len(card.independent_classes),
            -len(card.supporting_groups),
            card.candidate_ec,
        ),
    )
    return QueryIntegration(
        query_id=query_id,
        predicted_ec=selected.candidate_ec if selected else None,
        completeness=selected.completeness if selected else None,
        depth=selected.depth if selected else None,
        confidence=confidence,
        reason_codes=tuple(dict.fromkeys(reasons)),
        scorecards=tuple(card.to_dict() for card in ordered_cards),
        conflicts=tuple(conflicts),
        rule_evaluations=tuple(
            sorted(all_evaluations, key=lambda item: item["evaluation_id"])
        ),
        supporting_evidence_ids=(
            tuple(sorted(item.evidence_id for item in selected.supporting_records))
            if selected
            else ()
        ),
        independent_evidence_classes=selected.independent_classes if selected else (),
        availability=available,
    )


def integrate_collection(
    collection: EvidenceCollection,
    config: IntegrationConfig,
    *,
    query_ids: Sequence[str] | None = None,
    availability: Sequence[ProviderAvailability] = (),
    ec_rules: ECRules | None = None,
    family_id: str | None = None,
) -> tuple[QueryIntegration, ...]:
    """Integrate all queries, including valid queries with no EC candidates."""

    grouped = collection.by_query()
    selected_queries = sorted(set(query_ids or grouped))
    return tuple(
        integrate_query(
            query_id,
            grouped.get(query_id, ()),
            config,
            availability=availability,
            ec_rules=ec_rules,
            family_id=family_id,
        )
        for query_id in selected_queries
    )
