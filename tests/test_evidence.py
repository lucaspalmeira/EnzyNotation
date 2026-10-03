"""Canonical evidence boundary tests."""

import json
from pathlib import Path

import pytest

from enzynotation.evidence import (
    EvidenceRecord,
    ProviderAvailability,
    ProviderState,
    load_evidence_files,
)
from enzynotation.exceptions import IntegrationError


def test_loads_and_groups_canonical_jsonl() -> None:
    collection = load_evidence_files(
        [Path("tests/fixtures/integration/evidence.jsonl")],
        schema_path=Path("configs/schema/evidence.schema.json"),
    )
    assert len(collection.records) == 2
    assert tuple(collection.by_query()) == ("q1",)


def test_rejects_schema_invalid_record() -> None:
    with pytest.raises(IntegrationError, match="Invalid evidence record"):
        load_evidence_files(
            [Path("tests/fixtures/integration/malformed.jsonl")],
            schema_path=Path("configs/schema/evidence.schema.json"),
        )


def test_rejects_duplicate_evidence_ids(tmp_path: Path, canonical_record) -> None:
    record = canonical_record("duplicate")
    path = tmp_path / "duplicate.jsonl"
    path.write_text(json.dumps(record) + "\n" + json.dumps(record) + "\n")
    with pytest.raises(IntegrationError, match="Duplicate evidence_id"):
        load_evidence_files(
            [path], schema_path=Path("configs/schema/evidence.schema.json")
        )


def test_rejects_conflicting_sequence_identity(
    tmp_path: Path, canonical_record
) -> None:
    first = canonical_record("first")
    second = canonical_record("second", sequence_sha256="d" * 64)
    path = tmp_path / "conflicting.jsonl"
    path.write_text(json.dumps(first) + "\n" + json.dumps(second) + "\n")
    with pytest.raises(IntegrationError, match="Conflicting sequence identity"):
        load_evidence_files(
            [path], schema_path=Path("configs/schema/evidence.schema.json")
        )


def test_missing_and_failed_records_are_not_biological_assertions(
    canonical_record,
) -> None:
    missing = EvidenceRecord(canonical_record("missing", status="missing"))
    failed = EvidenceRecord(canonical_record("failed", status="failed"))
    assert missing.effect is None and missing.candidate_ec is None
    assert failed.effect is None and failed.candidate_ec is None


def test_provider_availability_is_separate_and_serializable() -> None:
    failed = ProviderAvailability(
        "clean", ProviderState.FAILED, required=False, roles=("candidate_source",)
    )
    assert failed.to_dict()["state"] == "failed"
    assert failed.to_dict()["required"] is False
