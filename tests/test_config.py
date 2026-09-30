"""Tests for layered configuration loading."""

from pathlib import Path

import pytest
import yaml

from enzynotation.config import DEFAULT_CONFIG, load_config
from enzynotation.exceptions import ConfigurationError


def test_defaults_are_valid() -> None:
    config = load_config()
    assert config.data == DEFAULT_CONFIG
    assert config.source_files == ()


def test_repository_default_matches_builtin() -> None:
    repository_default = yaml.safe_load(Path("configs/default.yaml").read_text())
    assert repository_default == DEFAULT_CONFIG


def test_overlays_are_deep_merged_in_order(tmp_path: Path) -> None:
    first = tmp_path / "first.yaml"
    second = tmp_path / "second.yaml"
    first.write_text("input:\n  min_sequence_length: 5\n", encoding="utf-8")
    second.write_text(
        "input:\n  min_sequence_length: 10\noutput:\n  line_width: 80\n",
        encoding="utf-8",
    )

    config = load_config([first, second])

    assert config.section("input")["min_sequence_length"] == 10
    assert config.section("input")["allow_terminal_stop"] is True
    assert config.section("output")["line_width"] == 80
    assert config.source_files == (first.resolve(), second.resolve())


@pytest.mark.parametrize(
    "content,match",
    [
        ("schema_version: 2\n", "Unsupported schema_version"),
        ("input:\n  allowed_residues: AACD\n", "unique uppercase"),
        ("input:\n  min_sequence_length: 0\n", "positive integer"),
        (
            "input:\n  min_sequence_length: 10\n  max_sequence_length: 5\n",
            "not smaller",
        ),
        ("output:\n  line_width: 0\n", "from 1 to 1000"),
        ("logging:\n  level: LOUD\n", "recognized logging level"),
    ],
)
def test_invalid_configuration_is_rejected(
    tmp_path: Path, content: str, match: str
) -> None:
    path = tmp_path / "invalid.yaml"
    path.write_text(content, encoding="utf-8")
    with pytest.raises(ConfigurationError, match=match):
        load_config([path])


def test_invalid_yaml_is_wrapped(tmp_path: Path) -> None:
    path = tmp_path / "invalid.yaml"
    path.write_text("input: [\n", encoding="utf-8")
    with pytest.raises(ConfigurationError, match="Invalid YAML"):
        load_config([path])


def test_missing_configuration_is_reported(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError, match="Cannot read configuration"):
        load_config([tmp_path / "missing.yaml"])
