"""Tests for BLAST configuration and command construction."""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path

import pytest
import yaml
from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError

from enzynotation.backends.base import CommandSpec, ExecutionBackend
from enzynotation.parsers.blast import BLAST_OUTFMT_FIELDS
from enzynotation.provenance import CommandProvenance, SoftwareProvenance
from enzynotation.tools.base import ToolConfigurationError
from enzynotation.tools.blast import (
    BlastTool,
    blast_config_from_mapping,
    load_blast_config,
)


def _mapping(tmp_path: Path) -> dict:
    return {
        "schema_version": 1,
        "blast": {
            "required": True,
            "thresholds_validated": False,
            "executable": "/opt/blast/bin/blastp",
            "database": {
                "path": str(tmp_path / "db" / "proteins"),
                "name": "curated-proteins",
                "version": "2026-03",
            },
            "metadata": {"path": str(tmp_path / "metadata.tsv")},
            "execution": {
                "cpus": 6,
                "evalue": 1e-7,
                "max_target_sequences": 123,
            },
            "filters": {
                "maximum_evalue": 1e-6,
                "minimum_query_coverage": 0.7,
                "minimum_subject_coverage": 0.6,
                "minimum_percent_identity": 35.0,
                "minimum_aligned_length": 80,
                "maximum_retained_hits_per_query": 25,
            },
        },
    }


class VersionBackend(ExecutionBackend):
    name = "version-test"

    def __init__(self) -> None:
        self.version_call: tuple[str, str, tuple[str, ...]] | None = None

    def execute(
        self,
        command: CommandSpec,
        *,
        stdout_path: Path,
        stderr_path: Path,
    ) -> CommandProvenance:
        raise AssertionError("execute is not used by this test")

    def capture_version(
        self,
        executable: str,
        *,
        name: str | None = None,
        arguments: Sequence[str] = ("--version",),
    ) -> SoftwareProvenance:
        self.version_call = (executable, name or "", tuple(arguments))
        return SoftwareProvenance(
            name=name or "blastp",
            version="blastp: 2.15.0+",
            executable=executable,
            version_command=(executable, *arguments),
            version_return_code=0,
        )


def test_example_configuration_satisfies_json_schema() -> None:
    schema = json.loads(Path("configs/schema/blast.schema.json").read_text())
    document = yaml.safe_load(Path("configs/tools/blast.yaml").read_text())
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(document)


def test_invalid_configuration_fails_json_schema() -> None:
    schema = json.loads(Path("configs/schema/blast.schema.json").read_text())
    document = yaml.safe_load(Path("configs/tools/blast.yaml").read_text())
    document["blast"]["filters"]["minimum_query_coverage"] = 1.1

    with pytest.raises(ValidationError):
        Draft202012Validator(schema).validate(document)


def test_load_config_and_construct_explicit_command(tmp_path: Path) -> None:
    path = tmp_path / "blast.yaml"
    path.write_text(yaml.safe_dump(_mapping(tmp_path)), encoding="utf-8")
    config = load_blast_config(path)
    command = BlastTool(config).build_command(
        query=Path("query.fasta"), output=Path("hits.tsv")
    )

    assert command.argv[0] == "/opt/blast/bin/blastp"
    assert command.argv[command.argv.index("-db") + 1].endswith("db/proteins")
    assert command.argv[command.argv.index("-num_threads") + 1] == "6"
    assert command.argv[command.argv.index("-evalue") + 1] == "1e-07"
    assert command.argv[command.argv.index("-max_target_seqs") + 1] == "123"
    assert command.argv[command.argv.index("-outfmt") + 1] == (
        "6 " + " ".join(BLAST_OUTFMT_FIELDS)
    )


def test_database_artifacts_are_external_inputs(tmp_path: Path) -> None:
    config = blast_config_from_mapping(_mapping(tmp_path))
    prefix = config.database
    prefix.parent.mkdir(parents=True)
    (prefix.parent / f"{prefix.name}.pin").write_bytes(b"index")
    (prefix.parent / f"{prefix.name}.psq").write_bytes(b"sequence")

    artifacts = BlastTool(config).database_artifacts()

    assert len(artifacts) == 2
    assert {path.suffix for path in artifacts.values()} == {".pin", ".psq"}


def test_missing_database_is_a_configuration_error(tmp_path: Path) -> None:
    config = blast_config_from_mapping(_mapping(tmp_path))
    with pytest.raises(ToolConfigurationError, match="no BLAST database artifacts"):
        BlastTool(config).database_artifacts()


def test_blast_version_capture_uses_dash_version(tmp_path: Path) -> None:
    tool = BlastTool(blast_config_from_mapping(_mapping(tmp_path)))
    backend = VersionBackend()

    software = tool.capture_version(backend)

    assert software.version == "blastp: 2.15.0+"
    assert backend.version_call == (
        "/opt/blast/bin/blastp",
        "blastp",
        ("-version",),
    )


@pytest.mark.parametrize(
    ("mutator", "message"),
    [
        (lambda value: value.update(schema_version=2), "schema_version"),
        (
            lambda value: value["blast"]["execution"].update(cpus=0),
            "positive integer",
        ),
        (
            lambda value: value["blast"]["filters"].update(minimum_query_coverage=1.1),
            "from 0 to 1",
        ),
        (
            lambda value: value["blast"]["filters"].update(
                minimum_percent_identity=101
            ),
            "from 0 to 100",
        ),
    ],
)
def test_invalid_configuration_is_rejected(
    tmp_path: Path, mutator, message: str
) -> None:
    value = _mapping(tmp_path)
    mutator(value)
    with pytest.raises(ToolConfigurationError, match=message):
        blast_config_from_mapping(value)
