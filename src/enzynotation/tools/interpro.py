"""Configurable InterProScan command construction."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from enzynotation.backends.base import CommandSpec, ExecutionBackend
from enzynotation.provenance import SoftwareProvenance
from enzynotation.tools.base import ExternalTool, ToolConfigurationError


@dataclass(frozen=True, slots=True)
class InterProConfig:
    """Validated optional InterProScan provider configuration."""

    enabled: bool
    required: bool
    executable: str
    database_name: str
    database_version: str
    data_directory: Path | None
    cpus: int
    applications: tuple[str, ...]
    include_go_terms: bool
    include_pathways: bool

    def to_dict(self) -> dict[str, Any]:
        """Return deterministic configuration data for cache signatures."""

        return {
            "enabled": self.enabled,
            "required": self.required,
            "executable": self.executable,
            "database": {
                "name": self.database_name,
                "version": self.database_version,
                "path": str(self.data_directory) if self.data_directory else None,
            },
            "execution": {
                "cpus": self.cpus,
                "applications": list(self.applications),
                "include_go_terms": self.include_go_terms,
                "include_pathways": self.include_pathways,
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


def interpro_config_from_mapping(value: dict[str, Any]) -> InterProConfig:
    """Validate and normalize a parsed InterProScan configuration."""

    if value.get("schema_version") != 1:
        raise ToolConfigurationError(
            "InterProScan configuration schema_version must be 1"
        )
    interpro = _mapping(value.get("interproscan"), "interproscan")
    database = _mapping(interpro.get("database"), "interproscan.database")
    execution = _mapping(interpro.get("execution"), "interproscan.execution")
    enabled = interpro.get("enabled", False)
    required = interpro.get("required", False)
    include_go = execution.get("include_go_terms", True)
    include_pathways = execution.get("include_pathways", True)
    if any(
        not isinstance(item, bool)
        for item in (enabled, required, include_go, include_pathways)
    ):
        raise ToolConfigurationError(
            "InterProScan enabled/required/include flags must be booleans"
        )
    cpus = execution.get("cpus")
    if not isinstance(cpus, int) or isinstance(cpus, bool) or cpus < 1:
        raise ToolConfigurationError("interproscan.execution.cpus must be positive")
    raw_applications = execution.get("applications", [])
    if not isinstance(raw_applications, list) or any(
        not isinstance(item, str) or not item.strip() for item in raw_applications
    ):
        raise ToolConfigurationError(
            "interproscan.execution.applications must be a list of names"
        )
    path = database.get("path")
    if path is not None and (not isinstance(path, str) or not path.strip()):
        raise ToolConfigurationError(
            "interproscan.database.path must be null or a non-empty string"
        )
    return InterProConfig(
        enabled=enabled,
        required=required,
        executable=_string(interpro.get("executable"), "interproscan.executable"),
        database_name=_string(database.get("name"), "interproscan.database.name"),
        database_version=_string(
            database.get("version"), "interproscan.database.version"
        ),
        data_directory=Path(path) if path else None,
        cpus=cpus,
        applications=tuple(item.strip() for item in raw_applications),
        include_go_terms=include_go,
        include_pathways=include_pathways,
    )


def load_interpro_config(path: Path) -> InterProConfig:
    """Load a versioned InterProScan YAML configuration."""

    source = Path(path)
    try:
        value = yaml.safe_load(source.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ToolConfigurationError(
            f"cannot read InterProScan configuration {path}: {exc}"
        ) from exc
    except yaml.YAMLError as exc:
        raise ToolConfigurationError(
            f"invalid InterProScan YAML {path}: {exc}"
        ) from exc
    if not isinstance(value, dict):
        raise ToolConfigurationError(
            "InterProScan configuration must contain a mapping"
        )
    return interpro_config_from_mapping(value)


class InterProTool(ExternalTool):
    """InterProScan wrapper producing standard TSV output."""

    tool_id = "interproscan"

    def __init__(self, config: InterProConfig) -> None:
        self.config = config

    def build_command(self, *, query: Path, output: Path) -> CommandSpec:
        """Construct an InterProScan TSV command without a shell."""

        argv = [
            self.config.executable,
            "-i",
            str(query),
            "-f",
            "TSV",
            "-o",
            str(output),
            "-cpu",
            str(self.config.cpus),
        ]
        if self.config.applications:
            argv.extend(("-appl", ",".join(self.config.applications)))
        if self.config.include_go_terms:
            argv.append("-goterms")
        if self.config.include_pathways:
            argv.append("-pa")
        return CommandSpec.from_sequence(argv)

    def database_artifacts(self) -> dict[str, Path]:
        """Return a configured InterPro data directory marker when available."""

        if self.config.data_directory is None:
            return {}
        directory = self.config.data_directory
        if not directory.exists():
            raise ToolConfigurationError(
                f"InterProScan data directory does not exist: {directory}"
            )
        if directory.is_file():
            return {"interpro_data": directory.resolve()}
        marker_candidates = sorted(
            path.resolve()
            for path in directory.iterdir()
            if path.is_file()
            and path.name in {"version.txt", "interproscan.properties"}
        )
        return {
            f"interpro_data_{index:04d}": path
            for index, path in enumerate(marker_candidates, start=1)
        }

    def capture_version(self, backend: ExecutionBackend) -> SoftwareProvenance:
        """Capture InterProScan version information."""

        return backend.capture_version(
            self.config.executable,
            name="interproscan",
            arguments=("--version",),
        )
