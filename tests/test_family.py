"""Tests for generic family-profile loading and validation."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml
from jsonschema import Draft202012Validator, FormatChecker

from enzynotation.family import FamilyConfigurationError, load_family_profile

FIXTURES = Path("tests/fixtures/domains")


def test_load_family_domain_motif_and_relationship_rules() -> None:
    profile = load_family_profile(FIXTURES / "family.yaml")

    assert profile.family_id == "fixture_family"
    assert len(profile.domains) == 3
    assert {rule.requirement for rule in profile.domains} == {
        "required",
        "expected",
        "forbidden",
    }
    assert len(profile.motifs) == 4
    assert profile.motifs[0].pattern_type == "literal"
    assert profile.motifs[0].catalytic_residues[0].name == "catalytic_cysteine"
    assert profile.motif_relationships[0].maximum_distance == 20


def test_gh32_example_validates_and_loads_without_special_code() -> None:
    path = Path("examples/configs/gh32.example.yaml")
    schema = json.loads(Path("configs/schema/family.schema.json").read_text())
    document = yaml.safe_load(path.read_text())
    Draft202012Validator(schema, format_checker=FormatChecker()).validate(document)

    profile = load_family_profile(path)
    assert profile.family_id == "gh32_example"
    assert {rule.signature_id for rule in profile.domains} == {"PF00251", "PF08244"}
    assert {rule.pattern for rule in profile.motifs} == {"[WF]MNDPNG", "RDP", "EC"}
    assert all(not rule.candidate_ecs for rule in profile.motifs)


def _write_profile(tmp_path: Path, mutation) -> Path:
    document = yaml.safe_load((FIXTURES / "family.yaml").read_text())
    mutation(document)
    path = tmp_path / "family.yaml"
    path.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")
    return path


def test_invalid_regex_is_rejected(tmp_path: Path) -> None:
    path = _write_profile(
        tmp_path,
        lambda value: value["rules"]["motifs"][1].update(pattern="[broken"),
    )
    with pytest.raises(FamilyConfigurationError, match="valid regex"):
        load_family_profile(path)


def test_empty_matching_regex_is_rejected(tmp_path: Path) -> None:
    path = _write_profile(
        tmp_path,
        lambda value: value["rules"]["motifs"][1].update(pattern="A*"),
    )
    with pytest.raises(FamilyConfigurationError, match="must not match an empty"):
        load_family_profile(path)


def test_unknown_relationship_motif_is_rejected(tmp_path: Path) -> None:
    path = _write_profile(
        tmp_path,
        lambda value: value["rules"]["motif_relationships"][0].update(
            downstream_motif="missing_rule"
        ),
    )
    with pytest.raises(FamilyConfigurationError, match="unknown motif"):
        load_family_profile(path)


def test_unknown_domain_context_and_invalid_literal_offset_are_rejected(
    tmp_path: Path,
) -> None:
    context_path = _write_profile(
        tmp_path,
        lambda value: value["rules"]["motifs"][0].update(
            position={
                "relative_to": "domain_start",
                "domain_rule_id": "missing_domain",
                "min_offset": 0,
                "max_offset": 5,
            }
        ),
    )
    with pytest.raises(FamilyConfigurationError, match="unknown domain rule"):
        load_family_profile(context_path)

    offset_path = _write_profile(
        tmp_path,
        lambda value: value["rules"]["motifs"][0]["catalytic_residues"][0].update(
            motif_offset=3
        ),
    )
    with pytest.raises(FamilyConfigurationError, match="outside its literal"):
        load_family_profile(offset_path)


def test_reversed_count_and_distance_ranges_are_rejected(tmp_path: Path) -> None:
    count_path = _write_profile(
        tmp_path,
        lambda value: value["rules"]["domains"][0].update(min_count=2, max_count=1),
    )
    with pytest.raises(FamilyConfigurationError, match="max_count"):
        load_family_profile(count_path)

    distance_path = _write_profile(
        tmp_path,
        lambda value: value["rules"]["motif_relationships"][0].update(
            minimum_distance=10, maximum_distance=5
        ),
    )
    with pytest.raises(FamilyConfigurationError, match="maximum_distance"):
        load_family_profile(distance_path)
