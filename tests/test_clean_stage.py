"""Integration tests for CLEAN evidence using only mocked external execution."""

from __future__ import annotations

import json
import shutil
from collections.abc import Sequence
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, FormatChecker

from enzynotation.backends.base import CommandSpec, ExecutionBackend
from enzynotation.config import load_config
from enzynotation.paths import RunPaths
from enzynotation.provenance import CommandProvenance, SoftwareProvenance, utc_now
from enzynotation.runner import PipelineRunner
from enzynotation.stages.clean import CleanStage
from enzynotation.stages.validate import ValidationStage
from enzynotation.state import RunStatus, StageStateStore, StageStatus
from enzynotation.tools.clean import CleanConfig, clean_config_from_mapping
from enzynotation.workflow import Workflow

FIXTURES = Path("tests/fixtures/clean")


def _mapping(
    tmp_path: Path,
    *,
    required: bool = True,
    strategy: str = "docker_compose",
    fixture_variant: str = "maxsep_distance",
) -> dict:
    direction = {
        "maxsep_distance": "lower_is_better",
        "maxsep_gmm_confidence": "higher_is_better",
        "opaque_wrapper": "unknown",
    }[fixture_variant]
    metric_name = {
        "maxsep_distance": "cluster_center_pairwise_distance",
        "maxsep_gmm_confidence": "gmm_confidence_estimate",
        "opaque_wrapper": "clean_model_score",
    }[fixture_variant]
    return {
        "schema_version": 1,
        "clean": {
            "enabled": True,
            "required": required,
            "execution": {
                "strategy": strategy,
                "docker_compose": {
                    "docker_executable": "docker-fixture",
                    "compose_file": str(Path("docker/clean.compose.yml").resolve()),
                    "service": "clean",
                    "image": "moleculemaker/clean-image-amd64:test",
                    "image_digest": "sha256:1234abcd",
                },
                "command": {
                    "prefix": ["conda", "run", "-n", "clean"],
                    "executable": "python-fixture",
                    "working_directory": str(tmp_path),
                },
            },
            "implementation": {
                "interface": "CLEAN_infer_fasta",
                "entrypoint": "/app/CLEAN_infer_fasta.py",
                "interpreter": "python",
                "inference_method": "max_separation",
                "output_variant": fixture_variant,
                "result_suffix": "_maxsep.csv",
                "software_version": "unknown",
            },
            "metric": {
                "name": metric_name,
                "semantics": (
                    "unknown"
                    if fixture_variant == "opaque_wrapper"
                    else f"fixture semantics for {metric_name}"
                ),
                "ranking_direction": direction,
                "calibrated": False,
            },
            "model": {
                "training_split": "fixture-split",
                "pretrained": True,
                "model_identifier": "fixture-clean-model",
                "version": "v1",
            },
            "mounts": {
                "input_directory": None,
                "output_directory": None,
                "torch_cache": None,
                "external_model_data": None,
            },
            "filtering": {
                "maximum_retained_candidates": None,
                "threshold": None,
                "threshold_comparison": None,
                "threshold_status": "operational",
                "allowed_ec_depths": None,
                "allow_partial_ec": True,
            },
            "resource_fingerprints": [],
        },
    }


def _config(tmp_path: Path, **kwargs) -> CleanConfig:
    return clean_config_from_mapping(_mapping(tmp_path, **kwargs))


class CleanFixtureBackend(ExecutionBackend):
    name = "clean-fixture"

    def __init__(
        self,
        fixture: Path,
        *,
        return_code: int = 0,
        stderr: str = "",
        version_codes: dict[str, int] | None = None,
        command_output_directory: Path | None = None,
    ) -> None:
        self.fixture = fixture
        self.return_code = return_code
        self.stderr = stderr
        self.version_codes = version_codes or {}
        self.command_output_directory = command_output_directory
        self.commands: list[CommandSpec] = []

    def execute(
        self,
        command: CommandSpec,
        *,
        stdout_path: Path,
        stderr_path: Path,
    ) -> CommandProvenance:
        self.commands.append(command)
        stdout_path.parent.mkdir(parents=True, exist_ok=True)
        stdout_path.write_text("mocked CLEAN output\n", encoding="utf-8")
        stderr_path.write_text(self.stderr, encoding="utf-8")
        if self.return_code == 0:
            output_directory = (
                Path(command.environment["CLEAN_OUTPUT_DIR"])
                if command.environment is not None
                else self.command_output_directory
            )
            assert output_directory is not None
            output_directory.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(
                self.fixture,
                output_directory / "enzynotation_clean_input_maxsep.csv",
            )
        now = utc_now()
        return CommandProvenance(
            argv=command.argv,
            cwd=str((command.cwd or Path.cwd()).resolve()),
            backend=self.name,
            started_at=now,
            finished_at=now,
            duration_seconds=0.0,
            return_code=self.return_code,
            stdout_path=str(stdout_path.resolve()),
            stderr_path=str(stderr_path.resolve()),
        )

    def capture_version(
        self,
        executable: str,
        *,
        name: str | None = None,
        arguments: Sequence[str] = ("--version",),
    ) -> SoftwareProvenance:
        software_name = name or executable
        return SoftwareProvenance(
            name=software_name,
            version=f"mocked-{software_name}-version",
            executable=executable,
            version_command=(executable, *arguments),
            version_return_code=self.version_codes.get(software_name, 0),
        )


def _runner(tmp_path: Path, backend: ExecutionBackend) -> PipelineRunner:
    return PipelineRunner(
        load_config(),
        results_root=tmp_path / "results",
        logs_root=tmp_path / "logs",
        backend=backend,
    )


def _workflow(config: CleanConfig) -> Workflow:
    return Workflow([ValidationStage(), CleanStage(config)])


def _input(fasta_file) -> Path:
    return fasta_file(">q1\nACDEFGHIKLMN\n>q2\nMNPQRSTVWYAA\n")


def _records(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line]


def test_distance_stage_stages_fasta_and_emits_schema_valid_evidence(
    tmp_path: Path, fasta_file
) -> None:
    config = _config(tmp_path)
    backend = CleanFixtureBackend(FIXTURES / "maxsep_distance.csv")
    result = _runner(tmp_path, backend).run(
        _input(fasta_file), workflow=_workflow(config), run_id="clean-distance"
    )
    paths = RunPaths(tmp_path / "results", tmp_path / "logs", "clean-distance")
    stage = paths.for_stage("clean")
    summary = json.loads((stage.normalized / "clean_summary.json").read_text())
    evidence = _records(paths.evidence / "clean_evidence.jsonl")

    assert result.status is RunStatus.COMPLETED
    assert len(backend.commands) == 1
    command = backend.commands[0]
    assert command.argv[:6] == (
        "docker-fixture",
        "compose",
        "-f",
        str(Path("docker/clean.compose.yml").resolve()),
        "run",
        "--rm",
    )
    input_directory = Path(command.environment["CLEAN_INPUT_DIR"])
    assert (input_directory / "enzynotation_clean_input.fasta").read_text() == (
        paths.normalized_fasta.read_text()
    )
    assert (stage.raw / "clean_result.csv").read_bytes() == (
        FIXTURES / "maxsep_distance.csv"
    ).read_bytes()
    assert summary["counts"]["valid_predictions"] == 3
    assert summary["counts"]["evidence_records"] == 3
    assert summary["counts"]["queries_without_predictions"] == 1
    assert summary["final_ec_prediction"] is None

    schema = json.loads(Path("configs/schema/evidence.schema.json").read_text())
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    for record in evidence:
        validator.validate(record)
    assert len({record["evidence_id"] for record in evidence}) == 3
    assert all(record["source"]["id"] == "clean" for record in evidence)
    assert all(
        record["source"]["evidence_class"] == "learned_sequence_model"
        for record in evidence
    )
    first = evidence[0]
    assert first["assertion"]["target"]["candidate_ec"]["ec"] == "3.2.1.26"
    assert first["metrics"]["metric_name"] == "cluster_center_pairwise_distance"
    assert first["metrics"]["ranking_direction"] == "lower_is_better"
    assert first["metrics"]["calibrated"] is False
    assert first["provenance"]["tool"]["container_digest"] == "sha256:1234abcd"
    assert first["provenance"]["command"] == list(command.argv)
    assert len({record["source"]["correlation_group"] for record in evidence}) == 1
    state = StageStateStore(stage).load()
    assert state is not None
    image_metadata = next(
        software
        for software in state.software
        if software["name"] == "clean-container-image"
    )
    assert image_metadata["version"] == "mocked-clean-container-image-version"


@pytest.mark.parametrize(
    ("variant", "fixture", "metric", "direction", "expected_rank"),
    [
        (
            "maxsep_gmm_confidence",
            "gmm_confidence.csv",
            "gmm_confidence_estimate",
            "higher_is_better",
            2,
        ),
        (
            "opaque_wrapper",
            "opaque.csv",
            "clean_model_score",
            "unknown",
            None,
        ),
    ],
)
def test_gmm_and_opaque_metric_semantics_are_preserved(
    tmp_path: Path,
    fasta_file,
    variant: str,
    fixture: str,
    metric: str,
    direction: str,
    expected_rank: int | None,
) -> None:
    config = _config(tmp_path, fixture_variant=variant)
    backend = CleanFixtureBackend(FIXTURES / fixture)
    run_id = f"clean-{variant}"
    result = _runner(tmp_path, backend).run(
        _input(fasta_file), workflow=_workflow(config), run_id=run_id
    )
    evidence = _records(
        RunPaths(tmp_path / "results", tmp_path / "logs", run_id).evidence
        / "clean_evidence.jsonl"
    )

    assert result.status is RunStatus.COMPLETED
    assert evidence[0]["metrics"]["metric_name"] == metric
    assert evidence[0]["metrics"]["ranking_direction"] == direction
    assert evidence[0]["metrics"]["normalized_rank"] == expected_rank
    assert evidence[0]["metrics"]["raw_serialized_value"] in {"0.8200", "0.7310"}


def test_unknown_model_version_is_explicitly_unavailable(
    tmp_path: Path, fasta_file
) -> None:
    document = _mapping(tmp_path)
    document["clean"]["model"]["version"] = "unknown"
    config = clean_config_from_mapping(document)
    backend = CleanFixtureBackend(FIXTURES / "maxsep_distance.csv")
    result = _runner(tmp_path, backend).run(
        _input(fasta_file),
        workflow=_workflow(config),
        run_id="clean-model-version-unknown",
    )
    paths = RunPaths(
        tmp_path / "results", tmp_path / "logs", "clean-model-version-unknown"
    )
    summary = json.loads(
        (paths.for_stage("clean").normalized / "clean_summary.json").read_text()
    )
    evidence = _records(paths.evidence / "clean_evidence.jsonl")

    assert result.status is RunStatus.COMPLETED
    assert summary["model"]["version"] == "unknown"
    assert summary["model"]["version_available"] is False
    assert evidence[0]["metrics"]["model_version_available"] is False


def test_malformed_candidates_remain_in_rejection_output(
    tmp_path: Path, fasta_file
) -> None:
    backend = CleanFixtureBackend(FIXTURES / "malformed_candidates.csv")
    result = _runner(tmp_path, backend).run(
        _input(fasta_file),
        workflow=_workflow(_config(tmp_path)),
        run_id="clean-rejections",
    )
    paths = RunPaths(tmp_path / "results", tmp_path / "logs", "clean-rejections")
    summary = json.loads(
        (paths.for_stage("clean").normalized / "clean_summary.json").read_text()
    )

    assert result.status is RunStatus.COMPLETED
    assert summary["counts"]["rejected_predictions"] == 4
    assert summary["counts"]["evidence_records"] == 0
    assert (
        len(_records(paths.for_stage("clean").normalized / "clean_rejections.jsonl"))
        == 4
    )


def test_empty_result_and_zero_retained_candidates_are_successes(
    tmp_path: Path, fasta_file
) -> None:
    empty_backend = CleanFixtureBackend(FIXTURES / "empty.csv")
    empty_result = _runner(tmp_path, empty_backend).run(
        _input(fasta_file),
        workflow=_workflow(_config(tmp_path)),
        run_id="clean-empty",
    )
    empty_paths = RunPaths(tmp_path / "results", tmp_path / "logs", "clean-empty")
    empty_summary = json.loads(
        (empty_paths.for_stage("clean").normalized / "clean_summary.json").read_text()
    )
    assert empty_result.status is RunStatus.COMPLETED
    assert empty_summary["empty_result"] is True

    document = _mapping(tmp_path)
    document["clean"]["filtering"].update(
        threshold=0.0,
        threshold_comparison="lte",
    )
    filtered_config = clean_config_from_mapping(document)
    filtered_backend = CleanFixtureBackend(FIXTURES / "maxsep_distance.csv")
    filtered_result = _runner(tmp_path, filtered_backend).run(
        _input(fasta_file),
        workflow=_workflow(filtered_config),
        run_id="clean-zero-retained",
    )
    filtered_paths = RunPaths(
        tmp_path / "results", tmp_path / "logs", "clean-zero-retained"
    )
    filtered_summary = json.loads(
        (
            filtered_paths.for_stage("clean").normalized / "clean_summary.json"
        ).read_text()
    )
    assert filtered_result.status is RunStatus.COMPLETED
    assert filtered_summary["zero_retained_candidates"] is True
    assert filtered_summary["counts"]["evidence_records"] == 0


def test_structurally_malformed_output_fails_cleanly(
    tmp_path: Path, fasta_file
) -> None:
    backend = CleanFixtureBackend(FIXTURES / "malformed_csv.csv")
    result = _runner(tmp_path, backend).run(
        _input(fasta_file),
        workflow=_workflow(_config(tmp_path)),
        run_id="clean-malformed-output",
    )
    paths = RunPaths(tmp_path / "results", tmp_path / "logs", "clean-malformed-output")
    summary = json.loads(
        (paths.for_stage("clean").normalized / "clean_summary.json").read_text()
    )
    assert result.status is RunStatus.FAILED
    assert summary["reason_code"] == "malformed_clean_output"
    assert (paths.for_stage("clean").raw / "clean_result.csv").is_file()


@pytest.mark.parametrize(
    ("required", "expected"),
    [
        (True, RunStatus.FAILED),
        (False, RunStatus.COMPLETED_WITH_OPTIONAL_FAILURES),
    ],
)
def test_command_failure_respects_required_semantics(
    tmp_path: Path, fasta_file, required: bool, expected: RunStatus
) -> None:
    backend = CleanFixtureBackend(
        FIXTURES / "empty.csv", return_code=2, stderr="CLEAN fixture failed"
    )
    result = _runner(tmp_path, backend).run(
        _input(fasta_file),
        workflow=_workflow(_config(tmp_path, required=required)),
        run_id=f"clean-command-failure-{required}",
    )
    assert result.status is expected
    assert result.stage_outcomes["clean"] is StageStatus.FAILED


@pytest.mark.parametrize(
    ("version_codes", "reason"),
    [
        ({"docker": 127}, "docker_runtime_unavailable"),
        ({"docker-compose": 1}, "docker_compose_unavailable"),
    ],
)
def test_missing_docker_runtime_or_compose_is_explicit(
    tmp_path: Path,
    fasta_file,
    version_codes: dict[str, int],
    reason: str,
) -> None:
    backend = CleanFixtureBackend(FIXTURES / "empty.csv", version_codes=version_codes)
    result = _runner(tmp_path, backend).run(
        _input(fasta_file),
        workflow=_workflow(_config(tmp_path)),
        run_id=f"clean-{reason}",
    )
    paths = RunPaths(tmp_path / "results", tmp_path / "logs", f"clean-{reason}")
    summary = json.loads(
        (paths.for_stage("clean").normalized / "clean_summary.json").read_text()
    )
    assert result.status is RunStatus.FAILED
    assert summary["reason_code"] == reason
    assert backend.commands == []


def test_unavailable_docker_image_is_detected_before_execution(
    tmp_path: Path, fasta_file
) -> None:
    backend = CleanFixtureBackend(
        FIXTURES / "empty.csv", version_codes={"clean-container-image": 1}
    )
    result = _runner(tmp_path, backend).run(
        _input(fasta_file),
        workflow=_workflow(_config(tmp_path)),
        run_id="clean-image-inspect-missing",
    )
    paths = RunPaths(
        tmp_path / "results", tmp_path / "logs", "clean-image-inspect-missing"
    )
    summary = json.loads(
        (paths.for_stage("clean").normalized / "clean_summary.json").read_text()
    )

    assert result.status is RunStatus.FAILED
    assert summary["reason_code"] == "docker_image_unavailable"
    assert backend.commands == []


def test_image_and_required_mount_failures_are_distinct(
    tmp_path: Path, fasta_file
) -> None:
    image_backend = CleanFixtureBackend(
        FIXTURES / "empty.csv", return_code=1, stderr="No such image"
    )
    _runner(tmp_path, image_backend).run(
        _input(fasta_file),
        workflow=_workflow(_config(tmp_path)),
        run_id="clean-image-missing",
    )
    image_paths = RunPaths(
        tmp_path / "results", tmp_path / "logs", "clean-image-missing"
    )
    image_summary = json.loads(
        (image_paths.for_stage("clean").normalized / "clean_summary.json").read_text()
    )
    assert image_summary["reason_code"] == "docker_image_unavailable"

    document = _mapping(tmp_path)
    document["clean"]["mounts"]["torch_cache"] = str(tmp_path / "missing-cache")
    mount_config = clean_config_from_mapping(document)
    mount_backend = CleanFixtureBackend(FIXTURES / "empty.csv")
    _runner(tmp_path, mount_backend).run(
        _input(fasta_file),
        workflow=_workflow(mount_config),
        run_id="clean-mount-missing",
    )
    mount_paths = RunPaths(
        tmp_path / "results", tmp_path / "logs", "clean-mount-missing"
    )
    mount_summary = json.loads(
        (mount_paths.for_stage("clean").normalized / "clean_summary.json").read_text()
    )
    assert mount_summary["reason_code"] == "required_mount_unavailable"
    assert mount_backend.commands == []


@pytest.mark.parametrize(
    ("strategy", "return_code", "stderr", "reason"),
    [
        ("command", 127, "runtime missing", "external_command_runtime_unavailable"),
        (
            "docker_compose",
            1,
            "pretrained weights model file missing",
            "model_files_unavailable",
        ),
    ],
)
def test_external_runtime_and_model_file_failures_are_distinct(
    tmp_path: Path,
    fasta_file,
    strategy: str,
    return_code: int,
    stderr: str,
    reason: str,
) -> None:
    version_codes = {"clean-runtime": 127} if strategy == "command" else None
    backend = CleanFixtureBackend(
        FIXTURES / "empty.csv",
        return_code=return_code,
        stderr=stderr,
        version_codes=version_codes,
    )
    run_id = f"clean-{reason}"
    result = _runner(tmp_path, backend).run(
        _input(fasta_file),
        workflow=_workflow(_config(tmp_path, strategy=strategy)),
        run_id=run_id,
    )
    paths = RunPaths(tmp_path / "results", tmp_path / "logs", run_id)
    summary = json.loads(
        (paths.for_stage("clean").normalized / "clean_summary.json").read_text()
    )

    assert result.status is RunStatus.FAILED
    assert summary["reason_code"] == reason


def test_missing_and_changed_resource_fingerprint_are_traced_and_invalidate(
    tmp_path: Path, fasta_file
) -> None:
    source = _input(fasta_file)
    fingerprint = tmp_path / "model.sha256"
    document = _mapping(tmp_path)
    document["clean"]["resource_fingerprints"] = [str(fingerprint)]
    config = clean_config_from_mapping(document)
    missing_backend = CleanFixtureBackend(FIXTURES / "empty.csv")
    missing_result = _runner(tmp_path, missing_backend).run(
        source,
        workflow=_workflow(config),
        run_id="clean-fingerprint-missing",
    )
    missing_paths = RunPaths(
        tmp_path / "results", tmp_path / "logs", "clean-fingerprint-missing"
    )
    missing_summary = json.loads(
        (missing_paths.for_stage("clean").normalized / "clean_summary.json").read_text()
    )
    assert missing_result.status is RunStatus.FAILED
    assert missing_summary["reason_code"] == "resource_fingerprint_unavailable"
    assert missing_backend.commands == []

    fingerprint.write_text("model-v1\n", encoding="utf-8")
    backend = CleanFixtureBackend(FIXTURES / "empty.csv")
    runner = _runner(tmp_path, backend)
    runner.run(source, workflow=_workflow(config), run_id="clean-fingerprint-cache")
    reused = runner.run(
        source, workflow=_workflow(config), run_id="clean-fingerprint-cache"
    )
    assert reused.stage_outcomes["clean"] is StageStatus.SKIPPED
    fingerprint.write_text("model-v2\n", encoding="utf-8")
    rerun = runner.run(
        source, workflow=_workflow(config), run_id="clean-fingerprint-cache"
    )
    assert rerun.stage_outcomes["clean"] is StageStatus.COMPLETED
    assert len(backend.commands) == 2


def test_command_strategy_uses_mocked_external_environment(
    tmp_path: Path, fasta_file
) -> None:
    output_directory = tmp_path / "command-output"
    document = _mapping(tmp_path, strategy="command")
    document["clean"]["mounts"]["output_directory"] = str(output_directory)
    config = clean_config_from_mapping(document)
    backend = CleanFixtureBackend(
        FIXTURES / "maxsep_distance.csv",
        command_output_directory=output_directory,
    )
    result = _runner(tmp_path, backend).run(
        _input(fasta_file), workflow=_workflow(config), run_id="clean-command"
    )
    assert result.status is RunStatus.COMPLETED
    assert backend.commands[0].argv[:5] == (
        "conda",
        "run",
        "-n",
        "clean",
        "python-fixture",
    )


def test_disabled_provider_does_not_execute(tmp_path: Path, fasta_file) -> None:
    document = _mapping(tmp_path)
    document["clean"]["enabled"] = False
    config = clean_config_from_mapping(document)
    backend = CleanFixtureBackend(FIXTURES / "empty.csv")
    result = _runner(tmp_path, backend).run(
        _input(fasta_file), workflow=_workflow(config), run_id="clean-disabled"
    )
    paths = RunPaths(tmp_path / "results", tmp_path / "logs", "clean-disabled")
    summary = json.loads(
        (paths.for_stage("clean").normalized / "clean_summary.json").read_text()
    )
    assert result.status is RunStatus.COMPLETED
    assert summary["status"] == "disabled"
    assert backend.commands == []


def test_cache_reuse_and_scientific_configuration_invalidation(
    tmp_path: Path, fasta_file
) -> None:
    source = _input(fasta_file)
    backend = CleanFixtureBackend(FIXTURES / "maxsep_distance.csv")
    runner = _runner(tmp_path, backend)
    config = _config(tmp_path)
    runner.run(source, workflow=_workflow(config), run_id="clean-cache")
    resumed = runner.run(source, workflow=_workflow(config), run_id="clean-cache")
    assert resumed.stage_outcomes["clean"] is StageStatus.SKIPPED
    assert len(backend.commands) == 1

    source.write_text(">q1\nACDEFGHIKLMQ\n>q2\nMNPQRSTVWYAA\n", encoding="utf-8")
    runner.run(source, workflow=_workflow(config), run_id="clean-cache")
    assert len(backend.commands) == 2

    changes = [
        ("model", "version", "v2"),
        ("metric", "semantics", "revised explicit distance semantics"),
        ("filtering", "maximum_retained_candidates", 1),
        ("execution.docker_compose", "image_digest", "sha256:9999aaaa"),
    ]
    for section, key, value in changes:
        document = _mapping(tmp_path)
        if section == "execution.docker_compose":
            document["clean"]["execution"]["docker_compose"][key] = value
        else:
            document["clean"][section][key] = value
        config = clean_config_from_mapping(document)
        runner.run(source, workflow=_workflow(config), run_id="clean-cache")
    assert len(backend.commands) == 6
    paths = RunPaths(tmp_path / "results", tmp_path / "logs", "clean-cache")
    state = StageStateStore(paths.for_stage("clean")).load()
    assert state is not None and state.attempt == 6
