"""Validation stage backed by the Milestone 1 FASTA implementation."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

from enzynotation import __version__
from enzynotation.fasta import (
    FastaValidationOptions,
    validate_fasta,
    write_normalized_fasta,
)
from enzynotation.provenance import (
    CommandProvenance,
    SoftwareProvenance,
    sha256_bytes,
    sha256_file,
    utc_now,
)
from enzynotation.stages.base import Stage, StageContext, StageResult
from enzynotation.state import atomic_write_bytes, atomic_write_json, atomic_write_text


class ValidationStage(Stage):
    """Snapshot, validate, and normalize the input protein FASTA."""

    def __init__(self) -> None:
        super().__init__("validate", implementation_version="1")

    def input_files(self, context: StageContext) -> dict[str, Path]:
        """Declare the external FASTA as the direct stage input."""

        return {"input_fasta": context.input_fasta}

    def configuration(self, context: StageContext) -> dict[str, Any]:
        """Limit invalidation to validation and FASTA-output settings."""

        return {
            "input": dict(context.config.section("input")),
            "output": dict(context.config.section("output")),
        }

    @staticmethod
    def _archive(path: Path, history: Path, label: str) -> None:
        if not path.is_file():
            return
        digest = sha256_file(path)
        suffix = "".join(path.suffixes)
        archive = history / f"{label}.{digest}{suffix}"
        if not archive.exists():
            atomic_write_bytes(archive, path.read_bytes())

    def _snapshot_input(self, context: StageContext) -> None:
        source_content = context.input_fasta.read_bytes()
        actual_checksum = sha256_bytes(source_content)
        expected_checksum = context.input_checksums["input_fasta"]
        if actual_checksum != expected_checksum:
            raise RuntimeError(
                "input FASTA changed while the validation stage was starting"
            )

        destination = context.paths.original_fasta
        if destination.is_file() and sha256_file(destination) != actual_checksum:
            self._archive(destination, context.paths.input_history, "original")
            self._archive(
                context.paths.normalized_fasta,
                context.paths.input_history,
                "normalized",
            )
            self._archive(
                context.paths.validation_report,
                context.paths.input_history,
                "validation",
            )
        atomic_write_bytes(destination, source_content)

    @staticmethod
    def _write_normalized(context: StageContext, records: tuple) -> None:
        destination = context.paths.normalized_fasta
        destination.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{destination.name}.", dir=destination.parent
        )
        os.close(descriptor)
        temporary = Path(temporary_name)
        try:
            output = context.config.section("output")
            write_normalized_fasta(
                records,
                temporary,
                line_width=int(output["line_width"]),
            )
            os.replace(temporary, destination)
        finally:
            temporary.unlink(missing_ok=True)

    def execute(self, context: StageContext) -> StageResult:
        """Run existing FASTA validation and capture its internal provenance."""

        stage_paths = context.paths.for_stage(self.stage_id)
        stage_paths.prepare()
        started_at = utc_now()
        started_clock = time.monotonic()

        self._snapshot_input(context)
        options = FastaValidationOptions.from_mapping(context.config.section("input"))
        result = validate_fasta(context.paths.original_fasta, options)
        atomic_write_json(context.paths.validation_report, result.to_dict())

        stdout = (
            f"records={len(result.records)} errors={len(result.errors)} "
            f"warnings={len(result.warnings)}\n"
        )
        stderr = "".join(
            json.dumps(issue.to_dict(), sort_keys=True) + "\n"
            for issue in result.issues
        )
        atomic_write_text(stage_paths.stdout, stdout)
        atomic_write_text(stage_paths.stderr, stderr)

        outputs: dict[str, Path] = {
            "original_fasta": context.paths.original_fasta,
            "validation_report": context.paths.validation_report,
        }
        if result.is_valid:
            self._write_normalized(context, result.records)
            outputs["normalized_fasta"] = context.paths.normalized_fasta
        else:
            self._archive(
                context.paths.normalized_fasta,
                context.paths.input_history,
                "normalized",
            )
            context.paths.normalized_fasta.unlink(missing_ok=True)

        return_code = 0 if result.is_valid else 1
        command = CommandProvenance(
            argv=("enzynotation", "validate", str(context.paths.original_fasta)),
            cwd=str(Path.cwd().resolve()),
            backend=context.backend.name,
            started_at=started_at,
            finished_at=utc_now(),
            duration_seconds=time.monotonic() - started_clock,
            return_code=return_code,
            stdout_path=str(stage_paths.stdout.resolve()),
            stderr_path=str(stage_paths.stderr.resolve()),
        )
        software = SoftwareProvenance(
            name="enzynotation",
            version=__version__,
            executable=sys.executable,
        )
        message = (
            f"validated {len(result.records)} protein record(s)"
            if result.is_valid
            else f"FASTA validation failed with {len(result.errors)} error(s)"
        )
        return StageResult(
            succeeded=result.is_valid,
            outputs=outputs,
            commands=(command,),
            software=(software,),
            message=message,
        )
