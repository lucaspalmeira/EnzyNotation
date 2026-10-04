"""Slurm configuration and DAG planning tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from enzynotation.paths import RunPaths
from enzynotation.slurm import (
    SchedulerState,
    SlurmConfigurationError,
    SlurmResources,
    build_slurm_plan,
    dependency_argument,
    load_slurm_config,
    normalize_scheduler_state,
    parse_job_id,
)
from enzynotation.stages.base import Stage, StageContext, StageResult
from enzynotation.workflow import Workflow


class FixtureStage(Stage):
    def execute(self, context: StageContext) -> StageResult:
        return StageResult(True, {})


def test_slurm_resources_follow_documented_precedence(tmp_path: Path) -> None:
    config_path = tmp_path / "slurm.yaml"
    config_path.write_text(
        """schema_version: 1
slurm:
  sbatch_executable: sbatch
  stage_script: slurm/run_stage.sbatch
  defaults: {cpus_per_task: 2, memory: 4G, time: '01:00:00'}
  stages:
    blast: {cpus_per_task: 8, memory: 16G}
  run_overrides:
    blast: {memory: 24G, time: '03:00:00'}
""",
        encoding="utf-8",
    )
    resources = load_slurm_config(config_path).resources_for("blast")
    assert resources.cpus_per_task == 8
    assert resources.memory == "24G"
    assert resources.time == "03:00:00"


def test_resource_argv_covers_scheduler_options() -> None:
    resources = SlurmResources(
        partition="cpu",
        account="project",
        qos="normal",
        cpus_per_task=8,
        memory="16G",
        time="04:00:00",
        gpus=1,
        gres="gpu:a100:1",
        constraint="zen4",
        nodes=2,
        ntasks=4,
    )
    argv = resources.sbatch_arguments()
    assert argv[argv.index("--partition") + 1] == "cpu"
    assert argv[argv.index("--cpus-per-task") + 1] == "8"
    assert argv[argv.index("--gres") + 1] == "gpu:a100:1"
    assert argv[argv.index("--ntasks") + 1] == "4"


def test_dependency_formatting_supports_multiple_ids_and_afterany() -> None:
    assert dependency_argument(afterok=("12", "34"), afterany=("56",)) == (
        "afterok:12:34,afterany:56"
    )
    assert dependency_argument() is None
    with pytest.raises(SlurmConfigurationError, match="numeric"):
        dependency_argument(afterok=("12; touch injected",))


@pytest.mark.parametrize(
    ("output", "expected"),
    [("Submitted batch job 123\n", "123"), ("456;cluster\n", "456")],
)
def test_job_id_parsing(output: str, expected: str) -> None:
    assert parse_job_id(output) == expected


def test_malformed_sbatch_output_is_rejected() -> None:
    with pytest.raises(SlurmConfigurationError, match="malformed"):
        parse_job_id("job identifier unavailable")


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("PENDING", SchedulerState.PENDING),
        ("RUNNING", SchedulerState.RUNNING),
        ("OUT_OF_MEMORY", SchedulerState.FAILED),
        ("CANCELLED+", SchedulerState.CANCELLED),
        ("DEPENDENCY", SchedulerState.DEPENDENCY_FAILED),
        ("SITE_SPECIFIC", SchedulerState.UNKNOWN),
        (None, SchedulerState.UNKNOWN),
    ],
)
def test_scheduler_state_normalization(raw, expected) -> None:
    assert normalize_scheduler_state(raw) is expected


def test_plan_preserves_required_dag_and_optional_integration_waits() -> None:
    validate = FixtureStage("validate")
    blast = FixtureStage("blast", dependencies=("validate",), required=True)
    optional = FixtureStage("clean", dependencies=("validate",), required=False)
    integrate = FixtureStage("integrate", dependencies=("validate",))
    report = FixtureStage("report", dependencies=("integrate",))
    workflow = Workflow([validate, blast, optional, integrate, report])
    config = load_slurm_config(Path("configs/slurm/default.yaml"))
    paths = RunPaths(Path("results"), Path("logs"), "dag-test")
    plans = {plan.stage_id: plan for plan in build_slurm_plan(workflow, config, paths)}

    assert plans["blast"].afterok_stages == ("validate",)
    assert plans["integrate"].afterok_stages == ("validate", "blast")
    assert plans["integrate"].afterany_stages == ("clean",)
    assert plans["report"].afterok_stages == ("integrate",)
    assert plans["blast"].stdout_pattern == Path(
        "logs/dag-test/slurm/blast/stdout-%j.log"
    )


def test_default_configuration_loads_and_stage_override_applies() -> None:
    config = load_slurm_config(Path("configs/slurm/default.yaml"))
    assert config.resources_for("validate").cpus_per_task == 4
    assert config.resources_for("blast").cpus_per_task == 8
