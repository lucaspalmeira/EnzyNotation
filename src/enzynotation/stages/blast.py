"""BLASTp evidence-provider stage."""

from __future__ import annotations

import csv
import io
import json
from pathlib import Path
from typing import Any

from enzynotation.curation import (
    CuratedAnnotation,
    CurationError,
    CurationIndex,
    load_curation_metadata,
    normalize_subject_accession,
)
from enzynotation.fasta import FastaValidationOptions, validate_fasta
from enzynotation.parsers.blast import (
    BlastParseError,
    FilterCriterion,
    RankedBlastHit,
    aggregate_hsps,
    filter_and_rank_hits,
    parse_blast_tabular,
)
from enzynotation.provenance import (
    SoftwareProvenance,
    canonical_json_sha256,
    sha256_bytes,
    sha256_file,
    utc_now,
)
from enzynotation.stages.base import Stage, StageContext, StageResult
from enzynotation.state import atomic_write_bytes, atomic_write_json, atomic_write_text
from enzynotation.tools.blast import BlastConfig, BlastTool

_PARSED_FIELDS = (
    "query_id",
    "subject_id",
    "subject_accession",
    "metadata_mapped",
    "protein_name",
    "ec_numbers",
    "rank",
    "retained_rank",
    "filter_passed",
    "filter_reasons",
    "percent_identity",
    "aligned_length",
    "query_coverage",
    "subject_coverage",
    "evalue",
    "bit_score",
    "hsp_count",
    "hsp_alignment_length_sum",
    "hsp_bit_score_sum",
    "raw_line_numbers",
)


class BlastStage(Stage):
    """Run BLASTp and emit homology and correlated annotation evidence."""

    def __init__(self, config: BlastConfig) -> None:
        super().__init__(
            "blast",
            dependencies=("validate",),
            required=config.required,
            implementation_version="1",
        )
        self.blast_config = config
        self.tool = BlastTool(config)

    def input_files(self, context: StageContext) -> dict[str, Path]:
        """Declare normalized FASTA, metadata, and database artifacts."""

        return {
            "normalized_fasta": context.paths.normalized_fasta,
            "curation_metadata": self.blast_config.metadata,
            **self.tool.database_artifacts(),
        }

    def configuration(self, context: StageContext) -> dict[str, Any]:
        """Use the full BLAST provider configuration as the cache input."""

        return self.blast_config.to_dict()

    @staticmethod
    def _archive_existing(path: Path, history: Path) -> None:
        if not path.is_file():
            return
        digest = sha256_file(path)
        archive_directory = history / "artifacts"
        archive = archive_directory / f"{path.name}.{digest}"
        if not archive.exists():
            atomic_write_bytes(archive, path.read_bytes())

    @staticmethod
    def _parsed_tsv(
        ranked_hits: tuple[RankedBlastHit, ...], metadata: CurationIndex
    ) -> str:
        output = io.StringIO()
        writer = csv.DictWriter(output, fieldnames=_PARSED_FIELDS, delimiter="\t")
        writer.writeheader()
        for ranked in ranked_hits:
            hit = ranked.hit
            accession = normalize_subject_accession(hit.subject_id)
            annotation = metadata.lookup(hit.subject_id)
            writer.writerow(
                {
                    "query_id": hit.query_id,
                    "subject_id": hit.subject_id,
                    "subject_accession": accession,
                    "metadata_mapped": str(annotation is not None).lower(),
                    "protein_name": annotation.protein_name if annotation else "",
                    "ec_numbers": (
                        ";".join(annotation.ec_numbers) if annotation else ""
                    ),
                    "rank": ranked.rank,
                    "retained_rank": ranked.retained_rank or "",
                    "filter_passed": str(ranked.passed).lower(),
                    "filter_reasons": ";".join(ranked.filter_reasons),
                    "percent_identity": f"{hit.percent_identity:.6f}",
                    "aligned_length": hit.aligned_length,
                    "query_coverage": f"{hit.query_coverage:.8f}",
                    "subject_coverage": f"{hit.subject_coverage:.8f}",
                    "evalue": f"{hit.evalue:.12g}",
                    "bit_score": f"{hit.bit_score:.6f}",
                    "hsp_count": hit.hsp_count,
                    "hsp_alignment_length_sum": hit.hsp_alignment_length_sum,
                    "hsp_bit_score_sum": f"{hit.hsp_bit_score_sum:.6f}",
                    "raw_line_numbers": ",".join(
                        str(line) for line in hit.raw_line_numbers
                    ),
                }
            )
        return output.getvalue()

    @staticmethod
    def _criterion_record(criterion: FilterCriterion) -> dict[str, Any]:
        return {
            "criterion_id": criterion.criterion_id,
            "metric": criterion.metric,
            "operator": criterion.operator,
            "threshold": criterion.threshold,
            "observed_value": criterion.observed_value,
            "passed": criterion.passed,
        }

    @staticmethod
    def _candidate_ec(ec: str) -> dict[str, Any]:
        parts = ec.split(".")
        depth = next((index for index, part in enumerate(parts) if part == "-"), 4)
        return {
            "namespace": "EC",
            "ec": ec,
            "depth": depth,
            "completeness": "complete" if depth == 4 else "partial",
        }

    @staticmethod
    def _metrics(
        ranked: RankedBlastHit,
        *,
        accession: str,
        annotation: CuratedAnnotation | None,
    ) -> dict[str, Any]:
        hit = ranked.hit
        metrics: dict[str, Any] = {
            "subject_original_id": hit.subject_id,
            "subject_accession": accession,
            "metadata_mapped": annotation is not None,
            "percent_identity": hit.percent_identity,
            "query_coverage": hit.query_coverage,
            "subject_coverage": hit.subject_coverage,
            "alignment_length": hit.aligned_length,
            "evalue": hit.evalue,
            "bit_score": hit.bit_score,
            "hsp_count": hit.hsp_count,
            "hsp_alignment_length_sum": hit.hsp_alignment_length_sum,
            "hsp_bit_score_sum": hit.hsp_bit_score_sum,
            "hit_rank": ranked.rank,
        }
        if annotation is not None:
            metrics["annotation_status"] = annotation.annotation_status
            metrics["annotation_source_database"] = annotation.source_database
            metrics["annotation_database_version"] = annotation.database_version
            if annotation.curation_status:
                metrics["curation_status"] = annotation.curation_status
        return metrics

    def _provenance(
        self,
        *,
        context: StageContext,
        raw_output: Path,
        ranked: RankedBlastHit,
        software: SoftwareProvenance,
        command: tuple[str, ...],
        database_checksum: str,
        metadata_checksum: str,
        annotation: CuratedAnnotation | None,
    ) -> dict[str, Any]:
        hit = ranked.hit
        databases: list[dict[str, Any]] = [
            {
                "name": self.blast_config.database_name,
                "version": self.blast_config.database_version,
                "path": str(self.blast_config.database),
                "checksum": f"sha256:{database_checksum}",
            }
        ]
        if annotation is not None:
            databases.append(
                {
                    "name": annotation.source_database,
                    "version": annotation.database_version,
                    "path": str(self.blast_config.metadata),
                    "checksum": f"sha256:{metadata_checksum}",
                }
            )
        return {
            "run_id": context.run_id,
            "stage_id": self.stage_id,
            "generated_at": utc_now(),
            "parser": {
                "name": "enzynotation-blast-parser",
                "version": "1",
            },
            "tool": {
                "name": "blastp",
                "version": software.version,
                "executable": software.executable,
            },
            "databases": databases,
            "raw_artifact": {
                "path": context.paths.relative_artifact(raw_output),
                "format": "blast-tabular-6",
                "sha256": sha256_file(raw_output),
                "record_locator": "lines:"
                + ",".join(str(line) for line in hit.raw_line_numbers),
            },
            "configuration_sha256": context.configuration_checksum,
            "input_sha256": sha256_file(context.paths.normalized_fasta),
            "command": list(command),
        }

    def _evidence_records(
        self,
        *,
        context: StageContext,
        ranked_hits: tuple[RankedBlastHit, ...],
        metadata: CurationIndex,
        raw_output: Path,
        software: SoftwareProvenance,
        command: tuple[str, ...],
        database_checksum: str,
        metadata_checksum: str,
    ) -> list[dict[str, Any]]:
        options = FastaValidationOptions.from_mapping(context.config.section("input"))
        validated = validate_fasta(context.paths.normalized_fasta, options)
        if not validated.is_valid:
            raise BlastParseError(
                "normalized FASTA failed validation before BLAST parsing"
            )
        sequences = {record.identifier: record.sequence for record in validated.records}

        evidence: list[dict[str, Any]] = []
        for ranked in ranked_hits:
            if not ranked.passed:
                continue
            hit = ranked.hit
            sequence = sequences.get(hit.query_id)
            if sequence is None:
                raise BlastParseError(
                    f"BLAST output references unknown query ID {hit.query_id!r}"
                )
            accession = normalize_subject_accession(hit.subject_id)
            annotation = metadata.lookup(hit.subject_id)
            subject_digest = sha256_bytes(hit.subject_id.encode("utf-8"))[:16]
            correlation_group = (
                f"{self.blast_config.database_name}:{accession}:{subject_digest}"
            )
            common = {
                "schema_version": 1,
                "query": {
                    "query_id": hit.query_id,
                    "sequence_sha256": sha256_bytes(sequence.encode("ascii")),
                    "sequence_length": len(sequence),
                },
                "record_status": "observed",
                "metrics": self._metrics(
                    ranked, accession=accession, annotation=annotation
                ),
                "criteria": [
                    self._criterion_record(criterion) for criterion in ranked.criteria
                ],
                "reference": {
                    "accession": accession,
                    "description": (
                        annotation.protein_name
                        if annotation
                        else "No curated functional metadata mapping"
                    ),
                    "curated": annotation.is_curated if annotation else False,
                    **(
                        {"annotation_source": annotation.source_database}
                        if annotation
                        else {}
                    ),
                },
                "provenance": self._provenance(
                    context=context,
                    raw_output=raw_output,
                    ranked=ranked,
                    software=software,
                    command=command,
                    database_checksum=database_checksum,
                    metadata_checksum=metadata_checksum,
                    annotation=annotation,
                ),
            }
            homology = {
                **common,
                "evidence_id": (f"blast:{hit.query_id}:{subject_digest}:homology"),
                "source": {
                    "id": "blastp",
                    "evidence_class": "sequence_homology",
                    "correlation_group": correlation_group,
                },
                "assertion": {
                    "target": {
                        "type": "feature",
                        "id": "sequence_homology_hit",
                    },
                    "effect": "supports",
                },
                "message": (
                    "curated functional metadata mapped"
                    if annotation
                    else "subject lacks curated functional metadata mapping"
                ),
            }
            evidence.append(homology)

            if annotation is None:
                continue
            for ec_index, ec in enumerate(annotation.ec_numbers, start=1):
                transferred = {
                    **common,
                    "evidence_id": (
                        f"blast:{hit.query_id}:{subject_digest}:annotation:{ec_index}"
                    ),
                    "source": {
                        "id": "blastp",
                        "evidence_class": "curated_annotation",
                        "correlation_group": correlation_group,
                    },
                    "assertion": {
                        "target": {
                            "type": "ec",
                            "candidate_ec": self._candidate_ec(ec),
                        },
                        "effect": "supports",
                    },
                    "message": (
                        "candidate EC transferred from explicitly mapped curated "
                        "metadata; not a final prediction"
                    ),
                }
                evidence.append(transferred)
        return evidence

    @staticmethod
    def _write_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
        content = "".join(
            json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n"
            for record in records
        )
        atomic_write_text(path, content)

    def execute(self, context: StageContext) -> StageResult:
        """Execute BLASTp, normalize all hits, and emit canonical evidence."""

        stage_paths = context.paths.for_stage(self.stage_id)
        stage_paths.prepare()
        raw_output = stage_paths.raw / "blast_hits.tsv"
        parsed_output = stage_paths.normalized / "blast_hits.tsv"
        summary_output = stage_paths.normalized / "blast_summary.json"
        config_output = stage_paths.normalized / "blast_config.json"
        evidence_output = context.paths.evidence / "blast_evidence.jsonl"

        for path in (
            raw_output,
            parsed_output,
            summary_output,
            config_output,
            evidence_output,
        ):
            self._archive_existing(path, stage_paths.history)
            path.unlink(missing_ok=True)
        atomic_write_json(config_output, self.blast_config.to_dict())

        software = self.tool.capture_version(context.backend)
        try:
            metadata = load_curation_metadata(self.blast_config.metadata)
        except CurationError as exc:
            summary = {
                "schema_version": 1,
                "provider": "blastp",
                "status": "failed",
                "message": str(exc),
            }
            atomic_write_json(summary_output, summary)
            return StageResult(
                succeeded=False,
                outputs={"configuration": config_output, "summary": summary_output},
                software=(software,),
                message=f"curation metadata error: {exc}",
            )

        command_spec = self.tool.build_command(
            query=context.paths.normalized_fasta,
            output=raw_output,
        )
        command = context.backend.execute(
            command_spec,
            stdout_path=stage_paths.stdout,
            stderr_path=stage_paths.stderr,
        )
        if command.return_code != 0:
            summary = {
                "schema_version": 1,
                "provider": "blastp",
                "status": "failed",
                "message": f"blastp exited with code {command.return_code}",
                "command_return_code": command.return_code,
            }
            atomic_write_json(summary_output, summary)
            outputs = {
                "configuration": config_output,
                "summary": summary_output,
            }
            if raw_output.is_file():
                outputs["raw_hits"] = raw_output
            return StageResult(
                succeeded=False,
                outputs=outputs,
                commands=(command,),
                software=(software,),
                message=summary["message"],
            )

        if not raw_output.exists():
            atomic_write_text(raw_output, "")

        try:
            hsps = parse_blast_tabular(raw_output)
            aggregated = aggregate_hsps(hsps)
            ranked = filter_and_rank_hits(aggregated, self.blast_config.filters)
            atomic_write_text(parsed_output, self._parsed_tsv(ranked, metadata))
            database_checksums = {
                name: sha256_file(path)
                for name, path in self.tool.database_artifacts().items()
            }
            database_checksum = canonical_json_sha256(database_checksums)
            metadata_checksum = sha256_file(self.blast_config.metadata)
            evidence = self._evidence_records(
                context=context,
                ranked_hits=ranked,
                metadata=metadata,
                raw_output=raw_output,
                software=software,
                command=command.argv,
                database_checksum=database_checksum,
                metadata_checksum=metadata_checksum,
            )
            self._write_jsonl(evidence_output, evidence)
        except (BlastParseError, CurationError, OSError, ValueError) as exc:
            summary = {
                "schema_version": 1,
                "provider": "blastp",
                "status": "failed",
                "message": str(exc),
            }
            atomic_write_json(summary_output, summary)
            outputs = {
                "configuration": config_output,
                "raw_hits": raw_output,
                "summary": summary_output,
            }
            return StageResult(
                succeeded=False,
                outputs=outputs,
                commands=(command,),
                software=(software,),
                message=f"BLAST normalization failed: {exc}",
            )

        retained = [item for item in ranked if item.passed]
        unmapped = sum(
            1 for item in retained if metadata.lookup(item.hit.subject_id) is None
        )
        summary = {
            "schema_version": 1,
            "provider": "blastp",
            "status": "completed",
            "empty_result": len(hsps) == 0,
            "thresholds_validated": self.blast_config.thresholds_validated,
            "counts": {
                "raw_hsps": len(hsps),
                "aggregated_hits": len(aggregated),
                "retained_hits": len(retained),
                "filtered_hits": len(ranked) - len(retained),
                "unmapped_retained_hits": unmapped,
                "evidence_records": len(evidence),
                "queries_with_hits": len({hsp.qseqid for hsp in hsps}),
            },
            "filters": self.blast_config.to_dict()["filters"],
            "ranking": [
                "evalue_ascending",
                "bit_score_descending",
                "query_coverage_descending",
                "percent_identity_descending",
                "subject_id_ascending",
            ],
            "final_ec_prediction": None,
        }
        atomic_write_json(summary_output, summary)
        return StageResult(
            succeeded=True,
            outputs={
                "configuration": config_output,
                "raw_hits": raw_output,
                "parsed_hits": parsed_output,
                "evidence": evidence_output,
                "summary": summary_output,
            },
            commands=(command,),
            software=(software,),
            message=(
                f"BLAST completed with {len(retained)} retained hit(s); "
                "no final EC prediction was made"
            ),
        )
