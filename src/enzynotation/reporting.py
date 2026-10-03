"""Pure presentation of Milestone 7 integration outputs."""

from __future__ import annotations

import html
import json
import platform
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from string import Template
from typing import Any

import yaml
from jsonschema import Draft202012Validator, FormatChecker

from enzynotation import __version__
from enzynotation.exceptions import ConfigurationError
from enzynotation.provenance import sha256_file

ANNOTATION_COLUMNS = (
    "query_id",
    "predicted_ec",
    "ec_depth",
    "ec_completeness",
    "confidence",
    "annotation_status",
    "reason_codes",
    "alternative_candidates",
    "major_conflict_count",
    "minor_conflict_count",
    "independent_evidence_class_count",
    "independent_correlation_group_count",
    "integration_policy_id",
    "integration_policy_version",
)

EVIDENCE_COLUMNS = (
    "query_id",
    "evidence_id",
    "provider",
    "evidence_class",
    "correlation_group",
    "record_status",
    "effect",
    "candidate_ec",
    "metrics",
    "reference_accession",
    "reference_description",
    "tool",
    "tool_version",
    "model_identifier",
    "model_version",
    "databases",
    "database_versions",
    "raw_artifact",
)

CANDIDATE_COLUMNS = (
    "query_id",
    "candidate_ec",
    "ec_depth",
    "completeness",
    "candidate_status",
    "supporting_evidence_ids",
    "contradicting_evidence_ids",
    "independent_evidence_classes",
    "supporting_correlation_groups",
    "failed_required_constraints",
    "conflict_ids",
    "rejection_reason_codes",
    "confidence_features",
)

CONFLICT_COLUMNS = (
    "query_id",
    "conflict_id",
    "conflict_type",
    "severity",
    "action",
    "candidate_ecs",
    "evidence_ids",
    "correlation_groups",
    "reason_code",
    "explanation",
)

RULE_COLUMNS = (
    "query_id",
    "candidate_ec",
    "rule_id",
    "rule_source",
    "rule_version",
    "outcome",
    "evidence_ids",
    "correlation_groups",
    "reason_code",
    "explanation",
)

PROVIDER_COLUMNS = (
    "query_id",
    "provider",
    "state",
    "required",
    "roles",
    "reason",
)

DEFAULT_REPORT_CONFIG: dict[str, Any] = {
    "schema_version": 1,
    "report": {
        "title": "EnzyNotation final report",
        "include_detailed_evidence": True,
        "include_provenance": True,
    },
}


@dataclass(frozen=True, slots=True)
class ReportConfig:
    """Presentation-only report settings."""

    document: Mapping[str, Any]
    source_path: Path | None = None

    @property
    def title(self) -> str:
        return str(self.document["report"]["title"])

    @property
    def include_detailed_evidence(self) -> bool:
        return bool(self.document["report"]["include_detailed_evidence"])

    @property
    def include_provenance(self) -> bool:
        return bool(self.document["report"]["include_provenance"])


@dataclass(frozen=True, slots=True)
class ReportInputs:
    """Already-decided integration artifacts consumed by reporting."""

    run_id: str
    final_annotation: Mapping[str, Any]
    candidates: tuple[Mapping[str, Any], ...]
    conflicts: tuple[Mapping[str, Any], ...]
    rule_evaluations: tuple[Mapping[str, Any], ...]
    evidence: tuple[Mapping[str, Any], ...]
    evidence_files: tuple[Path, ...]
    manifest: Mapping[str, Any]
    integration_status_path: str


@dataclass(frozen=True, slots=True)
class ReportBundle:
    """All deterministic rows and documents produced by reporting."""

    annotations: tuple[dict[str, Any], ...]
    evidence: tuple[dict[str, Any], ...]
    candidates: tuple[dict[str, Any], ...]
    conflicts: tuple[dict[str, Any], ...]
    rule_evaluations: tuple[dict[str, Any], ...]
    provider_status: tuple[dict[str, Any], ...]
    report: dict[str, Any]
    run_summary: dict[str, Any]
    html: str


def load_report_config(
    path: Path | None = None,
    *,
    schema_path: Path = Path("configs/schema/report-config.schema.json"),
) -> ReportConfig:
    """Load and validate presentation-only YAML configuration."""

    if path is None:
        document = json.loads(json.dumps(DEFAULT_REPORT_CONFIG))
        source = None
    else:
        source = Path(path).resolve()
        try:
            document = yaml.safe_load(source.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError) as exc:
            raise ConfigurationError(
                f"Cannot load report configuration {path}: {exc}"
            ) from exc
    try:
        schema = json.loads(Path(schema_path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigurationError(
            f"Cannot load report configuration schema {schema_path}: {exc}"
        ) from exc
    errors = sorted(
        Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(
            document
        ),
        key=lambda error: list(error.path),
    )
    if errors:
        location = ".".join(str(value) for value in errors[0].absolute_path)
        rendered = f" at {location}" if location else ""
        raise ConfigurationError(
            f"Invalid report configuration{rendered}: {errors[0].message}"
        )
    return ReportConfig(document, source)


def annotation_status(annotation: Mapping[str, Any]) -> str:
    """Render exact/partial/unresolved directly from the final annotation."""

    if annotation.get("predicted_ec") is None:
        return "unresolved"
    if annotation.get("ec_completeness") == "partial":
        return "partial"
    return "exact"


def _conflicts_by_query(
    conflicts: Sequence[Mapping[str, Any]],
) -> dict[str, list[Mapping[str, Any]]]:
    grouped: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for conflict in conflicts:
        grouped[str(conflict["query_id"])].append(conflict)
    return grouped


def annotation_rows(
    annotations: Sequence[Mapping[str, Any]],
    conflicts: Sequence[Mapping[str, Any]],
    candidates: Sequence[Mapping[str, Any]],
) -> tuple[dict[str, Any], ...]:
    """Flatten final annotations without changing their decisions."""

    conflict_groups = _conflicts_by_query(conflicts)
    candidate_groups: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for candidate in candidates:
        candidate_groups[str(candidate["query_id"])].append(candidate)
    rows: list[dict[str, Any]] = []
    for annotation in sorted(annotations, key=lambda value: str(value["query_id"])):
        query_id = str(annotation["query_id"])
        query_conflicts = conflict_groups.get(query_id, [])
        selected = next(
            (
                value
                for value in candidate_groups.get(query_id, [])
                if value.get("candidate_ec") == annotation.get("predicted_ec")
            ),
            None,
        )
        groups = selected.get("supporting_correlation_groups", []) if selected else []
        policy = annotation.get("policy", {})
        rows.append(
            {
                "query_id": query_id,
                "predicted_ec": annotation.get("predicted_ec"),
                "ec_depth": annotation.get("ec_depth"),
                "ec_completeness": annotation.get("ec_completeness"),
                "confidence": annotation["confidence"],
                "annotation_status": annotation_status(annotation),
                "reason_codes": annotation.get("reason_codes", []),
                "alternative_candidates": annotation.get("alternative_candidates", []),
                "major_conflict_count": sum(
                    value.get("severity") == "major" for value in query_conflicts
                ),
                "minor_conflict_count": sum(
                    value.get("severity") == "minor" for value in query_conflicts
                ),
                "independent_evidence_class_count": len(
                    annotation.get("independent_evidence_classes", [])
                ),
                "independent_correlation_group_count": len(groups),
                "integration_policy_id": policy.get("id"),
                "integration_policy_version": policy.get("version"),
            }
        )
    return tuple(rows)


def evidence_rows(evidence: Sequence[Mapping[str, Any]]) -> tuple[dict[str, Any], ...]:
    """Flatten stable evidence fields and retain provider metrics as JSON cells."""

    rows: list[dict[str, Any]] = []
    for record in sorted(
        evidence,
        key=lambda value: (
            str(value["query"]["query_id"]),
            str(value["evidence_id"]),
        ),
    ):
        assertion = record.get("assertion", {})
        target = assertion.get("target", {}) if isinstance(assertion, Mapping) else {}
        candidate = (
            target.get("candidate_ec", {}) if isinstance(target, Mapping) else {}
        )
        provenance = record["provenance"]
        tool = provenance.get("tool", {})
        databases = provenance.get("databases", [])
        metrics = record.get("metrics", {})
        reference = record.get("reference", {})
        rows.append(
            {
                "query_id": record["query"]["query_id"],
                "evidence_id": record["evidence_id"],
                "provider": record["source"]["id"],
                "evidence_class": record["source"]["evidence_class"],
                "correlation_group": record["source"]["correlation_group"],
                "record_status": record["record_status"],
                "effect": assertion.get("effect"),
                "candidate_ec": candidate.get("ec"),
                "metrics": metrics,
                "reference_accession": reference.get("accession"),
                "reference_description": reference.get("description"),
                "tool": tool.get("name"),
                "tool_version": tool.get("version"),
                "model_identifier": metrics.get("model_identifier"),
                "model_version": metrics.get("model_version"),
                "databases": [value.get("name") for value in databases],
                "database_versions": [value.get("version") for value in databases],
                "raw_artifact": provenance.get("raw_artifact", {}).get("path"),
            }
        )
    return tuple(rows)


def candidate_rows(
    candidates: Sequence[Mapping[str, Any]],
) -> tuple[dict[str, Any], ...]:
    """Select reporting fields while preserving Milestone 7 row order."""

    return tuple(
        {
            "query_id": value["query_id"],
            "candidate_ec": value["candidate_ec"],
            "ec_depth": value["depth"],
            "completeness": value["completeness"],
            "candidate_status": value["final_candidate_status"],
            "supporting_evidence_ids": value.get("supporting_evidence_ids", []),
            "contradicting_evidence_ids": value.get("contradicting_evidence_ids", []),
            "independent_evidence_classes": value.get(
                "independent_evidence_classes", []
            ),
            "supporting_correlation_groups": value.get(
                "supporting_correlation_groups", []
            ),
            "failed_required_constraints": value.get("failed_constraints", []),
            "conflict_ids": value.get("conflicts", []),
            "rejection_reason_codes": value.get("rejection_reasons", []),
            "confidence_features": value.get("confidence_features", {}),
        }
        for value in candidates
    )


def conflict_rows(
    conflicts: Sequence[Mapping[str, Any]],
) -> tuple[dict[str, Any], ...]:
    """Preserve explicit integration conflicts in stable order."""

    return tuple(
        {
            "query_id": value["query_id"],
            "conflict_id": value.get("conflict_id"),
            "conflict_type": value["conflict_type"],
            "severity": value["severity"],
            "action": value["action"],
            "candidate_ecs": value.get("candidate_ecs", []),
            "evidence_ids": value.get("evidence_ids", []),
            "correlation_groups": value.get("correlation_groups", []),
            "reason_code": value.get("reason_code"),
            "explanation": value.get("explanation"),
        }
        for value in sorted(
            conflicts,
            key=lambda item: (str(item["query_id"]), str(item.get("conflict_id", ""))),
        )
    )


def rule_rows(
    evaluations: Sequence[Mapping[str, Any]],
) -> tuple[dict[str, Any], ...]:
    """Preserve pass/fail/not-applicable/unknown rule traces."""

    return tuple(
        {
            "query_id": value["query_id"],
            "candidate_ec": value.get("candidate_ec"),
            "rule_id": value["rule_id"],
            "rule_source": value.get("config_source"),
            "rule_version": value.get("ruleset_version"),
            "outcome": value["outcome"],
            "evidence_ids": value.get("evidence_ids", []),
            "correlation_groups": value.get("correlation_groups", []),
            "reason_code": value.get("reason_code"),
            "explanation": value.get("explanation"),
        }
        for value in sorted(
            evaluations,
            key=lambda item: (
                str(item["query_id"]),
                str(item.get("candidate_ec", "")),
                str(item.get("evaluation_id", item["rule_id"])),
            ),
        )
    )


def provider_rows(
    annotations: Sequence[Mapping[str, Any]],
) -> tuple[dict[str, Any], ...]:
    """Flatten operational availability without treating it as evidence."""

    rows = [
        {
            "query_id": annotation["query_id"],
            "provider": provider["source_id"],
            "state": (
                "successful_zero_evidence"
                if provider["state"] == "successful_zero"
                else provider["state"]
            ),
            "required": provider.get("required", False),
            "roles": provider.get("roles", []),
            "reason": provider.get("reason"),
        }
        for annotation in annotations
        for provider in annotation.get("provider_availability", [])
    ]
    return tuple(
        sorted(rows, key=lambda value: (str(value["query_id"]), str(value["provider"])))
    )


def _run_summary(
    annotations: Sequence[Mapping[str, Any]],
    conflicts: Sequence[Mapping[str, Any]],
    providers: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    statuses = Counter(annotation_status(value) for value in annotations)
    confidence = Counter(str(value["confidence"]) for value in annotations)
    states = Counter(str(value["state"]) for value in providers)
    return {
        "schema_version": 1,
        "total_queries": len(annotations),
        "exact_annotations": statuses["exact"],
        "partial_annotations": statuses["partial"],
        "unresolved_queries": statuses["unresolved"],
        "high_confidence": confidence["high"],
        "medium_confidence": confidence["medium"],
        "low_confidence": confidence["low"],
        "unresolved_confidence": confidence["unresolved"],
        "major_conflicts": sum(value.get("severity") == "major" for value in conflicts),
        "minor_conflicts": sum(value.get("severity") == "minor" for value in conflicts),
        "provider_failures": states["failed"],
        "provider_unavailable": states["unavailable"],
    }


def _provenance_summary(inputs: ReportInputs) -> dict[str, Any]:
    tools: set[tuple[str, str, str | None, str | None]] = set()
    databases: set[tuple[str, str]] = set()
    models: set[tuple[str, str]] = set()
    raw_artifacts: set[str] = set()
    for record in inputs.evidence:
        provenance = record.get("provenance", {})
        tool = provenance.get("tool", {})
        tools.add(
            (
                str(tool.get("name", "unknown")),
                str(tool.get("version", "unknown")),
                tool.get("container_image"),
                tool.get("container_digest"),
            )
        )
        for database in provenance.get("databases", []):
            databases.add((str(database.get("name")), str(database.get("version"))))
        metrics = record.get("metrics", {})
        if metrics.get("model_identifier"):
            models.add(
                (
                    str(metrics["model_identifier"]),
                    str(metrics.get("model_version", "unknown")),
                )
            )
        raw = provenance.get("raw_artifact", {}).get("path")
        if raw:
            raw_artifacts.add(str(raw))
    final_provenance = inputs.final_annotation.get("provenance", {})
    manifest_configuration = inputs.manifest.get("configuration", {})
    configuration = {
        "sha256": manifest_configuration.get("sha256"),
        "snapshot": manifest_configuration.get("snapshot"),
        "sources": [
            {
                "name": Path(str(value.get("path", "unknown"))).name,
                "sha256": value.get("sha256"),
            }
            for value in manifest_configuration.get("sources", [])
        ],
    }
    return {
        "python_version": platform.python_version(),
        "manifest": "manifest.json",
        "integration_status": inputs.integration_status_path,
        "configuration": configuration,
        "integration": final_provenance,
        "tools": [
            {
                "name": name,
                "version": version,
                "container_image": image,
                "container_digest": digest,
            }
            for name, version, image, digest in sorted(
                tools, key=lambda value: tuple(str(item or "") for item in value)
            )
        ],
        "databases": [
            {"name": name, "version": version} for name, version in sorted(databases)
        ],
        "models": [
            {"identifier": identifier, "version": version}
            for identifier, version in sorted(models)
        ],
        "raw_artifacts": sorted(raw_artifacts),
    }


def _esc(value: Any) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def _json_html(value: Any) -> str:
    return _esc(json.dumps(value, ensure_ascii=False, sort_keys=True))


def _html_table(headers: Sequence[str], rows: Sequence[Sequence[Any]]) -> str:
    head = "".join(f"<th>{_esc(value)}</th>" for value in headers)
    body = "".join(
        "<tr>" + "".join(f"<td>{_esc(value)}</td>" for value in row) + "</tr>"
        for row in rows
    )
    return f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"


def _query_html(query: Mapping[str, Any], *, include_detailed_evidence: bool) -> str:
    annotation = query["annotation"]
    status = annotation["annotation_status"]
    predicted = annotation.get("predicted_ec") or "Unresolved"
    ec_label = f"EC {predicted}"
    if status == "partial":
        ec_label += " (partial / incomplete)"
    conflicts = query["conflicts"]
    candidates = query["candidates"]
    providers = query["provider_status"]
    evidence = query["evidence_summary"]
    rules = query["rule_evaluations"]
    major = [value for value in conflicts if value.get("severity") == "major"]
    conflict_html = (
        '<p class="none">No explicit conflicts.</p>'
        if not conflicts
        else _html_table(
            ("Severity", "Type", "Reason", "Candidates", "Groups", "Explanation"),
            [
                (
                    value.get("severity"),
                    value.get("conflict_type"),
                    value.get("reason_code"),
                    json.dumps(value.get("candidate_ecs", []), ensure_ascii=False),
                    json.dumps(value.get("correlation_groups", []), ensure_ascii=False),
                    value.get("explanation"),
                )
                for value in conflicts
            ],
        )
    )
    candidate_html = _html_table(
        (
            "Candidate",
            "Depth",
            "Status",
            "Independent classes",
            "Correlation groups",
            "Conflicts",
        ),
        [
            (
                value.get("candidate_ec"),
                value.get("ec_depth"),
                value.get("candidate_status"),
                json.dumps(
                    value.get("independent_evidence_classes", []),
                    ensure_ascii=False,
                ),
                json.dumps(
                    value.get("supporting_correlation_groups", []),
                    ensure_ascii=False,
                ),
                json.dumps(value.get("conflict_ids", []), ensure_ascii=False),
            )
            for value in candidates
        ],
    )
    provider_html = _html_table(
        ("Provider", "State", "Required", "Roles", "Operational reason"),
        [
            (
                value.get("provider"),
                value.get("state"),
                value.get("required"),
                json.dumps(value.get("roles", []), ensure_ascii=False),
                value.get("reason"),
            )
            for value in providers
        ],
    )
    evidence_html = (
        _html_table(
            (
                "Evidence",
                "Provider",
                "Class",
                "State",
                "Effect",
                "Candidate",
                "Correlation group",
                "Metrics",
            ),
            [
                (
                    value.get("evidence_id"),
                    value.get("provider"),
                    value.get("evidence_class"),
                    value.get("record_status"),
                    value.get("effect"),
                    value.get("candidate_ec"),
                    value.get("correlation_group"),
                    json.dumps(
                        value.get("metrics", {}),
                        ensure_ascii=False,
                        sort_keys=True,
                    ),
                )
                for value in evidence
            ],
        )
        if include_detailed_evidence
        else "<p>Detailed evidence is available in evidence.tsv.</p>"
    )
    rule_html = _html_table(
        ("Rule", "Candidate", "Outcome", "Reason", "Evidence", "Groups"),
        [
            (
                value.get("rule_id"),
                value.get("candidate_ec"),
                value.get("outcome"),
                value.get("reason_code"),
                json.dumps(value.get("evidence_ids", []), ensure_ascii=False),
                json.dumps(value.get("correlation_groups", []), ensure_ascii=False),
            )
            for value in rules
        ],
    )
    reason_codes = _json_html(annotation.get("reason_codes", []))
    alternatives = _json_html(annotation.get("alternative_candidates", []))
    conflict_class = "conflicts major" if major else "conflicts"
    return f"""
<article class="query status-{_esc(status)}">
  <header>
    <p class="query-id">{_esc(annotation["query_id"])}</p>
    <h2>{_esc(ec_label)}</h2>
    <span class="badge status">{_esc(status)}</span>
    <span class="badge confidence">{_esc(annotation["confidence"])} confidence</span>
  </header>
  <p><strong>Reason codes:</strong> {reason_codes}</p>
  <p><strong>Alternatives:</strong> {alternatives}</p>
  <section class="{conflict_class}"><h3>Conflicts</h3>{conflict_html}</section>
  <details open><summary>Candidate scorecards</summary>{candidate_html}</details>
  <details><summary>Supporting and contradicting evidence</summary>
    {evidence_html}
  </details>
  <details><summary>Provider availability</summary>{provider_html}</details>
  <details><summary>Rule evaluations</summary>{rule_html}</details>
</article>
"""


def render_html(
    report: Mapping[str, Any], config: ReportConfig, template_text: str
) -> str:
    """Render a static escaped HTML presentation without inference logic."""

    summary = report["run_summary"]
    overview = _html_table(
        ("Queries", "Exact", "Partial", "Unresolved", "Major conflicts"),
        [
            (
                summary["total_queries"],
                summary["exact_annotations"],
                summary["partial_annotations"],
                summary["unresolved_queries"],
                summary["major_conflicts"],
            )
        ],
    )
    queries = "".join(
        _query_html(query, include_detailed_evidence=config.include_detailed_evidence)
        for query in report["queries"]
    )
    provenance_json = _json_html(report["provenance"])
    provenance = ""
    if config.include_provenance:
        provenance = (
            "<details><summary>Run provenance</summary>"
            f"<pre>{provenance_json}</pre></details>"
        )
    return Template(template_text).substitute(
        title=_esc(config.title),
        run_id=_esc(report["run"]["run_id"]),
        generated_at=_esc(report["generated_at"]),
        overview=overview,
        queries=queries,
        provenance=provenance,
    )


def build_report(
    inputs: ReportInputs,
    config: ReportConfig,
    *,
    template_text: str,
    run_relative_evidence_paths: Sequence[str],
) -> ReportBundle:
    """Build all reports solely from existing integration decisions."""

    final_annotations = tuple(inputs.final_annotation["annotations"])
    annotations = annotation_rows(
        final_annotations,
        inputs.conflicts,
        inputs.candidates,
    )
    evidence = evidence_rows(inputs.evidence)
    candidates = candidate_rows(inputs.candidates)
    conflicts = conflict_rows(inputs.conflicts)
    rules = rule_rows(inputs.rule_evaluations)
    providers = provider_rows(final_annotations)
    summary = _run_summary(final_annotations, conflicts, providers)
    conflict_groups = _conflicts_by_query(conflicts)
    candidates_by_query: dict[str, list[dict[str, Any]]] = defaultdict(list)
    rules_by_query: dict[str, list[dict[str, Any]]] = defaultdict(list)
    evidence_by_query: dict[str, list[dict[str, Any]]] = defaultdict(list)
    providers_by_query: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for value in candidates:
        candidates_by_query[str(value["query_id"])].append(value)
    for value in rules:
        rules_by_query[str(value["query_id"])].append(value)
    for value in evidence:
        evidence_by_query[str(value["query_id"])].append(value)
    for value in providers:
        providers_by_query[str(value["query_id"])].append(value)
    evidence_files = [
        {
            "path": relative,
            "sha256": sha256_file(path),
            "record_count": sum(
                1
                for line in path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ),
        }
        for path, relative in zip(
            inputs.evidence_files, run_relative_evidence_paths, strict=True
        )
    ]
    policy = final_annotations[0].get("policy", {}) if final_annotations else {}
    final_by_query = {str(value["query_id"]): value for value in final_annotations}
    manifest_input = inputs.manifest.get("input", {})
    portable_input = {
        "name": Path(str(manifest_input.get("source", "input.fasta"))).name,
        "sha256": manifest_input.get("sha256"),
    }
    report: dict[str, Any] = {
        "schema_version": 1,
        "report_version": "1",
        "enzynotation_version": __version__,
        "generated_at": inputs.final_annotation["generated_at"],
        "run": {
            "run_id": inputs.run_id,
            "input": portable_input,
            "manifest": "manifest.json",
        },
        "integration_policy": {
            "id": policy.get("id"),
            "version": policy.get("version"),
        },
        "run_summary": summary,
        "provider_availability": list(providers),
        "queries": [
            {
                "query_id": annotation["query_id"],
                "annotation": {
                    **final_by_query[str(annotation["query_id"])],
                    **annotation,
                },
                "candidates": candidates_by_query[str(annotation["query_id"])],
                "conflicts": conflict_groups.get(str(annotation["query_id"]), []),
                "rule_evaluations": rules_by_query[str(annotation["query_id"])],
                "evidence_ids": [
                    value["evidence_id"]
                    for value in evidence_by_query[str(annotation["query_id"])]
                ],
                "evidence_summary": evidence_by_query[str(annotation["query_id"])],
                "provider_status": providers_by_query[str(annotation["query_id"])],
            }
            for annotation in annotations
        ],
        "evidence_files": evidence_files,
        "provenance": _provenance_summary(inputs),
    }
    rendered = render_html(report, config, template_text)
    return ReportBundle(
        annotations,
        evidence,
        candidates,
        conflicts,
        rules,
        providers,
        report,
        summary,
        rendered,
    )
