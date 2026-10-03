"""Canonical evidence loading at the provider/integration boundary."""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

from enzynotation.exceptions import IntegrationError


class ProviderState(StrEnum):
    """Execution availability, kept separate from biological evidence."""

    SUCCESSFUL = "successful"
    SUCCESSFUL_ZERO = "successful_zero"
    DISABLED = "disabled"
    NOT_CONFIGURED = "not_configured"
    FAILED = "failed"
    UNAVAILABLE = "unavailable"
    NOT_RUN = "not_run"


@dataclass(frozen=True, slots=True)
class ProviderAvailability:
    """Availability and configured role of one evidence provider."""

    source_id: str
    state: ProviderState
    required: bool = False
    roles: tuple[str, ...] = ()
    reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "source_id": self.source_id,
            "state": self.state.value,
            "required": self.required,
            "roles": list(self.roles),
        }
        if self.reason:
            result["reason"] = self.reason
        return result


@dataclass(frozen=True, slots=True)
class EvidenceRecord:
    """Thin typed view over a validated canonical evidence document."""

    data: Mapping[str, Any]

    @property
    def evidence_id(self) -> str:
        return str(self.data["evidence_id"])

    @property
    def query_id(self) -> str:
        return str(self.data["query"]["query_id"])

    @property
    def source_id(self) -> str:
        return str(self.data["source"]["id"])

    @property
    def evidence_class(self) -> str:
        return str(self.data["source"]["evidence_class"])

    @property
    def correlation_group(self) -> str:
        return str(self.data["source"]["correlation_group"])

    @property
    def record_status(self) -> str:
        return str(self.data["record_status"])

    @property
    def effect(self) -> str | None:
        assertion = self.data.get("assertion")
        return str(assertion["effect"]) if isinstance(assertion, Mapping) else None

    @property
    def target(self) -> Mapping[str, Any] | None:
        assertion = self.data.get("assertion")
        if not isinstance(assertion, Mapping):
            return None
        target = assertion.get("target")
        return target if isinstance(target, Mapping) else None

    @property
    def candidate_ec(self) -> str | None:
        target = self.target
        if target is None or target.get("type") != "ec":
            return None
        candidate = target.get("candidate_ec")
        return str(candidate["ec"]) if isinstance(candidate, Mapping) else None

    @property
    def metrics(self) -> Mapping[str, Any]:
        value = self.data.get("metrics", {})
        return value if isinstance(value, Mapping) else {}


@dataclass(frozen=True, slots=True)
class EvidenceCollection:
    """Validated records with deterministic query grouping."""

    records: tuple[EvidenceRecord, ...]
    input_files: tuple[Path, ...] = ()

    def by_query(self) -> dict[str, tuple[EvidenceRecord, ...]]:
        grouped: dict[str, list[EvidenceRecord]] = defaultdict(list)
        for record in self.records:
            grouped[record.query_id].append(record)
        return {
            query: tuple(sorted(values, key=lambda item: item.evidence_id))
            for query, values in sorted(grouped.items())
        }


def evidence_validator(schema_path: Path) -> Draft202012Validator:
    """Load and validate the canonical evidence JSON Schema itself."""

    try:
        schema = json.loads(Path(schema_path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise IntegrationError(
            f"Cannot load evidence schema {schema_path}: {exc}"
        ) from exc
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema, format_checker=FormatChecker())


def load_evidence_files(
    paths: Sequence[Path], *, schema_path: Path
) -> EvidenceCollection:
    """Read JSONL canonical evidence, validate it, and reject duplicate IDs."""

    validator = evidence_validator(schema_path)
    records: list[EvidenceRecord] = []
    seen: set[str] = set()
    query_identity: dict[str, tuple[str, int]] = {}
    for raw_path in sorted((Path(path) for path in paths), key=lambda path: str(path)):
        try:
            lines = raw_path.read_text(encoding="utf-8").splitlines()
        except OSError as exc:
            raise IntegrationError(
                f"Cannot read evidence file {raw_path}: {exc}"
            ) from exc
        for line_number, line in enumerate(lines, start=1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise IntegrationError(
                    f"Malformed JSON in {raw_path}:{line_number}: {exc.msg}"
                ) from exc
            errors = sorted(
                validator.iter_errors(value), key=lambda error: list(error.path)
            )
            if errors:
                path = ".".join(str(part) for part in errors[0].absolute_path)
                location = f" at {path}" if path else ""
                raise IntegrationError(
                    f"Invalid evidence record in {raw_path}:{line_number}{location}: "
                    f"{errors[0].message}"
                )
            evidence_id = str(value["evidence_id"])
            if evidence_id in seen:
                raise IntegrationError(f"Duplicate evidence_id: {evidence_id}")
            seen.add(evidence_id)
            query = value["query"]
            identity = (str(query["sequence_sha256"]), int(query["sequence_length"]))
            previous = query_identity.setdefault(str(query["query_id"]), identity)
            if previous != identity:
                raise IntegrationError(
                    f"Conflicting sequence identity for query {query['query_id']}"
                )
            records.append(EvidenceRecord(value))
    records.sort(key=lambda record: (record.query_id, record.evidence_id))
    return EvidenceCollection(tuple(records), tuple(Path(path) for path in paths))


def records_for_sources(
    records: Iterable[EvidenceRecord], sources: Iterable[str]
) -> tuple[EvidenceRecord, ...]:
    """Return records from selected providers in stable order."""

    selected = set(sources)
    return tuple(record for record in records if record.source_id in selected)
