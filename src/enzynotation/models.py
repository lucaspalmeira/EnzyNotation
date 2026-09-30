"""Core data models shared by Milestone 1 components."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any


class IssueSeverity(StrEnum):
    """Severity of an input-validation issue."""

    ERROR = "error"
    WARNING = "warning"


@dataclass(frozen=True, slots=True)
class ProteinRecord:
    """A normalized protein FASTA record."""

    identifier: str
    description: str
    sequence: str

    @property
    def header(self) -> str:
        """Return the normalized FASTA header without the leading ``>``."""

        if self.description:
            return f"{self.identifier} {self.description}"
        return self.identifier


@dataclass(frozen=True, slots=True)
class ValidationIssue:
    """A machine-readable problem found while validating an input file."""

    severity: IssueSeverity
    code: str
    message: str
    line: int | None = None
    record_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Serialize the issue for JSON reports."""

        result: dict[str, Any] = {
            "severity": self.severity.value,
            "code": self.code,
            "message": self.message,
        }
        if self.line is not None:
            result["line"] = self.line
        if self.record_id is not None:
            result["record_id"] = self.record_id
        return result


@dataclass(frozen=True, slots=True)
class FastaValidationResult:
    """Records and issues produced by FASTA validation."""

    source: Path
    records: tuple[ProteinRecord, ...]
    issues: tuple[ValidationIssue, ...]

    @property
    def errors(self) -> tuple[ValidationIssue, ...]:
        """Return validation errors."""

        return tuple(
            issue for issue in self.issues if issue.severity is IssueSeverity.ERROR
        )

    @property
    def warnings(self) -> tuple[ValidationIssue, ...]:
        """Return validation warnings."""

        return tuple(
            issue for issue in self.issues if issue.severity is IssueSeverity.WARNING
        )

    @property
    def is_valid(self) -> bool:
        """Whether the input contains records and has no validation errors."""

        return bool(self.records) and not self.errors

    def to_dict(self) -> dict[str, Any]:
        """Serialize the validation summary for JSON reports."""

        return {
            "source": str(self.source),
            "valid": self.is_valid,
            "record_count": len(self.records),
            "error_count": len(self.errors),
            "warning_count": len(self.warnings),
            "issues": [issue.to_dict() for issue in self.issues],
        }
