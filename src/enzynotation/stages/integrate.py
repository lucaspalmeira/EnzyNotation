"""Workflow stage for canonical evidence integration and EC prediction."""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path
from typing import Any

from enzynotation import __version__
from enzynotation.evidence import (
    ProviderAvailability,
    ProviderState,
    load_evidence_files,
)
from enzynotation.family import FamilyProfile
from enzynotation.fasta import FastaValidationOptions, validate_fasta
from enzynotation.integration import IntegrationConfig, integrate_collection
from enzynotation.provenance import (
    SoftwareProvenance,
    sha256_bytes,
    sha256_file,
    utc_now,
)
from enzynotation.rules import ECRules
from enzynotation.stages.base import Stage, StageContext, StageResult
from enzynotation.state import atomic_write_json, atomic_write_text


class IntegrateStage(Stage):
    """Integrate provider-neutral evidence using declarative scientific policy."""

    evidence_schema_version = "1"

    def __init__(
        self,
        integration_config: IntegrationConfig,
        *,
        ec_rules: ECRules | None = None,
        family_profile: FamilyProfile | None = None,
    ) -> None:
        super().__init__(
            "integrate",
            dependencies=("validate",),
            required=integration_config.required,
            implementation_version="1",
        )
        self.policy = integration_config
        self.ec_rules = ec_rules
        self.family_profile = family_profile
        self.evidence_schema = Path("configs/schema/evidence.schema.json").resolve()

    def _run_path(self, context: StageContext, relative: str) -> Path:
        return context.paths.run_root / relative

    def _available_evidence_paths(self, context: StageContext) -> tuple[Path, ...]:
        return tuple(
            path
            for relative in self.policy.evidence_paths
            if (path := self._run_path(context, relative)).is_file()
        )

    def input_files(self, context: StageContext) -> dict[str, Path]:
        """Hash canonical evidence, policies, schemas, and provider states."""

        files: dict[str, Path] = {
            "normalized_fasta": context.paths.normalized_fasta,
            "integration_config": self.policy.source_path,
            "confidence_policy": self.policy.confidence_path,
            "evidence_schema": self.evidence_schema,
        }
        if self.ec_rules is not None:
            files["ec_rules"] = self.ec_rules.source_path
        if self.family_profile is not None:
            files["family_profile"] = self.family_profile.source_path
        for index, path in enumerate(self._available_evidence_paths(context), start=1):
            files[f"evidence_{index:03d}"] = path
        for provider in self.policy.providers:
            status = context.paths.for_stage(provider.stage_id).status
            if status.is_file():
                files[f"provider_status_{provider.source_id}"] = status
            if provider.summary_path:
                summary = self._run_path(context, provider.summary_path)
                if summary.is_file():
                    files[f"provider_summary_{provider.source_id}"] = summary
        return files

    def configuration(self, context: StageContext) -> dict[str, Any]:
        """Include optional-file presence in the deterministic cache signature."""

        inventory = {
            relative: (
                sha256_file(self._run_path(context, relative))
                if self._run_path(context, relative).is_file()
                else None
            )
            for relative in self.policy.evidence_paths
        }
        return {
            "integration": self.policy.document,
            "confidence": self.policy.confidence_document,
            "ec_rules": self.ec_rules.document if self.ec_rules else None,
            "family": self.family_profile.document if self.family_profile else None,
            "evidence_inventory": inventory,
            "evidence_schema_version": self.evidence_schema_version,
        }

    @staticmethod
    def _lookup(value: Any, dotted_key: str | None) -> Any:
        current = value
        for part in (dotted_key or "").split("."):
            if not part:
                continue
            if not isinstance(current, dict) or part not in current:
                return None
            current = current[part]
        return current

    def _availability(
        self, context: StageContext, source_counts: dict[str, int]
    ) -> tuple[ProviderAvailability, ...]:
        states: list[ProviderAvailability] = []
        for provider in self.policy.providers:
            state_path = context.paths.for_stage(provider.stage_id).status
            reason: str | None = None
            state = (
                ProviderState.NOT_RUN
                if provider.required
                else ProviderState.NOT_CONFIGURED
            )
            if state_path.is_file():
                status_document = json.loads(state_path.read_text(encoding="utf-8"))
                stage_status = status_document.get("status")
                if stage_status == "failed":
                    state = ProviderState.FAILED
                    reason = str(status_document.get("message") or "stage failed")
                elif stage_status == "not_available":
                    state = ProviderState.UNAVAILABLE
                    reason = str(
                        status_document.get("message") or "dependencies unavailable"
                    )
                elif stage_status == "completed":
                    state = ProviderState.SUCCESSFUL

            provider_status = None
            if provider.summary_path:
                summary_path = self._run_path(context, provider.summary_path)
                if summary_path.is_file():
                    summary = json.loads(summary_path.read_text(encoding="utf-8"))
                    provider_status = self._lookup(summary, provider.summary_status_key)
            status_map = {
                "disabled": ProviderState.DISABLED,
                "not_configured": ProviderState.NOT_CONFIGURED,
                "failed": ProviderState.FAILED,
                "unavailable": ProviderState.UNAVAILABLE,
                "not_run": ProviderState.NOT_RUN,
                "empty": ProviderState.SUCCESSFUL_ZERO,
            }
            if provider_status in status_map:
                state = status_map[provider_status]
            evidence_missing = (
                provider.evidence_path is not None
                and not self._run_path(context, provider.evidence_path).is_file()
            )
            if state is ProviderState.SUCCESSFUL and evidence_missing:
                state = ProviderState.UNAVAILABLE
                reason = "canonical_evidence_file_missing"
            elif (
                state is ProviderState.SUCCESSFUL
                and source_counts.get(provider.source_id, 0) == 0
            ):
                state = ProviderState.SUCCESSFUL_ZERO
            states.append(
                ProviderAvailability(
                    provider.source_id,
                    state,
                    required=provider.required,
                    roles=provider.roles,
                    reason=reason,
                )
            )
        return tuple(states)

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
    def _archive(path: Path, history: Path) -> None:
        if not path.is_file():
            return
        destination = history / f"{path.name}.{sha256_file(path)}"
        if not destination.exists():
            shutil.copy2(path, destination)

    def execute(self, context: StageContext) -> StageResult:
        """Validate canonical inputs, integrate them, and publish final annotations."""

        stage_paths = context.paths.for_stage(self.stage_id)
        stage_paths.prepare()
        scorecards_path = stage_paths.normalized / "candidate_scorecards.jsonl"
        conflicts_path = stage_paths.normalized / "conflicts.jsonl"
        rules_path = stage_paths.normalized / "rule_evaluations.jsonl"
        summary_path = stage_paths.normalized / "integration_summary.json"
        policy_path = stage_paths.normalized / "integration_config.json"
        confidence_path = stage_paths.normalized / "confidence_policy.json"
        rules_config_path = stage_paths.normalized / "ec_rules.json"
        family_config_path = stage_paths.normalized / "family_profile.json"
        final_path = context.paths.run_root / "final_annotation.json"
        for path in (
            scorecards_path,
            conflicts_path,
            rules_path,
            summary_path,
            policy_path,
            confidence_path,
            rules_config_path,
            family_config_path,
            final_path,
        ):
            self._archive(path, stage_paths.history)
            path.unlink(missing_ok=True)
        atomic_write_json(policy_path, self.policy.document)
        atomic_write_json(confidence_path, self.policy.confidence_document)
        if self.ec_rules is not None:
            atomic_write_json(rules_config_path, self.ec_rules.document)
        if self.family_profile is not None:
            atomic_write_json(family_config_path, self.family_profile.document)

        evidence_paths = self._available_evidence_paths(context)
        collection = load_evidence_files(
            evidence_paths, schema_path=self.evidence_schema
        )
        source_counts: dict[str, int] = {}
        for record in collection.records:
            source_counts[record.source_id] = source_counts.get(record.source_id, 0) + 1
        availability = self._availability(context, source_counts)

        validation = validate_fasta(
            context.paths.normalized_fasta,
            FastaValidationOptions.from_mapping(context.config.section("input")),
        )
        if not validation.is_valid:
            return StageResult(False, {}, message="normalized FASTA is invalid")
        queries = {record.identifier: record.sequence for record in validation.records}
        for evidence in collection.records:
            sequence = queries.get(evidence.query_id)
            if sequence is None:
                raise ValueError(
                    f"Evidence {evidence.evidence_id} references unknown query "
                    f"{evidence.query_id}"
                )
            query = evidence.data["query"]
            if query["sequence_sha256"] != sha256_bytes(
                sequence.encode("ascii")
            ) or query["sequence_length"] != len(sequence):
                raise ValueError(
                    f"Evidence {evidence.evidence_id} does not match the validated "
                    f"sequence for {evidence.query_id}"
                )

        results = integrate_collection(
            collection,
            self.policy,
            query_ids=list(queries),
            availability=availability,
            ec_rules=self.ec_rules,
            family_id=(self.family_profile.family_id if self.family_profile else None),
        )
        generated_at = utc_now()
        input_evidence_checksums = {
            context.paths.relative_artifact(path): sha256_file(path)
            for path in evidence_paths
        }
        provenance: dict[str, Any] = {
            "generated_at": generated_at,
            "implementation": {"name": "enzynotation-integration", "version": "1"},
            "policy": {
                "id": self.policy.integration["policy_id"],
                "version": self.policy.integration["policy_version"],
                "calibration": self.policy.integration["calibration"],
            },
            "integration_configuration_sha256": sha256_file(self.policy.source_path),
            "confidence_policy_sha256": sha256_file(self.policy.confidence_path),
            "ec_rules_sha256": (
                sha256_file(self.ec_rules.source_path) if self.ec_rules else None
            ),
            "family_rules_sha256": (
                sha256_file(self.family_profile.source_path)
                if self.family_profile
                else None
            ),
            "input_evidence_sha256": input_evidence_checksums,
            "evidence_schema_version": self.evidence_schema_version,
            "evidence_schema_sha256": sha256_file(self.evidence_schema),
            "stage_configuration_sha256": context.configuration_checksum,
        }

        scorecards = [
            {"query_id": result.query_id, **card}
            for result in results
            for card in result.scorecards
        ]
        conflicts = [
            {"query_id": result.query_id, **conflict.to_dict()}
            for result in results
            for conflict in result.conflicts
        ]
        evaluations = [
            evaluation for result in results for evaluation in result.rule_evaluations
        ]
        self._write_jsonl(scorecards_path, scorecards)
        self._write_jsonl(conflicts_path, conflicts)
        self._write_jsonl(rules_path, evaluations)

        annotations = [
            result.annotation_dict(
                policy_id=str(self.policy.integration["policy_id"]),
                policy_version=str(self.policy.integration["policy_version"]),
                provenance=provenance,
            )
            for result in results
        ]
        final_document = {
            "schema_version": 1,
            "generated_at": generated_at,
            "annotations": annotations,
            "provenance": provenance,
        }
        atomic_write_json(final_path, final_document)
        summary = {
            "schema_version": 1,
            "status": "completed",
            "query_count": len(results),
            "exact_prediction_count": sum(
                item.completeness == "complete" for item in results
            ),
            "partial_prediction_count": sum(
                item.completeness == "partial" for item in results
            ),
            "unresolved_count": sum(item.predicted_ec is None for item in results),
            "provider_availability": [item.to_dict() for item in availability],
            "final_ec_prediction": (
                annotations[0]["predicted_ec"] if len(annotations) == 1 else None
            ),
            "policy": {
                "id": self.policy.integration["policy_id"],
                "version": self.policy.integration["policy_version"],
                "calibration": "heuristic",
            },
        }
        atomic_write_json(summary_path, summary)
        outputs = {
            "candidate_scorecards": scorecards_path,
            "conflicts": conflicts_path,
            "rule_evaluations": rules_path,
            "summary": summary_path,
            "final_annotation": final_path,
            "integration_configuration": policy_path,
            "confidence_policy": confidence_path,
        }
        if self.ec_rules is not None:
            outputs["ec_rules"] = rules_config_path
        if self.family_profile is not None:
            outputs["family_profile"] = family_config_path
        return StageResult(
            True,
            outputs,
            software=(SoftwareProvenance("enzynotation", __version__, sys.executable),),
            message=f"integrated {len(results)} query sequence(s)",
        )
