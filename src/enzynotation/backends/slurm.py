"""Safe argv-based Slurm submission backend."""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from typing import Any

from enzynotation.provenance import utc_now
from enzynotation.slurm import (
    SlurmConfig,
    SlurmConfigurationError,
    SlurmStagePlan,
    dependency_argument,
    parse_job_id,
)


@dataclass(frozen=True, slots=True)
class SlurmSubmission:
    """Submission result retained independently of scientific evidence."""

    stage_id: str
    job_id: str
    dependency_job_ids: tuple[str, ...]
    argv: tuple[str, ...]
    resources: dict[str, Any]
    submitted_at: str
    scheduler_state: str = "submitted"

    def to_dict(self) -> dict[str, Any]:
        return {
            "stage_id": self.stage_id,
            "job_id": self.job_id,
            "dependency_job_ids": list(self.dependency_job_ids),
            "sbatch_command": list(self.argv),
            "resolved_resources": self.resources,
            "submitted_at": self.submitted_at,
            "scheduler_state": self.scheduler_state,
        }


class SlurmBackend:
    """Construct and optionally submit one scheduler job per pipeline stage."""

    name = "slurm"

    def __init__(self, config: SlurmConfig) -> None:
        self.config = config

    def build_sbatch_argv(
        self,
        plan: SlurmStagePlan,
        worker_argv: tuple[str, ...],
        *,
        afterok_job_ids: tuple[str, ...] = (),
        afterany_job_ids: tuple[str, ...] = (),
    ) -> tuple[str, ...]:
        """Build injection-safe sbatch argv for a generic stage script."""

        dependency = dependency_argument(
            afterok=afterok_job_ids, afterany=afterany_job_ids
        )
        argv = [
            self.config.sbatch_executable,
            "--parsable",
            "--job-name",
            f"enzynotation-{plan.stage_id}",
            "--output",
            str(plan.stdout_pattern),
            "--error",
            str(plan.stderr_pattern),
            *plan.resources.sbatch_arguments(),
        ]
        if dependency is not None:
            argv.extend(("--dependency", dependency))
        argv.extend(
            (
                str(self.config.stage_script),
                plan.stage_id,
                *worker_argv,
            )
        )
        return tuple(argv)

    def submit(self, argv: tuple[str, ...], plan: SlurmStagePlan) -> SlurmSubmission:
        """Submit argv directly without a shell and parse the scheduler job ID."""

        plan.stdout_pattern.parent.mkdir(parents=True, exist_ok=True)
        try:
            completed = subprocess.run(
                argv,
                check=False,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
            )
        except OSError as exc:
            raise SlurmConfigurationError(f"cannot execute sbatch: {exc}") from exc
        if completed.returncode != 0:
            message = completed.stderr.strip() or completed.stdout.strip()
            raise SlurmConfigurationError(
                f"sbatch failed for stage {plan.stage_id!r} with code "
                f"{completed.returncode}: {message}"
            )
        job_id = parse_job_id(completed.stdout)
        dependency_ids: list[str] = []
        if "--dependency" in argv:
            expression = argv[argv.index("--dependency") + 1]
            dependency_ids.extend(re.findall(r"[0-9]+", expression))
        return SlurmSubmission(
            stage_id=plan.stage_id,
            job_id=job_id,
            dependency_job_ids=tuple(dependency_ids),
            argv=argv,
            resources=plan.resources.to_dict(),
            submitted_at=utc_now(),
        )
