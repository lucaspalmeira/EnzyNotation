"""Structural checks for the offline-safe Apptainer definition."""

from pathlib import Path

import yaml

from enzynotation.tools.foldseek import FoldseekTool, foldseek_config_from_mapping

DEFINITION = Path("apptainer/enzynotation.def")


def test_definition_has_runtime_and_versioned_project_metadata() -> None:
    content = DEFINITION.read_text(encoding="utf-8")
    assert "Bootstrap: docker" in content
    assert "From: python:3.12-slim" in content
    assert "org.opencontainers.image.version 0.1.0" in content
    assert 'exec enzynotation "$@"' in content


def test_definition_preserves_shared_bind_contract_without_resources() -> None:
    content = DEFINITION.read_text(encoding="utf-8")
    for path in (
        "/work/input",
        "/work/results",
        "/work/logs",
        "/databases",
        "/models",
        "/cache",
    ):
        assert path in content
    forbidden = ("COPY databases", "COPY models", "COPY results", "docker.sock")
    assert not any(value in content for value in forbidden)


def test_definition_installs_only_project_and_lightweight_tools() -> None:
    content = DEFINITION.read_text(encoding="utf-8")
    assert "ncbi-blast+" in content
    assert "hmmer" in content
    assert "foldseek database" not in content.lower()
    assert "clean model" not in content.lower()


def test_slurm_scripts_use_required_shell_safety() -> None:
    for script in (Path("slurm/run_stage.sbatch"), Path("slurm/submit_pipeline.sh")):
        content = script.read_text(encoding="utf-8")
        assert "set -eo pipefail" in content
        assert "set -u" not in content
        assert '"$@"' in content


def test_foldseek_hpc_command_prefix_uses_existing_provider_contract(
    tmp_path: Path,
) -> None:
    document = yaml.safe_load(Path("configs/tools/foldseek.yaml").read_text())
    document["foldseek"]["execution"] = {
        "strategy": "command",
        "command": {
            "prefix": ["apptainer", "exec", "/containers/foldseek.sif"],
            "executable": "foldseek",
            "version_arguments": ["version"],
        },
    }
    config = foldseek_config_from_mapping(document)
    command = FoldseekTool(config).build_command(
        query_directory=tmp_path / "query",
        output_directory=tmp_path / "output",
        temporary_directory=tmp_path / "tmp",
    )
    assert command.argv[:4] == (
        "apptainer",
        "exec",
        "/containers/foldseek.sif",
        "foldseek",
    )
    assert "easy-search" in command.argv
    assert command.environment is None
