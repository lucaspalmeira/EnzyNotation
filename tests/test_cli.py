"""Tests for the Milestone 1 command-line workflow."""

import json
from pathlib import Path

import pytest
import yaml

from enzynotation.cli import build_parser, main


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


def test_run_command_accepts_disabled_clean_provider_without_execution(
    tmp_path: Path, fasta_file, capsys
) -> None:
    source = fasta_file(">q1\nACDEFGHIK\n")
    clean_document = yaml.safe_load(Path("configs/tools/clean.yaml").read_text())
    clean_document["clean"]["enabled"] = False
    clean_config = tmp_path / "clean.yaml"
    clean_config.write_text(
        yaml.safe_dump(clean_document, sort_keys=False), encoding="utf-8"
    )

    status = main(
        [
            "run",
            str(source),
            "--run-id",
            "cli-clean-disabled",
            "--results-dir",
            str(tmp_path / "results"),
            "--logs-dir",
            str(tmp_path / "logs"),
            "--clean-config",
            str(clean_config),
        ]
    )

    assert status == 0
    summary = json.loads(
        (
            tmp_path
            / "results/cli-clean-disabled/stages/clean/normalized/clean_summary.json"
        ).read_text()
    )
    assert summary["status"] == "disabled"
    assert "RUN COMPLETED" in capsys.readouterr().out


def test_run_command_executes_supplied_structure_mapping(
    tmp_path: Path, fasta_file, capsys
) -> None:
    source = fasta_file(">q1\nMAG\n")
    structure = Path("tests/fixtures/structures/query.pdb").resolve()
    manifest = tmp_path / "manifest.tsv"
    manifest.write_text(
        "query_id\tstructure_id\tstructure_path\tstructure_format\tchain_id\t"
        "model_index\tstructure_source\n"
        f"q1\tq1-structure\t{structure}\tpdb\tA\t1\tuser_supplied\n"
    )
    config = tmp_path / "structures.yaml"
    config.write_text(
        yaml.safe_dump(
            {
                "schema_version": 1,
                "structures": {"required": True, "manifest": str(manifest)},
            },
            sort_keys=False,
        )
    )

    status = main(
        [
            "run",
            str(source),
            "--run-id",
            "cli-structures",
            "--results-dir",
            str(tmp_path / "results"),
            "--logs-dir",
            str(tmp_path / "logs"),
            "--structures-config",
            str(config),
        ]
    )

    assert status == 0
    normalized = (
        tmp_path
        / "results/cli-structures/stages/structures/normalized/structure_manifest.tsv"
    )
    assert normalized.is_file()
    assert "\texact\t" in normalized.read_text()
    assert "RUN COMPLETED" in capsys.readouterr().out


def test_structural_cli_dependencies_are_enforced(tmp_path: Path, fasta_file) -> None:
    source = fasta_file(">q1\nMAG\n")
    with pytest.raises(SystemExit) as foldseek_error:
        main(["run", str(source), "--foldseek-config", "foldseek.yaml"])
    assert foldseek_error.value.code == 2

    with pytest.raises(SystemExit) as tmalign_error:
        main(["run", str(source), "--tmalign-config", "tmalign.yaml"])
    assert tmalign_error.value.code == 2


def test_run_help_lists_structural_provider_options() -> None:
    help_text = (
        build_parser()._subparsers._group_actions[0].choices["run"].format_help()
    )

    assert "--structures-config" in help_text
    assert "--foldseek-config" in help_text
    assert "--tmalign-config" in help_text
