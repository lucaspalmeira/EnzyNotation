"""Tests for the Milestone 1 command-line workflow."""

import json
from pathlib import Path

from enzynotation.cli import main


def test_validate_command_writes_outputs(tmp_path: Path, fasta_file, capsys) -> None:
    source = fasta_file(">protein\nacd*\n")
    normalized = tmp_path / "normalized.fasta"
    report = tmp_path / "validation.json"

    status = main(
        [
            "validate",
            str(source),
            "--output",
            str(normalized),
            "--report",
            str(report),
        ]
    )

    assert status == 0
    assert normalized.read_text() == ">protein\nACD\n"
    assert json.loads(report.read_text())["valid"] is True
    assert "VALID: 1 record(s), 1 warning(s)" in capsys.readouterr().out


def test_invalid_input_does_not_write_normalized_fasta(
    tmp_path: Path, fasta_file, capsys
) -> None:
    source = fasta_file(">protein\nAC?\n")
    normalized = tmp_path / "normalized.fasta"
    report = tmp_path / "validation.json"

    status = main(
        [
            "validate",
            str(source),
            "--output",
            str(normalized),
            "--report",
            str(report),
        ]
    )

    assert status == 1
    assert not normalized.exists()
    assert json.loads(report.read_text())["valid"] is False
    assert "INVALID:" in capsys.readouterr().out
