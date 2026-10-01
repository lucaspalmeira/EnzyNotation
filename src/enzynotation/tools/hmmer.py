"""Configurable HMMER hmmscan command construction."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from enzynotation.backends.base import CommandSpec, ExecutionBackend
from enzynotation.provenance import SoftwareProvenance
from enzynotation.tools.base import ExternalTool, ToolConfigurationError


@dataclass(frozen=True, slots=True)
class HmmerConfig:
    """Validated HMMER provider configuration."""

    enabled: bool
    required: bool
    executable: str
    database: Path
    database_name: str
    database_version: str
    database_kind: str
    cpus: int
    search_sequence_evalue: float
    search_domain_evalue: float
    maximum_sequence_evalue: float
    maximum_domain_i_evalue: float
    minimum_bit_score: float
    minimum_query_coverage: float

    def to_dict(self) -> dict[str, Any]:
        """Return deterministic configuration data for cache signatures."""

        return {
            "enabled": self.enabled,
            "required": self.required,
            "executable": self.executable,
            "database": {
                "path": str(self.database),
                "name": self.database_name,
                "version": self.database_version,
                "kind": self.database_kind,
            },
            "execution": {
                "cpus": self.cpus,
                "sequence_evalue": self.search_sequence_evalue,
                "domain_evalue": self.search_domain_evalue,
            },
            "filters": {
                "maximum_sequence_evalue": self.maximum_sequence_evalue,
                "maximum_domain_i_evalue": self.maximum_domain_i_evalue,
                "minimum_bit_score": self.minimum_bit_score,
                "minimum_query_coverage": self.minimum_query_coverage,
            },
        }


def _mapping(value: Any, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ToolConfigurationError(f"{field} must be a mapping")
    return value


def _string(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ToolConfigurationError(f"{field} must be a non-empty string")
    return value.strip()


def _positive_int(value: Any, field: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ToolConfigurationError(f"{field} must be a positive integer")
    return value


def _number(value: Any, field: str, *, maximum: float | None = None) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool) or value < 0:
        raise ToolConfigurationError(f"{field} must be a non-negative number")
    number = float(value)
    if maximum is not None and number > maximum:
        raise ToolConfigurationError(f"{field} must be from 0 to {maximum}")
    return number


def hmmer_config_from_mapping(value: dict[str, Any]) -> HmmerConfig:
    """Validate and normalize a parsed HMMER configuration."""

    if value.get("schema_version") != 1:
        raise ToolConfigurationError("HMMER configuration schema_version must be 1")
    hmmer = _mapping(value.get("hmmer"), "hmmer")
    database = _mapping(hmmer.get("database"), "hmmer.database")
    execution = _mapping(hmmer.get("execution"), "hmmer.execution")
    filters = _mapping(hmmer.get("filters"), "hmmer.filters")
    enabled = hmmer.get("enabled", True)
    required = hmmer.get("required", True)
    if not isinstance(enabled, bool) or not isinstance(required, bool):
        raise ToolConfigurationError(
            "hmmer.enabled and hmmer.required must be booleans"
        )
    kind = _string(database.get("kind"), "hmmer.database.kind").lower()
    if kind not in {"pfam", "custom"}:
        raise ToolConfigurationError("hmmer.database.kind must be pfam or custom")
    return HmmerConfig(
        enabled=enabled,
        required=required,
        executable=_string(hmmer.get("executable"), "hmmer.executable"),
        database=Path(_string(database.get("path"), "hmmer.database.path")),
        database_name=_string(database.get("name"), "hmmer.database.name"),
        database_version=_string(database.get("version"), "hmmer.database.version"),
        database_kind=kind,
        cpus=_positive_int(execution.get("cpus"), "hmmer.execution.cpus"),
        search_sequence_evalue=_number(
            execution.get("sequence_evalue"), "hmmer.execution.sequence_evalue"
        ),
        search_domain_evalue=_number(
            execution.get("domain_evalue"), "hmmer.execution.domain_evalue"
        ),
        maximum_sequence_evalue=_number(
            filters.get("maximum_sequence_evalue"),
            "hmmer.filters.maximum_sequence_evalue",
        ),
        maximum_domain_i_evalue=_number(
            filters.get("maximum_domain_i_evalue"),
            "hmmer.filters.maximum_domain_i_evalue",
        ),
        minimum_bit_score=_number(
            filters.get("minimum_bit_score"), "hmmer.filters.minimum_bit_score"
        ),
        minimum_query_coverage=_number(
            filters.get("minimum_query_coverage"),
            "hmmer.filters.minimum_query_coverage",
            maximum=1.0,
        ),
    )


def load_hmmer_config(path: Path) -> HmmerConfig:
    """Load a versioned HMMER YAML configuration."""

    source = Path(path)
    try:
        value = yaml.safe_load(source.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ToolConfigurationError(
            f"cannot read HMMER configuration {path}: {exc}"
        ) from exc
    except yaml.YAMLError as exc:
        raise ToolConfigurationError(f"invalid HMMER YAML {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ToolConfigurationError("HMMER configuration must contain a mapping")
    return hmmer_config_from_mapping(value)


class HmmerTool(ExternalTool):
    """HMMER hmmscan wrapper using parser-compatible domtblout output."""

    tool_id = "hmmer"

    def __init__(self, config: HmmerConfig) -> None:
        self.config = config

    def build_command(self, *, query: Path, output: Path) -> CommandSpec:
        """Construct an hmmscan command without a shell."""

        return CommandSpec.from_sequence(
            [
                self.config.executable,
                "--domtblout",
                str(output),
                "--noali",
                "--cpu",
                str(self.config.cpus),
                "-E",
                str(self.config.search_sequence_evalue),
                "--domE",
                str(self.config.search_domain_evalue),
                str(self.config.database),
                str(query),
            ]
        )

    def database_artifacts(self) -> dict[str, Path]:
        """Return an HMM library and any hmmpress sidecar files."""

        database = self.config.database
        candidates: list[Path] = []
        if database.is_file():
            candidates.append(database.resolve())
        for suffix in (".h3f", ".h3i", ".h3m", ".h3p"):
            sidecar = Path(f"{database}{suffix}")
            if sidecar.is_file():
                candidates.append(sidecar.resolve())
        unique = sorted(set(candidates))
        if not unique:
            raise ToolConfigurationError(f"HMMER database does not exist: {database}")
        return {
            f"hmmer_database_{index:04d}": path
            for index, path in enumerate(unique, start=1)
        }

    def capture_version(self, backend: ExecutionBackend) -> SoftwareProvenance:
        """Capture HMMER version information using ``hmmscan -h``."""

        return backend.capture_version(
            self.config.executable,
            name="hmmscan",
            arguments=("-h",),
        )
