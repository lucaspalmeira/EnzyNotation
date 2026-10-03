"""Validation and canonical staging for user-supplied query structures."""

from __future__ import annotations

import csv
import io
from pathlib import Path
from typing import Any

from enzynotation.fasta import FastaValidationOptions, validate_fasta
from enzynotation.provenance import sha256_bytes, sha256_file
from enzynotation.stages.base import Stage, StageContext, StageResult
from enzynotation.state import atomic_write_json, atomic_write_text
from enzynotation.structure_mapping import (
    StructuresConfig,
    load_query_structure_manifest,
)
from enzynotation.structures import (
    StructureError,
    compare_structure_sequence,
    validate_structure,
)

_FIELDS = (
    "query_id",
    "structure_id",
    "original_path",
    "staged_path",
    "structure_format",
    "chain_id",
    "model_index",
    "model_identifier",
    "structure_source",
    "structure_source_version",
    "prediction_method",
    "prediction_model_version",
    "quality_metadata",
    "query_sequence_sha256",
    "declared_sequence_sha256",
    "structure_sha256",
    "structure_sequence",
    "structure_residue_count",
    "atom_count",
    "sequence_match_status",
    "sequence_match_message",
    "foldseek_query_id",
)


class StructuresStage(Stage):
    """Validate mappings and stage selected structures without prediction."""

    def __init__(self, config: StructuresConfig) -> None:
        super().__init__(
            "structures",
            dependencies=("validate",),
            required=config.required,
            implementation_version="1",
        )
        self.structures_config = config

    def input_files(self, context: StageContext) -> dict[str, Path]:
        files: dict[str, Path] = {}
        manifest = self.structures_config.manifest
        if manifest.is_file():
            files["structure_manifest"] = manifest
            try:
                records = load_query_structure_manifest(manifest)
            except StructureError:
                records = ()
            for index, record in enumerate(records, start=1):
                if record.structure_path.is_file():
                    files[f"query_structure_{index:04d}"] = record.structure_path
        return files

    def configuration(self, context: StageContext) -> dict[str, Any]:
        return self.structures_config.to_dict()

    @staticmethod
    def _failure(
        summary: Path, output: Path, config: Path, code: str, message: str
    ) -> StageResult:
        atomic_write_json(
            summary,
            {
                "schema_version": 1,
                "provider": "supplied_structures",
                "status": "failed",
                "reason_code": code,
                "message": message,
                "final_ec_prediction": None,
            },
        )
        atomic_write_text(output, "\t".join(_FIELDS) + "\n")
        return StageResult(
            False,
            {
                "configuration": config,
                "structure_manifest": output,
                "summary": summary,
            },
            message=f"{code}: {message}",
        )

    def execute(self, context: StageContext) -> StageResult:
        stage_paths = context.paths.for_stage(self.stage_id)
        stage_paths.prepare()
        output = stage_paths.normalized / "structure_manifest.tsv"
        summary = stage_paths.normalized / "structure_summary.json"
        config_output = stage_paths.normalized / "structures_config.json"
        query_directory = stage_paths.normalized / "query"
        query_directory.mkdir(parents=True, exist_ok=True)
        for child in query_directory.iterdir():
            if child.is_file():
                child.unlink()
        atomic_write_json(config_output, self.structures_config.to_dict())
        try:
            records = load_query_structure_manifest(self.structures_config.manifest)
        except StructureError as exc:
            return self._failure(
                summary,
                output,
                config_output,
                "malformed_structure_manifest",
                str(exc),
            )
        if not records:
            return self._failure(
                summary,
                output,
                config_output,
                "no_structure_supplied",
                "manifest contains no structures",
            )
        options = FastaValidationOptions.from_mapping(context.config.section("input"))
        validated_fasta = validate_fasta(context.paths.normalized_fasta, options)
        sequences = {
            record.identifier: record.sequence for record in validated_fasta.records
        }
        rendered_rows: list[dict[str, Any]] = []
        staged_outputs: dict[str, Path] = {}
        for index, record in enumerate(records, start=1):
            sequence = sequences.get(record.query_id)
            if sequence is None:
                return self._failure(
                    summary,
                    output,
                    config_output,
                    "missing_query_mapping",
                    f"unknown query ID {record.query_id!r}",
                )
            try:
                selection = validate_structure(
                    record.structure_path,
                    structure_format=record.structure_format,
                    chain_id=record.chain_id,
                    model_index=record.model_index,
                )
            except StructureError as exc:
                message = str(exc)
                if "does not exist" in message:
                    code = "structure_file_missing"
                elif "unsupported" in message or "does not match" in message:
                    code = "unsupported_structure_format"
                elif "chain" in message:
                    code = "missing_structure_chain"
                elif "model" in message:
                    code = "missing_structure_model"
                else:
                    code = "malformed_structure"
                return self._failure(summary, output, config_output, code, message)
            suffix = ".pdb" if selection.structure_format == "pdb" else ".cif"
            foldseek_id = f"query-structure-{index:04d}"
            staged = query_directory / f"{foldseek_id}{suffix}"
            atomic_write_text(staged, selection.selected_content)
            query_digest = sha256_bytes(sequence.encode("ascii"))
            match_status, match_message = compare_structure_sequence(
                sequence, selection.sequence
            )
            if record.sequence_sha256 and record.sequence_sha256 != query_digest:
                match_status = "declared_checksum_mismatch"
                match_message = "manifest sequence checksum differs from query FASTA"
            rendered_rows.append(
                {
                    "query_id": record.query_id,
                    "structure_id": record.structure_id,
                    "original_path": str(record.structure_path),
                    "staged_path": context.paths.relative_artifact(staged),
                    "structure_format": selection.structure_format,
                    "chain_id": selection.chain_id,
                    "model_index": selection.model_index,
                    "model_identifier": selection.model_identifier,
                    "structure_source": record.structure_source,
                    "structure_source_version": record.structure_source_version or "",
                    "prediction_method": record.prediction_method or "",
                    "prediction_model_version": record.prediction_model_version or "",
                    "quality_metadata": record.quality_metadata or "",
                    "query_sequence_sha256": query_digest,
                    "declared_sequence_sha256": record.sequence_sha256 or "",
                    "structure_sha256": sha256_file(staged),
                    "structure_sequence": selection.sequence,
                    "structure_residue_count": selection.residue_count,
                    "atom_count": selection.atom_count,
                    "sequence_match_status": match_status,
                    "sequence_match_message": match_message,
                    "foldseek_query_id": foldseek_id,
                }
            )
            staged_outputs[f"query_structure_{index:04d}"] = staged
        buffer = io.StringIO()
        writer = csv.DictWriter(buffer, fieldnames=_FIELDS, delimiter="\t")
        writer.writeheader()
        writer.writerows(rendered_rows)
        atomic_write_text(output, buffer.getvalue())
        counts: dict[str, int] = {}
        for row in rendered_rows:
            status = str(row["sequence_match_status"])
            counts[status] = counts.get(status, 0) + 1
        atomic_write_json(
            summary,
            {
                "schema_version": 1,
                "provider": "supplied_structures",
                "status": "completed",
                "counts": {"structures": len(rendered_rows), **counts},
                "coordinate_modification": False,
                "final_ec_prediction": None,
            },
        )
        return StageResult(
            True,
            {
                "configuration": config_output,
                "structure_manifest": output,
                "summary": summary,
                **staged_outputs,
            },
            message=f"validated {len(rendered_rows)} supplied structure(s)",
        )
