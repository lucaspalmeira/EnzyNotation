"""Tests for the Milestone 1 command-line workflow."""

import json
from pathlib import Path

import pytest

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


def test_run_command_executes_validation_workflow(
    tmp_path: Path, fasta_file, capsys
) -> None:
    source = fasta_file(">protein\nacd\n")
    results = tmp_path / "results"
    logs = tmp_path / "logs"

    status = main(
        [
            "run",
            str(source),
            "--run-id",
            "cli-run",
            "--results-dir",
            str(results),
            "--logs-dir",
            str(logs),
        ]
    )

    assert status == 0
    assert (results / "cli-run" / "manifest.json").is_file()
    assert (results / "cli-run" / "input" / "normalized.fasta").read_text() == (
        ">protein\nACD\n"
    )
    assert "RUN COMPLETED: cli-run" in capsys.readouterr().out


def test_run_command_executes_motif_workflow(
    tmp_path: Path, fasta_file, capsys
) -> None:
    source = fasta_file(">q1\nAAACATAAANDPAAAAAAAAAA\n")
    results = tmp_path / "results"
    logs = tmp_path / "logs"

    status = main(
        [
            "run",
            str(source),
            "--run-id",
            "cli-motifs",
            "--results-dir",
            str(results),
            "--logs-dir",
            str(logs),
            "--family-config",
            "tests/fixtures/domains/family.yaml",
            "--motifs",
        ]
    )

    assert status == 0
    assert (results / "cli-motifs" / "evidence" / "motif_evidence.jsonl").is_file()
    assert "RUN COMPLETED: cli-motifs" in capsys.readouterr().out


def test_domain_or_motif_options_require_family_profile(
    tmp_path: Path, fasta_file
) -> None:
    source = fasta_file(">q1\nACDEFG\n")
    with pytest.raises(SystemExit) as error:
        main(["run", str(source), "--motifs"])
    assert error.value.code == 2
