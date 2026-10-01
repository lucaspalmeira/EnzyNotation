"""Tests for CLEAN configuration and command construction without execution."""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path

import pytest
import yaml
from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError

from enzynotation.backends.base import CommandSpec, ExecutionBackend
from enzynotation.provenance import CommandProvenance, SoftwareProvenance
from enzynotation.tools.base import ToolConfigurationError
from enzynotation.tools.clean import (
    CleanTool,
    clean_config_from_mapping,
    load_clean_config,
)


def clean_mapping(tmp_path: Path, *, strategy: str = "docker_compose") -> dict:
    return {
        "schema_version": 1,
        "clean": {
            "enabled": True,
            "required": False,
            "execution": {
                "strategy": strategy,
                "docker_compose": {
                    "docker_executable": "/usr/bin/docker",
                    "compose_file": str(tmp_path / "clean.compose.yml"),
                    "service": "clean",
                    "image": "registry.example/clean:1.2",
                    "image_digest": "sha256:abcdef1234",
                },
                "command": {
                    "prefix": ["conda", "run", "-n", "clean"],
                    "executable": "/usr/bin/python",
                    "working_directory": str(tmp_path / "clean-app"),
                },
            },
            "implementation": {
                "interface": "CLEAN_infer_fasta",
                "entrypoint": "/app/CLEAN_infer_fasta.py",
                "interpreter": "python",
                "inference_method": "max_separation",
                "output_variant": "maxsep_gmm_confidence",
                "result_suffix": "_maxsep.csv",
                "software_version": "unknown",
            },
            "metric": {
                "name": "gmm_confidence_estimate",
                "semantics": "GMM estimate derived from embedding distance",
                "ranking_direction": "higher_is_better",
                "calibrated": False,
            },
            "model": {
                "training_split": "custom-split",
                "pretrained": True,
                "model_identifier": "custom-clean-model",
                "version": "2026-01",
            },
            "mounts": {
                "input_directory": None,
                "output_directory": None,
                "torch_cache": str(tmp_path / "torch-cache"),
                "external_model_data": str(tmp_path / "models"),
            },
            "filtering": {
                "maximum_retained_candidates": 5,
                "threshold": 0.7,
                "threshold_comparison": "gte",
                "threshold_status": "operational",
                "allowed_ec_depths": [3, 4],
                "allow_partial_ec": True,
            },
            "resource_fingerprints": [str(tmp_path / "model.sha256")],
        },
    }


class VersionBackend(ExecutionBackend):
    name = "clean-version-fixture"

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, tuple[str, ...]]] = []

    def execute(
        self,
        command: CommandSpec,
        *,
        stdout_path: Path,
        stderr_path: Path,
    ) -> CommandProvenance:
        raise AssertionError("CLEAN must not execute in tool-construction tests")

    def capture_version(
        self,
        executable: str,
        *,
        name: str | None = None,
        arguments: Sequence[str] = ("--version",),
    ) -> SoftwareProvenance:
        self.calls.append((executable, name or "", tuple(arguments)))
        return SoftwareProvenance(
            name=name or "runtime",
            version="fixture-version",
            executable=executable,
            version_command=(executable, *arguments),
            version_return_code=0,
        )


def test_clean_example_and_schema_are_valid() -> None:
    schema = json.loads(Path("configs/schema/clean.schema.json").read_text())
    document = yaml.safe_load(Path("configs/tools/clean.yaml").read_text())
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(document)
    assert load_clean_config(Path("configs/tools/clean.yaml")).strategy == (
        "docker_compose"
    )


def test_deliberately_invalid_clean_configuration_fails_schema() -> None:
    schema = json.loads(Path("configs/schema/clean.schema.json").read_text())
    document = yaml.safe_load(Path("configs/tools/clean.yaml").read_text())
    document["clean"]["metric"]["ranking_direction"] = "smallest_maybe"
    with pytest.raises(ValidationError):
        Draft202012Validator(schema).validate(document)


def test_docker_compose_command_mounts_and_image_are_explicit(tmp_path: Path) -> None:
    config = clean_config_from_mapping(clean_mapping(tmp_path))
    command = CleanTool(config).build_command(
        input_directory=tmp_path / "input mount",
        output_directory=tmp_path / "output mount",
        basename="validated_input",
    )

    assert command.argv == (
        "/usr/bin/docker",
        "compose",
        "-f",
        str(tmp_path / "clean.compose.yml"),
        "run",
        "--rm",
        "--volume",
        f"{tmp_path / 'torch-cache'}:/root/.cache/torch/hub/checkpoints",
        "--volume",
        f"{tmp_path / 'models'}:/app/data/pretrained:ro",
        "clean",
        "python",
        "/app/CLEAN_infer_fasta.py",
        "--fasta_data",
        "validated_input",
    )
    assert command.environment == {
        "CLEAN_IMAGE": "registry.example/clean:1.2@sha256:abcdef1234",
        "CLEAN_INPUT_DIR": str((tmp_path / "input mount").resolve()),
        "CLEAN_OUTPUT_DIR": str((tmp_path / "output mount").resolve()),
    }
    assert CleanTool(config).database_artifacts() == {}


def test_provider_compose_definition_is_ephemeral_and_mount_driven() -> None:
    document = yaml.safe_load(Path("docker/clean.compose.yml").read_text())
    service = document["services"]["clean"]

    assert service["image"] == "${CLEAN_IMAGE:-moleculemaker/clean-image-amd64}"
    assert service["pull_policy"] == "never"
    assert service["volumes"] == [
        {
            "type": "bind",
            "source": "${CLEAN_INPUT_DIR:?set CLEAN_INPUT_DIR}",
            "target": "/app/data/inputs",
        },
        {
            "type": "bind",
            "source": "${CLEAN_OUTPUT_DIR:?set CLEAN_OUTPUT_DIR}",
            "target": "/app/results/inputs",
        },
    ]


def test_external_command_prefix_and_expected_output(tmp_path: Path) -> None:
    config = clean_config_from_mapping(clean_mapping(tmp_path, strategy="command"))
    tool = CleanTool(config)
    command = tool.build_command(
        input_directory=tmp_path / "inputs",
        output_directory=tmp_path / "outputs",
        basename="enzynotation_clean_input",
    )

    assert command.argv == (
        "conda",
        "run",
        "-n",
        "clean",
        "/usr/bin/python",
        "/app/CLEAN_infer_fasta.py",
        "--fasta_data",
        "enzynotation_clean_input",
    )
    assert command.cwd == tmp_path / "clean-app"
    assert tool.expected_output(tmp_path, "sample") == tmp_path / "sample_maxsep.csv"


def test_runtime_version_commands_are_strategy_specific(tmp_path: Path) -> None:
    backend = VersionBackend()
    docker = CleanTool(clean_config_from_mapping(clean_mapping(tmp_path)))
    docker.capture_runtime_versions(backend)
    command = CleanTool(
        clean_config_from_mapping(clean_mapping(tmp_path, strategy="command"))
    )
    command.capture_runtime_versions(backend)

    assert backend.calls == [
        ("/usr/bin/docker", "docker", ("--version",)),
        ("/usr/bin/docker", "docker-compose", ("compose", "version")),
        (
            "/usr/bin/docker",
            "clean-container-image",
            (
                "image",
                "inspect",
                "--format={{json .Id}} {{json .RepoDigests}}",
                "registry.example/clean:1.2@sha256:abcdef1234",
            ),
        ),
        ("/usr/bin/python", "clean-runtime", ("--version",)),
    ]


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (
            lambda value: value["clean"]["metric"].update(
                ranking_direction="lower_is_better"
            ),
            "maxsep_gmm_confidence requires",
        ),
        (
            lambda value: value["clean"]["filtering"].update(
                threshold_comparison="lte"
            ),
            "conflicts with metric",
        ),
        (
            lambda value: (
                value["clean"]["execution"]["command"].update(executable=None)
                or value["clean"]["execution"].update(strategy="command")
            ),
            "executable is required",
        ),
        (
            lambda value: value["clean"]["execution"]["docker_compose"].update(
                image_digest="latest"
            ),
            "sha256",
        ),
    ],
)
def test_invalid_runtime_or_metric_configuration_is_rejected(
    tmp_path: Path, mutation, message: str
) -> None:
    document = clean_mapping(tmp_path)
    mutation(document)
    with pytest.raises(ToolConfigurationError, match=message):
        clean_config_from_mapping(document)


def test_unknown_metric_threshold_requires_explicit_comparison(tmp_path: Path) -> None:
    document = clean_mapping(tmp_path)
    document["clean"]["implementation"]["output_variant"] = "opaque_wrapper"
    document["clean"]["metric"].update(
        name="clean_model_score",
        semantics="unknown",
        ranking_direction="unknown",
    )
    document["clean"]["filtering"]["threshold_comparison"] = None

    with pytest.raises(ToolConfigurationError, match="requires threshold_comparison"):
        clean_config_from_mapping(document)


def test_raw_distance_variant_enforces_lower_is_better(tmp_path: Path) -> None:
    document = clean_mapping(tmp_path)
    document["clean"]["implementation"]["output_variant"] = "maxsep_distance"
    document["clean"]["metric"].update(
        name="cluster_center_pairwise_distance",
        semantics="embedding distance to predicted EC cluster center",
        ranking_direction="lower_is_better",
    )
    document["clean"]["filtering"].update(
        threshold=3.0,
        threshold_comparison=None,
    )

    config = clean_config_from_mapping(document)
    assert config.filtering.threshold_comparison == "lte"
    assert config.metric.calibrated is False
