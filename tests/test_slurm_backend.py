"""Safe Slurm submission backend and CLI dry-run tests."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from enzynotation.backends.slurm import SlurmBackend
from enzynotation.cli import main
from enzynotation.paths import RunPaths
from enzynotation.slurm import (
    SlurmConfigurationError,
    SlurmStagePlan,
    build_slurm_plan,
    load_slurm_config,
)
from enzynotation.stages.validate import ValidationStage
from enzynotation.workflow import Workflow


def _plan(tmp_path: Path) -> tuple[SlurmBackend, SlurmStagePlan]:
    config = load_slurm_config(Path("configs/slurm/default.yaml"))
    backend = SlurmBackend(config)
    paths = RunPaths(tmp_path / "results", tmp_path / "logs", "scheduler")
    plan = build_slurm_plan(Workflow([ValidationStage()]), config, paths)[0]
    return backend, plan


def test_sbatch_argv_is_safe_for_spaces_unicode_quotes_and_punctuation(
    tmp_path: Path,
) -> None:
    backend, plan = _plan(tmp_path)
    hostile = tmp_path / "dados ç 'quoted';$(touch nope).fasta"
    worker = ("enzynotation", "run", str(hostile), "--backend", "local")
    argv = backend.build_sbatch_argv(plan, worker, afterok_job_ids=("41", "42"))
    assert str(hostile) in argv
    assert argv[argv.index("--dependency") + 1] == "afterok:41:42"
    assert all(argument != "touch" for argument in argv)


def test_submission_parses_job_id_without_shell(monkeypatch, tmp_path: Path) -> None:
    backend, plan = _plan(tmp_path)
    observed = {}

    def fake_run(argv, **kwargs):
        observed.update(argv=argv, kwargs=kwargs)
        return subprocess.CompletedProcess(argv, 0, "927;cluster\n", "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    argv = backend.build_sbatch_argv(plan, ("enzynotation", "run", "input.faa"))
    result = backend.submit(argv, plan)
    assert result.job_id == "927"
    assert observed["kwargs"]["check"] is False
    assert "shell" not in observed["kwargs"]


def test_submission_failure_is_explicit(monkeypatch, tmp_path: Path) -> None:
    backend, plan = _plan(tmp_path)

    def fake_run(argv, **kwargs):
        return subprocess.CompletedProcess(argv, 1, "", "account invalid")

    monkeypatch.setattr(subprocess, "run", fake_run)
    argv = backend.build_sbatch_argv(plan, ("enzynotation", "run", "input.faa"))
    with pytest.raises(SlurmConfigurationError, match="account invalid"):
        backend.submit(argv, plan)


def test_slurm_dry_run_does_not_call_sbatch(
    monkeypatch, tmp_path: Path, capsys
) -> None:
    source = tmp_path / "input with spaces.faa"
    source.write_text(">q1\nACDE\n", encoding="utf-8")

    def forbidden(*args, **kwargs):
        raise AssertionError("dry-run must not contact sbatch")

    monkeypatch.setattr(subprocess, "run", forbidden)
    status = main(
        [
            "run",
            str(source),
            "--backend",
            "slurm",
            "--slurm-config",
            "configs/slurm/default.yaml",
            "--dry-run",
            "--run-id",
            "dry-run",
            "--results-dir",
            str(tmp_path / "results"),
            "--logs-dir",
            str(tmp_path / "logs"),
        ]
    )
    plan = json.loads(capsys.readouterr().out)
    assert status == 0
    assert plan["dry_run"] is True
    assert plan["jobs"][0]["stage_id"] == "validate"
    assert str(source) in plan["jobs"][0]["sbatch_command"]
    assert not (tmp_path / "results").exists()


def test_targeted_worker_reuses_completed_stage_cache(
    tmp_path: Path, fasta_file, monkeypatch
) -> None:
    source = fasta_file(">q1\nACDE\n")
    command = [
        "run",
        str(source),
        "--run-id",
        "worker-cache",
        "--results-dir",
        str(tmp_path / "results"),
        "--logs-dir",
        str(tmp_path / "logs"),
        "--stage-id",
        "validate",
        "--slurm-worker",
    ]
    monkeypatch.setenv("SLURM_JOB_ID", "1001")
    assert main(command) == 0
    assert main(command) == 0
    status = json.loads(
        (tmp_path / "results/worker-cache/stages/validate/status.json").read_text()
    )
    scheduler = json.loads(
        (tmp_path / "results/worker-cache/stages/validate/scheduler.json").read_text()
    )
    assert status["attempt"] == 1
    assert scheduler["job_id"] == "1001"
    assert scheduler["scheduler_state"] == "completed"


def test_targeted_worker_invalidates_when_input_changes(
    tmp_path: Path, fasta_file
) -> None:
    source = fasta_file(">q1\nACDE\n")
    command = [
        "run",
        str(source),
        "--run-id",
        "worker-invalidation",
        "--results-dir",
        str(tmp_path / "results"),
        "--logs-dir",
        str(tmp_path / "logs"),
        "--stage-id",
        "validate",
    ]
    assert main(command) == 0
    source.write_text(">q1\nFGHI\n", encoding="utf-8")
    assert main(command) == 0
    status = json.loads(
        (
            tmp_path / "results/worker-invalidation/stages/validate/status.json"
        ).read_text()
    )
    assert status["attempt"] == 2
