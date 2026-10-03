"""Configuration and TSV mappings for query and reference structures."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from enzynotation.ec import normalize_ec
from enzynotation.exceptions import ECNumberError
from enzynotation.structures import StructureError
from enzynotation.tools.base import ToolConfigurationError

QUERY_MANIFEST_FIELDS = (
    "query_id",
    "structure_id",
    "structure_path",
    "structure_format",
    "chain_id",
    "model_index",
    "structure_source",
    "structure_source_version",
    "prediction_method",
    "prediction_model_version",
    "sequence_sha256",
    "quality_metadata",
)

REFERENCE_METADATA_FIELDS = (
    "structure_id",
    "chain_id",
    "protein_accession",
    "protein_name",
    "ec_numbers",
    "annotation_status",
    "source_database",
    "database_version",
    "structure_method",
    "sequence_accession",
    "structure_path",
    "structure_format",
    "model_index",
)

STRUCTURE_SOURCES = {
    "user_supplied",
    "experimentally_determined",
    "externally_predicted",
}


@dataclass(frozen=True, slots=True)
class StructuresConfig:
    """Query-structure manifest configuration."""

    manifest: Path
    required: bool

    def to_dict(self) -> dict[str, Any]:
        return {"manifest": str(self.manifest), "required": self.required}


@dataclass(frozen=True, slots=True)
class QueryStructure:
    """One user-supplied structure mapping."""

    query_id: str
    structure_id: str
    structure_path: Path
    structure_format: str
    chain_id: str | None
    model_index: int | None
    structure_source: str
    structure_source_version: str | None
    prediction_method: str | None
    prediction_model_version: str | None
    sequence_sha256: str | None
    quality_metadata: str | None


@dataclass(frozen=True, slots=True)
class StructureReference:
    """Explicit functional metadata for one database structure."""

    structure_id: str
    chain_id: str | None
    protein_accession: str | None
    protein_name: str | None
    ec_numbers: tuple[str, ...]
    annotation_status: str
    source_database: str
    database_version: str
    structure_method: str | None
    sequence_accession: str | None
    structure_path: Path | None
    structure_format: str | None
    model_index: int | None

    @property
    def curated(self) -> bool:
        return self.annotation_status.lower() in {"curated", "reviewed"}


class StructureReferenceIndex:
    """Exact and conservative normalized lookup for structure metadata."""

    def __init__(self, records: tuple[StructureReference, ...]) -> None:
        self.records = records
        self._records = {record.structure_id: record for record in records}

    def lookup(self, target_id: str) -> StructureReference | None:
        candidates = [target_id, Path(target_id).name]
        for suffix in (".pdb", ".ent", ".cif", ".mmcif"):
            candidates.extend(
                candidate[: -len(suffix)]
                for candidate in tuple(candidates)
                if candidate.lower().endswith(suffix)
            )
        if "|" in target_id:
            candidates.append(target_id.split("|")[-1])
        for candidate in candidates:
            record = self._records.get(candidate)
            if record is not None:
                return record
        return None


@dataclass(frozen=True, slots=True)
class NormalizedQueryStructure:
    """One validated structure staged in the canonical run directory."""

    query_id: str
    structure_id: str
    staged_path: Path
    structure_format: str
    chain_id: str
    model_index: int
    model_identifier: str
    structure_sha256: str
    structure_sequence: str
    sequence_match_status: str
    foldseek_query_id: str


def _optional(value: str | None) -> str | None:
    if value is None or not value.strip() or value.strip() in {".", "-"}:
        return None
    return value.strip()


def _model_index(value: str | None, *, row: int) -> int | None:
    rendered = _optional(value)
    if rendered is None:
        return None
    try:
        parsed = int(rendered)
    except ValueError as exc:
        raise StructureError(f"row {row}: model_index must be an integer") from exc
    if parsed < 1:
        raise StructureError(f"row {row}: model_index must be positive")
    return parsed


def _rows(path: Path, required: tuple[str, ...]) -> tuple[list[dict[str, str]], Path]:
    source = Path(path)
    if not source.is_file():
        raise StructureError(f"mapping file does not exist: {source}")
    try:
        handle = source.open("r", encoding="utf-8", newline="")
    except OSError as exc:
        raise StructureError(f"cannot read mapping file {source}: {exc}") from exc
    with handle:
        reader = csv.DictReader(handle, delimiter="\t")
        if reader.fieldnames is None:
            raise StructureError(f"mapping file has no header: {source}")
        missing = sorted(set(required) - set(reader.fieldnames))
        if missing:
            raise StructureError(
                f"mapping file {source} is missing columns: {', '.join(missing)}"
            )
        try:
            return [dict(row) for row in reader], source.parent.resolve()
        except csv.Error as exc:
            raise StructureError(f"malformed TSV {source}: {exc}") from exc


def load_query_structure_manifest(path: Path) -> tuple[QueryStructure, ...]:
    """Load an explicit user query-to-structure TSV manifest."""

    rows, base = _rows(
        path,
        (
            "query_id",
            "structure_id",
            "structure_path",
            "structure_format",
            "structure_source",
        ),
    )
    records: list[QueryStructure] = []
    identifiers: set[str] = set()
    for row_number, row in enumerate(rows, start=2):
        query_id = _optional(row.get("query_id"))
        structure_id = _optional(row.get("structure_id"))
        raw_path = _optional(row.get("structure_path"))
        structure_format = _optional(row.get("structure_format"))
        source = _optional(row.get("structure_source"))
        if None in {query_id, structure_id, raw_path, structure_format, source}:
            raise StructureError(f"row {row_number}: required manifest value is empty")
        assert query_id and structure_id and raw_path and structure_format and source
        if source not in STRUCTURE_SOURCES:
            raise StructureError(
                f"row {row_number}: unsupported structure_source {source!r}"
            )
        if structure_id in identifiers:
            raise StructureError(
                f"row {row_number}: duplicate structure_id {structure_id!r}"
            )
        identifiers.add(structure_id)
        structure_path = Path(raw_path)
        if not structure_path.is_absolute():
            structure_path = base / structure_path
        sequence_sha256 = _optional(row.get("sequence_sha256"))
        if sequence_sha256 is not None and (
            len(sequence_sha256) != 64
            or any(character not in "0123456789abcdef" for character in sequence_sha256)
        ):
            raise StructureError(
                f"row {row_number}: sequence_sha256 must be lowercase SHA-256"
            )
        records.append(
            QueryStructure(
                query_id=query_id,
                structure_id=structure_id,
                structure_path=structure_path.resolve(),
                structure_format=structure_format,
                chain_id=_optional(row.get("chain_id")),
                model_index=_model_index(row.get("model_index"), row=row_number),
                structure_source=source,
                structure_source_version=_optional(row.get("structure_source_version")),
                prediction_method=_optional(row.get("prediction_method")),
                prediction_model_version=_optional(row.get("prediction_model_version")),
                sequence_sha256=sequence_sha256,
                quality_metadata=_optional(row.get("quality_metadata")),
            )
        )
    return tuple(records)


def load_structure_reference_metadata(path: Path) -> StructureReferenceIndex:
    """Load explicit Foldseek-reference annotations without text inference."""

    rows, base = _rows(
        path,
        (
            "structure_id",
            "ec_numbers",
            "annotation_status",
            "source_database",
            "database_version",
        ),
    )
    records: list[StructureReference] = []
    identifiers: set[str] = set()
    for row_number, row in enumerate(rows, start=2):
        structure_id = _optional(row.get("structure_id"))
        annotation_status = _optional(row.get("annotation_status"))
        source_database = _optional(row.get("source_database"))
        database_version = _optional(row.get("database_version"))
        if None in {
            structure_id,
            annotation_status,
            source_database,
            database_version,
        }:
            raise StructureError(f"row {row_number}: required metadata value is empty")
        assert (
            structure_id and annotation_status and source_database and database_version
        )
        if structure_id in identifiers:
            raise StructureError(
                f"row {row_number}: duplicate structure_id {structure_id!r}"
            )
        identifiers.add(structure_id)
        ec_values: list[str] = []
        for value in (_optional(row.get("ec_numbers")) or "").split(";"):
            if not value.strip():
                continue
            try:
                normalized = normalize_ec(value)
            except ECNumberError as exc:
                raise StructureError(
                    f"row {row_number}: invalid EC {value!r}: {exc}"
                ) from exc
            if normalized not in ec_values:
                ec_values.append(normalized)
        raw_structure_path = _optional(row.get("structure_path"))
        structure_path = Path(raw_structure_path) if raw_structure_path else None
        if structure_path is not None and not structure_path.is_absolute():
            structure_path = base / structure_path
        records.append(
            StructureReference(
                structure_id=structure_id,
                chain_id=_optional(row.get("chain_id")),
                protein_accession=_optional(row.get("protein_accession")),
                protein_name=_optional(row.get("protein_name")),
                ec_numbers=tuple(ec_values),
                annotation_status=annotation_status,
                source_database=source_database,
                database_version=database_version,
                structure_method=_optional(row.get("structure_method")),
                sequence_accession=_optional(row.get("sequence_accession")),
                structure_path=structure_path.resolve() if structure_path else None,
                structure_format=_optional(row.get("structure_format")),
                model_index=_model_index(row.get("model_index"), row=row_number),
            )
        )
    return StructureReferenceIndex(tuple(records))


def load_normalized_structure_manifest(
    path: Path, *, run_root: Path
) -> tuple[NormalizedQueryStructure, ...]:
    """Load the trusted output of the structures stage."""

    rows, _ = _rows(
        path,
        (
            "query_id",
            "structure_id",
            "staged_path",
            "structure_format",
            "chain_id",
            "model_index",
            "model_identifier",
            "structure_sha256",
            "structure_sequence",
            "sequence_match_status",
            "foldseek_query_id",
        ),
    )
    records: list[NormalizedQueryStructure] = []
    for row_number, row in enumerate(rows, start=2):
        staged = Path(row["staged_path"])
        if not staged.is_absolute():
            staged = Path(run_root) / staged
        try:
            model_index = int(row["model_index"])
        except ValueError as exc:
            raise StructureError(
                f"normalized manifest row {row_number}: invalid model index"
            ) from exc
        records.append(
            NormalizedQueryStructure(
                query_id=row["query_id"],
                structure_id=row["structure_id"],
                staged_path=staged.resolve(),
                structure_format=row["structure_format"],
                chain_id=row["chain_id"],
                model_index=model_index,
                model_identifier=row["model_identifier"],
                structure_sha256=row["structure_sha256"],
                structure_sequence=row["structure_sequence"],
                sequence_match_status=row["sequence_match_status"],
                foldseek_query_id=row["foldseek_query_id"],
            )
        )
    return tuple(records)


def structures_config_from_mapping(value: dict[str, Any]) -> StructuresConfig:
    """Validate the runtime subset of a structures configuration."""

    if value.get("schema_version") != 1:
        raise ToolConfigurationError("structures schema_version must be 1")
    section = value.get("structures")
    if not isinstance(section, dict):
        raise ToolConfigurationError("structures must be a mapping")
    manifest = section.get("manifest")
    required = section.get("required")
    if not isinstance(manifest, str) or not manifest.strip():
        raise ToolConfigurationError("structures.manifest must be a path")
    if not isinstance(required, bool):
        raise ToolConfigurationError("structures.required must be a boolean")
    return StructuresConfig(Path(manifest), required)


def load_structures_config(path: Path) -> StructuresConfig:
    """Load a versioned query-structure YAML configuration."""

    source = Path(path)
    try:
        value = yaml.safe_load(source.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ToolConfigurationError(
            f"cannot read structures configuration: {exc}"
        ) from exc
    except yaml.YAMLError as exc:
        raise ToolConfigurationError(f"invalid structures YAML: {exc}") from exc
    if not isinstance(value, dict):
        raise ToolConfigurationError("structures configuration must be a mapping")
    return structures_config_from_mapping(value)
