"""Durable run manifests and atomic stage-state records."""

from __future__ import annotations

import json
import os
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

from enzynotation.paths import StagePaths
from enzynotation.provenance import sha256_file


def atomic_write_bytes(path: Path, content: bytes) -> None:
    """Atomically replace a file with fully flushed content."""

    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.", dir=destination.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def atomic_write_text(path: Path, content: str) -> None:
    """Atomically replace a UTF-8 text file."""

    atomic_write_bytes(path, content.encode("utf-8"))


def atomic_write_json(path: Path, value: Any) -> None:
    """Atomically write deterministic, human-readable JSON."""

    content = json.dumps(value, indent=2, sort_keys=True) + "\n"
    atomic_write_text(path, content)


class StageStatus(StrEnum):
    """Final states published by a stage attempt."""

    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"
    NOT_AVAILABLE = "not_available"


class RunStatus(StrEnum):
    """Overall run outcomes."""

    RUNNING = "running"
    COMPLETED = "completed"
    COMPLETED_WITH_OPTIONAL_FAILURES = "completed_with_optional_failures"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class StageState:
    """Final, resumable state of one stage attempt."""

    stage_id: str
    implementation_version: str
    status: StageStatus
    required: bool
    attempt: int
    signature: str
    started_at: str
    finished_at: str
    input_checksums: dict[str, str]
    configuration_checksum: str
    dependency_signatures: dict[str, str]
    output_checksums: dict[str, str]
    commands: tuple[dict[str, Any], ...] = ()
    software: tuple[dict[str, Any], ...] = ()
    message: str = ""
    schema_version: int = 1

    def to_dict(self) -> dict[str, Any]:
        """Serialize this state for ``status.json``."""

        return {
            "schema_version": self.schema_version,
            "stage_id": self.stage_id,
            "implementation_version": self.implementation_version,
            "status": self.status.value,
            "required": self.required,
            "attempt": self.attempt,
            "signature": self.signature,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "input_checksums": self.input_checksums,
            "configuration_checksum": self.configuration_checksum,
            "dependency_signatures": self.dependency_signatures,
            "output_checksums": self.output_checksums,
            "commands": list(self.commands),
            "software": list(self.software),
            "message": self.message,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> StageState:
        """Deserialize and minimally type a stage status document."""

        return cls(
            schema_version=int(value["schema_version"]),
            stage_id=str(value["stage_id"]),
            implementation_version=str(value["implementation_version"]),
            status=StageStatus(value["status"]),
            required=bool(value["required"]),
            attempt=int(value["attempt"]),
            signature=str(value["signature"]),
            started_at=str(value["started_at"]),
            finished_at=str(value["finished_at"]),
            input_checksums=dict(value["input_checksums"]),
            configuration_checksum=str(value["configuration_checksum"]),
            dependency_signatures=dict(value["dependency_signatures"]),
            output_checksums=dict(value["output_checksums"]),
            commands=tuple(value.get("commands", ())),
            software=tuple(value.get("software", ())),
            message=str(value.get("message", "")),
        )


@dataclass(slots=True)
class RunManifest:
    """Mutable in-memory representation of ``manifest.json``."""

    run_id: str
    created_at: str
    updated_at: str
    status: RunStatus
    software: dict[str, Any]
    input: dict[str, Any]
    configuration: dict[str, Any]
    workflow: list[dict[str, Any]]
    stages: dict[str, dict[str, Any]] = field(default_factory=dict)
    schema_version: int = 1

    def to_dict(self) -> dict[str, Any]:
        """Serialize the complete run manifest."""

        return {
            "schema_version": self.schema_version,
            "run_id": self.run_id,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "status": self.status.value,
            "software": self.software,
            "input": self.input,
            "configuration": self.configuration,
            "workflow": self.workflow,
            "stages": self.stages,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> RunManifest:
        """Deserialize a previously written run manifest."""

        return cls(
            schema_version=int(value["schema_version"]),
            run_id=str(value["run_id"]),
            created_at=str(value["created_at"]),
            updated_at=str(value["updated_at"]),
            status=RunStatus(value["status"]),
            software=dict(value["software"]),
            input=dict(value["input"]),
            configuration=dict(value["configuration"]),
            workflow=list(value["workflow"]),
            stages=dict(value.get("stages", {})),
        )


class ManifestStore:
    """Atomic persistence for a run manifest."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def load(self) -> RunManifest | None:
        """Load the manifest when it exists."""

        if not self.path.exists():
            return None
        with self.path.open("r", encoding="utf-8") as handle:
            return RunManifest.from_dict(json.load(handle))

    def write(self, manifest: RunManifest) -> None:
        """Publish a complete manifest atomically."""

        atomic_write_json(self.path, manifest.to_dict())

    @contextmanager
    def _locked(self):
        """Serialize read/merge/write operations on shared HPC storage."""

        import fcntl

        lock_path = self.path.with_suffix(self.path.suffix + ".lock")
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        with lock_path.open("a+", encoding="utf-8") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def merge_write(self, manifest: RunManifest) -> None:
        """Atomically merge concurrent stage entries before publishing."""

        with self._locked():
            current = self.load()
            if current is not None:
                manifest.created_at = current.created_at
                manifest.stages = {**current.stages, **manifest.stages}
            self.write(manifest)


class StageStateStore:
    """Manage running markers, final status, and attempt history for one stage."""

    def __init__(self, paths: StagePaths) -> None:
        self.paths = paths

    def load(self) -> StageState | None:
        """Load the final stage state when present."""

        if not self.paths.status.exists():
            return None
        with self.paths.status.open("r", encoding="utf-8") as handle:
            return StageState.from_dict(json.load(handle))

    def load_running(self) -> dict[str, Any] | None:
        """Load an in-flight attempt marker when present."""

        if not self.paths.running.exists():
            return None
        with self.paths.running.open("r", encoding="utf-8") as handle:
            return dict(json.load(handle))

    def has_unfinished_attempt(self) -> bool:
        """Whether ``running.json`` represents work newer than final status."""

        running = self.load_running()
        if running is None:
            return False
        final = self.load()
        return final is None or int(running["attempt"]) > final.attempt

    def next_attempt(self) -> int:
        """Return an attempt number accounting for interrupted work."""

        attempts = [0]
        final = self.load()
        running = self.load_running()
        if final is not None:
            attempts.append(final.attempt)
        if running is not None:
            attempts.append(int(running["attempt"]))
        return max(attempts) + 1

    def prepare_attempt(self) -> None:
        """Archive prior final/interrupted records before a new attempt."""

        self.paths.prepare()
        final = self.load()
        running = self.load_running()
        if final is not None:
            archive = (
                self.paths.history
                / f"attempt-{final.attempt:04d}-{final.status.value}.json"
            )
            atomic_write_json(archive, final.to_dict())
            self.paths.status.unlink(missing_ok=True)
        if running is not None:
            running_attempt = int(running["attempt"])
            if final is None or running_attempt > final.attempt:
                archive = (
                    self.paths.history
                    / f"attempt-{running_attempt:04d}-interrupted.json"
                )
                atomic_write_json(archive, running)
            self.paths.running.unlink(missing_ok=True)

    def mark_running(
        self,
        *,
        stage_id: str,
        attempt: int,
        signature: str,
        started_at: str,
    ) -> None:
        """Publish an in-flight attempt marker atomically."""

        atomic_write_json(
            self.paths.running,
            {
                "schema_version": 1,
                "stage_id": stage_id,
                "attempt": attempt,
                "signature": signature,
                "started_at": started_at,
            },
        )

    def publish(self, state: StageState) -> None:
        """Atomically publish final state, then clear the running marker."""

        atomic_write_json(self.paths.status, state.to_dict())
        self.paths.running.unlink(missing_ok=True)


def outputs_are_intact(state: StageState, run_root: Path) -> bool:
    """Verify all recorded outputs still exist and match their checksums."""

    for relative_path, expected_checksum in state.output_checksums.items():
        output = run_root / relative_path
        if not output.is_file() or sha256_file(output) != expected_checksum:
            return False
    return True
