"""Integration tests for catalytic motif evidence and resumability."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker

from enzynotation.config import load_config
from enzynotation.family import load_family_profile
from enzynotation.paths import RunPaths
from enzynotation.runner import PipelineRunner
from enzynotation.stages.motifs import MotifsStage
from enzynotation.stages.validate import ValidationStage
from enzynotation.state import RunStatus, StageStateStore, StageStatus
from enzynotation.workflow import Workflow

FIXTURES = Path("tests/fixtures/domains")


def _profile(tmp_path: Path):
    path = tmp_path / "family.yaml"
    shutil.copyfile(FIXTURES / "family.yaml", path)
    return load_family_profile(path)


def _runner(tmp_path: Path) -> PipelineRunner:
    return PipelineRunner(
        load_config(),
        results_root=tmp_path / "results",
        logs_root=tmp_path / "logs",
    )


def _workflow(profile) -> Workflow:
    return Workflow([ValidationStage(), MotifsStage(profile)])


def _records(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line]


def test_motif_stage_emits_hits_absences_relationships_and_schema_valid_evidence(
    tmp_path: Path, fasta_file
) -> None:
    profile = _profile(tmp_path)
    source = fasta_file(">q1\nAAACATAAANDPAAAAAAAAAA\n")
    result = _runner(tmp_path).run(
        source, workflow=_workflow(profile), run_id="motif-success"
    )
    paths = RunPaths(tmp_path / "results", tmp_path / "logs", "motif-success")
    stage = paths.for_stage("motifs")
    evidence = _records(paths.evidence / "motif_evidence.jsonl")
    summary = json.loads((stage.normalized / "motif_summary.json").read_text())

    assert result.status is RunStatus.COMPLETED
    assert (stage.raw / "motif_matches.jsonl").is_file()
    assert (stage.normalized / "motif_hits.tsv").is_file()
    assert summary["counts"] == {
        "raw_matches": 2,
        "qualifying_matches": 2,
        "motif_evaluations": 4,
        "relationship_evaluations": 1,
        "evidence_records": 5,
    }
    assert summary["final_ec_prediction"] is None

    schema = json.loads(Path("configs/schema/evidence.schema.json").read_text())
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    for record in evidence:
        validator.validate(record)
    literal = next(
        record
        for record in evidence
        if record["metrics"].get("rule_id") == "literal_catalytic"
    )
    assert literal["source"]["evidence_class"] == "catalytic_motif"
    assert literal["metrics"]["motif_start"] == 4
    assert literal["metrics"]["matched_sequence"] == "CAT"
    assert "catalytic_cysteine" in literal["metrics"]["catalytic_residues_json"]
    assert literal["assertion"]["target"] == {
        "type": "family",
        "id": "fixture_family",
    }
    assert not any(
        record.get("assertion", {}).get("target", {}).get("type") == "ec"
        for record in evidence
    )


def test_required_absence_is_negative_only_for_complete_sequence(
    tmp_path: Path, fasta_file
) -> None:
    profile = _profile(tmp_path)
    complete = fasta_file(f">complete\n{'A' * 25}\n", name="complete.fasta")
    _runner(tmp_path).run(
        complete, workflow=_workflow(profile), run_id="motif-absent-complete"
    )
    complete_paths = RunPaths(
        tmp_path / "results", tmp_path / "logs", "motif-absent-complete"
    )
    complete_records = _records(complete_paths.evidence / "motif_evidence.jsonl")
    required = next(
        record
        for record in complete_records
        if record["metrics"].get("rule_id") == "literal_catalytic"
    )
    assert required["record_status"] == "negative"
    assert required["reason_code"] == "motif_absent_complete_sequence"
    assert required["assertion"]["effect"] == "contradicts"

    truncated = fasta_file(f">short\n{'A' * 10}\n", name="short.fasta")
    _runner(tmp_path).run(
        truncated, workflow=_workflow(profile), run_id="motif-absent-truncated"
    )
    truncated_paths = RunPaths(
        tmp_path / "results", tmp_path / "logs", "motif-absent-truncated"
    )
    truncated_records = _records(truncated_paths.evidence / "motif_evidence.jsonl")
    missing = next(
        record
        for record in truncated_records
        if record["metrics"].get("rule_id") == "literal_catalytic"
    )
    assert missing["record_status"] == "missing"
    assert missing["reason_code"] == "sequence_truncated"
    assert "assertion" not in missing


def test_forbidden_motif_presence_is_visible_conflicting_evidence(
    tmp_path: Path, fasta_file
) -> None:
    profile = _profile(tmp_path)
    source = fasta_file(">q1\nAAACATBADNDPAAAAAAAAAAA\n")
    _runner(tmp_path).run(source, workflow=_workflow(profile), run_id="motif-forbidden")
    paths = RunPaths(tmp_path / "results", tmp_path / "logs", "motif-forbidden")
    evidence = _records(paths.evidence / "motif_evidence.jsonl")
    forbidden = next(
        record
        for record in evidence
        if record["metrics"].get("rule_id") == "forbidden_region"
    )
    assert forbidden["record_status"] == "observed"
    assert forbidden["assertion"]["effect"] == "contradicts"


def test_motif_resume_and_input_change_invalidation(tmp_path: Path, fasta_file) -> None:
    profile = _profile(tmp_path)
    source = fasta_file(">q1\nAAACATAAANDPAAAAAAAAAA\n")
    runner = _runner(tmp_path)
    workflow = _workflow(profile)
    runner.run(source, workflow=workflow, run_id="motif-cache")
    paths = RunPaths(tmp_path / "results", tmp_path / "logs", "motif-cache")
    state = StageStateStore(paths.for_stage("motifs")).load()
    assert state is not None and state.attempt == 1

    resumed = runner.run(source, workflow=workflow, run_id="motif-cache")
    assert resumed.stage_outcomes["motifs"] is StageStatus.SKIPPED

    source.write_text(">q1\nAAACATAAANEPAAAAAAAAAA\n", encoding="utf-8")
    changed = runner.run(source, workflow=workflow, run_id="motif-cache")
    changed_state = StageStateStore(paths.for_stage("motifs")).load()
    assert changed.stage_outcomes["motifs"] is StageStatus.COMPLETED
    assert changed_state is not None and changed_state.attempt == 2


def test_family_change_invalidates_motif_cache(tmp_path: Path, fasta_file) -> None:
    profile = _profile(tmp_path)
    source = fasta_file(">q1\nAAACATAAANDPAAAAAAAAAA\n")
    runner = _runner(tmp_path)
    runner.run(source, workflow=_workflow(profile), run_id="motif-family-change")

    updated = profile.source_path.read_text().replace(
        "version: 1.0.0", "version: 1.0.1"
    )
    profile.source_path.write_text(updated, encoding="utf-8")
    changed_profile = load_family_profile(profile.source_path)
    changed = runner.run(
        source,
        workflow=_workflow(changed_profile),
        run_id="motif-family-change",
    )
    paths = RunPaths(tmp_path / "results", tmp_path / "logs", "motif-family-change")
    state = StageStateStore(paths.for_stage("motifs")).load()
    assert changed.stage_outcomes["motifs"] is StageStatus.COMPLETED
    assert state is not None and state.attempt == 2
