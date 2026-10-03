"""Pure reporting tests over completed Milestone 7 decisions."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, FormatChecker

from enzynotation.exceptions import ConfigurationError
from enzynotation.reporting import (
    ANNOTATION_COLUMNS,
    ReportInputs,
    annotation_rows,
    build_report,
    load_report_config,
)
from enzynotation.serialization import tsv_text

FIXTURE = Path("tests/fixtures/reporting/worked_cases.json")
TEMPLATE = Path("src/enzynotation/templates/report.html")
REPORT_SCHEMA = Path("configs/schema/report.schema.json")


def _record(
    evidence_id: str,
    query_id: str,
    source: str,
    evidence_class: str,
    group: str,
    ec: str,
    *,
    description: str = "curated reference",
) -> dict[str, object]:
    depth = next(
        (index for index, part in enumerate(ec.split(".")) if part == "-"),
        4,
    )
    return {
        "schema_version": 1,
        "evidence_id": evidence_id,
        "query": {
            "query_id": query_id,
            "sequence_sha256": "a" * 64,
            "sequence_length": 10,
        },
        "source": {
            "id": source,
            "evidence_class": evidence_class,
            "correlation_group": group,
        },
        "record_status": "observed",
        "assertion": {
            "target": {
                "type": "ec",
                "candidate_ec": {
                    "namespace": "EC",
                    "ec": ec,
                    "depth": depth,
                    "completeness": "complete" if depth == 4 else "partial",
                },
            },
            "effect": "supports",
        },
        "metrics": {"query_coverage": 0.8, "bit_score": 123.5},
        "reference": {
            "accession": "REF1",
            "description": description,
            "curated": True,
            "annotation_source": "fixture-db",
        },
        "provenance": {
            "run_id": "fixture-run",
            "stage_id": source,
            "generated_at": "2026-01-01T00:00:00Z",
            "parser": {"name": f"{source}-parser", "version": "1"},
            "tool": {"name": source, "version": "2.0"},
            "databases": [{"name": "fixture-db", "version": "2026_01"}],
            "raw_artifact": {
                "path": f"stages/{source}/raw/output.tsv",
                "format": "tsv",
            },
            "configuration_sha256": "b" * 64,
            "input_sha256": "c" * 64,
            "command": [source, "search", "--safe"],
        },
    }


def _bundle(tmp_path: Path):
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    shared_group = "structural:q_competing:reference-1"
    evidence = (
        _record(
            "ev:high:blast",
            "q_exact_high",
            "blastp",
            "curated_annotation",
            "blast:q_exact_high:ref",
            "1.1.1.1",
            description="alpha\tbeta\n<script>alert(1)</script> Café",
        ),
        _record(
            "ev:competing:structure",
            "q_competing",
            "foldseek",
            "structure_homology",
            shared_group,
            "2.2.2.2",
        ),
        _record(
            "ev:competing:tmalign",
            "q_competing",
            "tmalign",
            "structure_homology",
            shared_group,
            "2.2.2.2",
        ),
    )
    evidence_path = tmp_path / "evidence.jsonl"
    evidence_path.write_text(
        "".join(json.dumps(value, sort_keys=True) + "\n" for value in evidence),
        encoding="utf-8",
    )
    final = {
        "schema_version": 1,
        "generated_at": fixture["generated_at"],
        "annotations": fixture["annotations"],
        "provenance": {
            "policy": {
                "id": "fixture-policy",
                "version": "1",
                "calibration": "heuristic",
            },
            "integration_configuration_sha256": "d" * 64,
        },
    }
    inputs = ReportInputs(
        run_id="fixture-run",
        final_annotation=final,
        candidates=tuple(fixture["candidates"]),
        conflicts=tuple(fixture["conflicts"]),
        rule_evaluations=tuple(fixture["rule_evaluations"]),
        evidence=evidence,
        evidence_files=(evidence_path,),
        manifest={
            "input": {"source": "/portable/input.fasta", "sha256": "e" * 64},
            "configuration": {
                "sha256": "f" * 64,
                "snapshot": "config/resolved-pipeline.yaml",
            },
        },
        integration_status_path="stages/integrate/status.json",
    )
    return build_report(
        inputs,
        load_report_config(),
        template_text=TEMPLATE.read_text(encoding="utf-8"),
        run_relative_evidence_paths=("evidence/provider_evidence.jsonl",),
    )


def test_annotations_preserve_exact_partial_unresolved_and_alternatives(
    tmp_path: Path,
) -> None:
    bundle = _bundle(tmp_path)
    values = {row["query_id"]: row for row in bundle.annotations}
    assert values["q_exact_high"]["predicted_ec"] == "1.1.1.1"
    assert values["q_exact_high"]["annotation_status"] == "exact"
    assert values["q_exact_lower"]["confidence"] == "medium"
    assert values["q_partial"]["annotation_status"] == "partial"
    assert values["q_partial"]["predicted_ec"] == "3.2.1.-"
    assert values["q_competing"]["annotation_status"] == "unresolved"
    assert values["q_competing"]["predicted_ec"] is None
    assert values["q_competing"]["alternative_candidates"]
    assert values["q_exact_lower"]["minor_conflict_count"] == 1
    assert values["q_constraint"]["major_conflict_count"] == 1


def test_all_flattened_tables_keep_trace_fields_and_stable_order(
    tmp_path: Path,
) -> None:
    bundle = _bundle(tmp_path)
    assert [row["query_id"] for row in bundle.annotations] == sorted(
        row["query_id"] for row in bundle.annotations
    )
    assert bundle.candidates[0]["supporting_evidence_ids"]
    assert bundle.conflicts[0]["conflict_id"]
    assert {row["outcome"] for row in bundle.rule_evaluations} == {"pass", "fail"}
    failed = next(row for row in bundle.provider_status if row["state"] == "failed")
    assert failed["reason"] == "command failed <unsafe>"
    zero = next(
        row
        for row in bundle.provider_status
        if row["state"] == "successful_zero_evidence"
    )
    assert zero["provider"] == "hmmer"
    assert [row["evidence_id"] for row in bundle.evidence] == sorted(
        row["evidence_id"] for row in bundle.evidence
    )
    assert bundle.evidence[0]["metrics"]


def test_annotations_tsv_has_declared_columns_and_empty_unresolved_ec(
    tmp_path: Path,
) -> None:
    bundle = _bundle(tmp_path)
    text = tsv_text(ANNOTATION_COLUMNS, bundle.annotations)
    assert text.splitlines()[0].split("\t") == list(ANNOTATION_COLUMNS)
    unresolved = next(
        line for line in text.splitlines() if line.startswith("q_competing\t")
    )
    assert unresolved.split("\t")[1] == ""


def test_report_json_validates_and_summary_is_descriptive(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path)
    schema = json.loads(REPORT_SCHEMA.read_text(encoding="utf-8"))
    errors = list(
        Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(
            bundle.report
        )
    )
    assert errors == []
    assert bundle.report["run"]["input"] == {
        "name": "input.fasta",
        "sha256": "e" * 64,
    }
    assert "/portable" not in json.dumps(bundle.report)
    assert bundle.run_summary == {
        "schema_version": 1,
        "total_queries": 6,
        "exact_annotations": 2,
        "partial_annotations": 1,
        "unresolved_queries": 3,
        "high_confidence": 1,
        "medium_confidence": 1,
        "low_confidence": 1,
        "unresolved_confidence": 3,
        "major_conflicts": 2,
        "minor_conflicts": 1,
        "provider_failures": 1,
        "provider_unavailable": 0,
    }


def test_html_is_escaped_and_keeps_scientific_distinctions(tmp_path: Path) -> None:
    html = _bundle(tmp_path).html
    assert "status-exact" in html
    assert "status-partial" in html
    assert "status-unresolved" in html
    assert "EC 3.2.1.- (partial / incomplete)" in html
    assert "Unresolved" in html
    assert "high confidence" in html
    assert "heuristic and categorical" in html
    assert "conflicts major" in html
    assert "structural:q_competing:reference-1" in html
    assert "command failed &lt;unsafe&gt;" in html
    assert "Run provenance" in html
    assert "&lt;script&gt;" in html
    assert "<script>alert(1)</script>" not in html


def test_reporting_is_byte_deterministic_and_does_not_change_decisions(
    tmp_path: Path,
) -> None:
    first = _bundle(tmp_path)
    second = _bundle(tmp_path)
    assert first.report == second.report
    assert first.html == second.html
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    decided = {
        value["query_id"]: (value["predicted_ec"], value["confidence"])
        for value in fixture["annotations"]
    }
    reported = {
        value["query_id"]: (value["predicted_ec"], value["confidence"])
        for value in first.annotations
    }
    assert reported == decided


def test_annotation_row_function_does_not_rank_candidates() -> None:
    annotation = {
        "query_id": "q",
        "predicted_ec": None,
        "ec_depth": None,
        "ec_completeness": None,
        "confidence": "unresolved",
        "reason_codes": ["tied_candidates"],
        "alternative_candidates": ["9.9.9.9", "1.1.1.1"],
        "independent_evidence_classes": [],
        "policy": {"id": "p", "version": "1"},
    }
    rows = annotation_rows((annotation,), (), ())
    assert rows[0]["predicted_ec"] is None
    assert rows[0]["alternative_candidates"] == ["9.9.9.9", "1.1.1.1"]


def test_report_configuration_loader_enforces_schema(tmp_path: Path) -> None:
    invalid = tmp_path / "report.yaml"
    invalid.write_text(
        "schema_version: 1\n"
        "report:\n"
        "  title: Example\n"
        "  include_detailed_evidence: true\n"
        "  include_provenance: true\n"
        "  scientific_threshold: 0.9\n",
        encoding="utf-8",
    )
    with pytest.raises(ConfigurationError, match="Additional properties"):
        load_report_config(invalid)
