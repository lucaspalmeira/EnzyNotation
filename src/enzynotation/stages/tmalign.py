"""Selective TM-align confirmation of retained Foldseek structural pairs."""

from __future__ import annotations

import csv
import io
import json
import shutil
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from enzynotation.fasta import FastaValidationOptions, validate_fasta
from enzynotation.parsers.tmalign import (
    TMAlignParseError,
    TMAlignResult,
    parse_tmalign_output,
)
from enzynotation.provenance import sha256_bytes, sha256_file, utc_now
from enzynotation.stages.base import Stage, StageContext, StageResult
from enzynotation.state import atomic_write_json, atomic_write_text
from enzynotation.structure_mapping import (
    NormalizedQueryStructure,
    StructureReference,
    load_normalized_structure_manifest,
    load_structure_reference_metadata,
)
from enzynotation.structures import (
    StructureError,
    structural_correlation_group,
    validate_structure,
)
from enzynotation.tools.foldseek import FoldseekConfig
from enzynotation.tools.tmalign import TMAlignConfig, TMAlignTool

_RESULT_FIELDS = (
    "query_id",
    "query_structure_id",
    "reference_structure_id",
    "reference_chain_id",
    "reference_model_index",
    "foldseek_rank",
    "query_length",
    "target_length",
    "aligned_length",
    "query_coverage",
    "target_coverage",
    "rmsd",
    "sequence_identity_in_alignment",
    "tm_score_normalized_by_query",
    "tm_score_normalized_by_target",
    "query_structure_sha256",
    "reference_structure_sha256",
    "raw_output",
)


@dataclass(frozen=True, slots=True)
class _SelectedPair:
    query: NormalizedQueryStructure
    reference: StructureReference
    target_id: str
    foldseek_rank: int
    query_coverage: float
    target_coverage: float


class TMAlignStage(Stage):
    """Run TM-align only for configured top retained Foldseek candidates."""

    parser_version = "1"

    def __init__(self, config: TMAlignConfig, *, foldseek: FoldseekConfig) -> None:
        super().__init__(
            "tmalign",
            dependencies=("foldseek",),
            required=config.required,
            implementation_version="1",
        )
        self.tmalign_config = config
        self.foldseek_config = foldseek
        self.tool = TMAlignTool(config)

    def _structure_manifest(self, context: StageContext) -> Path:
        return (
            context.paths.for_stage("structures").normalized / "structure_manifest.tsv"
        )

    def _foldseek_hits(self, context: StageContext) -> Path:
        return context.paths.for_stage("foldseek").normalized / "foldseek_hits.tsv"

    def _selected_pairs(self, context: StageContext) -> tuple[_SelectedPair, ...]:
        structures = load_normalized_structure_manifest(
            self._structure_manifest(context), run_root=context.paths.run_root
        )
        by_foldseek = {item.foldseek_query_id: item for item in structures}
        metadata = load_structure_reference_metadata(
            self.foldseek_config.database.metadata
        )
        try:
            handle = self._foldseek_hits(context).open(
                "r", encoding="utf-8", newline=""
            )
        except OSError as exc:
            raise StructureError(f"cannot read Foldseek hits: {exc}") from exc

        candidates: dict[str, list[_SelectedPair]] = defaultdict(list)
        with handle:
            reader = csv.DictReader(handle, delimiter="\t")
            required = {
                "foldseek_query_id",
                "target_id",
                "rank",
                "filter_passed",
                "query_coverage",
                "target_coverage",
            }
            if reader.fieldnames is None or not required.issubset(reader.fieldnames):
                raise StructureError("normalized Foldseek hits have an invalid header")
            for row_number, row in enumerate(reader, start=2):
                if row["filter_passed"].lower() != "true":
                    continue
                try:
                    query_coverage = float(row["query_coverage"])
                    target_coverage = float(row["target_coverage"])
                    rank = int(row["rank"])
                except ValueError as exc:
                    raise StructureError(
                        f"Foldseek hits row {row_number} has malformed metrics"
                    ) from exc
                selection = self.tmalign_config.selection
                if (
                    query_coverage < selection.minimum_query_coverage
                    or target_coverage < selection.minimum_target_coverage
                ):
                    continue
                foldseek_id = Path(row["foldseek_query_id"]).stem
                query = by_foldseek.get(foldseek_id)
                if query is None:
                    raise StructureError(
                        f"Foldseek hits reference unknown query {foldseek_id!r}"
                    )
                reference = metadata.lookup(row["target_id"])
                if reference is None:
                    continue
                candidates[query.query_id].append(
                    _SelectedPair(
                        query=query,
                        reference=reference,
                        target_id=row["target_id"],
                        foldseek_rank=rank,
                        query_coverage=query_coverage,
                        target_coverage=target_coverage,
                    )
                )

        selected: list[_SelectedPair] = []
        maximum = self.tmalign_config.selection.top_hits_per_query
        for query_id in sorted(candidates):
            ordered = sorted(
                candidates[query_id],
                key=lambda item: (item.foldseek_rank, item.reference.structure_id),
            )
            selected.extend(ordered if maximum is None else ordered[:maximum])
        return tuple(selected)

    def input_files(self, context: StageContext) -> dict[str, Path]:
        files = {
            "structure_manifest": self._structure_manifest(context),
            "foldseek_hits": self._foldseek_hits(context),
            "reference_metadata": self.foldseek_config.database.metadata,
        }
        executable = shutil.which(self.tmalign_config.executable)
        if executable and Path(executable).is_file():
            files["tmalign_executable"] = Path(executable)
        try:
            pairs = self._selected_pairs(context)
        except StructureError:
            pairs = ()
        for index, pair in enumerate(pairs, start=1):
            files[f"query_structure_{index:04d}"] = pair.query.staged_path
            reference_path = pair.reference.structure_path
            if reference_path is not None and reference_path.is_file():
                files[f"reference_structure_{index:04d}"] = reference_path
        return files

    def configuration(self, context: StageContext) -> dict[str, Any]:
        return {
            **self.tmalign_config.to_dict(),
            "parser_implementation_version": self.parser_version,
        }

    @staticmethod
    def _failure(
        summary: Path,
        results: Path,
        evidence: Path,
        config: Path,
        code: str,
        message: str,
        *,
        commands=(),
        software=(),
        outputs: dict[str, Path] | None = None,
    ) -> StageResult:
        atomic_write_json(
            summary,
            {
                "schema_version": 1,
                "provider": "tmalign",
                "status": "failed",
                "reason_code": code,
                "message": message,
                "final_ec_prediction": None,
            },
        )
        if not results.is_file():
            atomic_write_text(results, "\t".join(_RESULT_FIELDS) + "\n")
        if not evidence.is_file():
            atomic_write_text(evidence, "")
        return StageResult(
            False,
            {
                "configuration": config,
                "results": results,
                "summary": summary,
                "evidence": evidence,
                **(outputs or {}),
            },
            commands=commands,
            software=software,
            message=f"{code}: {message}",
        )

    def _evidence_record(
        self,
        *,
        context: StageContext,
        pair: _SelectedPair,
        result: TMAlignResult,
        raw: Path,
        reference_path: Path,
        command,
        software,
        sequence: str,
    ) -> dict[str, Any]:
        reference = pair.reference
        correlation = structural_correlation_group(
            pair.query.query_id, reference.structure_id, reference.chain_id
        )
        digest = sha256_bytes(
            (
                f"{pair.query.structure_id}\0{reference.structure_id}\0"
                f"{reference.chain_id}"
            ).encode()
        )[:20]
        return {
            "schema_version": 1,
            "evidence_id": f"tmalign:{pair.query.query_id}:{digest}",
            "query": {
                "query_id": pair.query.query_id,
                "sequence_sha256": sha256_bytes(sequence.encode("ascii")),
                "sequence_length": len(sequence),
            },
            "source": {
                "id": "tmalign",
                "evidence_class": "structure_homology",
                "correlation_group": correlation,
            },
            "record_status": "observed",
            "assertion": {
                "target": {"type": "feature", "id": "structural_similarity_hit"},
                "effect": "supports",
            },
            "metrics": {
                "query_structure_id": pair.query.structure_id,
                "query_structure_chain": pair.query.chain_id,
                "query_structure_model_index": pair.query.model_index,
                "query_structure_sha256": sha256_file(pair.query.staged_path),
                "reference_structure_id": reference.structure_id,
                "reference_chain_id": reference.chain_id,
                "reference_model_index": reference.model_index,
                "reference_structure_sha256": sha256_file(reference_path),
                "foldseek_rank": pair.foldseek_rank,
                "foldseek_query_coverage": pair.query_coverage,
                "foldseek_target_coverage": pair.target_coverage,
                "query_length": result.query_length,
                "target_length": result.target_length,
                "aligned_length": result.aligned_length,
                "query_coverage": min(1.0, result.aligned_length / result.query_length),
                "target_coverage": min(
                    1.0, result.aligned_length / result.target_length
                ),
                "rmsd": result.rmsd,
                "sequence_identity_in_alignment": result.sequence_identity,
                "tm_score_normalized_by_query": (result.tm_score_normalized_by_query),
                "tm_score_normalized_by_target": (result.tm_score_normalized_by_target),
            },
            "criteria": [],
            "reference": {
                "accession": reference.structure_id,
                "description": reference.protein_name or "structural reference",
                "curated": reference.curated,
                "annotation_source": reference.source_database,
            },
            "provenance": {
                "run_id": context.run_id,
                "stage_id": self.stage_id,
                "generated_at": utc_now(),
                "parser": {
                    "name": "enzynotation-tmalign-parser",
                    "version": self.parser_version,
                },
                "tool": {
                    "name": "TMalign",
                    "version": software.version,
                    "executable": software.executable,
                },
                "databases": [
                    {
                        "name": reference.source_database,
                        "version": reference.database_version,
                        "path": str(self.foldseek_config.database.metadata),
                        "checksum": (
                            "sha256:"
                            + sha256_file(self.foldseek_config.database.metadata)
                        ),
                    }
                ],
                "raw_artifact": {
                    "path": context.paths.relative_artifact(raw),
                    "format": "tmalign-text",
                    "sha256": sha256_file(raw),
                    "record_locator": "complete-output",
                },
                "configuration_sha256": context.configuration_checksum,
                "input_sha256": sha256_file(context.paths.normalized_fasta),
                "command": list(command.argv),
            },
            "message": "TM-align structural similarity; not a final EC prediction",
        }

    def execute(self, context: StageContext) -> StageResult:
        stage_paths = context.paths.for_stage(self.stage_id)
        stage_paths.prepare()
        results_path = stage_paths.normalized / "tmalign_results.tsv"
        summary = stage_paths.normalized / "tmalign_summary.json"
        config_output = stage_paths.normalized / "tmalign_config.json"
        evidence_path = context.paths.evidence / "tmalign_evidence.jsonl"
        for stale in stage_paths.raw.glob("tmalign-*.txt"):
            stale.unlink()
        shutil.rmtree(stage_paths.raw / "references", ignore_errors=True)
        atomic_write_json(config_output, self.configuration(context))
        atomic_write_text(evidence_path, "")
        try:
            pairs = self._selected_pairs(context)
        except StructureError as exc:
            return self._failure(
                summary,
                results_path,
                evidence_path,
                config_output,
                "tmalign_selection_failed",
                str(exc),
            )

        if not pairs:
            atomic_write_text(results_path, "\t".join(_RESULT_FIELDS) + "\n")
            atomic_write_json(
                summary,
                {
                    "schema_version": 1,
                    "provider": "tmalign",
                    "status": "completed",
                    "zero_selected_pairs": True,
                    "counts": {"selected_pairs": 0, "results": 0},
                    "final_ec_prediction": None,
                },
            )
            return StageResult(
                True,
                {
                    "configuration": config_output,
                    "results": results_path,
                    "summary": summary,
                    "evidence": evidence_path,
                },
                message="no Foldseek pairs selected for TM-align",
            )

        software = self.tool.capture_version(context.backend)
        if software.version_return_code == 127:
            return self._failure(
                summary,
                results_path,
                evidence_path,
                config_output,
                "tmalign_executable_unavailable",
                f"TM-align executable is unavailable: {self.tmalign_config.executable}",
                software=(software,),
            )

        options = FastaValidationOptions.from_mapping(context.config.section("input"))
        fasta = validate_fasta(context.paths.normalized_fasta, options)
        sequences = {record.identifier: record.sequence for record in fasta.records}
        rows: list[dict[str, Any]] = []
        evidence: list[dict[str, Any]] = []
        commands = []
        raw_outputs: dict[str, Path] = {}
        failures: list[tuple[str, str]] = []
        stdout_chunks: list[str] = []
        stderr_chunks: list[str] = []
        references = stage_paths.raw / "references"
        references.mkdir(parents=True, exist_ok=True)

        for index, pair in enumerate(pairs, start=1):
            reference = pair.reference
            source = reference.structure_path
            if source is None or not source.is_file():
                failures.append(
                    ("reference_structure_unavailable", reference.structure_id)
                )
                continue
            structure_format = reference.structure_format or (
                "mmcif" if source.suffix.lower() in {".cif", ".mmcif"} else "pdb"
            )
            try:
                selection = validate_structure(
                    source,
                    structure_format=structure_format,
                    chain_id=reference.chain_id,
                    model_index=reference.model_index,
                )
            except StructureError as exc:
                failures.append(("reference_structure_unavailable", str(exc)))
                continue
            suffix = ".pdb" if selection.structure_format == "pdb" else ".cif"
            staged_reference = references / f"reference-{index:04d}{suffix}"
            atomic_write_text(staged_reference, selection.selected_content)
            raw = stage_paths.raw / f"tmalign-{index:04d}.txt"
            stderr = stage_paths.raw / f"tmalign-{index:04d}.stderr.txt"
            command = context.backend.execute(
                self.tool.build_pair_command(
                    query=pair.query.staged_path, reference=staged_reference
                ),
                stdout_path=raw,
                stderr_path=stderr,
            )
            commands.append(command)
            raw_outputs[f"raw_pair_{index:04d}"] = raw
            stdout_chunks.append(raw.read_text() if raw.is_file() else "")
            stderr_chunks.append(stderr.read_text() if stderr.is_file() else "")
            if command.return_code != 0:
                failures.append(("tmalign_command_failed", reference.structure_id))
                continue
            try:
                parsed = parse_tmalign_output(raw)
            except TMAlignParseError as exc:
                failures.append(("malformed_tmalign_output", str(exc)))
                continue
            query_coverage = min(1.0, parsed.aligned_length / parsed.query_length)
            target_coverage = min(1.0, parsed.aligned_length / parsed.target_length)
            rows.append(
                {
                    "query_id": pair.query.query_id,
                    "query_structure_id": pair.query.structure_id,
                    "reference_structure_id": reference.structure_id,
                    "reference_chain_id": reference.chain_id or "",
                    "reference_model_index": reference.model_index or "",
                    "foldseek_rank": pair.foldseek_rank,
                    "query_length": parsed.query_length,
                    "target_length": parsed.target_length,
                    "aligned_length": parsed.aligned_length,
                    "query_coverage": f"{query_coverage:.8f}",
                    "target_coverage": f"{target_coverage:.8f}",
                    "rmsd": f"{parsed.rmsd:.8f}",
                    "sequence_identity_in_alignment": (
                        f"{parsed.sequence_identity:.8f}"
                    ),
                    "tm_score_normalized_by_query": (
                        f"{parsed.tm_score_normalized_by_query:.8f}"
                    ),
                    "tm_score_normalized_by_target": (
                        f"{parsed.tm_score_normalized_by_target:.8f}"
                    ),
                    "query_structure_sha256": sha256_file(pair.query.staged_path),
                    "reference_structure_sha256": sha256_file(staged_reference),
                    "raw_output": context.paths.relative_artifact(raw),
                }
            )
            evidence.append(
                self._evidence_record(
                    context=context,
                    pair=pair,
                    result=parsed,
                    raw=raw,
                    reference_path=staged_reference,
                    command=command,
                    software=software,
                    sequence=sequences[pair.query.query_id],
                )
            )

        atomic_write_text(stage_paths.stdout, "\n".join(stdout_chunks))
        atomic_write_text(stage_paths.stderr, "\n".join(stderr_chunks))
        buffer = io.StringIO()
        writer = csv.DictWriter(buffer, fieldnames=_RESULT_FIELDS, delimiter="\t")
        writer.writeheader()
        writer.writerows(rows)
        atomic_write_text(results_path, buffer.getvalue())
        atomic_write_text(
            evidence_path,
            "".join(
                json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n"
                for record in evidence
            ),
        )
        atomic_write_json(
            summary,
            {
                "schema_version": 1,
                "provider": "tmalign",
                "status": "failed" if failures else "completed",
                "zero_selected_pairs": False,
                "counts": {
                    "selected_pairs": len(pairs),
                    "results": len(rows),
                    "failed_pairs": len(failures),
                    "evidence_records": len(evidence),
                },
                "failures": [
                    {"reason_code": code, "message": message}
                    for code, message in failures
                ],
                "final_ec_prediction": None,
            },
        )
        outputs = {
            "configuration": config_output,
            "results": results_path,
            "summary": summary,
            "evidence": evidence_path,
            **raw_outputs,
        }
        if failures:
            return StageResult(
                False,
                outputs,
                commands=tuple(commands),
                software=(software,),
                message=f"TM-align failed for {len(failures)} selected pair(s)",
            )
        return StageResult(
            True,
            outputs,
            commands=tuple(commands),
            software=(software,),
            message=f"TM-align completed for {len(rows)} selected pair(s)",
        )
