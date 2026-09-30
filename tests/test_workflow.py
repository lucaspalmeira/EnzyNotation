"""Tests for workflow ordering, execution, failure, and resumability."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from enzynotation.backends.base import CommandSpec
from enzynotation.config import load_config
from enzynotation.paths import RunPaths
from enzynotation.runner import PipelineRunner
from enzynotation.stages.base import Stage, StageContext, StageResult
from enzynotation.stages.validate import ValidationStage
from enzynotation.state import (
    RunStatus,
    StageStateStore,
    StageStatus,
    atomic_write_json,
    atomic_write_text,
)
from enzynotation.workflow import Workflow, WorkflowDefinitionError


class RecordingStage(Stage):
    """Small deterministic stage used to exercise the runner."""

    def __init__(
        self,
        stage_id: str,
        record: list[str],
        *,
        dependencies: tuple[str, ...] = (),
        required: bool = True,
        return_code: int = 0,
    ) -> None:
        super().__init__(
            stage_id,
            dependencies=dependencies,
            required=required,
        )
        self.record = record
        self.return_code = return_code

    def execute(self, context: StageContext) -> StageResult:
        self.record.append(self.stage_id)
        paths = context.paths.for_stage(self.stage_id)
        paths.prepare()
        command = context.backend.execute(
            CommandSpec.from_sequence(
                [sys.executable, "-c", f"import sys; sys.exit({self.return_code})"]
            ),
            stdout_path=paths.stdout,
            stderr_path=paths.stderr,
        )
        outputs: dict[str, Path] = {}
        if command.return_code == 0:
            output = paths.normalized / "result.txt"
            atomic_write_text(output, f"{self.stage_id}\n")
            outputs["result"] = output
        return StageResult(
            succeeded=command.return_code == 0,
            outputs=outputs,
            commands=(command,),
            software=(context.backend.capture_version(sys.executable, name="python"),),
            message=f"return code {command.return_code}",
        )


def _runner(tmp_path: Path) -> PipelineRunner:
    return PipelineRunner(
        load_config(),
        results_root=tmp_path / "results",
        logs_root=tmp_path / "logs",
    )


def test_dependency_order_is_deterministic() -> None:
    record: list[str] = []
    final = RecordingStage("final", record, dependencies=("middle",))
    first = RecordingStage("first", record)
    middle = RecordingStage("middle", record, dependencies=("first",))
    workflow = Workflow([final, first, middle])
    assert [stage.stage_id for stage in workflow] == ["first", "middle", "final"]


def test_invalid_workflow_dependencies_are_rejected() -> None:
    record: list[str] = []
    with pytest.raises(WorkflowDefinitionError, match="unknown dependencies"):
        Workflow([RecordingStage("stage", record, dependencies=("missing",))])

    first = RecordingStage("first", record, dependencies=("second",))
    second = RecordingStage("second", record, dependencies=("first",))
    with pytest.raises(WorkflowDefinitionError, match="cycle"):
        Workflow([first, second])


def test_validation_workflow_creates_manifest_and_canonical_layout(
    tmp_path: Path, fasta_file
) -> None:
    source = fasta_file(">alpha description\nacd*\n")
    result = _runner(tmp_path).run(source, run_id="successful-run")
    paths = RunPaths(tmp_path / "results", tmp_path / "logs", "successful-run")

    assert result.status is RunStatus.COMPLETED
    assert result.stage_outcomes == {"validate": StageStatus.COMPLETED}
    assert paths.manifest.is_file()
    assert paths.original_fasta.read_text() == ">alpha description\nacd*\n"
    assert paths.normalized_fasta.read_text() == ">alpha description\nACD\n"
    assert paths.validation_report.is_file()
    assert paths.resolved_pipeline.is_file()
    assert paths.evidence.is_dir()
    assert paths.integration.is_dir()
    assert paths.reports.is_dir()

    manifest = json.loads(paths.manifest.read_text())
    assert manifest["status"] == "completed"
    assert manifest["input"]["sha256"]
    assert manifest["configuration"]["sha256"]
    assert manifest["stages"]["validate"]["status"] == "completed"

    state = StageStateStore(paths.for_stage("validate")).load()
    assert state is not None
    assert state.status is StageStatus.COMPLETED
    assert state.commands[0]["argv"][0:2] == ["enzynotation", "validate"]
    assert state.software[0]["name"] == "enzynotation"
    assert paths.for_stage("validate").stdout.is_file()
    assert paths.for_stage("validate").stderr.is_file()


def test_failed_validation_can_resume_after_input_is_fixed(
    tmp_path: Path, fasta_file
) -> None:
    source = fasta_file(">broken\nAC?\n")
    runner = _runner(tmp_path)
    first = runner.run(source, run_id="repair-run")
    paths = RunPaths(tmp_path / "results", tmp_path / "logs", "repair-run")
    failed_state = StageStateStore(paths.for_stage("validate")).load()

    assert first.status is RunStatus.FAILED
    assert failed_state is not None
    assert failed_state.status is StageStatus.FAILED
    assert paths.validation_report.is_file()
    assert not paths.normalized_fasta.exists()

    source.write_text(">repaired\nACD\n", encoding="utf-8")
    second = runner.run(source, run_id="repair-run")
    completed_state = StageStateStore(paths.for_stage("validate")).load()
    assert second.status is RunStatus.COMPLETED
    assert completed_state is not None
    assert completed_state.attempt == 2
    assert paths.normalized_fasta.read_text() == ">repaired\nACD\n"


def test_unchanged_completed_stage_is_skipped_on_resume(
    tmp_path: Path, fasta_file
) -> None:
    source = fasta_file(">protein\nACD\n")
    runner = _runner(tmp_path)
    runner.run(source, run_id="resume-run")
    second = runner.run(source, run_id="resume-run")
    paths = RunPaths(tmp_path / "results", tmp_path / "logs", "resume-run")
    state = StageStateStore(paths.for_stage("validate")).load()

    assert second.stage_outcomes["validate"] is StageStatus.SKIPPED
    assert state is not None
    assert state.status is StageStatus.COMPLETED
    assert state.attempt == 1
    manifest = json.loads(paths.manifest.read_text())
    assert manifest["stages"]["validate"]["status"] == "skipped"
    assert manifest["stages"]["validate"]["final_status"] == "completed"


def test_interrupted_attempt_is_rerun_deterministically(
    tmp_path: Path, fasta_file
) -> None:
    source = fasta_file(">protein\nACD\n")
    runner = _runner(tmp_path)
    runner.run(source, run_id="interrupted-run")
    paths = RunPaths(tmp_path / "results", tmp_path / "logs", "interrupted-run")
    store = StageStateStore(paths.for_stage("validate"))
    completed = store.load()
    assert completed is not None
    atomic_write_json(
        paths.for_stage("validate").running,
        {
            "schema_version": 1,
            "stage_id": "validate",
            "attempt": 2,
            "signature": completed.signature,
            "started_at": "2026-09-30T15:00:00Z",
        },
    )

    resumed = runner.run(source, run_id="interrupted-run")
    final = store.load()
    assert resumed.stage_outcomes["validate"] is StageStatus.COMPLETED
    assert final is not None
    assert final.attempt == 3
    history = paths.for_stage("validate").history
    assert (history / "attempt-0002-interrupted.json").is_file()


def test_changed_input_invalidates_completed_stage(tmp_path: Path, fasta_file) -> None:
    source = fasta_file(">protein\nACD\n")
    runner = _runner(tmp_path)
    runner.run(source, run_id="changed-input-run")
    source.write_text(">protein\nEFG\n", encoding="utf-8")

    second = runner.run(source, run_id="changed-input-run")
    paths = RunPaths(tmp_path / "results", tmp_path / "logs", "changed-input-run")
    state = StageStateStore(paths.for_stage("validate")).load()
    assert second.stage_outcomes["validate"] is StageStatus.COMPLETED
    assert state is not None
    assert state.attempt == 2
    assert paths.normalized_fasta.read_text() == ">protein\nEFG\n"
    assert (
        paths.for_stage("validate").history / "attempt-0001-completed.json"
    ).is_file()
    assert list(paths.input_history.glob("original.*.fasta"))


def test_changed_input_invalidates_dependent_stage(tmp_path: Path, fasta_file) -> None:
    source = fasta_file(">protein\nACD\n")
    record: list[str] = []
    workflow = Workflow(
        [
            ValidationStage(),
            RecordingStage("downstream", record, dependencies=("validate",)),
        ]
    )
    runner = _runner(tmp_path)
    runner.run(source, workflow=workflow, run_id="downstream-run")
    source.write_text(">protein\nEFG\n", encoding="utf-8")
    second = runner.run(source, workflow=workflow, run_id="downstream-run")

    paths = RunPaths(tmp_path / "results", tmp_path / "logs", "downstream-run")
    downstream = StageStateStore(paths.for_stage("downstream")).load()
    assert record == ["downstream", "downstream"]
    assert second.stage_outcomes["downstream"] is StageStatus.COMPLETED
    assert downstream is not None
    assert downstream.attempt == 2


def test_relevant_config_change_invalidates_and_snapshots(
    tmp_path: Path, fasta_file
) -> None:
    source = fasta_file(">protein\nACD\n")
    roots = {
        "results_root": tmp_path / "results",
        "logs_root": tmp_path / "logs",
    }
    PipelineRunner(load_config(), **roots).run(source, run_id="config-change-run")

    overlay = tmp_path / "strict.yaml"
    overlay.write_text("input:\n  min_sequence_length: 4\n", encoding="utf-8")
    second = PipelineRunner(load_config([overlay]), **roots).run(
        source, run_id="config-change-run"
    )

    paths = RunPaths(tmp_path / "results", tmp_path / "logs", "config-change-run")
    state = StageStateStore(paths.for_stage("validate")).load()
    assert second.status is RunStatus.FAILED
    assert state is not None
    assert state.attempt == 2
    assert list(paths.config_history.glob("resolved-pipeline.*.yaml"))
    assert not paths.normalized_fasta.exists()


def test_missing_input_fails_cleanly_with_manifest(tmp_path: Path) -> None:
    missing = tmp_path / "missing.fasta"
    result = _runner(tmp_path).run(missing, run_id="missing-input-run")
    paths = RunPaths(tmp_path / "results", tmp_path / "logs", "missing-input-run")
    state = StageStateStore(paths.for_stage("validate")).load()

    assert result.status is RunStatus.FAILED
    assert paths.manifest.is_file()
    assert state is not None
    assert state.status is StageStatus.FAILED
    assert "stage preparation failed" in state.message
    assert paths.for_stage("validate").stderr.is_file()


def test_optional_failure_continues_but_required_failure_stops(
    tmp_path: Path, fasta_file
) -> None:
    source = fasta_file(">protein\nACD\n")

    optional_record: list[str] = []
    optional_workflow = Workflow(
        [
            RecordingStage(
                "optional_failure",
                optional_record,
                required=False,
                return_code=9,
            ),
            RecordingStage("after_optional", optional_record),
        ]
    )
    optional = _runner(tmp_path).run(
        source,
        workflow=optional_workflow,
        run_id="optional-run",
    )
    assert optional.status is RunStatus.COMPLETED_WITH_OPTIONAL_FAILURES
    assert optional_record == ["optional_failure", "after_optional"]

    required_record: list[str] = []
    required_workflow = Workflow(
        [
            RecordingStage("required_failure", required_record, return_code=9),
            RecordingStage("after_required", required_record),
        ]
    )
    required = _runner(tmp_path).run(
        source,
        workflow=required_workflow,
        run_id="required-run",
    )
    assert required.status is RunStatus.FAILED
    assert required_record == ["required_failure"]
    assert "after_required" not in required.stage_outcomes


def test_failed_optional_dependency_marks_dependent_unavailable(
    tmp_path: Path, fasta_file
) -> None:
    source = fasta_file(">protein\nACD\n")
    record: list[str] = []
    workflow = Workflow(
        [
            RecordingStage("optional_failure", record, required=False, return_code=3),
            RecordingStage(
                "dependent",
                record,
                dependencies=("optional_failure",),
                required=False,
            ),
            RecordingStage("independent", record),
        ]
    )
    result = _runner(tmp_path).run(
        source,
        workflow=workflow,
        run_id="dependency-failure-run",
    )
    assert result.status is RunStatus.COMPLETED_WITH_OPTIONAL_FAILURES
    assert result.stage_outcomes["dependent"] is StageStatus.NOT_AVAILABLE
    assert record == ["optional_failure", "independent"]
