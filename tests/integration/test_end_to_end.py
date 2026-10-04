"""Compact deterministic pipeline validation from FASTA through reports."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from enzynotation.config import load_config
from enzynotation.integration import load_integration_config
from enzynotation.provenance import sha256_bytes
from enzynotation.reporting import load_report_config
from enzynotation.runner import PipelineRunner
from enzynotation.stages.base import Stage, StageContext, StageResult
from enzynotation.stages.integrate import IntegrateStage
from enzynotation.stages.report import ReportStage
from enzynotation.stages.validate import ValidationStage
from enzynotation.state import StageStatus, atomic_write_json, atomic_write_text
from enzynotation.workflow import Workflow

FIXTURES = Path("tests/fixtures/e2e")
SEQUENCES = {
    "q_exact": "ACDEFGHIKL",
    "q_partial": "MNPQRSTVWY",
    "q_conflict": "GGGGAAAACC",
}


def _record(
    evidence_id: str,
    query_id: str,
    source: str,
    evidence_class: str,
    correlation_group: str,
    ec: str,
) -> dict[str, Any]:
    sequence = SEQUENCES[query_id]
    depth = next((index for index, part in enumerate(ec.split(".")) if part == "-"), 4)
    return {
        "schema_version": 1,
        "evidence_id": evidence_id,
        "query": {
            "query_id": query_id,
            "sequence_sha256": sha256_bytes(sequence.encode("ascii")),
            "sequence_length": len(sequence),
        },
        "source": {
            "id": source,
            "evidence_class": evidence_class,
            "correlation_group": correlation_group,
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
        "provenance": {
            "run_id": "synthetic-software-validation",
            "stage_id": source,
            "generated_at": "2026-01-01T00:00:00Z",
            "parser": {"name": "synthetic-fixture", "version": "1"},
            "tool": {"name": "synthetic-fixture", "version": "1"},
            "databases": [],
            "raw_artifact": {
                "path": f"stages/{source}/raw/synthetic.json",
                "format": "json",
            },
            "configuration_sha256": "a" * 64,
            "input_sha256": "b" * 64,
        },
        "message": "synthetic software behavior fixture; not a benchmark claim",
    }


class SyntheticEvidenceStage(Stage):
    """Deterministic provider fixture using the same canonical evidence contract."""

    def __init__(self, stage_id: str, source_id: str, filename: str, records) -> None:
        super().__init__(stage_id, dependencies=("validate",), required=False)
        self.source_id = source_id
        self.filename = filename
        self.records = tuple(records)

    def configuration(self, context: StageContext) -> dict[str, Any]:
        return {"fixture": "e2e-v1", "source": self.source_id}

    def execute(self, context: StageContext) -> StageResult:
        paths = context.paths.for_stage(self.stage_id)
        paths.prepare()
        raw = paths.raw / "synthetic.json"
        summary = paths.normalized / f"{self.stage_id}_summary.json"
        evidence = context.paths.evidence / self.filename
        atomic_write_json(raw, {"records": list(self.records)})
        atomic_write_text(
            evidence,
            "".join(
                json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n"
                for record in self.records
            ),
        )
        atomic_write_json(
            summary,
            {
                "schema_version": 1,
                "provider": self.source_id,
                "status": "completed",
                "counts": {"evidence_records": len(self.records)},
                "final_ec_prediction": None,
            },
        )
        return StageResult(
            True,
            {"raw": raw, "summary": summary, "evidence": evidence},
            message=f"emitted {len(self.records)} synthetic evidence record(s)",
        )


def _workflow() -> Workflow:
    blast = SyntheticEvidenceStage(
        "blast",
        "blastp",
        "blast_evidence.jsonl",
        (
            _record(
                "exact:blast",
                "q_exact",
                "blastp",
                "curated_annotation",
                "exact:blast",
                "1.1.1.1",
            ),
            _record(
                "partial:blast",
                "q_partial",
                "blastp",
                "curated_annotation",
                "partial:blast",
                "3.2.1.-",
            ),
            _record(
                "conflict:blast",
                "q_conflict",
                "blastp",
                "curated_annotation",
                "conflict:sequence",
                "1.1.1.1",
            ),
        ),
    )
    clean = SyntheticEvidenceStage(
        "clean",
        "clean",
        "clean_evidence.jsonl",
        (
            _record(
                "exact:clean",
                "q_exact",
                "clean",
                "learned_sequence_model",
                "exact:clean",
                "1.1.1.1",
            ),
        ),
    )
    foldseek = SyntheticEvidenceStage(
        "foldseek",
        "foldseek",
        "foldseek_evidence.jsonl",
        (
            _record(
                "exact:structure",
                "q_exact",
                "foldseek",
                "structure_homology",
                "structural:q_exact:ref",
                "1.1.1.1",
            ),
            _record(
                "conflict:structure",
                "q_conflict",
                "foldseek",
                "structure_homology",
                "structural:q_conflict:ref",
                "2.2.2.2",
            ),
        ),
    )
    integration = IntegrateStage(
        load_integration_config(Path("configs/integration/default.yaml"))
    )
    report = ReportStage(load_report_config())
    return Workflow([ValidationStage(), blast, clean, foldseek, integration, report])


def _projection(report: dict[str, Any]) -> dict[str, Any]:
    return {
        query["query_id"]: {
            "predicted_ec": query["annotation"]["predicted_ec"],
            "confidence": query["annotation"]["confidence"],
            "major_conflicts": query["annotation"]["major_conflicts"],
        }
        for query in report["queries"]
    }


def test_native_e2e_exact_partial_conflict_report_and_cache(tmp_path: Path) -> None:
    runner = PipelineRunner(
        load_config(),
        results_root=tmp_path / "results",
        logs_root=tmp_path / "logs",
    )
    source = FIXTURES / "input.fasta"
    workflow = _workflow()
    first = runner.run(source, workflow=workflow, run_id="native-e2e")
    report_path = tmp_path / "results/native-e2e/reports/report.json"
    first_bytes = report_path.read_bytes()
    report = json.loads(first_bytes)
    expected = json.loads((FIXTURES / "expected.json").read_text())
    projection = _projection(report)

    assert first.status.value == "completed"
    for query_id, expectation in expected.items():
        assert projection[query_id]["predicted_ec"] == expectation["predicted_ec"]
        assert projection[query_id]["confidence"] == expectation["confidence"]
    assert (
        "sequence_structure_disagreement" in projection["q_conflict"]["major_conflicts"]
    )

    second = runner.run(source, workflow=workflow, run_id="native-e2e")
    assert set(second.stage_outcomes.values()) == {StageStatus.SKIPPED}
    assert report_path.read_bytes() == first_bytes


def test_local_and_scheduled_stage_execution_have_scientific_parity(
    tmp_path: Path,
) -> None:
    source = FIXTURES / "input.fasta"
    workflow = _workflow()
    local = PipelineRunner(
        load_config(),
        results_root=tmp_path / "local-results",
        logs_root=tmp_path / "local-logs",
    )
    local.run(source, workflow=workflow, run_id="local")

    scheduled = PipelineRunner(
        load_config(),
        results_root=tmp_path / "scheduled-results",
        logs_root=tmp_path / "scheduled-logs",
    )
    for stage in workflow:
        result = scheduled.run_stage(
            source,
            workflow=workflow,
            stage_id=stage.stage_id,
            run_id="scheduled",
        )
        assert result.stage_outcomes[stage.stage_id] in {
            StageStatus.COMPLETED,
            StageStatus.SKIPPED,
        }

    local_report = json.loads(
        (tmp_path / "local-results/local/reports/report.json").read_text()
    )
    scheduled_report = json.loads(
        (tmp_path / "scheduled-results/scheduled/reports/report.json").read_text()
    )
    assert _projection(local_report) == _projection(scheduled_report)
