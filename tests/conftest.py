"""Shared pytest fixtures."""

from pathlib import Path
from typing import Any

import pytest


@pytest.fixture
def fasta_file(tmp_path: Path):
    """Return a helper that writes a temporary FASTA file."""

    def _write(content: str, name: str = "input.fasta") -> Path:
        path = tmp_path / name
        path.write_text(content, encoding="utf-8")
        return path

    return _write


@pytest.fixture
def canonical_record():
    """Build a minimal valid canonical evidence record for integration tests."""

    def _build(
        evidence_id: str,
        *,
        query_id: str = "q1",
        source: str = "blastp",
        evidence_class: str = "curated_annotation",
        group: str = "group-1",
        ec: str | None = "1.1.1.1",
        effect: str = "supports",
        status: str = "observed",
        feature: str = "family_compatibility",
        metrics: dict[str, Any] | None = None,
        sequence_sha256: str = "a" * 64,
        sequence_length: int = 10,
    ) -> dict[str, Any]:
        record: dict[str, Any] = {
            "schema_version": 1,
            "evidence_id": evidence_id,
            "query": {
                "query_id": query_id,
                "sequence_sha256": sequence_sha256,
                "sequence_length": sequence_length,
            },
            "source": {
                "id": source,
                "evidence_class": evidence_class,
                "correlation_group": group,
            },
            "record_status": status,
            "provenance": {
                "run_id": "integration-test",
                "stage_id": "provider",
                "generated_at": "2026-01-01T00:00:00Z",
                "parser": {"name": "fixture", "version": "1"},
                "tool": {"name": "fixture", "version": "1"},
                "databases": [],
                "raw_artifact": {
                    "path": "fixture.jsonl",
                    "format": "jsonl",
                },
                "configuration_sha256": "b" * 64,
                "input_sha256": "c" * 64,
            },
        }
        if status in {"observed", "negative"}:
            target: dict[str, Any]
            if ec:
                depth = next(
                    (index for index, part in enumerate(ec.split(".")) if part == "-"),
                    4,
                )
                target = {
                    "type": "ec",
                    "candidate_ec": {
                        "namespace": "EC",
                        "ec": ec,
                        "depth": depth,
                        "completeness": "complete" if depth == 4 else "partial",
                    },
                }
            else:
                target = {"type": "feature", "id": feature}
            record["assertion"] = {"target": target, "effect": effect}
        if status in {"negative", "missing", "failed"}:
            record["reason_code"] = f"fixture_{status}"
        if metrics is not None:
            record["metrics"] = metrics
        return record

    return _build
