"""Tests for HMMER and InterProScan tool adapters."""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path

import pytest
import yaml
from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError

from enzynotation.backends.base import CommandSpec, ExecutionBackend
from enzynotation.backends.local import LocalBackend
from enzynotation.provenance import CommandProvenance, SoftwareProvenance
from enzynotation.tools.base import ToolConfigurationError
from enzynotation.tools.hmmer import HmmerTool, hmmer_config_from_mapping
from enzynotation.tools.interpro import InterProTool, interpro_config_from_mapping


def _hmmer_mapping(tmp_path: Path) -> dict:
    return {
        "schema_version": 1,
        "hmmer": {
            "enabled": True,
            "required": True,
            "executable": "/opt/hmmer/bin/hmmscan",
            "database": {
                "path": str(tmp_path / "Pfam-A.hmm"),
                "name": "Pfam-A",
                "version": "36.0",
                "kind": "pfam",
            },
            "execution": {
                "cpus": 4,
                "sequence_evalue": 1e-3,
                "domain_evalue": 1e-2,
            },
            "filters": {
                "maximum_sequence_evalue": 1e-5,
                "maximum_domain_i_evalue": 1e-4,
                "minimum_bit_score": 25.0,
                "minimum_query_coverage": 0.2,
            },
        },
    }


def _interpro_mapping(tmp_path: Path) -> dict:
    return {
        "schema_version": 1,
        "interproscan": {
            "enabled": True,
            "required": False,
            "executable": "/opt/interpro/interproscan.sh",
            "database": {
                "name": "InterPro",
                "version": "100.0",
                "path": str(tmp_path / "interpro-data"),
            },
            "execution": {
                "cpus": 6,
                "applications": ["Pfam", "CDD"],
                "include_go_terms": True,
                "include_pathways": True,
            },
        },
    }


class VersionBackend(ExecutionBackend):
    name = "versions"

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[str, ...]]] = []

    def execute(
        self,
        command: CommandSpec,
        *,
        stdout_path: Path,
        stderr_path: Path,
    ) -> CommandProvenance:
        raise AssertionError("execute is not used")

    def capture_version(
        self,
        executable: str,
        *,
        name: str | None = None,
        arguments: Sequence[str] = ("--version",),
    ) -> SoftwareProvenance:
        self.calls.append((executable, tuple(arguments)))
        return SoftwareProvenance(name or "tool", "test-version", executable)


@pytest.mark.parametrize(
    ("schema_name", "config_name"),
    [("hmmer", "hmmer"), ("interpro", "interpro")],
)
def test_tool_examples_validate_against_json_schema(
    schema_name: str, config_name: str
) -> None:
    schema = json.loads(Path(f"configs/schema/{schema_name}.schema.json").read_text())
    document = yaml.safe_load(Path(f"configs/tools/{config_name}.yaml").read_text())
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(document)


def test_invalid_tool_config_fails_json_schema() -> None:
    schema = json.loads(Path("configs/schema/hmmer.schema.json").read_text())
    document = yaml.safe_load(Path("configs/tools/hmmer.yaml").read_text())
    document["hmmer"]["filters"]["minimum_query_coverage"] = 2
    with pytest.raises(ValidationError):
        Draft202012Validator(schema).validate(document)


def test_hmmer_command_and_database_artifacts(tmp_path: Path) -> None:
    config = hmmer_config_from_mapping(_hmmer_mapping(tmp_path))
    config.database.write_text("HMMER3/f\n", encoding="utf-8")
    Path(f"{config.database}.h3f").write_bytes(b"pressed")
    tool = HmmerTool(config)
    command = tool.build_command(query=Path("query.fasta"), output=Path("hits.domtbl"))

    assert command.argv == (
        "/opt/hmmer/bin/hmmscan",
        "--domtblout",
        "hits.domtbl",
        "--noali",
        "--cpu",
        "4",
        "-E",
        "0.001",
        "--domE",
        "0.01",
        str(config.database),
        "query.fasta",
    )
    assert len(tool.database_artifacts()) == 2


def test_hmmer_missing_database_is_explicit(tmp_path: Path) -> None:
    tool = HmmerTool(hmmer_config_from_mapping(_hmmer_mapping(tmp_path)))
    with pytest.raises(ToolConfigurationError, match="does not exist"):
        tool.database_artifacts()


def test_interpro_command_preserves_requested_annotations(tmp_path: Path) -> None:
    config = interpro_config_from_mapping(_interpro_mapping(tmp_path))
    command = InterProTool(config).build_command(
        query=Path("query.fasta"), output=Path("interpro.tsv")
    )

    assert command.argv[:9] == (
        "/opt/interpro/interproscan.sh",
        "-i",
        "query.fasta",
        "-f",
        "TSV",
        "-o",
        "interpro.tsv",
        "-cpu",
        "6",
    )
    assert command.argv[command.argv.index("-appl") + 1] == "Pfam,CDD"
    assert "-goterms" in command.argv
    assert "-pa" in command.argv


def test_interpro_data_directory_marker_is_cache_input(tmp_path: Path) -> None:
    config = interpro_config_from_mapping(_interpro_mapping(tmp_path))
    assert config.data_directory is not None
    config.data_directory.mkdir()
    (config.data_directory / "version.txt").write_text("100.0\n", encoding="utf-8")

    artifacts = InterProTool(config).database_artifacts()
    assert list(artifacts.values())[0].name == "version.txt"


def test_tool_version_commands_are_provider_specific(tmp_path: Path) -> None:
    backend = VersionBackend()
    HmmerTool(hmmer_config_from_mapping(_hmmer_mapping(tmp_path))).capture_version(
        backend
    )
    InterProTool(
        interpro_config_from_mapping(_interpro_mapping(tmp_path))
    ).capture_version(backend)

    assert backend.calls == [
        ("/opt/hmmer/bin/hmmscan", ("-h",)),
        ("/opt/interpro/interproscan.sh", ("--version",)),
    ]


def test_local_version_capture_selects_hmmer_version_line(tmp_path: Path) -> None:
    executable = tmp_path / "hmmscan"
    executable.write_text(
        "#!/bin/sh\n"
        "printf '%s\\n' '# hmmscan :: search profiles' '# HMMER 3.4 (Nov 2023)'\n",
        encoding="utf-8",
    )
    executable.chmod(0o755)

    software = LocalBackend().capture_version(
        str(executable), name="hmmscan", arguments=("-h",)
    )
    assert software.version == "# HMMER 3.4 (Nov 2023)"


@pytest.mark.parametrize(
    "mutation",
    [
        lambda value: value["hmmer"]["database"].update(kind="unknown"),
        lambda value: value["hmmer"]["execution"].update(cpus=0),
        lambda value: value["hmmer"]["filters"].update(minimum_query_coverage=1.1),
    ],
)
def test_invalid_hmmer_configuration_is_rejected(tmp_path: Path, mutation) -> None:
    document = _hmmer_mapping(tmp_path)
    mutation(document)
    with pytest.raises(ToolConfigurationError):
        hmmer_config_from_mapping(document)
