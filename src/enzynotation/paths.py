"""Canonical filesystem layout for EnzyNotation runs."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from enzynotation.provenance import validate_run_id

_STAGE_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


@dataclass(frozen=True, slots=True)
class StagePaths:
    """Scientific artifacts and logs owned by one stage."""

    root: Path
    raw: Path
    normalized: Path
    history: Path
    status: Path
    running: Path
    log_root: Path
    stdout: Path
    stderr: Path

    def prepare(self) -> None:
        """Create all directories owned by the stage."""

        for directory in (
            self.root,
            self.raw,
            self.normalized,
            self.history,
            self.log_root,
        ):
            directory.mkdir(parents=True, exist_ok=True)


@dataclass(frozen=True, slots=True)
class RunPaths:
    """Resolved canonical paths for a single run."""

    results_root: Path
    logs_root: Path
    run_id: str

    def __post_init__(self) -> None:
        validate_run_id(self.run_id)

    @property
    def run_root(self) -> Path:
        return self.results_root / self.run_id

    @property
    def manifest(self) -> Path:
        return self.run_root / "manifest.json"

    @property
    def config(self) -> Path:
        return self.run_root / "config"

    @property
    def config_history(self) -> Path:
        return self.config / "history"

    @property
    def resolved_pipeline(self) -> Path:
        return self.config / "resolved-pipeline.yaml"

    @property
    def input(self) -> Path:
        return self.run_root / "input"

    @property
    def input_history(self) -> Path:
        return self.input / "history"

    @property
    def original_fasta(self) -> Path:
        return self.input / "original.fasta"

    @property
    def normalized_fasta(self) -> Path:
        return self.input / "normalized.fasta"

    @property
    def validation_report(self) -> Path:
        return self.input / "validation.json"

    @property
    def stages(self) -> Path:
        return self.run_root / "stages"

    @property
    def evidence(self) -> Path:
        return self.run_root / "evidence"

    @property
    def integration(self) -> Path:
        return self.run_root / "integration"

    @property
    def reports(self) -> Path:
        return self.run_root / "reports"

    @property
    def run_logs(self) -> Path:
        return self.logs_root / self.run_id

    def prepare(self) -> None:
        """Create the complete canonical run directory skeleton."""

        directories = (
            self.run_root,
            self.config,
            self.config_history,
            self.input,
            self.input_history,
            self.stages,
            self.evidence,
            self.integration,
            self.reports,
            self.run_logs,
        )
        for directory in directories:
            directory.mkdir(parents=True, exist_ok=True)

    def for_stage(self, stage_id: str) -> StagePaths:
        """Return paths for a validated stage identifier."""

        if not _STAGE_ID_PATTERN.fullmatch(stage_id):
            raise ValueError(f"Invalid stage identifier: {stage_id!r}")
        root = self.stages / stage_id
        log_root = self.run_logs / stage_id
        return StagePaths(
            root=root,
            raw=root / "raw",
            normalized=root / "normalized",
            history=root / "history",
            status=root / "status.json",
            running=root / "running.json",
            log_root=log_root,
            stdout=log_root / "stdout.log",
            stderr=log_root / "stderr.log",
        )

    def relative_artifact(self, path: Path) -> str:
        """Return a POSIX path relative to the scientific run root."""

        try:
            return Path(path).resolve().relative_to(self.run_root.resolve()).as_posix()
        except ValueError as exc:
            raise ValueError(f"Artifact is outside run directory: {path}") from exc
