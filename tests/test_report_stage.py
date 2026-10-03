"""Workflow, CLI, cache, and invalidation tests for final reporting."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml
from jsonschema import Draft202012Validator, FormatChecker

from enzynotation.cli import build_parser, main
from enzynotation.config import load_config
from enzynotation.integration import load_integration_config
from enzynotation.reporting import load_report_config
from enzynotation.runner import PipelineRunner
from enzynotation.stages.integrate import IntegrateStage
from enzynotation.stages.report import ReportStage
from enzynotation.stages.validate import ValidationStage
from enzynotation.workflow import Workflow


def _command(tmp_path: Path, source: Path, *extra: str) -> list[str]:
    return [
        "run",
        str(source),
        "--run-id",
        "reporting",
        "--results-dir",
        str(tmp_path / "results"),
        "--logs-dir",
        str(tmp_path / "logs"),
        "--integration-config",
        "configs/integration/default.yaml",
        "--report",
        *extra,
    ]


def _status(tmp_path: Path, stage: str) -> dict[str, object]:
    path = tmp_path / f"results/reporting/stages/{stage}/status.json"
    return json.loads(path.read_text(encoding="utf-8"))


def test_report_stage_writes_complete_canonical_output_set(
    tmp_path: Path, fasta_file
) -> None:
    source = fasta_file(">q1\nACDEFGHIKL\n")
    assert main(_command(tmp_path, source)) == 0
    reports = tmp_path / "results/reporting/reports"
    expected = {
        "annotations.tsv",
        "evidence.tsv",
        "candidates.tsv",
        "conflicts.tsv",
        "rule_evaluations.tsv",
        "provider_status.tsv",
        "report.json",
        "report.html",
        "run_summary.json",
    }
    assert {path.name for path in reports.iterdir()} == expected
    report = json.loads((reports / "report.json").read_text(encoding="utf-8"))
    schema = json.loads(Path("configs/schema/report.schema.json").read_text())
    Draft202012Validator(schema, format_checker=FormatChecker()).validate(report)
    assert report["queries"][0]["annotation"]["predicted_ec"] is None
    assert report["queries"][0]["annotation"]["confidence"] == "unresolved"
    assert report["run_summary"]["unresolved_queries"] == 1
    assert _status(tmp_path, "report")["dependency_signatures"].keys() == {"integrate"}


def test_identical_report_run_reuses_cache(tmp_path: Path, fasta_file) -> None:
    source = fasta_file(">q1\nACDEFGHIKL\n")
    command = _command(tmp_path, source)
    assert main(command) == 0
    first = (tmp_path / "results/reporting/reports/report.json").read_bytes()
    assert main(command) == 0
    assert _status(tmp_path, "report")["attempt"] == 1
    manifest = json.loads(
        (tmp_path / "results/reporting/manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["stages"]["report"]["status"] == "skipped"
    assert (tmp_path / "results/reporting/reports/report.json").read_bytes() == first


def test_integration_output_change_invalidates_report(
    tmp_path: Path, fasta_file
) -> None:
    source = fasta_file(">q1\nACDEFGHIKL\n")
    command = _command(tmp_path, source)
    assert main(command) == 0
    final_path = tmp_path / "results/reporting/final_annotation.json"
    final = json.loads(final_path.read_text(encoding="utf-8"))
    final["annotations"][0]["reason_codes"].append("tampered_for_test")
    final_path.write_text(json.dumps(final), encoding="utf-8")
    assert main(command) == 0
    assert _status(tmp_path, "integrate")["attempt"] == 2
    assert _status(tmp_path, "report")["attempt"] == 2
    report = json.loads(
        (tmp_path / "results/reporting/reports/report.json").read_text(encoding="utf-8")
    )
    assert "tampered_for_test" not in report["queries"][0]["annotation"]["reason_codes"]


def test_report_config_change_invalidates_only_report(
    tmp_path: Path, fasta_file
) -> None:
    source = fasta_file(">q1\nACDEFGHIKL\n")
    config_path = tmp_path / "report.yaml"
    config = yaml.safe_load(Path("configs/report/default.yaml").read_text())
    config["report"]["title"] = "First title"
    config_path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    command = _command(tmp_path, source, "--report-config", str(config_path))
    assert main(command) == 0
    config["report"]["title"] = "Second title"
    config_path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    assert main(command) == 0
    assert _status(tmp_path, "integrate")["attempt"] == 1
    assert _status(tmp_path, "report")["attempt"] == 2
    html = (tmp_path / "results/reporting/reports/report.html").read_text()
    assert "Second title" in html


def test_template_change_invalidates_only_report(tmp_path: Path, fasta_file) -> None:
    source = fasta_file(">q1\nACDEFGHIKL\n")
    template = tmp_path / "report.html"
    template.write_text(
        Path("src/enzynotation/templates/report.html").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    runner = PipelineRunner(
        load_config(),
        results_root=tmp_path / "results",
        logs_root=tmp_path / "logs",
    )
    policy = load_integration_config(Path("configs/integration/default.yaml"))

    def workflow() -> Workflow:
        report = ReportStage(load_report_config())
        report.template = template
        return Workflow([ValidationStage(), IntegrateStage(policy), report])

    assert (
        runner.run(source, workflow=workflow(), run_id="reporting").status.value
        == "completed"
    )
    template.write_text(template.read_text() + "\n<!-- formatting changed -->\n")
    assert (
        runner.run(source, workflow=workflow(), run_id="reporting").status.value
        == "completed"
    )
    assert _status(tmp_path, "integrate")["attempt"] == 1
    assert _status(tmp_path, "report")["attempt"] == 2


def test_report_cli_options_enforce_integration_and_pairing(
    tmp_path: Path, fasta_file
) -> None:
    source = fasta_file(">q1\nACDEFGHIKL\n")
    with pytest.raises(SystemExit) as report_error:
        main(["run", str(source), "--report"])
    assert report_error.value.code == 2
    with pytest.raises(SystemExit) as config_error:
        main(["run", str(source), "--report-config", "report.yaml"])
    assert config_error.value.code == 2


def test_run_help_lists_reporting_options() -> None:
    help_text = (
        build_parser()._subparsers._group_actions[0].choices["run"].format_help()
    )
    assert "--report" in help_text
    assert "--report-config" in help_text
