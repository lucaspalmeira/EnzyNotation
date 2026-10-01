"""CLEAN protein-language-model EC-prediction evidence stage."""

from __future__ import annotations

import csv
import io
import json
from pathlib import Path
from typing import Any

from enzynotation.fasta import FastaValidationOptions, validate_fasta
from enzynotation.parsers.clean import (
    CleanFilterCriterion,
    CleanParseError,
    CleanRejectedPrediction,
    RankedCleanPrediction,
    parse_clean_csv,
    rank_and_filter_predictions,
)
from enzynotation.provenance import (
    CommandProvenance,
    SoftwareProvenance,
    canonical_json_sha256,
    sha256_bytes,
    sha256_file,
    utc_now,
)
from enzynotation.stages.base import Stage, StageContext, StageResult
from enzynotation.state import atomic_write_bytes, atomic_write_json, atomic_write_text
from enzynotation.tools.clean import CleanConfig, CleanTool

_PREDICTION_FIELDS = (
    "query_id",
    "ec",
    "ec_depth",
    "ec_complete",
    "raw_metric_value",
    "metric_value",
    "metric_name",
    "metric_semantics",
    "ranking_direction",
    "calibrated",
    "original_clean_rank",
    "normalized_rank",
    "retained_rank",
    "filter_passed",
    "filter_reasons",
    "raw_line_number",
    "raw_token",
)


class CleanStage(Stage):
    """Run configured CLEAN externally and emit candidate EC evidence only."""

    parser_version = "1"
    staging_basename = "enzynotation_clean_input"

    def __init__(self, config: CleanConfig) -> None:
        super().__init__(
            "clean",
            dependencies=("validate",),
            required=config.enabled and config.required,
            implementation_version="1",
        )
        self.clean_config = config
        self.tool = CleanTool(config)

    def input_files(self, context: StageContext) -> dict[str, Path]:
        """Declare immutable sequence, adapter, and configured fingerprint files."""

        files = {"normalized_fasta": context.paths.normalized_fasta}
        if (
            self.clean_config.enabled
            and self.clean_config.strategy == "docker_compose"
            and self.clean_config.docker_compose.compose_file.is_file()
        ):
            files["clean_compose_file"] = self.clean_config.docker_compose.compose_file
        for index, path in enumerate(self.clean_config.resource_fingerprints, start=1):
            if path.is_file():
                files[f"clean_resource_{index:04d}"] = path
        return files

    def configuration(self, context: StageContext) -> dict[str, Any]:
        """Use every scientifically relevant CLEAN setting in the cache key."""

        return {
            **self.clean_config.to_dict(),
            "parser_implementation_version": self.parser_version,
        }

    @staticmethod
    def _archive_existing(path: Path, history: Path) -> None:
        if not path.is_file():
            return
        digest = sha256_file(path)
        archive = history / "artifacts" / f"{path.name}.{digest}"
        if not archive.exists():
            atomic_write_bytes(archive, path.read_bytes())

    @staticmethod
    def _write_jsonl(path: Path, values: list[dict[str, Any]]) -> None:
        atomic_write_text(
            path,
            "".join(
                json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n"
                for value in values
            ),
        )

    @staticmethod
    def _candidate_ec(ec: str, depth: int, complete: bool) -> dict[str, Any]:
        return {
            "namespace": "EC",
            "ec": ec,
            "depth": depth,
            "completeness": "complete" if complete else "partial",
        }

    @staticmethod
    def _criterion(value: CleanFilterCriterion) -> dict[str, Any]:
        return {
            "criterion_id": value.criterion_id,
            "metric": value.metric,
            "operator": value.operator,
            "threshold": value.threshold,
            "observed_value": value.observed_value,
            "passed": value.passed,
        }

    def _directories(self, stage_raw: Path) -> tuple[Path, Path]:
        mounts = self.clean_config.mounts
        input_directory = mounts.input_directory or stage_raw / "clean-input"
        output_directory = mounts.output_directory or stage_raw / "clean-output"
        return input_directory, output_directory

    def _prediction_tsv(self, values: tuple[RankedCleanPrediction, ...]) -> str:
        output = io.StringIO()
        writer = csv.DictWriter(output, fieldnames=_PREDICTION_FIELDS, delimiter="\t")
        writer.writeheader()
        metric = self.clean_config.metric
        for ranked in values:
            prediction = ranked.prediction
            writer.writerow(
                {
                    "query_id": prediction.query_id,
                    "ec": prediction.ec,
                    "ec_depth": prediction.ec_depth,
                    "ec_complete": str(prediction.ec_complete).lower(),
                    "raw_metric_value": prediction.raw_metric_value,
                    "metric_value": prediction.metric_value,
                    "metric_name": metric.name,
                    "metric_semantics": metric.semantics,
                    "ranking_direction": metric.ranking_direction,
                    "calibrated": str(metric.calibrated).lower(),
                    "original_clean_rank": prediction.original_rank,
                    "normalized_rank": ranked.normalized_rank or "",
                    "retained_rank": ranked.retained_rank or "",
                    "filter_passed": str(ranked.passed).lower(),
                    "filter_reasons": ";".join(ranked.filter_reasons),
                    "raw_line_number": prediction.raw_line_number,
                    "raw_token": prediction.raw_token,
                }
            )
        return output.getvalue()

    @staticmethod
    def _rejection_record(value: CleanRejectedPrediction) -> dict[str, Any]:
        return {
            "query_id": value.query_id,
            "original_clean_rank": value.original_rank,
            "raw_line_number": value.raw_line_number,
            "raw_token": value.raw_token,
            "raw_row": value.raw_row,
            "reason_code": value.reason_code,
            "message": value.message,
        }

    def _model_database(self) -> dict[str, Any]:
        model = self.clean_config.model
        record: dict[str, Any] = {
            "name": model.model_identifier,
            "version": model.version,
        }
        mounts = self.clean_config.mounts
        if mounts.external_model_data is not None:
            record["path"] = str(mounts.external_model_data)
        fingerprint_values = {
            str(path): sha256_file(path)
            for path in self.clean_config.resource_fingerprints
            if path.is_file()
        }
        if fingerprint_values:
            record["checksum"] = f"sha256:{canonical_json_sha256(fingerprint_values)}"
        return record

    def _tool_provenance(self, software: SoftwareProvenance) -> dict[str, Any]:
        record: dict[str, Any] = {
            "name": software.name,
            "version": software.version,
            "executable": software.executable,
        }
        if self.clean_config.strategy == "docker_compose":
            docker = self.clean_config.docker_compose
            record["container_image"] = docker.image
            if docker.image_digest is not None:
                record["container_digest"] = docker.image_digest
        return record

    def _evidence_record(
        self,
        *,
        context: StageContext,
        ranked: RankedCleanPrediction,
        sequence: str,
        raw_output: Path,
        software: SoftwareProvenance,
        command: CommandProvenance,
    ) -> dict[str, Any]:
        prediction = ranked.prediction
        config = self.clean_config
        metric = config.metric
        model = config.model
        return {
            "schema_version": 1,
            "evidence_id": (
                f"clean:{prediction.query_id}:rank:{prediction.original_rank}:"
                f"{prediction.ec}"
            ),
            "query": {
                "query_id": prediction.query_id,
                "sequence_sha256": sha256_bytes(sequence.encode("ascii")),
                "sequence_length": len(sequence),
            },
            "source": {
                "id": "clean",
                "evidence_class": "learned_sequence_model",
                "correlation_group": (
                    f"clean:{context.run_id}:{model.model_identifier}:"
                    f"{model.version}:{prediction.query_id}"
                ),
            },
            "record_status": "observed",
            "assertion": {
                "target": {
                    "type": "ec",
                    "candidate_ec": self._candidate_ec(
                        prediction.ec,
                        prediction.ec_depth,
                        prediction.ec_complete,
                    ),
                },
                "effect": "supports",
            },
            "metrics": {
                "raw_serialized_value": prediction.raw_metric_value,
                "numeric_value": prediction.metric_value,
                "metric_name": metric.name,
                "metric_semantics": metric.semantics,
                "ranking_direction": metric.ranking_direction,
                "calibrated": metric.calibrated,
                "output_variant": config.implementation.output_variant,
                "inference_method": config.implementation.inference_method,
                "original_clean_rank": prediction.original_rank,
                "normalized_rank": ranked.normalized_rank,
                "retained_rank": ranked.retained_rank,
                "model_identifier": model.model_identifier,
                "model_version": model.version,
                "model_version_available": model.version.lower()
                not in {"unknown", "unavailable"},
                "training_split": model.training_split,
                "pretrained": model.pretrained,
                "threshold_status": config.filtering.threshold_status,
            },
            "criteria": [self._criterion(value) for value in ranked.criteria],
            "reference": {
                "accession": model.model_identifier,
                "description": (
                    f"CLEAN {config.implementation.inference_method} candidate"
                ),
                "curated": False,
                "annotation_source": "CLEAN",
            },
            "provenance": {
                "run_id": context.run_id,
                "stage_id": self.stage_id,
                "generated_at": utc_now(),
                "parser": {
                    "name": "enzynotation-clean-parser",
                    "version": self.parser_version,
                },
                "tool": self._tool_provenance(software),
                "databases": [self._model_database()],
                "raw_artifact": {
                    "path": context.paths.relative_artifact(raw_output),
                    "format": "clean-maxsep-csv",
                    "sha256": sha256_file(raw_output),
                    "record_locator": (
                        f"line:{prediction.raw_line_number};"
                        f"candidate:{prediction.original_rank}"
                    ),
                },
                "configuration_sha256": context.configuration_checksum,
                "input_sha256": sha256_file(context.paths.normalized_fasta),
                "command": list(command.argv),
            },
            "message": "CLEAN candidate evidence; not a final EC prediction",
        }

    @staticmethod
    def _failure_code(
        *, config: CleanConfig, command: CommandProvenance, stderr: str
    ) -> str:
        lowered = stderr.lower()
        if command.return_code == 127:
            return (
                "docker_runtime_unavailable"
                if config.strategy == "docker_compose"
                else "external_command_runtime_unavailable"
            )
        if "no such image" in lowered or "manifest unknown" in lowered:
            return "docker_image_unavailable"
        if "pretrained weights" in lowered or "model file" in lowered:
            return "model_files_unavailable"
        return "clean_command_failed"

    def _write_failure(
        self,
        *,
        summary_path: Path,
        evidence_path: Path,
        config_path: Path,
        reason_code: str,
        message: str,
        commands: tuple[CommandProvenance, ...] = (),
        software: tuple[SoftwareProvenance, ...] = (),
        raw_path: Path | None = None,
    ) -> StageResult:
        summary = {
            "schema_version": 1,
            "provider": "clean",
            "status": "failed",
            "reason_code": reason_code,
            "message": message,
            "final_ec_prediction": None,
        }
        atomic_write_json(summary_path, summary)
        atomic_write_text(evidence_path, "")
        outputs: dict[str, Path] = {
            "configuration": config_path,
            "summary": summary_path,
            "evidence": evidence_path,
        }
        if raw_path is not None and raw_path.is_file():
            outputs["raw_predictions"] = raw_path
        return StageResult(
            False,
            outputs,
            commands=commands,
            software=software,
            message=f"{reason_code}: {message}",
        )

    def execute(self, context: StageContext) -> StageResult:
        """Stage FASTA, invoke CLEAN externally, and normalize candidate evidence."""

        stage_paths = context.paths.for_stage(self.stage_id)
        stage_paths.prepare()
        raw_output = stage_paths.raw / "clean_result.csv"
        predictions_output = stage_paths.normalized / "clean_predictions.tsv"
        rejections_output = stage_paths.normalized / "clean_rejections.jsonl"
        summary_output = stage_paths.normalized / "clean_summary.json"
        config_output = stage_paths.normalized / "clean_config.json"
        evidence_output = context.paths.evidence / "clean_evidence.jsonl"
        for path in (
            raw_output,
            predictions_output,
            rejections_output,
            summary_output,
            config_output,
            evidence_output,
        ):
            self._archive_existing(path, stage_paths.history)
            path.unlink(missing_ok=True)
        atomic_write_json(config_output, self.configuration(context))

        if not self.clean_config.enabled:
            atomic_write_text(evidence_output, "")
            atomic_write_text(predictions_output, "\t".join(_PREDICTION_FIELDS) + "\n")
            atomic_write_text(rejections_output, "")
            atomic_write_json(
                summary_output,
                {
                    "schema_version": 1,
                    "provider": "clean",
                    "status": "disabled",
                    "counts": {"predictions": 0, "evidence_records": 0},
                    "final_ec_prediction": None,
                },
            )
            return StageResult(
                True,
                {
                    "configuration": config_output,
                    "predictions": predictions_output,
                    "rejections": rejections_output,
                    "summary": summary_output,
                    "evidence": evidence_output,
                },
                message="CLEAN provider disabled",
            )

        compose = self.clean_config.docker_compose.compose_file
        if self.clean_config.strategy == "docker_compose" and not compose.is_file():
            return self._write_failure(
                summary_path=summary_output,
                evidence_path=evidence_output,
                config_path=config_output,
                reason_code="docker_compose_file_unavailable",
                message=f"Docker Compose file does not exist: {compose}",
            )
        for label, path in (
            ("torch_cache", self.clean_config.mounts.torch_cache),
            ("external_model_data", self.clean_config.mounts.external_model_data),
        ):
            if path is not None and not path.is_dir():
                return self._write_failure(
                    summary_path=summary_output,
                    evidence_path=evidence_output,
                    config_path=config_output,
                    reason_code="required_mount_unavailable",
                    message=f"configured {label} directory does not exist: {path}",
                )
        for path in self.clean_config.resource_fingerprints:
            if not path.is_file():
                return self._write_failure(
                    summary_path=summary_output,
                    evidence_path=evidence_output,
                    config_path=config_output,
                    reason_code="resource_fingerprint_unavailable",
                    message=f"configured resource fingerprint does not exist: {path}",
                )

        input_directory, output_directory = self._directories(stage_paths.raw)
        input_directory.mkdir(parents=True, exist_ok=True)
        output_directory.mkdir(parents=True, exist_ok=True)
        staged_input = input_directory / f"{self.staging_basename}.fasta"
        atomic_write_bytes(staged_input, context.paths.normalized_fasta.read_bytes())
        expected_output = self.tool.expected_output(
            output_directory, self.staging_basename
        )
        expected_output.unlink(missing_ok=True)

        runtime_software = self.tool.capture_runtime_versions(context.backend)
        if self.clean_config.strategy == "docker_compose":
            docker, compose_software, image_software = runtime_software
            if docker.version_return_code not in {None, 0}:
                return self._write_failure(
                    summary_path=summary_output,
                    evidence_path=evidence_output,
                    config_path=config_output,
                    reason_code="docker_runtime_unavailable",
                    message="Docker runtime version could not be captured",
                    software=runtime_software,
                )
            if compose_software.version_return_code not in {None, 0}:
                return self._write_failure(
                    summary_path=summary_output,
                    evidence_path=evidence_output,
                    config_path=config_output,
                    reason_code="docker_compose_unavailable",
                    message="Docker Compose version could not be captured",
                    software=runtime_software,
                )
            if image_software.version_return_code not in {None, 0}:
                return self._write_failure(
                    summary_path=summary_output,
                    evidence_path=evidence_output,
                    config_path=config_output,
                    reason_code="docker_image_unavailable",
                    message="configured CLEAN Docker image metadata is unavailable",
                    software=runtime_software,
                )
        elif runtime_software[0].version_return_code == 127:
            return self._write_failure(
                summary_path=summary_output,
                evidence_path=evidence_output,
                config_path=config_output,
                reason_code="external_command_runtime_unavailable",
                message="external CLEAN command runtime is unavailable",
                software=runtime_software,
            )

        command = context.backend.execute(
            self.tool.build_command(
                input_directory=input_directory,
                output_directory=output_directory,
                basename=self.staging_basename,
            ),
            stdout_path=stage_paths.stdout,
            stderr_path=stage_paths.stderr,
        )
        clean_software = self.tool.clean_software()
        software = (*runtime_software, clean_software)
        if command.return_code != 0:
            stderr = (
                Path(command.stderr_path).read_text(encoding="utf-8")
                if Path(command.stderr_path).is_file()
                else ""
            )
            reason = self._failure_code(
                config=self.clean_config, command=command, stderr=stderr
            )
            return self._write_failure(
                summary_path=summary_output,
                evidence_path=evidence_output,
                config_path=config_output,
                reason_code=reason,
                message=f"CLEAN runtime exited with code {command.return_code}",
                commands=(command,),
                software=software,
            )
        if not expected_output.is_file():
            return self._write_failure(
                summary_path=summary_output,
                evidence_path=evidence_output,
                config_path=config_output,
                reason_code="clean_output_missing",
                message=f"expected CLEAN output was not created: {expected_output}",
                commands=(command,),
                software=software,
            )
        atomic_write_bytes(raw_output, expected_output.read_bytes())

        try:
            parsed = parse_clean_csv(raw_output)
        except CleanParseError as exc:
            return self._write_failure(
                summary_path=summary_output,
                evidence_path=evidence_output,
                config_path=config_output,
                reason_code="malformed_clean_output",
                message=str(exc),
                commands=(command,),
                software=software,
                raw_path=raw_output,
            )

        options = FastaValidationOptions.from_mapping(context.config.section("input"))
        validated = validate_fasta(context.paths.normalized_fasta, options)
        sequences = {record.identifier: record.sequence for record in validated.records}
        valid_predictions = []
        rejections = list(parsed.rejections)
        for prediction in parsed.predictions:
            if prediction.query_id in sequences:
                valid_predictions.append(prediction)
            else:
                rejections.append(
                    CleanRejectedPrediction(
                        query_id=prediction.query_id,
                        original_rank=prediction.original_rank,
                        raw_line_number=prediction.raw_line_number,
                        raw_token=prediction.raw_token,
                        raw_row=prediction.raw_token,
                        reason_code="unknown_query_id",
                        message="CLEAN prediction query is not in normalized FASTA",
                    )
                )
        filtering = self.clean_config.filtering
        ranked = rank_and_filter_predictions(
            tuple(valid_predictions),
            ranking_direction=self.clean_config.metric.ranking_direction,
            maximum_retained_candidates=filtering.maximum_retained_candidates,
            threshold=filtering.threshold,
            threshold_comparison=filtering.threshold_comparison,
            allowed_ec_depths=filtering.allowed_ec_depths,
            allow_partial_ec=filtering.allow_partial_ec,
        )
        atomic_write_text(predictions_output, self._prediction_tsv(ranked))
        self._write_jsonl(
            rejections_output,
            [self._rejection_record(value) for value in rejections],
        )
        retained = tuple(value for value in ranked if value.passed)
        evidence = [
            self._evidence_record(
                context=context,
                ranked=value,
                sequence=sequences[value.prediction.query_id],
                raw_output=raw_output,
                software=clean_software,
                command=command,
            )
            for value in retained
        ]
        self._write_jsonl(evidence_output, evidence)

        predicted_queries = {value.prediction.query_id for value in ranked}
        summary = {
            "schema_version": 1,
            "provider": "clean",
            "status": "completed",
            "execution_strategy": self.clean_config.strategy,
            "interface": self.clean_config.implementation.interface,
            "inference_method": self.clean_config.implementation.inference_method,
            "output_variant": self.clean_config.implementation.output_variant,
            "metric": {
                "name": self.clean_config.metric.name,
                "semantics": self.clean_config.metric.semantics,
                "ranking_direction": self.clean_config.metric.ranking_direction,
                "calibrated": self.clean_config.metric.calibrated,
            },
            "model": {
                "identifier": self.clean_config.model.model_identifier,
                "version": self.clean_config.model.version,
                "version_available": self.clean_config.model.version.lower()
                not in {"unknown", "unavailable"},
                "training_split": self.clean_config.model.training_split,
            },
            "counts": {
                "input_queries": len(sequences),
                "queries_reported_by_clean": len(set(parsed.query_ids)),
                "queries_with_valid_predictions": len(predicted_queries),
                "queries_without_predictions": len(set(sequences) - predicted_queries),
                "valid_predictions": len(ranked),
                "rejected_predictions": len(rejections),
                "retained_candidates": len(retained),
                "filtered_candidates": len(ranked) - len(retained),
                "evidence_records": len(evidence),
            },
            "empty_result": not parsed.query_ids and not parsed.predictions,
            "zero_retained_candidates": bool(ranked) and not retained,
            "final_ec_prediction": None,
        }
        atomic_write_json(summary_output, summary)
        return StageResult(
            True,
            {
                "configuration": config_output,
                "raw_predictions": raw_output,
                "predictions": predictions_output,
                "rejections": rejections_output,
                "summary": summary_output,
                "evidence": evidence_output,
            },
            commands=(command,),
            software=software,
            message="CLEAN evidence completed; no final EC prediction was made",
        )
