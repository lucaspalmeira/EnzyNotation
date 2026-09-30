"""Checksums, identifiers, and execution provenance records."""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_RUN_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def utc_now() -> str:
    """Return the current UTC time in RFC 3339 form."""

    return datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def create_run_id(
    *, timestamp: datetime | None = None, suffix: str | None = None
) -> str:
    """Create a sortable, collision-resistant run identifier."""

    moment = timestamp or datetime.now(UTC)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    moment = moment.astimezone(UTC)
    entropy = suffix or uuid.uuid4().hex[:8]
    run_id = f"run-{moment:%Y%m%dT%H%M%SZ}-{entropy}"
    validate_run_id(run_id)
    return run_id


def validate_run_id(run_id: str) -> str:
    """Validate a run identifier before it is used as a path component."""

    if not _RUN_ID_PATTERN.fullmatch(run_id):
        raise ValueError(
            "run_id must start with an alphanumeric character and contain only "
            "letters, numbers, '.', '_', or '-'"
        )
    return run_id


def sha256_bytes(content: bytes) -> str:
    """Return the lowercase SHA-256 digest for bytes."""

    return hashlib.sha256(content).hexdigest()


def sha256_file(path: Path, *, chunk_size: int = 1024 * 1024) -> str:
    """Stream a file and return its lowercase SHA-256 digest."""

    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_json_bytes(value: Any) -> bytes:
    """Serialize JSON-compatible data deterministically."""

    return json.dumps(
        value,
        ensure_ascii=True,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def canonical_json_sha256(value: Any) -> str:
    """Hash a JSON-compatible value independent of mapping insertion order."""

    return sha256_bytes(canonical_json_bytes(value))


@dataclass(frozen=True, slots=True)
class SoftwareProvenance:
    """Version information for software used by a stage or command."""

    name: str
    version: str
    executable: str
    version_command: tuple[str, ...] = ()
    version_return_code: int | None = None

    def to_dict(self) -> dict[str, Any]:
        """Serialize this record for a manifest or stage status."""

        result: dict[str, Any] = {
            "name": self.name,
            "version": self.version,
            "executable": self.executable,
        }
        if self.version_command:
            result["version_command"] = list(self.version_command)
        if self.version_return_code is not None:
            result["version_return_code"] = self.version_return_code
        return result


@dataclass(frozen=True, slots=True)
class CommandProvenance:
    """Auditable result of one command execution."""

    argv: tuple[str, ...]
    cwd: str
    backend: str
    started_at: str
    finished_at: str
    duration_seconds: float
    return_code: int
    stdout_path: str
    stderr_path: str

    def to_dict(self) -> dict[str, Any]:
        """Serialize this command without shell reconstruction."""

        return {
            "argv": list(self.argv),
            "cwd": self.cwd,
            "backend": self.backend,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "duration_seconds": self.duration_seconds,
            "return_code": self.return_code,
            "stdout_path": self.stdout_path,
            "stderr_path": self.stderr_path,
        }


def stage_signature(
    *,
    stage_id: str,
    implementation_version: str,
    input_checksums: Mapping[str, str],
    configuration_checksum: str,
    dependency_signatures: Mapping[str, str],
) -> str:
    """Create the deterministic cache key for a stage attempt."""

    return canonical_json_sha256(
        {
            "stage_id": stage_id,
            "implementation_version": implementation_version,
            "input_checksums": dict(input_checksums),
            "configuration_checksum": configuration_checksum,
            "dependency_signatures": dict(dependency_signatures),
        }
    )


def checksums_for_files(files: Mapping[str, Path]) -> dict[str, str]:
    """Calculate checksums in sorted logical-name order."""

    return {name: sha256_file(files[name]) for name in sorted(files)}


def normalize_command(argv: Sequence[str]) -> tuple[str, ...]:
    """Convert a command to an immutable, non-empty string tuple."""

    normalized = tuple(str(argument) for argument in argv)
    if not normalized or not normalized[0]:
        raise ValueError("command must contain an executable")
    return normalized
