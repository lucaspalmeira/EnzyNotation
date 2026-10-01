"""Configuration-driven catalytic motif evidence stage."""

from __future__ import annotations

import csv
import io
import json
import re
import sys
from pathlib import Path
from typing import Any

from enzynotation import __version__
from enzynotation.family import FamilyProfile
from enzynotation.fasta import FastaValidationOptions, validate_fasta
from enzynotation.motifs import (
    MotifAnalysisResult,
    MotifEvaluation,
    MotifHit,
    RelationshipEvaluation,
    analyze_motifs,
)
from enzynotation.provenance import (
    SoftwareProvenance,
    sha256_bytes,
    sha256_file,
    utc_now,
)
from enzynotation.stages.base import Stage, StageContext, StageResult
from enzynotation.state import atomic_write_bytes, atomic_write_json, atomic_write_text

_MOTIF_FIELDS = (
    "query_id",
    "rule_id",
    "occurrence",
    "start",
    "end",
    "matched_sequence",
    "coordinate_satisfied",
    "position_satisfied",
    "residues_satisfied",
    "qualifying",
    "catalytic_residues_json",
)

_MISSING_REASONS = {
    "motif_not_evaluated",
    "domain_context_unavailable",
    "motif_outside_analyzed_region",
    "sequence_truncated",
    "sequence_completeness_unknown",
    "sequence_length_outside_expected_range",
    "relationship_not_evaluated_missing_motif",
}


class MotifsStage(Stage):
    """Evaluate family-configured motifs without assigning an EC number."""

    def __init__(self, profile: FamilyProfile) -> None:
        super().__init__(
            "motifs",
            dependencies=("validate",),
            required=True,
            implementation_version="1",
        )
        self.profile = profile

    def input_files(self, context: StageContext) -> dict[str, Path]:
        """Declare normalized sequences and the family profile."""

        return {
            "normalized_fasta": context.paths.normalized_fasta,
            "family_profile": self.profile.source_path,
        }

    def configuration(self, context: StageContext) -> dict[str, Any]:
        """Use the complete family motif policy as the stage configuration."""

        return {"family": self.profile.to_dict()}

    @staticmethod
    def _archive_existing(path: Path, history: Path) -> None:
        if not path.is_file():
            return
        digest = sha256_file(path)
        archive = history / "artifacts" / f"{path.name}.{digest}"
        if not archive.exists():
            atomic_write_bytes(archive, path.read_bytes())

    @staticmethod
    def _safe_id(value: str) -> str:
        rendered = re.sub(r"[^A-Za-z0-9._-]", "_", value)
        return rendered if rendered and rendered[0].isalnum() else f"motif_{rendered}"

    @staticmethod
    def _residues(hit: MotifHit) -> list[dict[str, Any]]:
        return [
            {
                "name": residue.name,
                "residue": residue.residue,
                "position": residue.position,
                "allowed_residue": residue.allowed_residue,
                "expected_position": residue.expected_position,
                "tolerance": residue.tolerance,
                "position_satisfied": residue.position_satisfied,
            }
            for residue in hit.residues
        ]

    def _hits_tsv(self, hits: tuple[MotifHit, ...]) -> str:
        output = io.StringIO()
        writer = csv.DictWriter(output, fieldnames=_MOTIF_FIELDS, delimiter="\t")
        writer.writeheader()
        for hit in hits:
            writer.writerow(
                {
                    "query_id": hit.query_id,
                    "rule_id": hit.rule_id,
                    "occurrence": hit.occurrence,
                    "start": hit.start,
                    "end": hit.end,
                    "matched_sequence": hit.matched_sequence,
                    "coordinate_satisfied": str(hit.coordinate_satisfied).lower(),
                    "position_satisfied": (
                        ""
                        if hit.position_satisfied is None
                        else str(hit.position_satisfied).lower()
                    ),
                    "residues_satisfied": str(hit.residues_satisfied).lower(),
                    "qualifying": str(hit.qualifying).lower(),
                    "catalytic_residues_json": json.dumps(
                        self._residues(hit), sort_keys=True, separators=(",", ":")
                    ),
                }
            )
        return output.getvalue()

    def _raw_documents(
        self, result: MotifAnalysisResult
    ) -> tuple[list[dict[str, Any]], dict[tuple[Any, ...], int]]:
        documents: list[dict[str, Any]] = []
        locators: dict[tuple[Any, ...], int] = {}
        for hit in result.hits:
            record = {
                "kind": "motif_hit",
                "query_id": hit.query_id,
                "rule_id": hit.rule_id,
                "occurrence": hit.occurrence,
                "start": hit.start,
                "end": hit.end,
                "matched_sequence": hit.matched_sequence,
                "coordinate_satisfied": hit.coordinate_satisfied,
                "position_satisfied": hit.position_satisfied,
                "residues_satisfied": hit.residues_satisfied,
                "qualifying": hit.qualifying,
                "catalytic_residues": self._residues(hit),
            }
            documents.append(record)
            locators[("hit", hit.query_id, hit.rule_id, hit.occurrence)] = len(
                documents
            )
        for evaluation in result.evaluations:
            documents.append(
                {
                    "kind": "motif_evaluation",
                    "query_id": evaluation.query_id,
                    "rule_id": evaluation.rule.rule_id,
                    "completeness": evaluation.completeness,
                    "analyzed_start": evaluation.analyzed_start,
                    "analyzed_end": evaluation.analyzed_end,
                    "raw_match_count": evaluation.raw_match_count,
                    "qualifying_count": evaluation.qualifying_count,
                    "satisfied": evaluation.satisfied,
                    "reason_code": evaluation.reason_code,
                }
            )
            locators[("evaluation", evaluation.query_id, evaluation.rule.rule_id)] = (
                len(documents)
            )
        for evaluation in result.relationships:
            documents.append(
                {
                    "kind": "motif_relationship",
                    "query_id": evaluation.query_id,
                    "relationship_id": evaluation.relationship.relationship_id,
                    "satisfied": evaluation.satisfied,
                    "observed_distance": evaluation.observed_distance,
                    "reason_code": evaluation.reason_code,
                }
            )
            locators[
                (
                    "relationship",
                    evaluation.query_id,
                    evaluation.relationship.relationship_id,
                )
            ] = len(documents)
        return documents, locators

    @staticmethod
    def _write_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
        atomic_write_text(
            path,
            "".join(
                json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n"
                for record in records
            ),
        )

    def _provenance(
        self,
        *,
        context: StageContext,
        raw_path: Path,
        locator: int,
        software: SoftwareProvenance,
    ) -> dict[str, Any]:
        return {
            "run_id": context.run_id,
            "stage_id": self.stage_id,
            "generated_at": utc_now(),
            "parser": {"name": "enzynotation-motif-engine", "version": "1"},
            "tool": {
                "name": software.name,
                "version": software.version,
                "executable": software.executable,
            },
            "databases": [
                {
                    "name": f"family-profile:{self.profile.family_id}",
                    "version": self.profile.version,
                    "path": str(self.profile.source_path),
                    "checksum": f"sha256:{sha256_file(self.profile.source_path)}",
                }
            ],
            "raw_artifact": {
                "path": context.paths.relative_artifact(raw_path),
                "format": "enzynotation-motif-jsonl",
                "sha256": sha256_file(raw_path),
                "record_locator": f"line:{locator}",
            },
            "configuration_sha256": context.configuration_checksum,
            "input_sha256": sha256_file(context.paths.normalized_fasta),
            "command": ["enzynotation", "motif-analysis", self.profile.family_id],
        }

    def _query(
        self, query_id: str, sequences: dict[str, str]
    ) -> tuple[str, dict[str, Any]]:
        sequence = sequences[query_id]
        return sequence, {
            "query_id": query_id,
            "sequence_sha256": sha256_bytes(sequence.encode("ascii")),
            "sequence_length": len(sequence),
        }

    def _hit_evidence(
        self,
        *,
        context: StageContext,
        hit: MotifHit,
        evaluation: MotifEvaluation,
        sequences: dict[str, str],
        raw_path: Path,
        locator: int,
        software: SoftwareProvenance,
    ) -> dict[str, Any]:
        _, query = self._query(hit.query_id, sequences)
        effect = (
            "contradicts" if evaluation.rule.requirement == "forbidden" else "supports"
        )
        residues_json = json.dumps(
            self._residues(hit), sort_keys=True, separators=(",", ":")
        )
        return {
            "schema_version": 1,
            "evidence_id": (
                f"motif:{hit.query_id}:{hit.rule_id}:{hit.start}-{hit.end}:"
                f"{hit.occurrence}"
            ),
            "query": query,
            "source": {
                "id": "catalytic_motif",
                "evidence_class": "catalytic_motif",
                "correlation_group": (
                    f"motif:{self.profile.family_id}:{hit.rule_id}:{hit.query_id}"
                ),
            },
            "record_status": "observed",
            "assertion": {
                "target": {"type": "family", "id": self.profile.family_id},
                "effect": effect,
            },
            "metrics": {
                "rule_id": hit.rule_id,
                "requirement": evaluation.rule.requirement,
                "motif_start": hit.start,
                "motif_end": hit.end,
                "matched_sequence": hit.matched_sequence,
                "occurrence": hit.occurrence,
                "coordinate_satisfied": hit.coordinate_satisfied,
                "position_satisfied": hit.position_satisfied,
                "residues_satisfied": hit.residues_satisfied,
                "catalytic_residues_json": residues_json,
                "candidate_ecs": ";".join(evaluation.rule.candidate_ecs) or None,
            },
            "criteria": [
                {
                    "criterion_id": "coordinate_constraint",
                    "metric": "coordinate_satisfied",
                    "operator": "eq",
                    "threshold": True,
                    "observed_value": hit.coordinate_satisfied,
                    "passed": hit.coordinate_satisfied,
                },
                {
                    "criterion_id": "catalytic_residue_constraints",
                    "metric": "residues_satisfied",
                    "operator": "eq",
                    "threshold": True,
                    "observed_value": hit.residues_satisfied,
                    "passed": hit.residues_satisfied,
                },
            ],
            "reference": {
                "accession": hit.rule_id,
                "description": evaluation.rule.description,
                "curated": False,
                "annotation_source": f"family-profile:{self.profile.family_id}",
            },
            "provenance": self._provenance(
                context=context,
                raw_path=raw_path,
                locator=locator,
                software=software,
            ),
            "message": "motif compatibility evidence; not a final EC prediction",
        }

    def _evaluation_evidence(
        self,
        *,
        context: StageContext,
        evaluation: MotifEvaluation,
        sequences: dict[str, str],
        raw_path: Path,
        locator: int,
        software: SoftwareProvenance,
    ) -> dict[str, Any] | None:
        rule = evaluation.rule
        if evaluation.qualifying_count > 0:
            return None
        _, query = self._query(evaluation.query_id, sequences)
        common: dict[str, Any] = {
            "schema_version": 1,
            "evidence_id": (f"motif:{evaluation.query_id}:{rule.rule_id}:evaluation"),
            "query": query,
            "source": {
                "id": "catalytic_motif",
                "evidence_class": "catalytic_motif",
                "correlation_group": (
                    f"motif:{self.profile.family_id}:{rule.rule_id}:"
                    f"{evaluation.query_id}"
                ),
            },
            "reason_code": evaluation.reason_code,
            "metrics": {
                "rule_id": rule.rule_id,
                "requirement": rule.requirement,
                "raw_match_count": evaluation.raw_match_count,
                "qualifying_count": evaluation.qualifying_count,
                "sequence_completeness": evaluation.completeness,
                "analyzed_start": evaluation.analyzed_start,
                "analyzed_end": evaluation.analyzed_end,
            },
            "reference": {
                "accession": rule.rule_id,
                "description": rule.description,
                "curated": False,
                "annotation_source": f"family-profile:{self.profile.family_id}",
            },
            "provenance": self._provenance(
                context=context,
                raw_path=raw_path,
                locator=locator,
                software=software,
            ),
        }
        if evaluation.reason_code in _MISSING_REASONS:
            return {
                **common,
                "record_status": "missing",
                "message": "motif was unavailable or not evaluable",
            }
        if rule.requirement == "required":
            return {
                **common,
                "record_status": "negative",
                "assertion": {
                    "target": {"type": "family", "id": self.profile.family_id},
                    "effect": "contradicts",
                },
                "message": "required motif was not observed in a complete analysis",
            }
        if rule.requirement == "forbidden":
            return {
                **common,
                "record_status": "observed",
                "assertion": {
                    "target": {"type": "family", "id": self.profile.family_id},
                    "effect": "supports",
                },
                "message": "configured forbidden motif was absent",
            }
        return {
            **common,
            "record_status": "observed",
            "assertion": {
                "target": {"type": "feature", "id": "motif_absence"},
                "effect": "neutral",
            },
            "message": "non-required motif was absent",
        }

    def _relationship_evidence(
        self,
        *,
        context: StageContext,
        evaluation: RelationshipEvaluation,
        sequences: dict[str, str],
        raw_path: Path,
        locator: int,
        software: SoftwareProvenance,
    ) -> dict[str, Any]:
        relationship = evaluation.relationship
        _, query = self._query(evaluation.query_id, sequences)
        common: dict[str, Any] = {
            "schema_version": 1,
            "evidence_id": (
                f"motif:{evaluation.query_id}:relationship:"
                f"{self._safe_id(relationship.relationship_id)}"
            ),
            "query": query,
            "source": {
                "id": "catalytic_motif",
                "evidence_class": "catalytic_motif",
                "correlation_group": (
                    f"motif:{self.profile.family_id}:relationship:"
                    f"{relationship.relationship_id}:{evaluation.query_id}"
                ),
            },
            "reason_code": evaluation.reason_code,
            "metrics": {
                "relationship_id": relationship.relationship_id,
                "upstream_motif": relationship.upstream_motif,
                "downstream_motif": relationship.downstream_motif,
                "minimum_distance": relationship.minimum_distance,
                "maximum_distance": relationship.maximum_distance,
                "observed_distance": evaluation.observed_distance,
                "requirement": relationship.requirement,
            },
            "provenance": self._provenance(
                context=context,
                raw_path=raw_path,
                locator=locator,
                software=software,
            ),
        }
        if evaluation.satisfied is None:
            return {
                **common,
                "record_status": "missing",
                "message": "motif relationship could not be evaluated",
            }
        effect = "supports" if evaluation.satisfied else "contradicts"
        return {
            **common,
            "record_status": "observed" if evaluation.satisfied else "negative",
            "assertion": {
                "target": {"type": "family", "id": self.profile.family_id},
                "effect": effect,
            },
            "message": "configured motif order and distance evaluation",
        }

    def execute(self, context: StageContext) -> StageResult:
        """Evaluate motifs and write raw, normalized, and canonical outputs."""

        stage_paths = context.paths.for_stage(self.stage_id)
        stage_paths.prepare()
        raw_output = stage_paths.raw / "motif_matches.jsonl"
        hits_output = stage_paths.normalized / "motif_hits.tsv"
        summary_output = stage_paths.normalized / "motif_summary.json"
        config_output = stage_paths.normalized / "motif_config.json"
        evidence_output = context.paths.evidence / "motif_evidence.jsonl"
        for path in (
            raw_output,
            hits_output,
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
        result = analyze_motifs(validated.records, self.profile)
        raw_documents, locators = self._raw_documents(result)
        self._write_jsonl(raw_output, raw_documents)
        atomic_write_text(hits_output, self._hits_tsv(result.hits))

        software = SoftwareProvenance(
            name="enzynotation-motif-engine",
            version=__version__,
            executable=sys.executable,
        )
        sequences = {record.identifier: record.sequence for record in validated.records}
        evaluation_by_key = {
            (evaluation.query_id, evaluation.rule.rule_id): evaluation
            for evaluation in result.evaluations
        }
        evidence: list[dict[str, Any]] = []
        for hit in result.hits:
            if not hit.qualifying:
                continue
            evaluation = evaluation_by_key[(hit.query_id, hit.rule_id)]
            evidence.append(
                self._hit_evidence(
                    context=context,
                    hit=hit,
                    evaluation=evaluation,
                    sequences=sequences,
                    raw_path=raw_output,
                    locator=locators[
                        ("hit", hit.query_id, hit.rule_id, hit.occurrence)
                    ],
                    software=software,
                )
            )
        for evaluation in result.evaluations:
            record = self._evaluation_evidence(
                context=context,
                evaluation=evaluation,
                sequences=sequences,
                raw_path=raw_output,
                locator=locators[
                    ("evaluation", evaluation.query_id, evaluation.rule.rule_id)
                ],
                software=software,
            )
            if record is not None:
                evidence.append(record)
        for evaluation in result.relationships:
            evidence.append(
                self._relationship_evidence(
                    context=context,
                    evaluation=evaluation,
                    sequences=sequences,
                    raw_path=raw_output,
                    locator=locators[
                        (
                            "relationship",
                            evaluation.query_id,
                            evaluation.relationship.relationship_id,
                        )
                    ],
                    software=software,
                )
            )
        self._write_jsonl(evidence_output, evidence)

        summary = {
            "schema_version": 1,
            "provider": "catalytic_motif",
            "status": "completed",
            "family_id": self.profile.family_id,
            "family_version": self.profile.version,
            "counts": {
                "raw_matches": len(result.hits),
                "qualifying_matches": sum(hit.qualifying for hit in result.hits),
                "motif_evaluations": len(result.evaluations),
                "relationship_evaluations": len(result.relationships),
                "evidence_records": len(evidence),
            },
            "evaluations": [
                {
                    "query_id": item.query_id,
                    "rule_id": item.rule.rule_id,
                    "requirement": item.rule.requirement,
                    "raw_match_count": item.raw_match_count,
                    "qualifying_count": item.qualifying_count,
                    "satisfied": item.satisfied,
                    "reason_code": item.reason_code,
                    "sequence_completeness": item.completeness,
                }
                for item in result.evaluations
            ],
            "relationships": [
                {
                    "query_id": item.query_id,
                    "relationship_id": item.relationship.relationship_id,
                    "satisfied": item.satisfied,
                    "observed_distance": item.observed_distance,
                    "reason_code": item.reason_code,
                }
                for item in result.relationships
            ],
            "final_ec_prediction": None,
        }
        atomic_write_json(summary_output, summary)
        return StageResult(
            True,
            {
                "configuration": config_output,
                "raw_matches": raw_output,
                "parsed_hits": hits_output,
                "evidence": evidence_output,
                "summary": summary_output,
            },
            software=(software,),
            message="motif evidence completed; no final EC prediction was made",
        )
