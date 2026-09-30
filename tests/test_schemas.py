"""Validation tests for the Milestone 0 JSON Schema contracts."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest
import yaml
from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import ValidationError

SCHEMA_DIRECTORY = Path("configs/schema")
EXAMPLE_DIRECTORY = Path("examples/configs")
INVALID_DIRECTORY = Path("tests/fixtures/schema/invalid")

CONTRACTS = {
    "pipeline": (
        SCHEMA_DIRECTORY / "pipeline.schema.json",
        EXAMPLE_DIRECTORY / "pipeline.example.yaml",
        INVALID_DIRECTORY / "pipeline.invalid.yaml",
    ),
    "family": (
        SCHEMA_DIRECTORY / "family.schema.json",
        EXAMPLE_DIRECTORY / "family.example.yaml",
        INVALID_DIRECTORY / "family.invalid.yaml",
    ),
    "ec-rules": (
        SCHEMA_DIRECTORY / "ec-rules.schema.json",
        EXAMPLE_DIRECTORY / "ec-rules.example.yaml",
        INVALID_DIRECTORY / "ec-rules.invalid.yaml",
    ),
    "evidence": (
        SCHEMA_DIRECTORY / "evidence.schema.json",
        EXAMPLE_DIRECTORY / "evidence.example.json",
        INVALID_DIRECTORY / "evidence.invalid.json",
    ),
}


def _load_document(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        if path.suffix == ".json":
            return json.load(handle)
        return yaml.safe_load(handle)


def _validator(schema_path: Path) -> Draft202012Validator:
    schema = _load_document(schema_path)
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema, format_checker=FormatChecker())


@pytest.mark.parametrize("contract", CONTRACTS)
def test_schema_is_valid_draft_2020_12(contract: str) -> None:
    schema_path, _, _ = CONTRACTS[contract]
    schema = _load_document(schema_path)
    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    Draft202012Validator.check_schema(schema)


@pytest.mark.parametrize("contract", CONTRACTS)
def test_valid_contract_example(contract: str) -> None:
    schema_path, valid_path, _ = CONTRACTS[contract]
    _validator(schema_path).validate(_load_document(valid_path))


@pytest.mark.parametrize("contract", CONTRACTS)
def test_invalid_contract_example(contract: str) -> None:
    schema_path, _, invalid_path = CONTRACTS[contract]
    with pytest.raises(ValidationError):
        _validator(schema_path).validate(_load_document(invalid_path))


def test_milestone_1_default_is_valid_pipeline_configuration() -> None:
    _validator(CONTRACTS["pipeline"][0]).validate(
        _load_document(Path("configs/default.yaml"))
    )


@pytest.mark.parametrize(
    ("ec", "depth", "completeness"),
    [
        ("1.2.3.4", 4, "complete"),
        ("1.2.3.-", 3, "partial"),
        ("1.2.-.-", 2, "partial"),
        ("1.-.-.-", 1, "partial"),
    ],
)
def test_candidate_ec_depth_and_completeness_are_consistent(
    ec: str, depth: int, completeness: str
) -> None:
    example = _load_document(CONTRACTS["evidence"][1])
    candidate = example["assertion"]["target"]["candidate_ec"]
    candidate.update(ec=ec, depth=depth, completeness=completeness)
    _validator(CONTRACTS["evidence"][0]).validate(example)


def test_candidate_ec_rejects_inconsistent_depth() -> None:
    example = _load_document(CONTRACTS["evidence"][1])
    candidate = example["assertion"]["target"]["candidate_ec"]
    candidate.update(ec="1.2.3.-", depth=4, completeness="complete")
    with pytest.raises(ValidationError):
        _validator(CONTRACTS["evidence"][0]).validate(example)


def test_missing_evidence_requires_reason_and_forbids_assertion() -> None:
    example = _load_document(CONTRACTS["evidence"][1])
    missing = copy.deepcopy(example)
    missing["record_status"] = "missing"
    missing["reason_code"] = "structure_not_provided"
    del missing["assertion"]
    _validator(CONTRACTS["evidence"][0]).validate(missing)

    del missing["reason_code"]
    with pytest.raises(ValidationError):
        _validator(CONTRACTS["evidence"][0]).validate(missing)


def test_negative_evidence_must_contradict_an_explicit_target() -> None:
    example = _load_document(CONTRACTS["evidence"][1])
    negative = copy.deepcopy(example)
    negative["record_status"] = "negative"
    negative["reason_code"] = "required_motif_absent"
    negative["assertion"]["effect"] = "contradicts"
    _validator(CONTRACTS["evidence"][0]).validate(negative)

    negative["assertion"]["effect"] = "supports"
    with pytest.raises(ValidationError):
        _validator(CONTRACTS["evidence"][0]).validate(negative)
