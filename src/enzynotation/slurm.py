"""Slurm configuration, dependency planning, and scheduler provenance."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, fields
from enum import StrEnum
from pathlib import Path
from typing import Any

import yaml
from jsonschema import Draft202012Validator

from enzynotation.exceptions import EnzyNotationError
from enzynotation.paths import RunPaths
from enzynotation.state import atomic_write_json
from enzynotation.workflow import Workflow


class SlurmConfigurationError(EnzyNotationError):
    """Raised when scheduler configuration cannot be used safely."""


class SchedulerState(StrEnum):
    """Normalized scheduler lifecycle states, separate from biological state."""

    PLANNED = "planned"
    SUBMITTED = "submitted"
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    DEPENDENCY_FAILED = "dependency_failed"
    UNKNOWN = "unknown"


_STATE_MAP = {
    "PENDING": SchedulerState.PENDING,
    "CONFIGURING": SchedulerState.PENDING,
    "RUNNING": SchedulerState.RUNNING,
    "COMPLETING": SchedulerState.RUNNING,
    "COMPLETED": SchedulerState.COMPLETED,
    "FAILED": SchedulerState.FAILED,
    "TIMEOUT": SchedulerState.FAILED,
    "OUT_OF_MEMORY": SchedulerState.FAILED,
    "NODE_FAIL": SchedulerState.FAILED,
    "CANCELLED": SchedulerState.CANCELLED,
    "DEADLINE": SchedulerState.CANCELLED,
    "DEPENDENCY": SchedulerState.DEPENDENCY_FAILED,
}


def normalize_scheduler_state(value: str | None) -> SchedulerState:
    """Map a Slurm state/banner to the stable scheduler-state vocabulary."""

    if not value:
        return SchedulerState.UNKNOWN
    normalized = value.strip().upper().split(maxsplit=1)[0].split("+", maxsplit=1)[0]
    return _STATE_MAP.get(normalized, SchedulerState.UNKNOWN)


@dataclass(frozen=True, slots=True)
class SlurmResources:
    """Resolved scheduler resources for one stage."""

    partition: str | None = None
    account: str | None = None
    qos: str | None = None
    cpus_per_task: int | None = None
    memory: str | None = None
    time: str | None = None
    gpus: int | None = None
    gres: str | None = None
    constraint: str | None = None
    nodes: int | None = None
    ntasks: int | None = None

    @classmethod
    def from_mapping(cls, value: dict[str, Any]) -> SlurmResources:
        """Create resources from a schema-validated mapping."""

        accepted = {item.name for item in fields(cls)}
        return cls(**{key: value[key] for key in accepted if key in value})

    def overlay(self, override: SlurmResources) -> SlurmResources:
        """Apply non-null values from a more specific configuration layer."""

        return SlurmResources(
            **{
                item.name: (
                    getattr(override, item.name)
                    if getattr(override, item.name) is not None
                    else getattr(self, item.name)
                )
                for item in fields(self)
            }
        )

    def to_dict(self) -> dict[str, Any]:
        """Return deterministic resource data for plans and provenance."""

        return {item.name: getattr(self, item.name) for item in fields(self)}

    def sbatch_arguments(self) -> tuple[str, ...]:
        """Render scheduler options as argv without shell interpolation."""

        mapping = (
            ("partition", "--partition"),
            ("account", "--account"),
            ("qos", "--qos"),
            ("cpus_per_task", "--cpus-per-task"),
            ("memory", "--mem"),
            ("time", "--time"),
            ("gpus", "--gpus"),
            ("gres", "--gres"),
            ("constraint", "--constraint"),
            ("nodes", "--nodes"),
            ("ntasks", "--ntasks"),
        )
        arguments: list[str] = []
        for attribute, option in mapping:
            value = getattr(self, attribute)
            if value is not None and not (attribute == "gpus" and value == 0):
                arguments.extend((option, str(value)))
        return tuple(arguments)


@dataclass(frozen=True, slots=True)
class SlurmConfig:
    """Validated scheduler and per-stage resource configuration."""

    source_path: Path
    sbatch_executable: str
    stage_script: Path
    defaults: SlurmResources
    stages: dict[str, SlurmResources]
    run_overrides: dict[str, SlurmResources]
    document: dict[str, Any]

    def resources_for(self, stage_id: str) -> SlurmResources:
        """Resolve defaults, stage override, then run/site override."""

        resolved = self.defaults.overlay(self.stages.get(stage_id, SlurmResources()))
        return resolved.overlay(self.run_overrides.get(stage_id, SlurmResources()))


def load_slurm_config(
    path: Path,
    *,
    schema_path: Path = Path("configs/schema/slurm.schema.json"),
) -> SlurmConfig:
    """Load and validate Slurm YAML without contacting a scheduler."""

    try:
        document = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
        schema = json.loads(Path(schema_path).read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError, json.JSONDecodeError) as exc:
        raise SlurmConfigurationError(
            f"cannot load Slurm configuration: {exc}"
        ) from exc
    if not isinstance(document, dict):
        raise SlurmConfigurationError("Slurm configuration must be a mapping")
    errors = sorted(
        Draft202012Validator(schema).iter_errors(document),
        key=lambda error: list(error.absolute_path),
    )
    if errors:
        location = ".".join(str(part) for part in errors[0].absolute_path)
        raise SlurmConfigurationError(
            f"invalid Slurm configuration at {location or '<root>'}: "
            f"{errors[0].message}"
        )
    section = document["slurm"]
    return SlurmConfig(
        source_path=Path(path).resolve(),
        sbatch_executable=section["sbatch_executable"],
        stage_script=Path(section["stage_script"]),
        defaults=SlurmResources.from_mapping(section["defaults"]),
        stages={
            stage: SlurmResources.from_mapping(resources)
            for stage, resources in section["stages"].items()
        },
        run_overrides={
            stage: SlurmResources.from_mapping(resources)
            for stage, resources in section["run_overrides"].items()
        },
        document=document,
    )


def parse_job_id(output: str) -> str:
    """Extract a numeric job ID from conventional or parsable sbatch output."""

    rendered = output.strip()
    match = re.fullmatch(r"Submitted batch job ([0-9]+)", rendered)
    if match:
        return match.group(1)
    match = re.fullmatch(r"([0-9]+)(?:;[^\s;]+)?", rendered)
    if match:
        return match.group(1)
    raise SlurmConfigurationError(f"malformed sbatch output: {rendered!r}")


def dependency_argument(
    *, afterok: tuple[str, ...] = (), afterany: tuple[str, ...] = ()
) -> str | None:
    """Build one deterministic Slurm dependency expression."""

    identifiers = (*afterok, *afterany)
    if any(not re.fullmatch(r"[0-9]+", value) for value in identifiers):
        raise SlurmConfigurationError("dependency job IDs must be numeric")
    groups: list[str] = []
    if afterok:
        groups.append("afterok:" + ":".join(afterok))
    if afterany:
        groups.append("afterany:" + ":".join(afterany))
    return ",".join(groups) if groups else None


@dataclass(frozen=True, slots=True)
class SlurmStagePlan:
    """Scheduler-neutral description of one stage job."""

    stage_id: str
    required: bool
    afterok_stages: tuple[str, ...]
    afterany_stages: tuple[str, ...]
    resources: SlurmResources
    stdout_pattern: Path
    stderr_pattern: Path


def build_slurm_plan(
    workflow: Workflow,
    config: SlurmConfig,
    paths: RunPaths,
) -> tuple[SlurmStagePlan, ...]:
    """Translate the existing workflow DAG into stage-granular scheduler jobs."""

    stages = tuple(workflow)
    by_id = {stage.stage_id: stage for stage in stages}
    position = {stage.stage_id: index for index, stage in enumerate(stages)}
    plans: list[SlurmStagePlan] = []
    for stage in stages:
        afterok = list(stage.dependencies)
        afterany: list[str] = []
        if stage.stage_id == "integrate":
            for upstream in stages[: position[stage.stage_id]]:
                if upstream.stage_id in afterok or upstream.stage_id == "validate":
                    continue
                target = afterok if upstream.required else afterany
                target.append(upstream.stage_id)
        log_root = paths.run_logs / "slurm" / stage.stage_id
        plans.append(
            SlurmStagePlan(
                stage_id=stage.stage_id,
                required=stage.required,
                afterok_stages=tuple(dict.fromkeys(afterok)),
                afterany_stages=tuple(dict.fromkeys(afterany)),
                resources=config.resources_for(stage.stage_id),
                stdout_pattern=log_root / "stdout-%j.log",
                stderr_pattern=log_root / "stderr-%j.log",
            )
        )
    unknown = {
        dependency
        for plan in plans
        for dependency in (*plan.afterok_stages, *plan.afterany_stages)
        if dependency not in by_id
    }
    if unknown:
        raise SlurmConfigurationError(
            "Slurm plan contains unknown stages: " + ", ".join(sorted(unknown))
        )
    return tuple(plans)


def write_slurm_plan(path: Path, document: dict[str, Any]) -> None:
    """Atomically publish scheduler plan/submission provenance."""

    atomic_write_json(path, document)
