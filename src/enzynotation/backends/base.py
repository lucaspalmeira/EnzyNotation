"""Interfaces shared by execution backends."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from enzynotation.provenance import CommandProvenance, SoftwareProvenance


@dataclass(frozen=True, slots=True)
class CommandSpec:
    """A command expressed as arguments, never as a shell string."""

    argv: tuple[str, ...]
    cwd: Path | None = None
    environment: Mapping[str, str] | None = None

    @classmethod
    def from_sequence(
        cls,
        argv: Sequence[str],
        *,
        cwd: Path | None = None,
        environment: Mapping[str, str] | None = None,
    ) -> CommandSpec:
        """Build a validated command specification."""

        normalized = tuple(str(argument) for argument in argv)
        if not normalized or not normalized[0]:
            raise ValueError("command must contain an executable")
        return cls(normalized, cwd=cwd, environment=environment)


class ExecutionBackend(ABC):
    """Backend capable of executing commands and identifying software."""

    name: str

    @abstractmethod
    def execute(
        self,
        command: CommandSpec,
        *,
        stdout_path: Path,
        stderr_path: Path,
    ) -> CommandProvenance:
        """Execute a command and record its logs and return code."""

    @abstractmethod
    def capture_version(
        self,
        executable: str,
        *,
        name: str | None = None,
        arguments: Sequence[str] = ("--version",),
    ) -> SoftwareProvenance:
        """Capture a software version without using a shell."""
