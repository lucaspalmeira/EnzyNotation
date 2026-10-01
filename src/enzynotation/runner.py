"""Deterministic local workflow runner with resumable stage state."""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from enzynotation import __version__
from enzynotation.backends.base import ExecutionBackend
from enzynotation.backends.local import LocalBackend
from enzynotation.config import ResolvedConfig
from enzynotation.exceptions import EnzyNotationError
from enzynotation.paths import RunPaths, StagePaths
from enzynotation.provenance import (
    SoftwareProvenance,
    canonical_json_bytes,
    canonical_json_sha256,
    checksums_for_files,
    create_run_id,
    sha256_bytes,
    sha256_file,
    stage_signature,
    utc_now,
    validate_run_id,
)
from enzynotation.stages.base import Stage, StageContext, StageResult
from enzynotation.state import (
    ManifestStore,
    RunManifest,
    RunStatus,
    StageState,
    StageStateStore,
    StageStatus,
    atomic_write_bytes,
    atomic_write_text,
    outputs_are_intact,
)
from enzynotation.workflow import Workflow


class RunnerError(EnzyNotationError):
    """Raised when a run cannot be initialized or safely resumed."""


@dataclass(frozen=True, slots=True)
class RunResult:
    """Summary returned to callers after workflow execution."""

    run_id: str
    status: RunStatus
    manifest_path: Path
    stage_outcomes: dict[str, StageStatus]


class PipelineRunner:
    """Execute a workflow locally while preserving deterministic run state."""

    def __init__(
        self,
        config: ResolvedConfig,
        *,
        results_root: Path = Path("results"),
        logs_root: Path = Path("logs"),
        backend: ExecutionBackend | None = None,
    ) -> None:
        self.config = config
        self.results_root = Path(results_root)
        self.logs_root = Path(logs_root)
        self.backend = backend or LocalBackend()

    def _resolved_config_data(self) -> dict[str, Any]:
        return json.loads(canonical_json_bytes(self.config.data))

    def _snapshot_configuration(
        self, paths: RunPaths
    ) -> tuple[str, list[dict[str, str]]]:
        data = self._resolved_config_data()
        checksum = canonical_json_sha256(data)
        content = yaml.safe_dump(data, sort_keys=True).encode("utf-8")
        snapshot = paths.resolved_pipeline
        if snapshot.is_file():
            previous = snapshot.read_bytes()
            if previous != content:
                previous_checksum = sha256_bytes(previous)
                archive = (
                    paths.config_history / f"resolved-pipeline.{previous_checksum}.yaml"
                )
                if not archive.exists():
                    atomic_write_bytes(archive, previous)
        atomic_write_bytes(snapshot, content)

        sources: list[dict[str, str]] = []
        for source in self.config.source_files:
            record = {"path": str(source)}
            if source.is_file():
                record["sha256"] = sha256_file(source)
            else:
                record["sha256"] = "unavailable"
            sources.append(record)
        return checksum, sources

    @staticmethod
    def _workflow_description(workflow: Workflow) -> list[dict[str, Any]]:
        return [
            {
                "stage_id": stage.stage_id,
                "implementation_version": stage.implementation_version,
                "dependencies": list(stage.dependencies),
                "required": stage.required,
            }
            for stage in workflow
        ]

    def _initialize_manifest(
        self,
        *,
        paths: RunPaths,
        workflow: Workflow,
        input_fasta: Path,
        config_checksum: str,
        config_sources: list[dict[str, str]],
        input_checksum: str,
    ) -> tuple[RunManifest, ManifestStore]:
        store = ManifestStore(paths.manifest)
        previous = store.load()
        now = utc_now()
        software = SoftwareProvenance(
            name="enzynotation",
            version=__version__,
            executable=sys.executable,
        ).to_dict()
        manifest = RunManifest(
            run_id=paths.run_id,
            created_at=previous.created_at if previous else now,
            updated_at=now,
            status=RunStatus.RUNNING,
            software=software,
            input={
                "source": str(input_fasta.resolve()),
                "sha256": input_checksum,
            },
            configuration={
                "sha256": config_checksum,
                "snapshot": paths.relative_artifact(paths.resolved_pipeline),
                "sources": config_sources,
            },
            workflow=self._workflow_description(workflow),
            stages=dict(previous.stages) if previous else {},
        )
        store.write(manifest)
        return manifest, store

    @staticmethod
    def _stage_context(
        *,
        paths: RunPaths,
        config: ResolvedConfig,
        backend: ExecutionBackend,
        input_fasta: Path,
        input_checksums: dict[str, str],
        configuration_checksum: str,
    ) -> StageContext:
        return StageContext(
            run_id=paths.run_id,
            paths=paths,
            config=config,
            backend=backend,
            input_fasta=input_fasta,
            input_checksums=input_checksums,
            configuration_checksum=configuration_checksum,
        )

    @staticmethod
    def _output_checksums(result: StageResult, paths: RunPaths) -> dict[str, str]:
        checksums: dict[str, str] = {}
        for logical_name in sorted(result.outputs):
            output = Path(result.outputs[logical_name])
            if not output.is_file():
                raise RunnerError(
                    f"Stage declared missing output {logical_name!r}: {output}"
                )
            relative = paths.relative_artifact(output)
            checksums[relative] = sha256_file(output)
        return checksums

    @staticmethod
    def _write_runner_error(stage_paths: StagePaths, message: str) -> None:
        stage_paths.prepare()
        existing = (
            stage_paths.stderr.read_text(encoding="utf-8")
            if stage_paths.stderr.exists()
            else ""
        )
        atomic_write_text(stage_paths.stderr, f"{existing}runner: {message}\n")
        if not stage_paths.stdout.exists():
            atomic_write_text(stage_paths.stdout, "")

    @staticmethod
    def _manifest_stage_entry(
        state: StageState,
        *,
        action: StageStatus | None = None,
    ) -> dict[str, Any]:
        entry: dict[str, Any] = {
            "status": (action or state.status).value,
            "final_status": state.status.value,
            "required": state.required,
            "attempt": state.attempt,
            "signature": state.signature,
            "status_path": f"stages/{state.stage_id}/status.json",
            "message": state.message,
        }
        return entry

    def _publish_terminal_state(
        self,
        *,
        stage: Stage,
        paths: RunPaths,
        status: StageStatus,
        signature: str,
        input_checksums: dict[str, str],
        configuration_checksum: str,
        dependency_signatures: dict[str, str],
        started_at: str,
        attempt: int,
        message: str,
        result: StageResult | None = None,
    ) -> StageState:
        stage_paths = paths.for_stage(stage.stage_id)
        store = StageStateStore(stage_paths)
        output_checksums: dict[str, str] = {}
        commands: tuple[dict[str, Any], ...] = ()
        software: tuple[dict[str, Any], ...] = ()
        if result is not None:
            try:
                output_checksums = self._output_checksums(result, paths)
            except (OSError, RunnerError, ValueError) as exc:
                status = StageStatus.FAILED
                message = f"{message}; output verification failed: {exc}"
            commands = tuple(command.to_dict() for command in result.commands)
            software = tuple(item.to_dict() for item in result.software)

        state = StageState(
            stage_id=stage.stage_id,
            implementation_version=stage.implementation_version,
            status=status,
            required=stage.required,
            attempt=attempt,
            signature=signature,
            started_at=started_at,
            finished_at=utc_now(),
            input_checksums=input_checksums,
            configuration_checksum=configuration_checksum,
            dependency_signatures=dependency_signatures,
            output_checksums=output_checksums,
            commands=commands,
            software=software,
            message=message,
        )
        store.publish(state)
        return state

    def _unavailable_stage(
        self,
        *,
        stage: Stage,
        paths: RunPaths,
        dependency_states: dict[str, StageState],
    ) -> StageState:
        stage_paths = paths.for_stage(stage.stage_id)
        store = StageStateStore(stage_paths)
        attempt = store.next_attempt()
        store.prepare_attempt()
        dependency_signatures = {
            dependency: dependency_states[dependency].signature
            for dependency in stage.dependencies
            if dependency in dependency_states
        }
        signature = stage_signature(
            stage_id=stage.stage_id,
            implementation_version=stage.implementation_version,
            input_checksums={},
            configuration_checksum="unavailable",
            dependency_signatures=dependency_signatures,
        )
        started_at = utc_now()
        store.mark_running(
            stage_id=stage.stage_id,
            attempt=attempt,
            signature=signature,
            started_at=started_at,
        )
        unavailable = [
            dependency
            for dependency in stage.dependencies
            if dependency_states[dependency].status is not StageStatus.COMPLETED
        ]
        message = f"dependencies unavailable: {', '.join(unavailable)}"
        self._write_runner_error(stage_paths, message)
        return self._publish_terminal_state(
            stage=stage,
            paths=paths,
            status=StageStatus.NOT_AVAILABLE,
            signature=signature,
            input_checksums={},
            configuration_checksum="unavailable",
            dependency_signatures=dependency_signatures,
            started_at=started_at,
            attempt=attempt,
            message=message,
        )

    def run(
        self,
        input_fasta: Path,
        *,
        workflow: Workflow | None = None,
        run_id: str | None = None,
        resume: bool = True,
    ) -> RunResult:
        """Execute or resume a workflow and return its terminal state."""

        selected_workflow = workflow or Workflow.validation_only()
        pipeline_config = self.config.section("pipeline")
        selected_run_id = run_id or pipeline_config.get("run_id") or create_run_id()
        try:
            selected_run_id = validate_run_id(str(selected_run_id))
        except ValueError as exc:
            raise RunnerError(str(exc)) from exc

        paths = RunPaths(self.results_root, self.logs_root, selected_run_id)
        if not resume and paths.run_root.exists():
            raise RunnerError(
                f"Run already exists and resume is disabled: {selected_run_id}"
            )
        paths.prepare()

        config_checksum, config_sources = self._snapshot_configuration(paths)
        source = Path(input_fasta)
        try:
            input_checksum = sha256_file(source)
        except OSError:
            input_checksum = "unavailable"

        manifest, manifest_store = self._initialize_manifest(
            paths=paths,
            workflow=selected_workflow,
            input_fasta=source,
            config_checksum=config_checksum,
            config_sources=config_sources,
            input_checksum=input_checksum,
        )

        states: dict[str, StageState] = {}
        outcomes: dict[str, StageStatus] = {}
        required_failure = False
        optional_failure = False

        for stage in selected_workflow:
            dependency_states = {
                dependency: states[dependency] for dependency in stage.dependencies
            }
            if any(
                state.status is not StageStatus.COMPLETED
                for state in dependency_states.values()
            ):
                state = self._unavailable_stage(
                    stage=stage,
                    paths=paths,
                    dependency_states=dependency_states,
                )
                states[stage.stage_id] = state
                outcomes[stage.stage_id] = StageStatus.NOT_AVAILABLE
                manifest.stages[stage.stage_id] = self._manifest_stage_entry(state)
                manifest.updated_at = utc_now()
                manifest_store.write(manifest)
                if stage.required:
                    required_failure = True
                    break
                optional_failure = True
                continue

            preliminary = self._stage_context(
                paths=paths,
                config=self.config,
                backend=self.backend,
                input_fasta=source,
                input_checksums={},
                configuration_checksum="",
            )
            dependency_signatures = {
                dependency: state.signature
                for dependency, state in dependency_states.items()
            }
            try:
                relevant_config = dict(stage.configuration(preliminary))
                stage_config_checksum = canonical_json_sha256(relevant_config)
                input_checksums = checksums_for_files(stage.input_files(preliminary))
                signature = stage_signature(
                    stage_id=stage.stage_id,
                    implementation_version=stage.implementation_version,
                    input_checksums=input_checksums,
                    configuration_checksum=stage_config_checksum,
                    dependency_signatures=dependency_signatures,
                )
            except (OSError, ValueError, TypeError, EnzyNotationError) as exc:
                stage_paths = paths.for_stage(stage.stage_id)
                store = StageStateStore(stage_paths)
                attempt = store.next_attempt()
                store.prepare_attempt()
                started_at = utc_now()
                stage_config_checksum = "unavailable"
                input_checksums = {}
                signature = stage_signature(
                    stage_id=stage.stage_id,
                    implementation_version=stage.implementation_version,
                    input_checksums={},
                    configuration_checksum=stage_config_checksum,
                    dependency_signatures=dependency_signatures,
                )
                store.mark_running(
                    stage_id=stage.stage_id,
                    attempt=attempt,
                    signature=signature,
                    started_at=started_at,
                )
                message = f"stage preparation failed: {exc}"
                self._write_runner_error(stage_paths, message)
                state = self._publish_terminal_state(
                    stage=stage,
                    paths=paths,
                    status=StageStatus.FAILED,
                    signature=signature,
                    input_checksums=input_checksums,
                    configuration_checksum=stage_config_checksum,
                    dependency_signatures=dependency_signatures,
                    started_at=started_at,
                    attempt=attempt,
                    message=message,
                )
                states[stage.stage_id] = state
                outcomes[stage.stage_id] = StageStatus.FAILED
                manifest.stages[stage.stage_id] = self._manifest_stage_entry(state)
                manifest.updated_at = utc_now()
                manifest_store.write(manifest)
                if stage.required:
                    required_failure = True
                    break
                optional_failure = True
                continue

            stage_paths = paths.for_stage(stage.stage_id)
            store = StageStateStore(stage_paths)
            cached = store.load()
            if (
                resume
                and cached is not None
                and cached.status is StageStatus.COMPLETED
                and cached.signature == signature
                and not store.has_unfinished_attempt()
                and outputs_are_intact(cached, paths.run_root)
            ):
                stage_paths.running.unlink(missing_ok=True)
                states[stage.stage_id] = cached
                outcomes[stage.stage_id] = StageStatus.SKIPPED
                manifest.stages[stage.stage_id] = self._manifest_stage_entry(
                    cached, action=StageStatus.SKIPPED
                )
                manifest.updated_at = utc_now()
                manifest_store.write(manifest)
                continue

            attempt = store.next_attempt()
            store.prepare_attempt()
            started_at = utc_now()
            store.mark_running(
                stage_id=stage.stage_id,
                attempt=attempt,
                signature=signature,
                started_at=started_at,
            )
            context = self._stage_context(
                paths=paths,
                config=self.config,
                backend=self.backend,
                input_fasta=source,
                input_checksums=input_checksums,
                configuration_checksum=stage_config_checksum,
            )
            result: StageResult | None = None
            try:
                result = stage.execute(context)
                status = (
                    StageStatus.COMPLETED if result.succeeded else StageStatus.FAILED
                )
                message = result.message
            except Exception as exc:  # noqa: BLE001 - stage boundaries record all failures
                status = StageStatus.FAILED
                message = f"stage execution failed: {type(exc).__name__}: {exc}"
                self._write_runner_error(stage_paths, message)

            state = self._publish_terminal_state(
                stage=stage,
                paths=paths,
                status=status,
                signature=signature,
                input_checksums=input_checksums,
                configuration_checksum=stage_config_checksum,
                dependency_signatures=dependency_signatures,
                started_at=started_at,
                attempt=attempt,
                message=message,
                result=result,
            )
            states[stage.stage_id] = state
            outcomes[stage.stage_id] = state.status
            manifest.stages[stage.stage_id] = self._manifest_stage_entry(state)
            manifest.updated_at = utc_now()
            manifest_store.write(manifest)

            if state.status is not StageStatus.COMPLETED:
                if stage.required:
                    required_failure = True
                    break
                optional_failure = True

        if required_failure:
            manifest.status = RunStatus.FAILED
        elif optional_failure:
            manifest.status = RunStatus.COMPLETED_WITH_OPTIONAL_FAILURES
        else:
            manifest.status = RunStatus.COMPLETED
        manifest.updated_at = utc_now()
        manifest_store.write(manifest)
        return RunResult(
            run_id=selected_run_id,
            status=manifest.status,
            manifest_path=paths.manifest,
            stage_outcomes=outcomes,
        )
