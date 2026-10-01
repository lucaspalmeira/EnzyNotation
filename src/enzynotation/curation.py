"""Database-agnostic curated accession-to-function metadata."""

from __future__ import annotations

import csv
import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from enzynotation.ec import normalize_ec
from enzynotation.exceptions import ECNumberError

_PIPE_PREFIXES = {
    "sp",
    "tr",
    "ref",
    "gb",
    "emb",
    "dbj",
    "pir",
    "prf",
    "pdb",
    "lcl",
}
_COLON_PREFIXES = _PIPE_PREFIXES | {"uniprot", "uniprotkb", "swissprot"}


class CurationError(ValueError):
    """Raised when curated functional metadata is missing or malformed."""


@dataclass(frozen=True, slots=True)
class CuratedAnnotation:
    """Curated functional metadata for one normalized accession."""

    accession: str
    protein_name: str
    ec_numbers: tuple[str, ...]
    annotation_status: str
    source_database: str
    database_version: str
    curation_status: str | None = None

    @property
    def is_curated(self) -> bool:
        """Whether the metadata explicitly describes reviewed curation."""

        status = (self.curation_status or self.annotation_status).lower()
        return status in {"curated", "reviewed", "expert_reviewed", "manual"}


class CurationIndex:
    """Accession index that never fabricates records for unknown subjects."""

    def __init__(self, annotations: Iterable[CuratedAnnotation]) -> None:
        self._records: dict[str, CuratedAnnotation] = {}
        for annotation in annotations:
            aliases = accession_candidates(annotation.accession)
            for alias in aliases:
                existing = self._records.get(alias)
                if existing is not None and existing != annotation:
                    raise CurationError(
                        f"metadata accession alias {alias!r} maps to multiple records"
                    )
                self._records[alias] = annotation

    def lookup(self, subject_id: str) -> CuratedAnnotation | None:
        """Return explicit metadata for a BLAST subject, or ``None``."""

        for candidate in accession_candidates(subject_id):
            annotation = self._records.get(candidate)
            if annotation is not None:
                return annotation
        return None

    def __len__(self) -> int:
        return len({record.accession for record in self._records.values()})


def normalize_subject_accession(subject_id: str) -> str:
    """Normalize common database-prefixed identifiers without losing provenance."""

    value = subject_id.strip().lstrip(">")
    if not value:
        raise CurationError("subject accession is empty")
    value = value.split(maxsplit=1)[0]

    pipe_parts = value.split("|")
    if len(pipe_parts) >= 2 and pipe_parts[0].lower() in _PIPE_PREFIXES:
        if pipe_parts[0].lower() == "lcl" and len(pipe_parts) == 2:
            value = pipe_parts[1]
        elif pipe_parts[1]:
            value = pipe_parts[1]
    elif ":" in value:
        prefix, remainder = value.split(":", 1)
        if prefix.lower() in _COLON_PREFIXES and remainder:
            value = remainder

    value = value.strip()
    if not value:
        raise CurationError(f"cannot normalize subject accession {subject_id!r}")
    return value


def accession_candidates(subject_id: str) -> tuple[str, ...]:
    """Return exact normalized and optional versionless accession candidates."""

    normalized = normalize_subject_accession(subject_id)
    candidates = [normalized]
    if re.fullmatch(r".+\.\d+", normalized):
        candidates.append(normalized.rsplit(".", 1)[0])
    return tuple(candidates)


def _normalize_ec_values(value: Any, row_number: int) -> tuple[str, ...]:
    if value is None or value == "":
        return ()
    if isinstance(value, list):
        raw_values = [str(item).strip() for item in value]
    elif isinstance(value, str):
        raw_values = [part.strip() for part in re.split(r"[;,]", value)]
    else:
        raise CurationError(
            f"metadata row {row_number}: ec_numbers must be a string or list"
        )

    normalized: list[str] = []
    for raw_ec in raw_values:
        if not raw_ec:
            continue
        try:
            ec = normalize_ec(raw_ec)
        except ECNumberError as exc:
            raise CurationError(
                f"metadata row {row_number}: invalid EC number {raw_ec!r}: {exc}"
            ) from exc
        if ec not in normalized:
            normalized.append(ec)
    return tuple(normalized)


def _annotation_from_mapping(
    row: Mapping[str, Any], row_number: int
) -> CuratedAnnotation:
    required = (
        "accession",
        "protein_name",
        "ec_numbers",
        "annotation_status",
        "source_database",
        "database_version",
    )
    missing = [field for field in required if field not in row]
    if missing:
        raise CurationError(
            f"metadata row {row_number}: missing fields {', '.join(missing)}"
        )

    accession = normalize_subject_accession(str(row["accession"]))
    string_values: dict[str, str] = {}
    for field in (
        "protein_name",
        "annotation_status",
        "source_database",
        "database_version",
    ):
        value = str(row[field]).strip()
        if not value:
            raise CurationError(f"metadata row {row_number}: {field} must be non-empty")
        string_values[field] = value

    curation_value = row.get("curation_status")
    curation_status = (
        str(curation_value).strip() if curation_value not in (None, "") else None
    )
    return CuratedAnnotation(
        accession=accession,
        protein_name=string_values["protein_name"],
        ec_numbers=_normalize_ec_values(row["ec_numbers"], row_number),
        annotation_status=string_values["annotation_status"],
        source_database=string_values["source_database"],
        database_version=string_values["database_version"],
        curation_status=curation_status,
    )


def _load_delimited(path: Path) -> list[Mapping[str, Any]]:
    delimiter = "," if path.suffix.lower() == ".csv" else "\t"
    try:
        with path.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle, delimiter=delimiter)
            if reader.fieldnames is None:
                raise CurationError(f"metadata file {path} has no header")
            return [dict(row) for row in reader]
    except (OSError, UnicodeError, csv.Error) as exc:
        raise CurationError(f"cannot read metadata file {path}: {exc}") from exc


def _load_json(path: Path) -> list[Mapping[str, Any]]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise CurationError(f"cannot read metadata file {path}: {exc}") from exc
    if isinstance(data, dict):
        data = data.get("records")
    if not isinstance(data, list) or any(not isinstance(row, dict) for row in data):
        raise CurationError(
            f"metadata JSON {path} must be a list or an object with a records list"
        )
    return data


def load_curation_metadata(path: Path) -> CurationIndex:
    """Load TSV, CSV, or JSON accession metadata with normalized EC numbers."""

    metadata_path = Path(path)
    if not metadata_path.is_file():
        raise CurationError(f"metadata file does not exist: {metadata_path}")
    suffix = metadata_path.suffix.lower()
    if suffix == ".json":
        rows = _load_json(metadata_path)
    elif suffix in {".tsv", ".txt", ".csv"}:
        rows = _load_delimited(metadata_path)
    else:
        raise CurationError(
            f"unsupported metadata format {suffix!r}; use TSV, CSV, or JSON"
        )

    annotations = [
        _annotation_from_mapping(row, row_number)
        for row_number, row in enumerate(rows, start=2)
    ]
    return CurationIndex(annotations)
