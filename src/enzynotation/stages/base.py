"""Stage interface used by workflow execution backends."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from enzynotation.backends.base import ExecutionBackend
from enzynotation.config import ResolvedConfig
from enzynotation.paths import RunPaths
from enzynotation.provenance import CommandProvenance, SoftwareProvenance


@dataclass(frozen=True, slots=True)
class StageContext:
    """Inputs and services made available to one stage attempt."""

    run_id: str
    paths: RunPaths
    config: ResolvedConfig
    backend: ExecutionBackend
    input_fasta: Path
    input_checksums: Mapping[str, str]
    configuration_checksum: str


@dataclass(frozen=True, slots=True)
class StageResult:
    """Outputs and provenance returned by a stage implementation."""

    succeeded: bool
    outputs: Mapping[str, Path]
    commands: tuple[CommandProvenance, ...] = ()
    software: tuple[SoftwareProvenance, ...] = ()
    message: str = ""


class Stage(ABC):
    """A deterministic pipeline unit with declared dependencies and inputs."""

    def __init__(
        self,
        stage_id: str,
        *,
        dependencies: Sequence[str] = (),
        required: bool = True,
        implementation_version: str = "1",
    ) -> None:
        self.stage_id = stage_id
        self.dependencies = tuple(dependencies)
        self.required = required
        self.implementation_version = implementation_version

    def input_files(self, context: StageContext) -> Mapping[str, Path]:
        """Return direct file inputs whose content invalidates this stage."""

        return {}

    def configuration(self, context: StageContext) -> Mapping[str, Any]:
        """Return only configuration fields relevant to this stage."""

        return {}

    @abstractmethod
    def execute(self, context: StageContext) -> StageResult:
        """Perform one stage attempt and return all generated outputs."""
