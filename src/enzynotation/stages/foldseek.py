"""Foldseek structural-search stage using the provider-specific Compose adapter."""

from __future__ import annotations

import csv
import io
import json
import shutil
from pathlib import Path
from typing import Any

from enzynotation.fasta import FastaValidationOptions, validate_fasta
from enzynotation.parsers.foldseek import (
    FoldseekCriterion,
    FoldseekParseError,
    RankedFoldseekHit,
    aggregate_foldseek_alignments,
    parse_foldseek_tabular,
    rank_and_filter_foldseek_hits,
)
from enzynotation.provenance import (
    SoftwareProvenance,
    sha256_bytes,
    sha256_file,
    utc_now,
)
from enzynotation.stages.base import Stage, StageContext, StageResult
from enzynotation.state import atomic_write_bytes, atomic_write_json, atomic_write_text
from enzynotation.structure_mapping import (
    NormalizedQueryStructure,
    StructureReference,
    load_normalized_structure_manifest,
    load_structure_reference_metadata,
)
from enzynotation.structures import StructureError, structural_correlation_group
from enzynotation.tools.foldseek import FoldseekConfig, FoldseekTool

_HIT_FIELDS = (
    "query_id",
    "query_structure_id",
    "foldseek_query_id",
    "target_id",
    "reference_structure_id",
    "reference_chain_id",
    "reference_model_index",
    "metadata_mapped",
    "protein_accession",
    "protein_name",
    "ec_numbers",
    "reference_structure_path",
    "rank",
    "retained_rank",
    "filter_passed",
    "filter_reasons",
    "percent_identity",
    "aligned_length",
    "query_coverage",
    "target_coverage",
    "evalue",
    "bit_score",
    "alignment_tm_score",
    "query_tm_score",
    "target_tm_score",
    "lddt",
    "alignment_count",
    "alignment_length_sum",
    "raw_line_numbers",
)


class FoldseekStage(Stage):
    """Run broad structural search and emit correlated structural evidence."""

    parser_version = "1"

    def __init__(self, config: FoldseekConfig) -> None:
        super().__init__(
            "foldseek",
            dependencies=("structures",),
            required=config.required,
            implementation_version="1",
        )
        self.foldseek_config = config
        self.tool = FoldseekTool(config)

    def _manifest(self, context: StageContext) -> Path:
        return (
            context.paths.for_stage("structures").normalized / "structure_manifest.tsv"
        )

    def input_files(self, context: StageContext) -> dict[str, Path]:
        files = {"structure_manifest": self._manifest(context)}
        metadata = self.foldseek_config.database.metadata
        if metadata.is_file():
            files["reference_metadata"] = metadata
        compose = self.foldseek_config.docker.compose_file
        if compose.is_file():
            files["foldseek_compose"] = compose
        fingerprint = self.foldseek_config.database.fingerprint
        if fingerprint is not None and fingerprint.is_file():
            files["foldseek_database_fingerprint"] = fingerprint
        try:
            structures = load_normalized_structure_manifest(
                self._manifest(context), run_root=context.paths.run_root
            )
        except StructureError:
            structures = ()
        for index, structure in enumerate(structures, start=1):
            if structure.staged_path.is_file():
                files[f"query_structure_{index:04d}"] = structure.staged_path
        return files

    def configuration(self, context: StageContext) -> dict[str, Any]:
        return {
            **self.foldseek_config.to_dict(),
            "parser_implementation_version": self.parser_version,
        }

    @staticmethod
    def _failure(
        summary: Path,
        evidence: Path,
        config: Path,
        code: str,
        message: str,
        *,
        commands=(),
        software=(),
        raw: Path | None = None,
    ) -> StageResult:
        atomic_write_json(
            summary,
            {
                "schema_version": 1,
                "provider": "foldseek",
                "status": "failed",
                "reason_code": code,
                "message": message,
                "final_ec_prediction": None,
            },
        )
        atomic_write_text(evidence, "")
        outputs = {"configuration": config, "summary": summary, "evidence": evidence}
        if raw is not None and raw.is_file():
            outputs["raw_hits"] = raw
        return StageResult(
            False,
            outputs,
            commands=commands,
            software=software,
            message=f"{code}: {message}",
        )

    @staticmethod
    def _structure_lookup(
        structures: tuple[NormalizedQueryStructure, ...], foldseek_id: str
    ) -> NormalizedQueryStructure | None:
        candidates = {foldseek_id, Path(foldseek_id).name, Path(foldseek_id).stem}
        for structure in structures:
            if structure.foldseek_query_id in candidates:
                return structure
        return None

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
    def _criterion(value: FoldseekCriterion) -> dict[str, Any]:
        return {
            "criterion_id": value.criterion_id,
            "metric": value.metric,
            "operator": value.operator,
            "threshold": value.threshold,
            "observed_value": value.observed_value,
            "passed": value.passed,
        }

    def _parsed_tsv(
        self,
        ranked: tuple[RankedFoldseekHit, ...],
        structures: tuple[NormalizedQueryStructure, ...],
        metadata,
        context: StageContext,
    ) -> str:
        buffer = io.StringIO()
        writer = csv.DictWriter(buffer, fieldnames=_HIT_FIELDS, delimiter="\t")
        writer.writeheader()
        for item in ranked:
            hit = item.hit
            query = self._structure_lookup(structures, hit.query_id)
            annotation = metadata.lookup(hit.target_id)
            writer.writerow(
                {
                    "query_id": query.query_id if query else "",
                    "query_structure_id": query.structure_id if query else "",
                    "foldseek_query_id": hit.query_id,
                    "target_id": hit.target_id,
                    "reference_structure_id": (
                        annotation.structure_id if annotation else hit.target_id
                    ),
                    "reference_chain_id": annotation.chain_id if annotation else "",
                    "reference_model_index": (
                        annotation.model_index if annotation else ""
                    ),
                    "metadata_mapped": str(annotation is not None).lower(),
                    "protein_accession": (
                        annotation.protein_accession if annotation else ""
                    ),
                    "protein_name": annotation.protein_name if annotation else "",
                    "ec_numbers": ";".join(annotation.ec_numbers) if annotation else "",
                    "reference_structure_path": (
                        str(annotation.structure_path)
                        if annotation and annotation.structure_path
                        else ""
                    ),
                    "rank": item.rank,
                    "retained_rank": item.retained_rank or "",
                    "filter_passed": str(item.passed).lower(),
                    "filter_reasons": ";".join(item.filter_reasons),
                    "percent_identity": f"{hit.percent_identity:.8f}",
                    "aligned_length": hit.aligned_length,
                    "query_coverage": f"{hit.query_coverage:.8f}",
                    "target_coverage": f"{hit.target_coverage:.8f}",
                    "evalue": f"{hit.evalue:.12g}",
                    "bit_score": f"{hit.bit_score:.8f}",
                    "alignment_tm_score": f"{hit.alignment_tm_score:.8f}",
                    "query_tm_score": f"{hit.query_tm_score:.8f}",
                    "target_tm_score": f"{hit.target_tm_score:.8f}",
                    "lddt": f"{hit.lddt:.8f}",
                    "alignment_count": hit.alignment_count,
                    "alignment_length_sum": hit.alignment_length_sum,
                    "raw_line_numbers": ",".join(
                        str(value) for value in hit.raw_line_numbers
                    ),
                }
            )
        return buffer.getvalue()

    def _provenance(
        self,
        context: StageContext,
        raw: Path,
        item: RankedFoldseekHit,
        software: SoftwareProvenance,
        command: tuple[str, ...],
        annotation: StructureReference | None,
    ) -> dict[str, Any]:
        database: dict[str, Any] = {
            "name": self.foldseek_config.database.name,
            "version": self.foldseek_config.database.version,
            "path": str(self.foldseek_config.database.path),
        }
        fingerprint = self.foldseek_config.database.fingerprint
        if fingerprint is not None and fingerprint.is_file():
            database["checksum"] = f"sha256:{sha256_file(fingerprint)}"
        databases = [database]
        if annotation is not None:
            databases.append(
                {
                    "name": annotation.source_database,
                    "version": annotation.database_version,
                    "path": str(self.foldseek_config.database.metadata),
                    "checksum": (
                        f"sha256:{sha256_file(self.foldseek_config.database.metadata)}"
                    ),
                }
            )
        tool: dict[str, Any] = {
            "name": "foldseek",
            "version": software.version,
            "executable": software.executable,
            "container_image": self.foldseek_config.docker.image,
        }
        if self.foldseek_config.docker.image_digest is not None:
            tool["container_digest"] = self.foldseek_config.docker.image_digest
        return {
            "run_id": context.run_id,
            "stage_id": self.stage_id,
            "generated_at": utc_now(),
            "parser": {"name": "enzynotation-foldseek-parser", "version": "1"},
            "tool": tool,
            "databases": databases,
            "raw_artifact": {
                "path": context.paths.relative_artifact(raw),
                "format": "foldseek-tabular",
                "sha256": sha256_file(raw),
                "record_locator": "lines:"
                + ",".join(str(value) for value in item.hit.raw_line_numbers),
            },
            "configuration_sha256": context.configuration_checksum,
            "input_sha256": sha256_file(context.paths.normalized_fasta),
            "command": list(command),
        }

    def _evidence(
        self,
        *,
        context: StageContext,
        ranked: tuple[RankedFoldseekHit, ...],
        structures: tuple[NormalizedQueryStructure, ...],
        metadata,
        raw: Path,
        software: SoftwareProvenance,
        command: tuple[str, ...],
    ) -> list[dict[str, Any]]:
        options = FastaValidationOptions.from_mapping(context.config.section("input"))
        validated = validate_fasta(context.paths.normalized_fasta, options)
        sequences = {record.identifier: record.sequence for record in validated.records}
        evidence: list[dict[str, Any]] = []
        for item in ranked:
            if not item.passed:
                continue
            hit = item.hit
            query = self._structure_lookup(structures, hit.query_id)
            if query is None:
                raise FoldseekParseError(
                    "Foldseek output references unknown query structure "
                    f"{hit.query_id!r}"
                )
            sequence = sequences[query.query_id]
            annotation = metadata.lookup(hit.target_id)
            reference_id = annotation.structure_id if annotation else hit.target_id
            reference_chain = annotation.chain_id if annotation else None
            correlation = structural_correlation_group(
                query.query_id, reference_id, reference_chain
            )
            digest = sha256_bytes(
                f"{query.structure_id}\0{reference_id}\0{reference_chain}".encode()
            )[:20]
            metrics = {
                "query_structure_id": query.structure_id,
                "query_structure_chain": query.chain_id,
                "query_structure_model_index": query.model_index,
                "query_structure_sha256": query.structure_sha256,
                "reference_structure_id": reference_id,
                "reference_chain_id": reference_chain,
                "reference_model_index": (
                    annotation.model_index if annotation else None
                ),
                "metadata_mapped": annotation is not None,
                "percent_identity": hit.percent_identity,
                "aligned_length": hit.aligned_length,
                "query_coverage": hit.query_coverage,
                "target_coverage": hit.target_coverage,
                "evalue": hit.evalue,
                "bit_score": hit.bit_score,
                "alignment_tm_score": hit.alignment_tm_score,
                "query_tm_score": hit.query_tm_score,
                "target_tm_score": hit.target_tm_score,
                "lddt": hit.lddt,
                "hit_rank": item.rank,
                "retained_rank": item.retained_rank,
                "threshold_status": self.foldseek_config.filters.threshold_status,
            }
            common = {
                "schema_version": 1,
                "query": {
                    "query_id": query.query_id,
                    "sequence_sha256": sha256_bytes(sequence.encode("ascii")),
                    "sequence_length": len(sequence),
                },
                "record_status": "observed",
                "metrics": metrics,
                "criteria": [self._criterion(value) for value in item.criteria],
                "reference": {
                    "accession": reference_id,
                    "description": (
                        annotation.protein_name
                        if annotation and annotation.protein_name
                        else "structural reference without curated functional metadata"
                    ),
                    "curated": annotation.curated if annotation else False,
                    **(
                        {"annotation_source": annotation.source_database}
                        if annotation
                        else {}
                    ),
                },
                "provenance": self._provenance(
                    context, raw, item, software, command, annotation
                ),
            }
            evidence.append(
                {
                    **common,
                    "evidence_id": f"foldseek:{query.query_id}:{digest}:similarity",
                    "source": {
                        "id": "foldseek",
                        "evidence_class": "structure_homology",
                        "correlation_group": correlation,
                    },
                    "assertion": {
                        "target": {
                            "type": "feature",
                            "id": "structural_similarity_hit",
                        },
                        "effect": "supports",
                    },
                    "message": (
                        "Foldseek structural similarity; not a final EC prediction"
                    ),
                }
            )
            if annotation is None:
                continue
            for ec_index, ec in enumerate(annotation.ec_numbers, start=1):
                evidence.append(
                    {
                        **common,
                        "evidence_id": (
                            f"foldseek:{query.query_id}:{digest}:annotation:{ec_index}"
                        ),
                        "source": {
                            "id": "foldseek",
                            "evidence_class": "curated_annotation",
                            "correlation_group": correlation,
                        },
                        "assertion": {
                            "target": {
                                "type": "ec",
                                "candidate_ec": self._candidate_ec(ec),
                            },
                            "effect": "supports",
                        },
                        "message": (
                            "candidate EC from explicit structural-reference metadata; "
                            "not a final prediction"
                        ),
                    }
                )
        return evidence

    def execute(self, context: StageContext) -> StageResult:
        stage_paths = context.paths.for_stage(self.stage_id)
        stage_paths.prepare()
        raw = stage_paths.raw / "foldseek.tsv"
        parsed_output = stage_paths.normalized / "foldseek_hits.tsv"
        summary = stage_paths.normalized / "foldseek_summary.json"
        config_output = stage_paths.normalized / "foldseek_config.json"
        evidence_output = context.paths.evidence / "foldseek_evidence.jsonl"
        atomic_write_json(config_output, self.configuration(context))
        atomic_write_text(evidence_output, "")
        config = self.foldseek_config
        if not config.docker.compose_file.is_file():
            return self._failure(
                summary,
                evidence_output,
                config_output,
                "foldseek_compose_unavailable",
                f"Compose file does not exist: {config.docker.compose_file}",
            )
        if not config.database.path.exists():
            return self._failure(
                summary,
                evidence_output,
                config_output,
                "foldseek_database_unavailable",
                f"database path does not exist: {config.database.path}",
            )
        if not config.database.metadata.is_file():
            return self._failure(
                summary,
                evidence_output,
                config_output,
                "structure_metadata_unavailable",
                f"metadata file does not exist: {config.database.metadata}",
            )
        if (
            config.database.fingerprint is not None
            and not config.database.fingerprint.is_file()
        ):
            return self._failure(
                summary,
                evidence_output,
                config_output,
                "foldseek_database_fingerprint_unavailable",
                f"fingerprint does not exist: {config.database.fingerprint}",
            )
        try:
            structures = load_normalized_structure_manifest(
                self._manifest(context), run_root=context.paths.run_root
            )
            metadata = load_structure_reference_metadata(config.database.metadata)
        except StructureError as exc:
            return self._failure(
                summary,
                evidence_output,
                config_output,
                "malformed_structure_metadata",
                str(exc),
            )
        runtime = self.tool.capture_runtime_versions(context.backend)
        docker, compose, image = runtime
        if docker.version_return_code not in {None, 0}:
            return self._failure(
                summary,
                evidence_output,
                config_output,
                "docker_runtime_unavailable",
                "Docker version could not be captured",
                software=runtime,
            )
        if compose.version_return_code not in {None, 0}:
            return self._failure(
                summary,
                evidence_output,
                config_output,
                "docker_compose_unavailable",
                "Docker Compose version could not be captured",
                software=runtime,
            )
        if image.version_return_code not in {None, 0}:
            return self._failure(
                summary,
                evidence_output,
                config_output,
                "foldseek_image_unavailable",
                "configured Foldseek image metadata is unavailable",
                software=runtime,
            )
        output_directory = stage_paths.raw / "work-output"
        temporary_directory = stage_paths.raw / "work-tmp"
        shutil.rmtree(output_directory, ignore_errors=True)
        shutil.rmtree(temporary_directory, ignore_errors=True)
        output_directory.mkdir(parents=True, exist_ok=True)
        temporary_directory.mkdir(parents=True, exist_ok=True)
        generated = output_directory / "foldseek.tsv"
        generated.unlink(missing_ok=True)
        command = context.backend.execute(
            self.tool.build_command(
                query_directory=(
                    context.paths.for_stage("structures").normalized / "query"
                ),
                output_directory=output_directory,
                temporary_directory=temporary_directory,
            ),
            stdout_path=stage_paths.stdout,
            stderr_path=stage_paths.stderr,
        )
        software = (*runtime, self.tool.foldseek_software())
        if command.return_code != 0:
            stderr = (
                Path(command.stderr_path).read_text(encoding="utf-8")
                if Path(command.stderr_path).is_file()
                else ""
            )
            code = (
                "foldseek_image_unavailable"
                if "no such image" in stderr.lower()
                else (
                    "foldseek_database_malformed"
                    if "database" in stderr.lower()
                    and any(
                        marker in stderr.lower()
                        for marker in ("invalid", "malformed", "cannot open", "corrupt")
                    )
                    else "foldseek_command_failed"
                )
            )
            return self._failure(
                summary,
                evidence_output,
                config_output,
                code,
                f"Foldseek exited with code {command.return_code}",
                commands=(command,),
                software=software,
            )
        if not generated.is_file():
            return self._failure(
                summary,
                evidence_output,
                config_output,
                "foldseek_output_missing",
                "Foldseek did not create its configured output",
                commands=(command,),
                software=software,
            )
        atomic_write_bytes(raw, generated.read_bytes())
        try:
            alignments = parse_foldseek_tabular(raw)
            hits = aggregate_foldseek_alignments(alignments)
            ranked = rank_and_filter_foldseek_hits(hits, config.filters)
            atomic_write_text(
                parsed_output,
                self._parsed_tsv(ranked, structures, metadata, context),
            )
            evidence = self._evidence(
                context=context,
                ranked=ranked,
                structures=structures,
                metadata=metadata,
                raw=raw,
                software=self.tool.foldseek_software(),
                command=command.argv,
            )
            atomic_write_text(
                evidence_output,
                "".join(
                    json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n"
                    for record in evidence
                ),
            )
        except (FoldseekParseError, StructureError, OSError, KeyError) as exc:
            return self._failure(
                summary,
                evidence_output,
                config_output,
                "malformed_foldseek_output",
                str(exc),
                commands=(command,),
                software=software,
                raw=raw,
            )
        retained = [item for item in ranked if item.passed]
        unmapped = sum(
            1 for item in retained if metadata.lookup(item.hit.target_id) is None
        )
        atomic_write_json(
            summary,
            {
                "schema_version": 1,
                "provider": "foldseek",
                "status": "completed",
                "empty_result": not alignments,
                "counts": {
                    "raw_alignments": len(alignments),
                    "aggregated_hits": len(hits),
                    "retained_hits": len(retained),
                    "unmapped_retained_hits": unmapped,
                    "evidence_records": len(evidence),
                },
                "threshold_status": config.filters.threshold_status,
                "final_ec_prediction": None,
            },
        )
        return StageResult(
            True,
            {
                "configuration": config_output,
                "raw_hits": raw,
                "parsed_hits": parsed_output,
                "summary": summary,
                "evidence": evidence_output,
            },
            commands=(command,),
            software=software,
            message=f"Foldseek completed with {len(retained)} retained hit(s)",
        )
