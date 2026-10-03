"""Tests for compact Foldseek Compose and external TM-align adapters."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from enzynotation.parsers.foldseek import FOLDSEEK_OUTPUT_FIELDS
from enzynotation.tools.base import ToolConfigurationError
from enzynotation.tools.foldseek import FoldseekTool, foldseek_config_from_mapping
from enzynotation.tools.tmalign import TMAlignTool, tmalign_config_from_mapping


def _foldseek_mapping(tmp_path: Path, *, digest: str | None = None) -> dict:
    return {
        "schema_version": 1,
        "foldseek": {
            "required": False,
            "execution": {
                "strategy": "docker_compose",
                "docker_compose": {
                    "docker_executable": "docker-fixture",
                    "compose_file": str(Path("docker/foldseek.compose.yml").resolve()),
                    "service": "foldseek",
                    "image": "ghcr.io/steineggerlab/foldseek:10-941cd33",
                    "image_digest": digest,
                },
            },
            "database": {
                "path": str(tmp_path / "database"),
                "name": "curated_structures",
                "version": "fixture-v1",
                "fingerprint": None,
                "metadata": str(tmp_path / "metadata.tsv"),
            },
            "parameters": {
                "cpus": 3,
                "evalue": 0.001,
                "max_seqs": 20,
                "sensitivity": 7.5,
            },
            "filters": {
                "maximum_evalue": 0.001,
                "minimum_query_coverage": 0.2,
                "minimum_target_coverage": 0.3,
                "minimum_aligned_length": 10,
                "maximum_retained_hits_per_query": 4,
                "threshold_status": "operational",
            },
            "software_version": "10-941cd33",
        },
    }


def _tmalign_mapping(*, top_n: int | None = 5) -> dict:
    return {
        "schema_version": 1,
        "tmalign": {
            "required": False,
            "executable": "/opt/TMalign",
            "version_arguments": ["-version"],
            "selection": {
                "top_hits_per_query": top_n,
                "minimum_query_coverage": 0.1,
                "minimum_target_coverage": 0.2,
            },
            "thresholds_validated": False,
        },
    }


def test_foldseek_compose_command_uses_explicit_fields_and_mounts(
    tmp_path: Path,
) -> None:
    config = foldseek_config_from_mapping(_foldseek_mapping(tmp_path))
    command = FoldseekTool(config).build_command(
        query_directory=tmp_path / "queries",
        output_directory=tmp_path / "output",
        temporary_directory=tmp_path / "tmp",
    )

    assert command.argv[:7] == (
        "docker-fixture",
        "compose",
        "-f",
        str(Path("docker/foldseek.compose.yml").resolve()),
        "run",
        "--rm",
        "foldseek",
    )
    assert command.argv[7:12] == (
        "easy-search",
        "/work/query",
        "/database/curated_structures",
        "/work/output/foldseek.tsv",
        "/work/tmp",
    )
    field_index = command.argv.index("--format-output")
    assert command.argv[field_index + 1] == ",".join(FOLDSEEK_OUTPUT_FIELDS)
    assert command.argv[-2:] == ("-s", "7.5")
    assert command.environment == {
        "FOLDSEEK_IMAGE": "ghcr.io/steineggerlab/foldseek:10-941cd33",
        "FOLDSEEK_QUERY_DIR": str((tmp_path / "queries").resolve()),
        "FOLDSEEK_DB_DIR": str((tmp_path / "database").resolve()),
        "FOLDSEEK_OUTPUT_DIR": str((tmp_path / "output").resolve()),
        "FOLDSEEK_TMP_DIR": str((tmp_path / "tmp").resolve()),
    }


def test_foldseek_image_digest_is_propagated(tmp_path: Path) -> None:
    digest = "sha256:1234abcd"
    config = foldseek_config_from_mapping(_foldseek_mapping(tmp_path, digest=digest))
    command = FoldseekTool(config).build_command(
        query_directory=tmp_path / "q",
        output_directory=tmp_path / "o",
        temporary_directory=tmp_path / "t",
    )

    assert command.environment["FOLDSEEK_IMAGE"] == (
        "ghcr.io/steineggerlab/foldseek:10-941cd33@sha256:1234abcd"
    )
    assert config.to_dict()["execution"]["docker_compose"]["image_digest"] == digest


def test_foldseek_image_environment_override_is_in_resolved_config(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv(
        "FOLDSEEK_IMAGE", "registry.example/foldseek:test@sha256:feed1234"
    )

    config = foldseek_config_from_mapping(_foldseek_mapping(tmp_path))

    assert config.docker.image == "registry.example/foldseek:test"
    assert config.docker.image_digest == "sha256:feed1234"
    assert config.docker.image_reference == (
        "registry.example/foldseek:test@sha256:feed1234"
    )


def test_foldseek_configuration_rejects_non_compose_strategy(tmp_path: Path) -> None:
    mapping = _foldseek_mapping(tmp_path)
    mapping["foldseek"]["execution"]["strategy"] = "local"

    with pytest.raises(ToolConfigurationError, match="docker_compose"):
        foldseek_config_from_mapping(mapping)


def test_tmalign_command_is_query_first() -> None:
    config = tmalign_config_from_mapping(_tmalign_mapping())
    command = TMAlignTool(config).build_pair_command(
        query=Path("query.pdb"), reference=Path("reference.cif")
    )

    assert command.argv == ("/opt/TMalign", "query.pdb", "reference.cif")
    assert config.selection.top_hits_per_query == 5
    assert config.thresholds_validated is False


def test_tmalign_allows_no_top_n_limit_and_rejects_zero() -> None:
    assert (
        tmalign_config_from_mapping(
            _tmalign_mapping(top_n=None)
        ).selection.top_hits_per_query
        is None
    )
    mapping = _tmalign_mapping(top_n=0)
    with pytest.raises(ToolConfigurationError, match="top_hits_per_query"):
        tmalign_config_from_mapping(mapping)


def test_compose_adapter_has_only_ephemeral_foldseek_service() -> None:
    compose = yaml.safe_load(Path("docker/foldseek.compose.yml").read_text())

    assert set(compose["services"]) == {"foldseek"}
    service = compose["services"]["foldseek"]
    assert service["pull_policy"] == "never"
    assert service["image"].startswith("${FOLDSEEK_IMAGE:-")
    assert all("source" in volume for volume in service["volumes"])
