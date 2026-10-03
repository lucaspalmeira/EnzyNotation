"""Workflow stage for deterministic final report generation."""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

from enzynotation import __version__
from enzynotation.provenance import SoftwareProvenance, sha256_file
from enzynotation.reporting import (
    ANNOTATION_COLUMNS,
    CANDIDATE_COLUMNS,
    CONFLICT_COLUMNS,
    EVIDENCE_COLUMNS,
    PROVIDER_COLUMNS,
    RULE_COLUMNS,
    ReportConfig,
    ReportInputs,
    build_report,
)
from enzynotation.serialization import load_json, load_jsonl, write_json, write_tsv
from enzynotation.stages.base import Stage, StageContext, StageResult
from enzynotation.state import atomic_write_text


class ReportStage(Stage):
    """Present integration decisions without recalculating them."""

    def __init__(self, config: ReportConfig) -> None:
        super().__init__(
            "report",
            dependencies=("integrate",),
            required=True,
            implementation_version="1",
        )
        self.report_config = config
        package_root = Path(__file__).resolve().parents[1]
        self.template = package_root / "templates" / "report.html"
        self.report_schema = Path("configs/schema/report.schema.json").resolve()
        self.report_config_schema = Path(
            "configs/schema/report-config.schema.json"
        ).resolve()

    @staticmethod
    def _integration_paths(context: StageContext) -> dict[str, Path]:
        normalized = context.paths.for_stage("integrate").normalized
        return {
            "final_annotation": context.paths.run_root / "final_annotation.json",
            "candidate_scorecards": normalized / "candidate_scorecards.jsonl",
            "conflicts": normalized / "conflicts.jsonl",
            "rule_evaluations": normalized / "rule_evaluations.jsonl",
            "integration_summary": normalized / "integration_summary.json",
            "integration_status": context.paths.for_stage("integrate").status,
        }

    def _evidence_paths(self, context: StageContext) -> tuple[Path, ...]:
        return tuple(sorted(context.paths.evidence.glob("*_evidence.jsonl")))

    def input_files(self, context: StageContext) -> dict[str, Path]:
        """Hash integration products, canonical evidence, schema, and template."""

        files = self._integration_paths(context)
        files.update(
            {
                "report_schema": self.report_schema,
                "report_config_schema": self.report_config_schema,
                "template": self.template,
            }
        )
        if self.report_config.source_path is not None:
            files["report_config"] = self.report_config.source_path
        for index, path in enumerate(self._evidence_paths(context), start=1):
            files[f"evidence_{index:03d}"] = path
        return files

    def configuration(self, context: StageContext) -> dict[str, Any]:
        """Include only stable manifest provenance and presentation settings."""

        manifest = load_json(context.paths.manifest)
        return {
            "report": self.report_config.document,
            "run": {
                "run_id": manifest["run_id"],
                "software": manifest["software"],
                "input": manifest["input"],
                "configuration": manifest["configuration"],
            },
        }

    @staticmethod
    def _archive(path: Path, history: Path) -> None:
        if not path.is_file():
            return
        destination = history / f"{path.name}.{sha256_file(path)}"
        if not destination.exists():
            shutil.copy2(path, destination)

    def execute(self, context: StageContext) -> StageResult:
        """Generate TSV, JSON, and static HTML from completed integration outputs."""

        stage_paths = context.paths.for_stage(self.stage_id)
        stage_paths.prepare()
        context.paths.reports.mkdir(parents=True, exist_ok=True)
        outputs = {
            "annotations": context.paths.reports / "annotations.tsv",
            "evidence": context.paths.reports / "evidence.tsv",
            "candidates": context.paths.reports / "candidates.tsv",
            "conflicts": context.paths.reports / "conflicts.tsv",
            "rule_evaluations": context.paths.reports / "rule_evaluations.tsv",
            "provider_status": context.paths.reports / "provider_status.tsv",
            "report_json": context.paths.reports / "report.json",
            "report_html": context.paths.reports / "report.html",
            "run_summary": context.paths.reports / "run_summary.json",
            "report_configuration": stage_paths.normalized / "report_config.json",
        }
        for path in outputs.values():
            self._archive(path, stage_paths.history)
            path.unlink(missing_ok=True)

        integration = self._integration_paths(context)
        evidence_paths = self._evidence_paths(context)
        final_annotation = load_json(integration["final_annotation"])
        manifest = load_json(context.paths.manifest)
        evidence = tuple(load_jsonl(evidence_paths))
        inputs = ReportInputs(
            run_id=context.run_id,
            final_annotation=final_annotation,
            candidates=tuple(load_jsonl((integration["candidate_scorecards"],))),
            conflicts=tuple(load_jsonl((integration["conflicts"],))),
            rule_evaluations=tuple(load_jsonl((integration["rule_evaluations"],))),
            evidence=evidence,
            evidence_files=evidence_paths,
            manifest=manifest,
            integration_status_path=context.paths.relative_artifact(
                integration["integration_status"]
            ),
        )
        bundle = build_report(
            inputs,
            self.report_config,
            template_text=self.template.read_text(encoding="utf-8"),
            run_relative_evidence_paths=[
                context.paths.relative_artifact(path) for path in evidence_paths
            ],
        )

        schema = json.loads(self.report_schema.read_text(encoding="utf-8"))
        validator = Draft202012Validator(schema, format_checker=FormatChecker())
        errors = sorted(
            validator.iter_errors(bundle.report), key=lambda error: list(error.path)
        )
        if errors:
            location = ".".join(str(value) for value in errors[0].absolute_path)
            raise ValueError(
                f"Generated report failed schema validation at {location}: "
                f"{errors[0].message}"
            )

        write_tsv(outputs["annotations"], ANNOTATION_COLUMNS, bundle.annotations)
        write_tsv(outputs["evidence"], EVIDENCE_COLUMNS, bundle.evidence)
        write_tsv(outputs["candidates"], CANDIDATE_COLUMNS, bundle.candidates)
        write_tsv(outputs["conflicts"], CONFLICT_COLUMNS, bundle.conflicts)
        write_tsv(outputs["rule_evaluations"], RULE_COLUMNS, bundle.rule_evaluations)
        write_tsv(outputs["provider_status"], PROVIDER_COLUMNS, bundle.provider_status)
        write_json(outputs["report_json"], bundle.report)
        write_json(outputs["run_summary"], bundle.run_summary)
        write_json(outputs["report_configuration"], self.report_config.document)
        atomic_write_text(outputs["report_html"], bundle.html)
        return StageResult(
            True,
            outputs,
            software=(SoftwareProvenance("enzynotation", __version__, sys.executable),),
            message=f"reported {len(bundle.annotations)} query sequence(s)",
        )
