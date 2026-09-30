"""Layered YAML configuration loading and Milestone 1 validation."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from enzynotation.exceptions import ConfigurationError

SUPPORTED_SCHEMA_VERSION = 1

DEFAULT_CONFIG: dict[str, Any] = {
    "schema_version": SUPPORTED_SCHEMA_VERSION,
    "pipeline": {"name": "EnzyNotation"},
    "input": {
        "allowed_residues": "ACDEFGHIKLMNPQRSTVWYBXZJUO",
        "allow_terminal_stop": True,
        "min_sequence_length": 1,
        "max_sequence_length": None,
        "uppercase": True,
    },
    "output": {"line_width": 60},
    "logging": {"level": "INFO"},
}


@dataclass(frozen=True, slots=True)
class ResolvedConfig:
    """Validated configuration and the ordered files that contributed to it."""

    data: Mapping[str, Any]
    source_files: tuple[Path, ...]

    def section(self, name: str) -> Mapping[str, Any]:
        """Return a named mapping from the resolved configuration."""

        value = self.data.get(name)
        if not isinstance(value, Mapping):
            raise ConfigurationError(f"Configuration section {name!r} is missing")
        return value


def _deep_merge(base: dict[str, Any], overlay: Mapping[str, Any]) -> dict[str, Any]:
    result = deepcopy(base)
    for key, value in overlay.items():
        current = result.get(key)
        if isinstance(current, dict) and isinstance(value, Mapping):
            result[key] = _deep_merge(current, value)
        else:
            result[key] = deepcopy(value)
    return result


def _require_mapping(config: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    value = config.get(key)
    if not isinstance(value, Mapping):
        raise ConfigurationError(f"Configuration field {key!r} must be a mapping")
    return value


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def validate_config(config: Mapping[str, Any]) -> None:
    """Validate fields used by the Milestone 1 implementation."""

    version = config.get("schema_version")
    if version != SUPPORTED_SCHEMA_VERSION:
        raise ConfigurationError(
            f"Unsupported schema_version {version!r}; "
            f"expected {SUPPORTED_SCHEMA_VERSION}"
        )

    pipeline = _require_mapping(config, "pipeline")
    if not isinstance(pipeline.get("name"), str) or not pipeline["name"].strip():
        raise ConfigurationError("pipeline.name must be a non-empty string")

    input_config = _require_mapping(config, "input")
    residues = input_config.get("allowed_residues")
    if (
        not isinstance(residues, str)
        or not residues
        or not residues.isascii()
        or not residues.isalpha()
        or residues != residues.upper()
        or len(set(residues)) != len(residues)
    ):
        raise ConfigurationError(
            "input.allowed_residues must contain unique uppercase ASCII letters"
        )
    for name in ("allow_terminal_stop", "uppercase"):
        if not isinstance(input_config.get(name), bool):
            raise ConfigurationError(f"input.{name} must be a boolean")

    minimum = input_config.get("min_sequence_length")
    maximum = input_config.get("max_sequence_length")
    if not _is_int(minimum) or minimum < 1:
        raise ConfigurationError("input.min_sequence_length must be a positive integer")
    if maximum is not None and (not _is_int(maximum) or maximum < minimum):
        raise ConfigurationError(
            "input.max_sequence_length must be null or an integer not smaller than "
            "input.min_sequence_length"
        )

    output = _require_mapping(config, "output")
    line_width = output.get("line_width")
    if not _is_int(line_width) or not 1 <= line_width <= 1000:
        raise ConfigurationError("output.line_width must be an integer from 1 to 1000")

    logging_config = _require_mapping(config, "logging")
    level = logging_config.get("level")
    if not isinstance(level, str) or level.upper() not in {
        "CRITICAL",
        "ERROR",
        "WARNING",
        "INFO",
        "DEBUG",
    }:
        raise ConfigurationError("logging.level is not a recognized logging level")


def load_config(paths: Sequence[Path] = ()) -> ResolvedConfig:
    """Load YAML overlays in order on top of built-in defaults."""

    merged = deepcopy(DEFAULT_CONFIG)
    sources: list[Path] = []
    for raw_path in paths:
        path = Path(raw_path)
        try:
            with path.open("r", encoding="utf-8") as handle:
                loaded = yaml.safe_load(handle)
        except OSError as exc:
            raise ConfigurationError(
                f"Cannot read configuration {path}: {exc}"
            ) from exc
        except yaml.YAMLError as exc:
            raise ConfigurationError(
                f"Invalid YAML in configuration {path}: {exc}"
            ) from exc

        if loaded is None:
            loaded = {}
        if not isinstance(loaded, Mapping):
            raise ConfigurationError(f"Configuration {path} must contain a mapping")
        merged = _deep_merge(merged, loaded)
        sources.append(path.resolve())

    validate_config(merged)
    return ResolvedConfig(data=merged, source_files=tuple(sources))
