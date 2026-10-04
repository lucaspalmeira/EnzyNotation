"""Docker Compose command and configuration for Foldseek structural search."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from enzynotation.backends.base import CommandSpec, ExecutionBackend
from enzynotation.parsers.foldseek import FOLDSEEK_OUTPUT_FIELDS, FoldseekFilterSettings
from enzynotation.provenance import SoftwareProvenance
from enzynotation.tools.base import ExternalTool, ToolConfigurationError


@dataclass(frozen=True, slots=True)
class FoldseekDockerConfig:
    docker_executable: str
    compose_file: Path
    service: str
    image: str
    image_digest: str | None

    @property
    def image_reference(self) -> str:
        return (
            self.image
            if self.image_digest is None
            else f"{self.image}@{self.image_digest}"
        )


@dataclass(frozen=True, slots=True)
class FoldseekCommandConfig:
    """Native or administrator-wrapped Foldseek command for HPC."""

    prefix: tuple[str, ...]
    executable: str
    version_arguments: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class FoldseekDatabaseConfig:
    path: Path
    name: str
    version: str
    fingerprint: Path | None
    metadata: Path


@dataclass(frozen=True, slots=True)
class FoldseekParameters:
    cpus: int
    evalue: float
    max_seqs: int
    sensitivity: float | None


@dataclass(frozen=True, slots=True)
class FoldseekConfig:
    required: bool
    strategy: str
    docker: FoldseekDockerConfig | None
    command: FoldseekCommandConfig | None
    database: FoldseekDatabaseConfig
    parameters: FoldseekParameters
    filters: FoldseekFilterSettings
    software_version: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "required": self.required,
            "execution": {
                "strategy": self.strategy,
                "docker_compose": (
                    {
                        "docker_executable": self.docker.docker_executable,
                        "compose_file": str(self.docker.compose_file),
                        "service": self.docker.service,
                        "image": self.docker.image,
                        "image_digest": self.docker.image_digest,
                    }
                    if self.docker is not None
                    else None
                ),
                "command": (
                    {
                        "prefix": list(self.command.prefix),
                        "executable": self.command.executable,
                        "version_arguments": list(self.command.version_arguments),
                    }
                    if self.command is not None
                    else None
                ),
            },
            "database": {
                "path": str(self.database.path),
                "name": self.database.name,
                "version": self.database.version,
                "fingerprint": (
                    str(self.database.fingerprint)
                    if self.database.fingerprint is not None
                    else None
                ),
                "metadata": str(self.database.metadata),
            },
            "parameters": {
                "cpus": self.parameters.cpus,
                "evalue": self.parameters.evalue,
                "max_seqs": self.parameters.max_seqs,
                "sensitivity": self.parameters.sensitivity,
            },
            "filters": {
                "maximum_evalue": self.filters.maximum_evalue,
                "minimum_query_coverage": self.filters.minimum_query_coverage,
                "minimum_target_coverage": self.filters.minimum_target_coverage,
                "minimum_aligned_length": self.filters.minimum_aligned_length,
                "maximum_retained_hits_per_query": (
                    self.filters.maximum_retained_hits_per_query
                ),
                "threshold_status": self.filters.threshold_status,
            },
            "software_version": self.software_version,
            "output_fields": list(FOLDSEEK_OUTPUT_FIELDS),
        }


def _mapping(value: Any, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ToolConfigurationError(f"{field} must be a mapping")
    return value


def _string(value: Any, field: str, *, nullable: bool = False) -> str | None:
    if value is None and nullable:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ToolConfigurationError(f"{field} must be a non-empty string")
    return value.strip()


def _positive_int(value: Any, field: str, *, nullable: bool = False) -> int | None:
    if value is None and nullable:
        return None
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ToolConfigurationError(f"{field} must be a positive integer")
    return value


def _number(value: Any, field: str, *, nullable: bool = False) -> float | None:
    if value is None and nullable:
        return None
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ToolConfigurationError(f"{field} must be numeric")
    rendered = float(value)
    if rendered < 0 or rendered != rendered or rendered == float("inf"):
        raise ToolConfigurationError(f"{field} must be finite and non-negative")
    return rendered


def _fraction(value: Any, field: str) -> float:
    rendered = _number(value, field)
    assert rendered is not None
    if rendered > 1:
        raise ToolConfigurationError(f"{field} must be from 0 to 1")
    return rendered


def foldseek_config_from_mapping(value: dict[str, Any]) -> FoldseekConfig:
    """Validate and normalize Foldseek configuration."""

    if value.get("schema_version") != 1:
        raise ToolConfigurationError("Foldseek schema_version must be 1")
    section = _mapping(value.get("foldseek"), "foldseek")
    execution = _mapping(section.get("execution"), "foldseek.execution")
    strategy = execution.get("strategy")
    if strategy not in {"docker_compose", "command"}:
        raise ToolConfigurationError(
            "Foldseek strategy must be docker_compose or command"
        )
    database = _mapping(section.get("database"), "foldseek.database")
    parameters = _mapping(section.get("parameters"), "foldseek.parameters")
    filters = _mapping(section.get("filters"), "foldseek.filters")
    required = section.get("required")
    if not isinstance(required, bool):
        raise ToolConfigurationError("foldseek.required must be a boolean")
    docker_config = None
    command_config = None
    if strategy == "docker_compose":
        docker = _mapping(
            execution.get("docker_compose"), "foldseek.execution.docker_compose"
        )
        digest = _string(
            docker.get("image_digest"),
            "foldseek.execution.docker_compose.image_digest",
            nullable=True,
        )
        if digest is not None and not re.fullmatch(r"sha256:[a-fA-F0-9]+", digest):
            raise ToolConfigurationError("Foldseek image_digest must use sha256:<hex>")
        image = str(_string(docker.get("image"), "foldseek image"))
        if digest is not None and "@" in image:
            raise ToolConfigurationError("Foldseek image and image_digest overlap")
        image_override = os.environ.get("FOLDSEEK_IMAGE", "").strip()
        if image_override:
            if "@" in image_override:
                image, digest = image_override.rsplit("@", maxsplit=1)
                if not image or not re.fullmatch(r"sha256:[a-fA-F0-9]+", digest):
                    raise ToolConfigurationError(
                        "FOLDSEEK_IMAGE digest reference must use image@sha256:..."
                    )
            else:
                image = image_override
                digest = None
        docker_config = FoldseekDockerConfig(
            docker_executable=str(
                _string(docker.get("docker_executable"), "foldseek docker executable")
            ),
            compose_file=Path(
                str(_string(docker.get("compose_file"), "foldseek compose file"))
            ),
            service=str(_string(docker.get("service"), "foldseek service")),
            image=image,
            image_digest=digest,
        )
    else:
        command = _mapping(execution.get("command"), "foldseek.execution.command")
        prefix = command.get("prefix")
        version_arguments = command.get("version_arguments")
        if not isinstance(prefix, list) or any(
            not isinstance(item, str) or not item for item in prefix
        ):
            raise ToolConfigurationError(
                "foldseek command prefix must be a string list"
            )
        if not isinstance(version_arguments, list) or any(
            not isinstance(item, str) or not item for item in version_arguments
        ):
            raise ToolConfigurationError(
                "foldseek command version_arguments must be a string list"
            )
        command_config = FoldseekCommandConfig(
            prefix=tuple(prefix),
            executable=str(_string(command.get("executable"), "foldseek executable")),
            version_arguments=tuple(version_arguments),
        )
    fingerprint = _string(
        database.get("fingerprint"), "foldseek.database.fingerprint", nullable=True
    )
    database_name = str(_string(database.get("name"), "foldseek.database.name"))
    if Path(database_name).name != database_name or database_name in {".", ".."}:
        raise ToolConfigurationError("Foldseek database name must be a file name")
    maximum_retained = _positive_int(
        filters.get("maximum_retained_hits_per_query"),
        "foldseek.filters.maximum_retained_hits_per_query",
        nullable=True,
    )
    threshold_status = _string(
        filters.get("threshold_status"), "foldseek.filters.threshold_status"
    )
    if threshold_status not in {
        "operational",
        "externally_documented",
        "empirically_calibrated",
    }:
        raise ToolConfigurationError("unsupported Foldseek threshold_status")
    sensitivity = _number(
        parameters.get("sensitivity"), "foldseek.parameters.sensitivity", nullable=True
    )
    software_version = str(
        _string(section.get("software_version"), "foldseek.software_version")
    )
    return FoldseekConfig(
        required=required,
        strategy=strategy,
        docker=docker_config,
        command=command_config,
        database=FoldseekDatabaseConfig(
            path=Path(str(_string(database.get("path"), "foldseek.database.path"))),
            name=database_name,
            version=str(_string(database.get("version"), "foldseek.database.version")),
            fingerprint=Path(fingerprint) if fingerprint else None,
            metadata=Path(
                str(_string(database.get("metadata"), "foldseek.database.metadata"))
            ),
        ),
        parameters=FoldseekParameters(
            cpus=int(_positive_int(parameters.get("cpus"), "foldseek.parameters.cpus")),
            evalue=float(
                _number(parameters.get("evalue"), "foldseek.parameters.evalue")
            ),
            max_seqs=int(
                _positive_int(
                    parameters.get("max_seqs"), "foldseek.parameters.max_seqs"
                )
            ),
            sensitivity=sensitivity,
        ),
        filters=FoldseekFilterSettings(
            maximum_evalue=float(
                _number(
                    filters.get("maximum_evalue"),
                    "foldseek.filters.maximum_evalue",
                )
            ),
            minimum_query_coverage=_fraction(
                filters.get("minimum_query_coverage"),
                "foldseek.filters.minimum_query_coverage",
            ),
            minimum_target_coverage=_fraction(
                filters.get("minimum_target_coverage"),
                "foldseek.filters.minimum_target_coverage",
            ),
            minimum_aligned_length=int(
                _positive_int(
                    filters.get("minimum_aligned_length"),
                    "foldseek.filters.minimum_aligned_length",
                )
            ),
            maximum_retained_hits_per_query=maximum_retained,
            threshold_status=threshold_status,
        ),
        software_version=software_version,
    )


def load_foldseek_config(path: Path) -> FoldseekConfig:
    """Load a versioned Foldseek YAML configuration."""

    try:
        value = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    except OSError as exc:
        raise ToolConfigurationError(
            f"cannot read Foldseek configuration: {exc}"
        ) from exc
    except yaml.YAMLError as exc:
        raise ToolConfigurationError(f"invalid Foldseek YAML: {exc}") from exc
    if not isinstance(value, dict):
        raise ToolConfigurationError("Foldseek configuration must be a mapping")
    return foldseek_config_from_mapping(value)


class FoldseekTool(ExternalTool):
    """Build an ephemeral official-image Foldseek easy-search invocation."""

    tool_id = "foldseek"
    query_mount = Path("/work/query")
    database_mount = Path("/database")
    output_mount = Path("/work/output")
    tmp_mount = Path("/work/tmp")

    def __init__(self, config: FoldseekConfig) -> None:
        self.config = config

    def build_command(
        self,
        *,
        query_directory: Path,
        output_directory: Path,
        temporary_directory: Path,
    ) -> CommandSpec:
        """Construct Docker Compose argv without invoking a shell."""

        config = self.config
        environment = None
        if config.strategy == "docker_compose":
            assert config.docker is not None
            output = self.output_mount / "foldseek.tsv"
            database = self.database_mount / config.database.name
            prefix = [
                config.docker.docker_executable,
                "compose",
                "-f",
                str(config.docker.compose_file),
                "run",
                "--rm",
                config.docker.service,
            ]
            query = self.query_mount
            temporary = self.tmp_mount
            environment = {
                "FOLDSEEK_IMAGE": config.docker.image_reference,
                "FOLDSEEK_QUERY_DIR": str(query_directory.resolve()),
                "FOLDSEEK_DB_DIR": str(config.database.path.resolve()),
                "FOLDSEEK_OUTPUT_DIR": str(output_directory.resolve()),
                "FOLDSEEK_TMP_DIR": str(temporary_directory.resolve()),
            }
        else:
            assert config.command is not None
            prefix = [*config.command.prefix, config.command.executable]
            query = query_directory
            database = config.database.path / config.database.name
            output = output_directory / "foldseek.tsv"
            temporary = temporary_directory
        argv = [
            *prefix,
            "easy-search",
            str(query),
            str(database),
            str(output),
            str(temporary),
            "--format-output",
            ",".join(FOLDSEEK_OUTPUT_FIELDS),
            "--threads",
            str(config.parameters.cpus),
            "-e",
            str(config.parameters.evalue),
            "--max-seqs",
            str(config.parameters.max_seqs),
        ]
        if config.parameters.sensitivity is not None:
            argv.extend(("-s", str(config.parameters.sensitivity)))
        return CommandSpec.from_sequence(
            argv,
            environment=environment,
        )

    def database_artifacts(self) -> dict[str, Path]:
        """Return an optional stable external database fingerprint."""

        fingerprint = self.config.database.fingerprint
        return {"foldseek_database_fingerprint": fingerprint} if fingerprint else {}

    def capture_runtime_versions(
        self, backend: ExecutionBackend
    ) -> tuple[SoftwareProvenance, ...]:
        """Capture Docker, Compose, and non-running image metadata."""

        if self.config.strategy == "command":
            assert self.config.command is not None
            executable = (
                self.config.command.prefix[0]
                if self.config.command.prefix
                else self.config.command.executable
            )
            arguments = (
                (
                    *self.config.command.prefix[1:],
                    self.config.command.executable,
                    *self.config.command.version_arguments,
                )
                if self.config.command.prefix
                else self.config.command.version_arguments
            )
            return (
                backend.capture_version(
                    executable, name="foldseek", arguments=arguments
                ),
            )
        assert self.config.docker is not None
        executable = self.config.docker.docker_executable
        return (
            backend.capture_version(executable, name="docker"),
            backend.capture_version(
                executable, name="docker-compose", arguments=("compose", "version")
            ),
            backend.capture_version(
                executable,
                name="foldseek-container-image",
                arguments=(
                    "image",
                    "inspect",
                    "--format={{json .Id}} {{json .RepoDigests}}",
                    self.config.docker.image_reference,
                ),
            ),
        )

    def foldseek_software(self) -> SoftwareProvenance:
        """Represent Foldseek using the configured release/image identity."""

        executable = (
            self.config.docker.image_reference
            if self.config.docker is not None
            else self.config.command.executable
        )
        return SoftwareProvenance(
            name="foldseek",
            version=self.config.software_version,
            executable=executable,
        )
