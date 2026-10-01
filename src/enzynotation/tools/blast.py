"""Configurable BLASTp command construction and database discovery."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from enzynotation.backends.base import CommandSpec, ExecutionBackend
from enzynotation.parsers.blast import BLAST_OUTFMT_FIELDS, BlastFilterSettings
from enzynotation.provenance import SoftwareProvenance
from enzynotation.tools.base import ExternalTool, ToolConfigurationError


@dataclass(frozen=True, slots=True)
class BlastConfig:
    """Validated execution, database, metadata, and filter configuration."""

    executable: str
    database: Path
    database_name: str
    database_version: str
    metadata: Path
    cpus: int
    evalue: float
    max_target_sequences: int
    filters: BlastFilterSettings
    required: bool = True
    thresholds_validated: bool = False

    def to_dict(self) -> dict[str, Any]:
        """Return a deterministic cache-signature representation."""

        return {
            "executable": self.executable,
            "database": str(self.database),
            "database_name": self.database_name,
            "database_version": self.database_version,
            "metadata": str(self.metadata),
            "cpus": self.cpus,
            "evalue": self.evalue,
            "max_target_sequences": self.max_target_sequences,
            "filters": {
                "maximum_evalue": self.filters.maximum_evalue,
                "minimum_query_coverage": self.filters.minimum_query_coverage,
                "minimum_subject_coverage": self.filters.minimum_subject_coverage,
                "minimum_percent_identity": self.filters.minimum_percent_identity,
                "minimum_aligned_length": self.filters.minimum_aligned_length,
                "maximum_retained_hits_per_query": (
                    self.filters.maximum_retained_hits_per_query
                ),
            },
            "required": self.required,
            "thresholds_validated": self.thresholds_validated,
            "outfmt_fields": list(BLAST_OUTFMT_FIELDS),
        }


def _mapping(value: Any, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ToolConfigurationError(f"{field} must be a mapping")
    return value


def _positive_int(value: Any, field: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ToolConfigurationError(f"{field} must be a positive integer")
    return value


def _nonnegative_number(value: Any, field: str) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool) or value < 0:
        raise ToolConfigurationError(f"{field} must be a non-negative number")
    return float(value)


def _fraction(value: Any, field: str) -> float:
    number = _nonnegative_number(value, field)
    if number > 1:
        raise ToolConfigurationError(f"{field} must be from 0 to 1")
    return number


def blast_config_from_mapping(value: dict[str, Any]) -> BlastConfig:
    """Validate and normalize a parsed BLAST configuration document."""

    if value.get("schema_version") != 1:
        raise ToolConfigurationError("BLAST configuration schema_version must be 1")
    blast = _mapping(value.get("blast"), "blast")
    database = _mapping(blast.get("database"), "blast.database")
    metadata = _mapping(blast.get("metadata"), "blast.metadata")
    execution = _mapping(blast.get("execution"), "blast.execution")
    filters = _mapping(blast.get("filters"), "blast.filters")

    required_strings = {
        "blast.executable": blast.get("executable"),
        "blast.database.path": database.get("path"),
        "blast.database.name": database.get("name"),
        "blast.database.version": database.get("version"),
        "blast.metadata.path": metadata.get("path"),
    }
    for field, field_value in required_strings.items():
        if not isinstance(field_value, str) or not field_value.strip():
            raise ToolConfigurationError(f"{field} must be a non-empty string")

    maximum_retained = filters.get("maximum_retained_hits_per_query")
    if maximum_retained is not None:
        maximum_retained = _positive_int(
            maximum_retained, "blast.filters.maximum_retained_hits_per_query"
        )

    minimum_identity = _nonnegative_number(
        filters.get("minimum_percent_identity"),
        "blast.filters.minimum_percent_identity",
    )
    if minimum_identity > 100:
        raise ToolConfigurationError(
            "blast.filters.minimum_percent_identity must be from 0 to 100"
        )
    required = blast.get("required", True)
    thresholds_validated = blast.get("thresholds_validated", False)
    if not isinstance(required, bool) or not isinstance(thresholds_validated, bool):
        raise ToolConfigurationError(
            "blast.required and blast.thresholds_validated must be booleans"
        )

    return BlastConfig(
        executable=str(required_strings["blast.executable"]),
        database=Path(str(required_strings["blast.database.path"])),
        database_name=str(required_strings["blast.database.name"]),
        database_version=str(required_strings["blast.database.version"]),
        metadata=Path(str(required_strings["blast.metadata.path"])),
        cpus=_positive_int(execution.get("cpus"), "blast.execution.cpus"),
        evalue=_nonnegative_number(execution.get("evalue"), "blast.execution.evalue"),
        max_target_sequences=_positive_int(
            execution.get("max_target_sequences"),
            "blast.execution.max_target_sequences",
        ),
        filters=BlastFilterSettings(
            maximum_evalue=_nonnegative_number(
                filters.get("maximum_evalue"), "blast.filters.maximum_evalue"
            ),
            minimum_query_coverage=_fraction(
                filters.get("minimum_query_coverage"),
                "blast.filters.minimum_query_coverage",
            ),
            minimum_subject_coverage=_fraction(
                filters.get("minimum_subject_coverage"),
                "blast.filters.minimum_subject_coverage",
            ),
            minimum_percent_identity=minimum_identity,
            minimum_aligned_length=_positive_int(
                filters.get("minimum_aligned_length"),
                "blast.filters.minimum_aligned_length",
            ),
            maximum_retained_hits_per_query=maximum_retained,
        ),
        required=required,
        thresholds_validated=thresholds_validated,
    )


def load_blast_config(path: Path) -> BlastConfig:
    """Load a versioned YAML BLAST configuration."""

    config_path = Path(path)
    try:
        value = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ToolConfigurationError(
            f"cannot read BLAST configuration {path}: {exc}"
        ) from exc
    except yaml.YAMLError as exc:
        raise ToolConfigurationError(f"invalid BLAST YAML {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ToolConfigurationError("BLAST configuration must contain a mapping")
    return blast_config_from_mapping(value)


class BlastTool(ExternalTool):
    """BLASTp wrapper with an explicit, parser-compatible outfmt contract."""

    tool_id = "blastp"

    def __init__(self, config: BlastConfig) -> None:
        self.config = config

    def build_command(self, *, query: Path, output: Path) -> CommandSpec:
        """Construct the BLASTp argv captured later in provenance."""

        outfmt = "6 " + " ".join(BLAST_OUTFMT_FIELDS)
        return CommandSpec.from_sequence(
            [
                self.config.executable,
                "-query",
                str(query),
                "-db",
                str(self.config.database),
                "-out",
                str(output),
                "-outfmt",
                outfmt,
                "-evalue",
                str(self.config.evalue),
                "-max_target_seqs",
                str(self.config.max_target_sequences),
                "-num_threads",
                str(self.config.cpus),
            ]
        )

    def database_artifacts(self) -> dict[str, Path]:
        """Discover external BLAST database files for cache invalidation."""

        database = self.config.database
        candidates: list[Path] = []
        if database.is_file():
            candidates.append(database)
        parent = database.parent if database.parent != Path("") else Path(".")
        if parent.is_dir():
            candidates.extend(
                path for path in parent.glob(f"{database.name}.*") if path.is_file()
            )
        unique = sorted({path.resolve() for path in candidates})
        if not unique:
            raise ToolConfigurationError(
                f"no BLAST database artifacts found for prefix {database}"
            )
        return {
            f"database_{index:04d}": path for index, path in enumerate(unique, start=1)
        }

    def capture_version(self, backend: ExecutionBackend) -> SoftwareProvenance:
        """Capture ``blastp -version`` through the selected backend."""

        return backend.capture_version(
            self.config.executable,
            name="blastp",
            arguments=("-version",),
        )
