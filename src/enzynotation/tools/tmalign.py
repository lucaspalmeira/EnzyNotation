"""External TM-align configuration and deterministic query-first command."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from enzynotation.backends.base import CommandSpec, ExecutionBackend
from enzynotation.provenance import SoftwareProvenance
from enzynotation.tools.base import ExternalTool, ToolConfigurationError


@dataclass(frozen=True, slots=True)
class TMAlignSelection:
    top_hits_per_query: int | None
    minimum_query_coverage: float
    minimum_target_coverage: float


@dataclass(frozen=True, slots=True)
class TMAlignConfig:
    required: bool
    executable: str
    version_arguments: tuple[str, ...]
    selection: TMAlignSelection
    thresholds_validated: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "required": self.required,
            "executable": self.executable,
            "version_arguments": list(self.version_arguments),
            "selection": {
                "top_hits_per_query": self.selection.top_hits_per_query,
                "minimum_query_coverage": self.selection.minimum_query_coverage,
                "minimum_target_coverage": self.selection.minimum_target_coverage,
            },
            "thresholds_validated": self.thresholds_validated,
        }


def _fraction(value: Any, field: str) -> float:
    if (
        not isinstance(value, (int, float))
        or isinstance(value, bool)
        or not 0 <= value <= 1
    ):
        raise ToolConfigurationError(f"{field} must be from 0 to 1")
    return float(value)


def tmalign_config_from_mapping(value: dict[str, Any]) -> TMAlignConfig:
    """Validate a parsed TM-align configuration document."""

    if value.get("schema_version") != 1:
        raise ToolConfigurationError("TM-align schema_version must be 1")
    section = value.get("tmalign")
    if not isinstance(section, dict):
        raise ToolConfigurationError("tmalign must be a mapping")
    selection = section.get("selection")
    if not isinstance(selection, dict):
        raise ToolConfigurationError("tmalign.selection must be a mapping")
    executable = section.get("executable")
    if not isinstance(executable, str) or not executable.strip():
        raise ToolConfigurationError("tmalign.executable must be non-empty")
    required = section.get("required")
    validated = section.get("thresholds_validated")
    if not isinstance(required, bool) or not isinstance(validated, bool):
        raise ToolConfigurationError(
            "tmalign.required and thresholds_validated must be booleans"
        )
    top_n = selection.get("top_hits_per_query")
    if top_n is not None and (
        not isinstance(top_n, int) or isinstance(top_n, bool) or top_n < 1
    ):
        raise ToolConfigurationError("top_hits_per_query must be positive or null")
    version_arguments = section.get("version_arguments")
    if not isinstance(version_arguments, list) or any(
        not isinstance(item, str) or not item for item in version_arguments
    ):
        raise ToolConfigurationError("version_arguments must be a string list")
    return TMAlignConfig(
        required=required,
        executable=executable.strip(),
        version_arguments=tuple(version_arguments),
        selection=TMAlignSelection(
            top_hits_per_query=top_n,
            minimum_query_coverage=_fraction(
                selection.get("minimum_query_coverage"),
                "tmalign.selection.minimum_query_coverage",
            ),
            minimum_target_coverage=_fraction(
                selection.get("minimum_target_coverage"),
                "tmalign.selection.minimum_target_coverage",
            ),
        ),
        thresholds_validated=validated,
    )


def load_tmalign_config(path: Path) -> TMAlignConfig:
    """Load a versioned TM-align YAML configuration."""

    try:
        value = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    except OSError as exc:
        raise ToolConfigurationError(
            f"cannot read TM-align configuration: {exc}"
        ) from exc
    except yaml.YAMLError as exc:
        raise ToolConfigurationError(f"invalid TM-align YAML: {exc}") from exc
    if not isinstance(value, dict):
        raise ToolConfigurationError("TM-align configuration must be a mapping")
    return tmalign_config_from_mapping(value)


class TMAlignTool(ExternalTool):
    """Construct query-first TM-align commands through the shared backend."""

    tool_id = "tmalign"

    def __init__(self, config: TMAlignConfig) -> None:
        self.config = config

    def build_command(self, *, query: Path, output: Path) -> CommandSpec:
        """Use Structure 1 as query and Structure 2 as reference.

        The `output` parameter is the reference structure for compatibility with
        the compact external-tool interface; TM-align writes metrics to stdout.
        """

        return self.build_pair_command(query=query, reference=output)

    def build_pair_command(self, *, query: Path, reference: Path) -> CommandSpec:
        """Construct argv with query as Structure 1 and reference as 2."""

        return CommandSpec.from_sequence(
            (self.config.executable, str(query), str(reference))
        )

    def database_artifacts(self) -> dict[str, Path]:
        """TM-align has no database; candidates are declared stage inputs."""

        return {}

    def capture_version(self, backend: ExecutionBackend) -> SoftwareProvenance:
        """Capture an available TM-align version/banner without assuming it."""

        return backend.capture_version(
            self.config.executable,
            name="TMalign",
            arguments=self.config.version_arguments,
        )
