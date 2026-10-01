"""Configuration and command construction for external CLEAN runtimes."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from enzynotation.backends.base import CommandSpec, ExecutionBackend
from enzynotation.provenance import SoftwareProvenance
from enzynotation.tools.base import ExternalTool, ToolConfigurationError


@dataclass(frozen=True, slots=True)
class CleanDockerComposeConfig:
    """Docker Compose runtime settings for CLEAN."""

    docker_executable: str
    compose_file: Path
    service: str
    image: str
    image_digest: str | None

    @property
    def image_reference(self) -> str:
        """Return the configured tag/reference optionally pinned by digest."""

        if self.image_digest is None:
            return self.image
        return f"{self.image}@{self.image_digest}"


@dataclass(frozen=True, slots=True)
class CleanCommandConfig:
    """Externally managed command runtime settings."""

    prefix: tuple[str, ...]
    executable: str | None
    working_directory: Path | None


@dataclass(frozen=True, slots=True)
class CleanImplementationConfig:
    """CLEAN wrapper and output interface settings."""

    interface: str
    entrypoint: str
    interpreter: str
    inference_method: str
    output_variant: str
    result_suffix: str
    software_version: str


@dataclass(frozen=True, slots=True)
class CleanMetricConfig:
    """Explicit interpretation of CLEAN's serialized numeric value."""

    name: str
    semantics: str
    ranking_direction: str
    calibrated: bool


@dataclass(frozen=True, slots=True)
class CleanModelConfig:
    """CLEAN model identity and training-data lineage."""

    training_split: str
    pretrained: bool
    model_identifier: str
    version: str


@dataclass(frozen=True, slots=True)
class CleanMountConfig:
    """Host-side staging and optional external resource mounts."""

    input_directory: Path | None
    output_directory: Path | None
    torch_cache: Path | None
    external_model_data: Path | None


@dataclass(frozen=True, slots=True)
class CleanFilterConfig:
    """Operational candidate-retention settings."""

    maximum_retained_candidates: int | None
    threshold: float | None
    threshold_comparison: str | None
    threshold_status: str
    allowed_ec_depths: tuple[int, ...] | None
    allow_partial_ec: bool


@dataclass(frozen=True, slots=True)
class CleanConfig:
    """Validated CLEAN provider configuration."""

    enabled: bool
    required: bool
    strategy: str
    docker_compose: CleanDockerComposeConfig
    command: CleanCommandConfig
    implementation: CleanImplementationConfig
    metric: CleanMetricConfig
    model: CleanModelConfig
    mounts: CleanMountConfig
    filtering: CleanFilterConfig
    resource_fingerprints: tuple[Path, ...]

    def to_dict(self) -> dict[str, Any]:
        """Return deterministic configuration data for stage signatures."""

        return {
            "enabled": self.enabled,
            "required": self.required,
            "execution": {
                "strategy": self.strategy,
                "docker_compose": {
                    "docker_executable": self.docker_compose.docker_executable,
                    "compose_file": str(self.docker_compose.compose_file),
                    "service": self.docker_compose.service,
                    "image": self.docker_compose.image,
                    "image_digest": self.docker_compose.image_digest,
                },
                "command": {
                    "prefix": list(self.command.prefix),
                    "executable": self.command.executable,
                    "working_directory": (
                        str(self.command.working_directory)
                        if self.command.working_directory is not None
                        else None
                    ),
                },
            },
            "implementation": {
                "interface": self.implementation.interface,
                "entrypoint": self.implementation.entrypoint,
                "interpreter": self.implementation.interpreter,
                "inference_method": self.implementation.inference_method,
                "output_variant": self.implementation.output_variant,
                "result_suffix": self.implementation.result_suffix,
                "software_version": self.implementation.software_version,
            },
            "metric": {
                "name": self.metric.name,
                "semantics": self.metric.semantics,
                "ranking_direction": self.metric.ranking_direction,
                "calibrated": self.metric.calibrated,
            },
            "model": {
                "training_split": self.model.training_split,
                "pretrained": self.model.pretrained,
                "model_identifier": self.model.model_identifier,
                "version": self.model.version,
            },
            "mounts": {
                "input_directory": (
                    str(self.mounts.input_directory)
                    if self.mounts.input_directory is not None
                    else None
                ),
                "output_directory": (
                    str(self.mounts.output_directory)
                    if self.mounts.output_directory is not None
                    else None
                ),
                "torch_cache": (
                    str(self.mounts.torch_cache)
                    if self.mounts.torch_cache is not None
                    else None
                ),
                "external_model_data": (
                    str(self.mounts.external_model_data)
                    if self.mounts.external_model_data is not None
                    else None
                ),
            },
            "filtering": {
                "maximum_retained_candidates": (
                    self.filtering.maximum_retained_candidates
                ),
                "threshold": self.filtering.threshold,
                "threshold_comparison": self.filtering.threshold_comparison,
                "threshold_status": self.filtering.threshold_status,
                "allowed_ec_depths": (
                    list(self.filtering.allowed_ec_depths)
                    if self.filtering.allowed_ec_depths is not None
                    else None
                ),
                "allow_partial_ec": self.filtering.allow_partial_ec,
            },
            "resource_fingerprints": [str(path) for path in self.resource_fingerprints],
        }


def _mapping(value: Any, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ToolConfigurationError(f"{field} must be a mapping")
    return value


def _string(value: Any, field: str, *, optional: bool = False) -> str | None:
    if value is None and optional:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ToolConfigurationError(f"{field} must be a non-empty string")
    return value.strip()


def _boolean(value: Any, field: str) -> bool:
    if not isinstance(value, bool):
        raise ToolConfigurationError(f"{field} must be a boolean")
    return value


def _optional_path(value: Any, field: str) -> Path | None:
    rendered = _string(value, field, optional=True)
    return Path(rendered) if rendered is not None else None


def _optional_positive_int(value: Any, field: str) -> int | None:
    if value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ToolConfigurationError(f"{field} must be null or a positive integer")
    return value


def _optional_number(value: Any, field: str) -> float | None:
    if value is None:
        return None
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ToolConfigurationError(f"{field} must be null or numeric")
    number = float(value)
    if number != number or number in {float("inf"), float("-inf")}:
        raise ToolConfigurationError(f"{field} must be finite")
    return number


def clean_config_from_mapping(value: dict[str, Any]) -> CleanConfig:
    """Validate and normalize a parsed CLEAN configuration document."""

    if value.get("schema_version") != 1:
        raise ToolConfigurationError("CLEAN configuration schema_version must be 1")
    clean = _mapping(value.get("clean"), "clean")
    execution = _mapping(clean.get("execution"), "clean.execution")
    docker = _mapping(execution.get("docker_compose"), "clean.execution.docker_compose")
    command = _mapping(execution.get("command"), "clean.execution.command")
    implementation = _mapping(clean.get("implementation"), "clean.implementation")
    metric = _mapping(clean.get("metric"), "clean.metric")
    model = _mapping(clean.get("model"), "clean.model")
    mounts = _mapping(clean.get("mounts"), "clean.mounts")
    filtering = _mapping(clean.get("filtering"), "clean.filtering")

    strategy = _string(execution.get("strategy"), "clean.execution.strategy")
    if strategy not in {"docker_compose", "command"}:
        raise ToolConfigurationError(
            "clean.execution.strategy must be docker_compose or command"
        )
    prefix = command.get("prefix")
    if not isinstance(prefix, list) or any(
        not isinstance(item, str) or not item.strip() for item in prefix
    ):
        raise ToolConfigurationError("clean.execution.command.prefix must be a list")
    command_executable = _string(
        command.get("executable"),
        "clean.execution.command.executable",
        optional=True,
    )
    if strategy == "command" and command_executable is None:
        raise ToolConfigurationError(
            "clean.execution.command.executable is required for command strategy"
        )

    image = _string(docker.get("image"), "clean.execution.docker_compose.image")
    image_digest = _string(
        docker.get("image_digest"),
        "clean.execution.docker_compose.image_digest",
        optional=True,
    )
    assert image is not None
    if "@" in image and image_digest is not None:
        raise ToolConfigurationError(
            "CLEAN image must not contain '@' when image_digest is separate"
        )
    if image_digest is not None and not image_digest.startswith("sha256:"):
        raise ToolConfigurationError("clean image_digest must start with sha256:")

    direction = _string(
        metric.get("ranking_direction"), "clean.metric.ranking_direction"
    )
    if direction not in {"lower_is_better", "higher_is_better", "unknown"}:
        raise ToolConfigurationError("unsupported CLEAN metric ranking direction")
    output_variant = _string(
        implementation.get("output_variant"),
        "clean.implementation.output_variant",
    )
    metric_name = _string(metric.get("name"), "clean.metric.name")
    assert output_variant is not None and metric_name is not None
    expected_semantics = {
        "maxsep_distance": ("cluster_center_pairwise_distance", "lower_is_better"),
        "maxsep_gmm_confidence": ("gmm_confidence_estimate", "higher_is_better"),
    }
    expected = expected_semantics.get(output_variant)
    if expected is not None and (metric_name, direction) != expected:
        raise ToolConfigurationError(
            f"{output_variant} requires metric {expected[0]} with {expected[1]}"
        )

    threshold = _optional_number(
        filtering.get("threshold"), "clean.filtering.threshold"
    )
    comparison = _string(
        filtering.get("threshold_comparison"),
        "clean.filtering.threshold_comparison",
        optional=True,
    )
    if comparison is not None and comparison not in {"lte", "gte"}:
        raise ToolConfigurationError("threshold_comparison must be lte, gte, or null")
    if threshold is not None:
        expected_comparison = {
            "lower_is_better": "lte",
            "higher_is_better": "gte",
        }.get(direction)
        if comparison is None:
            if expected_comparison is None:
                raise ToolConfigurationError(
                    "unknown metric direction requires threshold_comparison"
                )
            comparison = expected_comparison
        elif expected_comparison is not None and comparison != expected_comparison:
            raise ToolConfigurationError(
                "threshold_comparison conflicts with metric ranking direction"
            )
    elif comparison is not None:
        raise ToolConfigurationError(
            "threshold_comparison must be null when threshold is null"
        )

    raw_depths = filtering.get("allowed_ec_depths")
    allowed_depths: tuple[int, ...] | None
    if raw_depths is None:
        allowed_depths = None
    elif (
        not isinstance(raw_depths, list)
        or not raw_depths
        or any(
            not isinstance(item, int) or isinstance(item, bool) or not 1 <= item <= 4
            for item in raw_depths
        )
        or len(set(raw_depths)) != len(raw_depths)
    ):
        raise ToolConfigurationError(
            "clean.filtering.allowed_ec_depths must contain unique depths 1-4"
        )
    else:
        allowed_depths = tuple(raw_depths)

    raw_fingerprints = clean.get("resource_fingerprints", [])
    if not isinstance(raw_fingerprints, list) or any(
        not isinstance(item, str) or not item.strip() for item in raw_fingerprints
    ):
        raise ToolConfigurationError("clean.resource_fingerprints must be paths")
    threshold_status = _string(
        filtering.get("threshold_status"), "clean.filtering.threshold_status"
    )
    if threshold_status not in {
        "operational",
        "externally_documented",
        "empirically_calibrated",
    }:
        raise ToolConfigurationError("unsupported CLEAN threshold_status")

    return CleanConfig(
        enabled=_boolean(clean.get("enabled"), "clean.enabled"),
        required=_boolean(clean.get("required"), "clean.required"),
        strategy=strategy,
        docker_compose=CleanDockerComposeConfig(
            docker_executable=str(
                _string(
                    docker.get("docker_executable"),
                    "clean.execution.docker_compose.docker_executable",
                )
            ),
            compose_file=Path(
                str(
                    _string(
                        docker.get("compose_file"),
                        "clean.execution.docker_compose.compose_file",
                    )
                )
            ),
            service=str(
                _string(docker.get("service"), "clean.execution.docker_compose.service")
            ),
            image=image,
            image_digest=image_digest,
        ),
        command=CleanCommandConfig(
            tuple(item.strip() for item in prefix),
            command_executable,
            _optional_path(
                command.get("working_directory"),
                "clean.execution.command.working_directory",
            ),
        ),
        implementation=CleanImplementationConfig(
            interface=str(
                _string(
                    implementation.get("interface"), "clean.implementation.interface"
                )
            ),
            entrypoint=str(
                _string(
                    implementation.get("entrypoint"),
                    "clean.implementation.entrypoint",
                )
            ),
            interpreter=str(
                _string(
                    implementation.get("interpreter"),
                    "clean.implementation.interpreter",
                )
            ),
            inference_method=str(
                _string(
                    implementation.get("inference_method"),
                    "clean.implementation.inference_method",
                )
            ),
            output_variant=output_variant,
            result_suffix=str(
                _string(
                    implementation.get("result_suffix"),
                    "clean.implementation.result_suffix",
                )
            ),
            software_version=str(
                _string(
                    implementation.get("software_version"),
                    "clean.implementation.software_version",
                )
            ),
        ),
        metric=CleanMetricConfig(
            name=metric_name,
            semantics=str(_string(metric.get("semantics"), "clean.metric.semantics")),
            ranking_direction=direction,
            calibrated=_boolean(metric.get("calibrated"), "clean.metric.calibrated"),
        ),
        model=CleanModelConfig(
            training_split=str(
                _string(model.get("training_split"), "clean.model.training_split")
            ),
            pretrained=_boolean(model.get("pretrained"), "clean.model.pretrained"),
            model_identifier=str(
                _string(model.get("model_identifier"), "clean.model.model_identifier")
            ),
            version=str(_string(model.get("version"), "clean.model.version")),
        ),
        mounts=CleanMountConfig(
            input_directory=_optional_path(
                mounts.get("input_directory"), "clean.mounts.input_directory"
            ),
            output_directory=_optional_path(
                mounts.get("output_directory"), "clean.mounts.output_directory"
            ),
            torch_cache=_optional_path(
                mounts.get("torch_cache"), "clean.mounts.torch_cache"
            ),
            external_model_data=_optional_path(
                mounts.get("external_model_data"),
                "clean.mounts.external_model_data",
            ),
        ),
        filtering=CleanFilterConfig(
            maximum_retained_candidates=_optional_positive_int(
                filtering.get("maximum_retained_candidates"),
                "clean.filtering.maximum_retained_candidates",
            ),
            threshold=threshold,
            threshold_comparison=comparison,
            threshold_status=threshold_status,
            allowed_ec_depths=allowed_depths,
            allow_partial_ec=_boolean(
                filtering.get("allow_partial_ec"),
                "clean.filtering.allow_partial_ec",
            ),
        ),
        resource_fingerprints=tuple(Path(item.strip()) for item in raw_fingerprints),
    )


def load_clean_config(path: Path) -> CleanConfig:
    """Load a versioned CLEAN YAML configuration."""

    source = Path(path)
    try:
        value = yaml.safe_load(source.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ToolConfigurationError(
            f"cannot read CLEAN configuration {path}: {exc}"
        ) from exc
    except yaml.YAMLError as exc:
        raise ToolConfigurationError(f"invalid CLEAN YAML {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ToolConfigurationError("CLEAN configuration must contain a mapping")
    return clean_config_from_mapping(value)


class CleanTool(ExternalTool):
    """Construct CLEAN commands for Docker Compose or external environments."""

    tool_id = "clean"
    container_input = Path("/app/data/inputs")
    container_output = Path("/app/results/inputs")
    container_torch_cache = Path("/root/.cache/torch/hub/checkpoints")
    container_model_data = Path("/app/data/pretrained")

    def __init__(self, config: CleanConfig) -> None:
        self.config = config

    def expected_output(self, output_directory: Path, basename: str) -> Path:
        """Return the wrapper-specific host output path."""

        return (
            output_directory / f"{basename}{self.config.implementation.result_suffix}"
        )

    def database_artifacts(self) -> dict[str, Path]:
        """Return configured model/resource files used for cache invalidation."""

        return {
            f"clean_resource_{index:04d}": path
            for index, path in enumerate(
                self.config.resource_fingerprints,
                start=1,
            )
            if path.is_file()
        }

    def build_command(
        self,
        *,
        input_directory: Path,
        output_directory: Path,
        basename: str,
    ) -> CommandSpec:
        """Build the configured CLEAN invocation without running it."""

        implementation = self.config.implementation
        wrapper = (
            implementation.interpreter,
            implementation.entrypoint,
            "--fasta_data",
            basename,
        )
        if self.config.strategy == "command":
            executable = self.config.command.executable
            assert executable is not None
            return CommandSpec.from_sequence(
                (*self.config.command.prefix, executable, *wrapper[1:]),
                cwd=self.config.command.working_directory,
            )

        docker = self.config.docker_compose
        argv: list[str] = [
            docker.docker_executable,
            "compose",
            "-f",
            str(docker.compose_file),
            "run",
            "--rm",
        ]
        mounts = self.config.mounts
        if mounts.torch_cache is not None:
            argv.extend(
                (
                    "--volume",
                    f"{mounts.torch_cache}:{self.container_torch_cache}",
                )
            )
        if mounts.external_model_data is not None:
            argv.extend(
                (
                    "--volume",
                    f"{mounts.external_model_data}:{self.container_model_data}:ro",
                )
            )
        argv.extend((docker.service, *wrapper))
        return CommandSpec.from_sequence(
            argv,
            environment={
                "CLEAN_IMAGE": docker.image_reference,
                "CLEAN_INPUT_DIR": str(input_directory.resolve()),
                "CLEAN_OUTPUT_DIR": str(output_directory.resolve()),
            },
        )

    def capture_runtime_versions(
        self, backend: ExecutionBackend
    ) -> tuple[SoftwareProvenance, ...]:
        """Capture available runtime and image metadata without invoking CLEAN."""

        if self.config.strategy == "docker_compose":
            executable = self.config.docker_compose.docker_executable
            return (
                backend.capture_version(executable, name="docker"),
                backend.capture_version(
                    executable,
                    name="docker-compose",
                    arguments=("compose", "version"),
                ),
                backend.capture_version(
                    executable,
                    name="clean-container-image",
                    arguments=(
                        "image",
                        "inspect",
                        "--format={{json .Id}} {{json .RepoDigests}}",
                        self.config.docker_compose.image_reference,
                    ),
                ),
            )
        executable = self.config.command.executable
        assert executable is not None
        return (backend.capture_version(executable, name="clean-runtime"),)

    def clean_software(self) -> SoftwareProvenance:
        """Represent configured CLEAN software identity without inventing it."""

        executable = (
            self.config.docker_compose.image_reference
            if self.config.strategy == "docker_compose"
            else str(self.config.command.executable)
        )
        return SoftwareProvenance(
            name="CLEAN",
            version=self.config.implementation.software_version,
            executable=executable,
        )
