"""HMMER and optional InterProScan domain-evidence stage."""

from __future__ import annotations

import csv
import io
import json
import re
from pathlib import Path
from typing import Any

from enzynotation.domains import (
    DomainCriterion,
    DomainObservation,
    DomainRuleEvaluation,
    evaluate_domain_rules,
    hmmer_observations,
    interpro_observations,
    matching_domain_rules,
)
from enzynotation.family import DomainRule, FamilyProfile
from enzynotation.fasta import FastaValidationOptions, validate_fasta
from enzynotation.parsers.hmmer import HmmerParseError, parse_hmmer_domtblout
from enzynotation.parsers.interpro import InterProParseError, parse_interpro_tsv
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
from enzynotation.tools.base import ToolConfigurationError
from enzynotation.tools.hmmer import HmmerConfig, HmmerTool
from enzynotation.tools.interpro import InterProConfig, InterProTool

_DOMAIN_FIELDS = (
    "provider",
    "query_id",
    "signature_accession",
    "signature_name",
    "member_database",
    "interpro_accession",
    "description",
    "start",
    "end",
    "query_coverage",
    "sequence_evalue",
    "domain_i_evalue",
    "bit_score",
    "occurrence",
    "filter_passed",
    "filter_reasons",
    "matched_rule_ids",
    "go_terms",
    "pathways",
    "raw_line_number",
)


class DomainsStage(Stage):
    """Collect normalized domain evidence from configured external providers."""

    def __init__(
        self,
        profile: FamilyProfile,
        *,
        hmmer: HmmerConfig | None = None,
        interpro: InterProConfig | None = None,
    ) -> None:
        providers = tuple(config for config in (hmmer, interpro) if config is not None)
        required = any(config.enabled and config.required for config in providers)
        super().__init__(
            "domains",
            dependencies=("validate",),
            required=required,
            implementation_version="1",
        )
        self.profile = profile
        self.hmmer_config = hmmer
        self.interpro_config = interpro
        self.hmmer_tool = HmmerTool(hmmer) if hmmer is not None else None
        self.interpro_tool = InterProTool(interpro) if interpro is not None else None

    def input_files(self, context: StageContext) -> dict[str, Path]:
        """Declare sequence, family profile, and available database artifacts."""

        files = {
            "normalized_fasta": context.paths.normalized_fasta,
            "family_profile": self.profile.source_path,
        }
        for tool, enabled in (
            (self.hmmer_tool, self.hmmer_config and self.hmmer_config.enabled),
            (self.interpro_tool, self.interpro_config and self.interpro_config.enabled),
        ):
            if tool is None or not enabled:
                continue
            try:
                files.update(tool.database_artifacts())
            except ToolConfigurationError:
                # Provider-level failure semantics are recorded by execute().
                continue
        return files

    def configuration(self, context: StageContext) -> dict[str, Any]:
        """Return family and provider configuration used by this stage."""

        return {
            "family": self.profile.to_dict(),
            "hmmer": self.hmmer_config.to_dict() if self.hmmer_config else None,
            "interproscan": (
                self.interpro_config.to_dict() if self.interpro_config else None
            ),
        }

    @staticmethod
    def _archive_existing(path: Path, history: Path) -> None:
        if not path.is_file():
            return
        digest = sha256_file(path)
        archive = history / "artifacts" / f"{path.name}.{digest}"
        if not archive.exists():
            atomic_write_bytes(archive, path.read_bytes())

    def _domain_tsv(self, observations: tuple[DomainObservation, ...]) -> str:
        output = io.StringIO()
        writer = csv.DictWriter(output, fieldnames=_DOMAIN_FIELDS, delimiter="\t")
        writer.writeheader()
        for item in observations:
            rules = matching_domain_rules(item, self.profile)
            writer.writerow(
                {
                    "provider": item.provider,
                    "query_id": item.query_id,
                    "signature_accession": item.signature_accession,
                    "signature_name": item.signature_name,
                    "member_database": item.member_database or "",
                    "interpro_accession": item.interpro_accession or "",
                    "description": item.description,
                    "start": item.start,
                    "end": item.end,
                    "query_coverage": f"{item.query_coverage:.8f}",
                    "sequence_evalue": (
                        "" if item.sequence_evalue is None else item.sequence_evalue
                    ),
                    "domain_i_evalue": (
                        "" if item.domain_i_evalue is None else item.domain_i_evalue
                    ),
                    "bit_score": "" if item.bit_score is None else item.bit_score,
                    "occurrence": item.occurrence,
                    "filter_passed": str(item.passed).lower(),
                    "filter_reasons": ";".join(item.filter_reasons),
                    "matched_rule_ids": ";".join(rule.rule_id for rule in rules),
                    "go_terms": ";".join(item.go_terms),
                    "pathways": ";".join(item.pathways),
                    "raw_line_number": item.raw_line_number,
                }
            )
        return output.getvalue()

    @staticmethod
    def _criterion(item: DomainCriterion) -> dict[str, Any]:
        return {
            "criterion_id": item.criterion_id,
            "metric": item.metric,
            "operator": item.operator,
            "threshold": item.threshold,
            "observed_value": item.observed_value,
            "passed": item.passed,
        }

    @staticmethod
    def _safe_id(value: str) -> str:
        rendered = re.sub(r"[^A-Za-z0-9._-]", "_", value)
        return rendered if rendered and rendered[0].isalnum() else f"domain_{rendered}"

    @staticmethod
    def _signature_key(value: str) -> str:
        return re.sub(r"\.\d+$", "", value).upper()

    def _correlation_group(self, item: DomainObservation) -> str:
        database = (item.member_database or item.database_name).lower()
        return (
            f"domain:{database}:{self._signature_key(item.signature_accession)}:"
            f"{item.query_id}:{item.start}-{item.end}"
        )

    @staticmethod
    def _provider_for_rule(rule: DomainRule) -> str:
        return "interproscan" if rule.source == "interproscan" else "hmmer"

    @staticmethod
    def _raw_format(provider: str) -> str:
        return "hmmer-domtblout" if provider == "hmmer" else "interproscan-tsv"

    def _database_record(
        self,
        item: DomainObservation,
        database_checksums: dict[str, str],
    ) -> dict[str, Any]:
        record: dict[str, Any] = {
            "name": item.database_name,
            "version": item.database_version,
        }
        if item.provider == "hmmer" and self.hmmer_config is not None:
            record["path"] = str(self.hmmer_config.database)
            if database_checksums:
                record["checksum"] = (
                    f"sha256:{canonical_json_sha256(database_checksums)}"
                )
        elif (
            item.provider == "interproscan"
            and self.interpro_config is not None
            and self.interpro_config.data_directory is not None
        ):
            record["path"] = str(self.interpro_config.data_directory)
            if database_checksums:
                record["checksum"] = (
                    f"sha256:{canonical_json_sha256(database_checksums)}"
                )
        return record

    def _provenance(
        self,
        *,
        context: StageContext,
        item: DomainObservation,
        raw_path: Path,
        software: SoftwareProvenance,
        command: CommandProvenance,
        database_checksums: dict[str, str],
    ) -> dict[str, Any]:
        return {
            "run_id": context.run_id,
            "stage_id": self.stage_id,
            "generated_at": utc_now(),
            "parser": {
                "name": f"enzynotation-{item.provider}-parser",
                "version": "1",
            },
            "tool": {
                "name": software.name,
                "version": software.version,
                "executable": software.executable,
            },
            "databases": [self._database_record(item, database_checksums)],
            "raw_artifact": {
                "path": context.paths.relative_artifact(raw_path),
                "format": self._raw_format(item.provider),
                "sha256": sha256_file(raw_path),
                "record_locator": f"line:{item.raw_line_number}",
            },
            "configuration_sha256": context.configuration_checksum,
            "input_sha256": sha256_file(context.paths.normalized_fasta),
            "command": list(command.argv),
        }

    def _hit_evidence(
        self,
        *,
        context: StageContext,
        item: DomainObservation,
        sequence: str,
        raw_path: Path,
        software: SoftwareProvenance,
        command: CommandProvenance,
        database_checksums: dict[str, str],
    ) -> list[dict[str, Any]]:
        if not item.passed:
            return []
        matched = matching_domain_rules(item, self.profile)
        rules: tuple[DomainRule | None, ...] = matched or (None,)
        records: list[dict[str, Any]] = []
        for rule in rules:
            rule_token = rule.rule_id if rule else "unconfigured"
            effect = "neutral" if rule is None else "supports"
            if rule is not None and rule.requirement == "forbidden":
                effect = "contradicts"
            metrics: dict[str, Any] = {
                "signature_accession": item.signature_accession,
                "signature_name": item.signature_name,
                "domain_start": item.start,
                "domain_end": item.end,
                "query_coverage": item.query_coverage,
                "occurrence": item.occurrence,
                "database_name": item.database_name,
                "database_version": item.database_version,
                "member_database": item.member_database,
                "description": item.description,
            }
            optional_metrics = {
                "sequence_evalue": item.sequence_evalue,
                "domain_i_evalue": item.domain_i_evalue,
                "bit_score": item.bit_score,
                "interpro_accession": item.interpro_accession,
                "interpro_description": item.interpro_description,
                "go_terms": ";".join(item.go_terms) or None,
                "pathways": ";".join(item.pathways) or None,
                "rule_id": rule.rule_id if rule else None,
                "requirement": rule.requirement if rule else None,
                "candidate_ecs": (
                    ";".join(rule.candidate_ecs)
                    if rule and rule.candidate_ecs
                    else None
                ),
            }
            metrics.update(optional_metrics)
            records.append(
                {
                    "schema_version": 1,
                    "evidence_id": (
                        f"domain:{item.provider}:{item.query_id}:"
                        f"{self._safe_id(item.signature_accession)}:"
                        f"{item.start}-{item.end}:{rule_token}"
                    ),
                    "query": {
                        "query_id": item.query_id,
                        "sequence_sha256": sha256_bytes(sequence.encode("ascii")),
                        "sequence_length": len(sequence),
                    },
                    "source": {
                        "id": item.provider,
                        "evidence_class": "domain_architecture",
                        "correlation_group": self._correlation_group(item),
                    },
                    "record_status": "observed",
                    "assertion": {
                        "target": (
                            {"type": "family", "id": self.profile.family_id}
                            if rule
                            else {"type": "feature", "id": "protein_domain"}
                        ),
                        "effect": effect,
                    },
                    "metrics": metrics,
                    "criteria": [self._criterion(value) for value in item.criteria],
                    "reference": {
                        "accession": item.signature_accession,
                        "description": item.description,
                        "curated": False,
                        "annotation_source": item.database_name,
                    },
                    "provenance": self._provenance(
                        context=context,
                        item=item,
                        raw_path=raw_path,
                        software=software,
                        command=command,
                        database_checksums=database_checksums,
                    ),
                    "message": (
                        "configured forbidden domain observed"
                        if effect == "contradicts"
                        else "domain observation; not a final EC prediction"
                    ),
                }
            )
        return records

    def _rule_evidence(
        self,
        *,
        context: StageContext,
        evaluation: DomainRuleEvaluation,
        sequence: str,
        raw_path: Path,
        software: SoftwareProvenance,
        command: CommandProvenance,
        database_checksums: dict[str, str],
    ) -> dict[str, Any] | None:
        rule = evaluation.rule
        if evaluation.satisfied and evaluation.observed_count > 0:
            return None
        provider = self._provider_for_rule(rule)
        config = self.hmmer_config if provider == "hmmer" else self.interpro_config
        assert config is not None
        database_name = config.database_name
        database_version = config.database_version
        completeness = self.profile.sequence_analysis.completeness(len(sequence))
        reason = "domain_rule_satisfied"
        status = "observed"
        assertion: dict[str, Any] | None = {
            "target": {"type": "family", "id": self.profile.family_id},
            "effect": "supports",
        }
        if evaluation.satisfied and rule.requirement == "forbidden":
            reason = "forbidden_domain_absent"
        elif completeness != "complete" and evaluation.observed_count == 0:
            status = "missing"
            assertion = None
            reason = (
                "sequence_truncated"
                if completeness == "truncated"
                else "sequence_completeness_unknown"
            )
        elif rule.requirement == "required" or evaluation.observed_count > 0:
            status = "negative"
            assertion = {
                "target": {"type": "family", "id": self.profile.family_id},
                "effect": "contradicts",
            }
            if not evaluation.count_satisfied:
                reason = "domain_count_constraint_failed"
            else:
                reason = "domain_architecture_order_failed"
        else:
            assertion = {
                "target": {"type": "feature", "id": "domain_absence"},
                "effect": "neutral",
            }
            reason = "domain_absent_complete_sequence"

        database: dict[str, Any] = {
            "name": database_name,
            "version": database_version,
        }
        if provider == "hmmer" and self.hmmer_config is not None:
            database["path"] = str(self.hmmer_config.database)
        elif (
            provider == "interproscan"
            and self.interpro_config is not None
            and self.interpro_config.data_directory is not None
        ):
            database["path"] = str(self.interpro_config.data_directory)
        if database_checksums:
            database["checksum"] = f"sha256:{canonical_json_sha256(database_checksums)}"

        record: dict[str, Any] = {
            "schema_version": 1,
            "evidence_id": (
                f"domain:{provider}:{evaluation.query_id}:rule:{rule.rule_id}"
            ),
            "query": {
                "query_id": evaluation.query_id,
                "sequence_sha256": sha256_bytes(sequence.encode("ascii")),
                "sequence_length": len(sequence),
            },
            "source": {
                "id": provider,
                "evidence_class": "domain_architecture",
                "correlation_group": (
                    f"domain:{rule.source.lower()}:{self._signature_key(rule.signature_id)}:"
                    f"{evaluation.query_id}"
                ),
            },
            "record_status": status,
            "reason_code": reason,
            "metrics": {
                "rule_id": rule.rule_id,
                "signature_accession": rule.signature_id,
                "requirement": rule.requirement,
                "observed_count": evaluation.observed_count,
                "minimum_count": rule.min_count,
                "maximum_count": rule.max_count,
                "sequence_completeness": completeness,
                "architecture_order_satisfied": (
                    evaluation.architecture_order_satisfied
                ),
                "candidate_ecs": ";".join(rule.candidate_ecs) or None,
            },
            "criteria": [
                {
                    "criterion_id": "minimum_domain_count",
                    "metric": "observed_count",
                    "operator": "gte",
                    "threshold": rule.min_count,
                    "observed_value": evaluation.observed_count,
                    "passed": evaluation.observed_count >= rule.min_count,
                },
                {
                    "criterion_id": "maximum_domain_count",
                    "metric": "observed_count",
                    "operator": "lte",
                    "threshold": rule.max_count,
                    "observed_value": evaluation.observed_count,
                    "passed": (
                        rule.max_count is None
                        or evaluation.observed_count <= rule.max_count
                    ),
                },
            ],
            "reference": {
                "accession": rule.signature_id,
                "description": rule.description,
                "curated": False,
                "annotation_source": database_name,
            },
            "provenance": {
                "run_id": context.run_id,
                "stage_id": self.stage_id,
                "generated_at": utc_now(),
                "parser": {
                    "name": f"enzynotation-{provider}-rule-evaluator",
                    "version": "1",
                },
                "tool": {
                    "name": software.name,
                    "version": software.version,
                    "executable": software.executable,
                },
                "databases": [database],
                "raw_artifact": {
                    "path": context.paths.relative_artifact(raw_path),
                    "format": self._raw_format(provider),
                    "sha256": sha256_file(raw_path),
                    "record_locator": f"query:{evaluation.query_id}",
                },
                "configuration_sha256": context.configuration_checksum,
                "input_sha256": sha256_file(context.paths.normalized_fasta),
                "command": list(command.argv),
            },
            "message": "configured domain rule evaluation; not a final EC prediction",
        }
        if assertion is not None:
            record["assertion"] = assertion
        return record

    @staticmethod
    def _write_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
        atomic_write_text(
            path,
            "".join(
                json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n"
                for record in records
            ),
        )

    def _run_hmmer(
        self,
        context: StageContext,
        raw_path: Path,
    ) -> tuple[
        str,
        tuple[DomainObservation, ...],
        CommandProvenance | None,
        SoftwareProvenance | None,
        dict[str, str],
        str,
    ]:
        config = self.hmmer_config
        tool = self.hmmer_tool
        if config is None or tool is None:
            return "not_configured", (), None, None, {}, "provider not configured"
        if not config.enabled:
            return "disabled", (), None, None, {}, "provider disabled"
        try:
            artifacts = tool.database_artifacts()
            checksums = {name: sha256_file(path) for name, path in artifacts.items()}
        except (OSError, ToolConfigurationError) as exc:
            return "failed", (), None, None, {}, str(exc)
        software = tool.capture_version(context.backend)
        command = context.backend.execute(
            tool.build_command(query=context.paths.normalized_fasta, output=raw_path),
            stdout_path=context.paths.for_stage(self.stage_id).log_root
            / "hmmer.stdout.log",
            stderr_path=context.paths.for_stage(self.stage_id).log_root
            / "hmmer.stderr.log",
        )
        if command.return_code != 0:
            return (
                "failed",
                (),
                command,
                software,
                checksums,
                f"hmmscan exited with code {command.return_code}",
            )
        if not raw_path.exists():
            atomic_write_text(raw_path, "")
        try:
            parsed = parse_hmmer_domtblout(raw_path)
            observations = hmmer_observations(parsed, config)
        except (HmmerParseError, OSError, ValueError) as exc:
            return "failed", (), command, software, checksums, str(exc)
        status = "empty" if not parsed else "completed"
        return status, observations, command, software, checksums, ""

    def _run_interpro(
        self,
        context: StageContext,
        raw_path: Path,
    ) -> tuple[
        str,
        tuple[DomainObservation, ...],
        CommandProvenance | None,
        SoftwareProvenance | None,
        dict[str, str],
        str,
    ]:
        config = self.interpro_config
        tool = self.interpro_tool
        if config is None or tool is None:
            return "not_configured", (), None, None, {}, "provider not configured"
        if not config.enabled:
            return "disabled", (), None, None, {}, "provider disabled"
        try:
            artifacts = tool.database_artifacts()
            checksums = {name: sha256_file(path) for name, path in artifacts.items()}
        except (OSError, ToolConfigurationError) as exc:
            return "failed", (), None, None, {}, str(exc)
        software = tool.capture_version(context.backend)
        command = context.backend.execute(
            tool.build_command(query=context.paths.normalized_fasta, output=raw_path),
            stdout_path=context.paths.for_stage(self.stage_id).log_root
            / "interpro.stdout.log",
            stderr_path=context.paths.for_stage(self.stage_id).log_root
            / "interpro.stderr.log",
        )
        if command.return_code != 0:
            return (
                "failed",
                (),
                command,
                software,
                checksums,
                f"InterProScan exited with code {command.return_code}",
            )
        if not raw_path.exists():
            atomic_write_text(raw_path, "")
        try:
            parsed = parse_interpro_tsv(raw_path)
            observations = interpro_observations(
                parsed,
                database_name=config.database_name,
                database_version=config.database_version,
            )
        except (InterProParseError, OSError, ValueError) as exc:
            return "failed", (), command, software, checksums, str(exc)
        status = "empty" if not parsed else "completed"
        return status, observations, command, software, checksums, ""

    def execute(self, context: StageContext) -> StageResult:
        """Run enabled domain providers and emit canonical evidence."""

        stage_paths = context.paths.for_stage(self.stage_id)
        stage_paths.prepare()
        hmmer_raw = stage_paths.raw / "hmmer.domtblout"
        interpro_raw = stage_paths.raw / "interpro.tsv"
        parsed_output = stage_paths.normalized / "domain_hits.tsv"
        summary_output = stage_paths.normalized / "domain_summary.json"
        config_output = stage_paths.normalized / "domain_config.json"
        evidence_output = context.paths.evidence / "domain_evidence.jsonl"
        for path in (
            hmmer_raw,
            interpro_raw,
            parsed_output,
            summary_output,
            config_output,
            evidence_output,
        ):
            self._archive_existing(path, stage_paths.history)
            path.unlink(missing_ok=True)
        atomic_write_json(config_output, self.configuration(context))

        options = FastaValidationOptions.from_mapping(context.config.section("input"))
        validated = validate_fasta(context.paths.normalized_fasta, options)
        if not validated.is_valid:
            return StageResult(
                False,
                {"configuration": config_output},
                message="normalized FASTA is invalid",
            )
        sequences = {record.identifier: record.sequence for record in validated.records}

        hmmer_result = self._run_hmmer(context, hmmer_raw)
        interpro_result = self._run_interpro(context, interpro_raw)
        provider_results = {"hmmer": hmmer_result, "interproscan": interpro_result}
        observations = tuple(
            item for result in provider_results.values() for item in result[1]
        )
        atomic_write_text(parsed_output, self._domain_tsv(observations))
        evaluations = evaluate_domain_rules(
            profile=self.profile,
            query_ids=tuple(sequences),
            observations=tuple(item for item in observations if item.passed),
        )

        evidence: list[dict[str, Any]] = []
        raw_paths = {"hmmer": hmmer_raw, "interproscan": interpro_raw}
        for item in observations:
            result = provider_results[item.provider]
            command, software, checksums = result[2], result[3], result[4]
            if command is None or software is None:
                continue
            evidence.extend(
                self._hit_evidence(
                    context=context,
                    item=item,
                    sequence=sequences[item.query_id],
                    raw_path=raw_paths[item.provider],
                    software=software,
                    command=command,
                    database_checksums=checksums,
                )
            )
        for evaluation in evaluations:
            provider = self._provider_for_rule(evaluation.rule)
            result = provider_results[provider]
            status, command, software, checksums = (
                result[0],
                result[2],
                result[3],
                result[4],
            )
            if (
                status not in {"completed", "empty"}
                or command is None
                or software is None
            ):
                continue
            record = self._rule_evidence(
                context=context,
                evaluation=evaluation,
                sequence=sequences[evaluation.query_id],
                raw_path=raw_paths[provider],
                software=software,
                command=command,
                database_checksums=checksums,
            )
            if record is not None:
                evidence.append(record)
        self._write_jsonl(evidence_output, evidence)

        required_failures = []
        optional_failures = []
        provider_summary: dict[str, Any] = {}
        configs = {"hmmer": self.hmmer_config, "interproscan": self.interpro_config}
        for provider, result in provider_results.items():
            status, provider_hits, command, software, _, message = result
            config = configs[provider]
            provider_summary[provider] = {
                "status": status,
                "enabled": bool(config and config.enabled),
                "required": bool(config and config.required),
                "hit_count": len(provider_hits),
                "message": message,
                "return_code": command.return_code if command else None,
                "version": software.version if software else None,
            }
            if status == "failed" and config is not None:
                (required_failures if config.required else optional_failures).append(
                    provider
                )

        summary = {
            "schema_version": 1,
            "provider": "domains",
            "status": "failed" if required_failures else "completed",
            "family_id": self.profile.family_id,
            "family_version": self.profile.version,
            "providers": provider_summary,
            "counts": {
                "raw_domain_hits": len(observations),
                "retained_domain_hits": sum(item.passed for item in observations),
                "filtered_domain_hits": sum(not item.passed for item in observations),
                "evidence_records": len(evidence),
            },
            "rule_evaluations": [
                {
                    "query_id": evaluation.query_id,
                    "rule_id": evaluation.rule.rule_id,
                    "requirement": evaluation.rule.requirement,
                    "observed_count": evaluation.observed_count,
                    "count_satisfied": evaluation.count_satisfied,
                    "architecture_order_satisfied": (
                        evaluation.architecture_order_satisfied
                    ),
                    "satisfied": evaluation.satisfied,
                }
                for evaluation in evaluations
            ],
            "optional_provider_failures": optional_failures,
            "final_ec_prediction": None,
        }
        atomic_write_json(summary_output, summary)
        outputs = {
            "configuration": config_output,
            "parsed_hits": parsed_output,
            "evidence": evidence_output,
            "summary": summary_output,
        }
        if hmmer_raw.exists():
            outputs["hmmer_raw"] = hmmer_raw
        if interpro_raw.exists():
            outputs["interpro_raw"] = interpro_raw
        commands = tuple(
            result[2] for result in provider_results.values() if result[2] is not None
        )
        software = tuple(
            result[3] for result in provider_results.values() if result[3] is not None
        )
        return StageResult(
            succeeded=not required_failures,
            outputs=outputs,
            commands=commands,
            software=software,
            message=(
                f"required domain providers failed: {', '.join(required_failures)}"
                if required_failures
                else (
                    f"domain evidence completed; optional failures: "
                    f"{', '.join(optional_failures)}"
                    if optional_failures
                    else "domain evidence completed; no final EC prediction was made"
                )
            ),
        )
