"""Protein FASTA parsing, validation, and normalized output."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path

from enzynotation.exceptions import ConfigurationError, FastaError
from enzynotation.models import (
    FastaValidationResult,
    IssueSeverity,
    ProteinRecord,
    ValidationIssue,
)


@dataclass(frozen=True, slots=True)
class FastaValidationOptions:
    """Rules controlling protein FASTA validation and normalization."""

    allowed_residues: frozenset[str]
    allow_terminal_stop: bool = True
    min_sequence_length: int = 1
    max_sequence_length: int | None = None
    uppercase: bool = True

    @classmethod
    def from_mapping(cls, config: Mapping[str, object]) -> FastaValidationOptions:
        """Build options from a validated ``input`` configuration section."""

        try:
            return cls(
                allowed_residues=frozenset(str(config["allowed_residues"])),
                allow_terminal_stop=bool(config["allow_terminal_stop"]),
                min_sequence_length=int(config["min_sequence_length"]),
                max_sequence_length=(
                    None
                    if config["max_sequence_length"] is None
                    else int(config["max_sequence_length"])
                ),
                uppercase=bool(config["uppercase"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ConfigurationError("Invalid input configuration section") from exc


def _finalize_record(
    header: str,
    header_line: int,
    sequence_lines: list[tuple[int, str]],
    options: FastaValidationOptions,
    seen_ids: set[str],
) -> tuple[ProteinRecord | None, list[ValidationIssue]]:
    issues: list[ValidationIssue] = []
    if not header:
        issues.append(
            ValidationIssue(
                IssueSeverity.ERROR,
                "empty_header",
                "FASTA header does not contain an identifier",
                line=header_line,
            )
        )
        return None, issues

    header_parts = header.split(maxsplit=1)
    identifier = header_parts[0]
    description = header_parts[1] if len(header_parts) == 2 else ""
    if identifier in seen_ids:
        issues.append(
            ValidationIssue(
                IssueSeverity.ERROR,
                "duplicate_identifier",
                f"Duplicate FASTA identifier: {identifier}",
                line=header_line,
                record_id=identifier,
            )
        )
    else:
        seen_ids.add(identifier)

    if not sequence_lines:
        issues.append(
            ValidationIssue(
                IssueSeverity.ERROR,
                "empty_sequence",
                "FASTA record has no sequence",
                line=header_line,
                record_id=identifier,
            )
        )
        return ProteinRecord(identifier, description, ""), issues

    raw_sequence = "".join(content for _, content in sequence_lines)
    sequence = raw_sequence.upper() if options.uppercase else raw_sequence

    whitespace_line = next(
        (
            line
            for line, content in sequence_lines
            if any(char.isspace() for char in content)
        ),
        None,
    )
    if whitespace_line is not None:
        issues.append(
            ValidationIssue(
                IssueSeverity.ERROR,
                "sequence_whitespace",
                "Whitespace inside a sequence line is not allowed",
                line=whitespace_line,
                record_id=identifier,
            )
        )

    if "*" in sequence:
        if (
            options.allow_terminal_stop
            and sequence.endswith("*")
            and "*" not in sequence[:-1]
        ):
            sequence = sequence[:-1]
            issues.append(
                ValidationIssue(
                    IssueSeverity.WARNING,
                    "terminal_stop_removed",
                    "Terminal stop marker was removed during normalization",
                    record_id=identifier,
                )
            )
        else:
            issues.append(
                ValidationIssue(
                    IssueSeverity.ERROR,
                    "invalid_stop_marker",
                    "Stop marker is not permitted at this sequence position",
                    record_id=identifier,
                )
            )

    invalid = sorted(set(sequence) - options.allowed_residues)
    if invalid:
        rendered = ", ".join(repr(character) for character in invalid)
        issues.append(
            ValidationIssue(
                IssueSeverity.ERROR,
                "invalid_residue",
                f"Sequence contains unsupported residue characters: {rendered}",
                record_id=identifier,
            )
        )

    length = len(sequence)
    if length < options.min_sequence_length:
        issues.append(
            ValidationIssue(
                IssueSeverity.ERROR,
                "sequence_too_short",
                f"Sequence length {length} is below minimum "
                f"{options.min_sequence_length}",
                record_id=identifier,
            )
        )
    if options.max_sequence_length is not None and length > options.max_sequence_length:
        issues.append(
            ValidationIssue(
                IssueSeverity.ERROR,
                "sequence_too_long",
                f"Sequence length {length} exceeds maximum "
                f"{options.max_sequence_length}",
                record_id=identifier,
            )
        )

    return ProteinRecord(identifier, description, sequence), issues


def validate_fasta(
    path: Path,
    options: FastaValidationOptions,
) -> FastaValidationResult:
    """Read and validate a protein FASTA file without silently dropping records."""

    source = Path(path)
    try:
        lines = source.read_text(encoding="utf-8-sig").splitlines()
    except (OSError, UnicodeError) as exc:
        raise FastaError(f"Cannot read FASTA file {source}: {exc}") from exc

    records: list[ProteinRecord] = []
    issues: list[ValidationIssue] = []
    seen_ids: set[str] = set()
    header: str | None = None
    header_line = 0
    sequence_lines: list[tuple[int, str]] = []

    for line_number, line in enumerate(lines, start=1):
        if line.startswith(">"):
            if header is not None:
                record, record_issues = _finalize_record(
                    header, header_line, sequence_lines, options, seen_ids
                )
                if record is not None:
                    records.append(record)
                issues.extend(record_issues)
            header = line[1:].strip()
            header_line = line_number
            sequence_lines = []
        elif not line.strip():
            continue
        elif header is None:
            issues.append(
                ValidationIssue(
                    IssueSeverity.ERROR,
                    "sequence_before_header",
                    "Sequence data appears before the first FASTA header",
                    line=line_number,
                )
            )
        else:
            sequence_lines.append((line_number, line.strip("\r\n")))

    if header is not None:
        record, record_issues = _finalize_record(
            header, header_line, sequence_lines, options, seen_ids
        )
        if record is not None:
            records.append(record)
        issues.extend(record_issues)

    if not lines or header is None:
        issues.append(
            ValidationIssue(
                IssueSeverity.ERROR,
                "no_fasta_records",
                "Input does not contain a FASTA record",
            )
        )

    return FastaValidationResult(source, tuple(records), tuple(issues))


def write_normalized_fasta(
    records: Iterable[ProteinRecord],
    path: Path,
    *,
    line_width: int = 60,
) -> None:
    """Write normalized protein records with deterministic wrapping."""

    if line_width < 1:
        raise ValueError("line_width must be positive")

    destination = Path(path)
    output_lines: list[str] = []
    for record in records:
        output_lines.append(f">{record.header}")
        output_lines.extend(
            record.sequence[index : index + line_width]
            for index in range(0, len(record.sequence), line_width)
        )

    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text("\n".join(output_lines) + "\n", encoding="utf-8")
    except OSError as exc:
        raise FastaError(f"Cannot write normalized FASTA {destination}: {exc}") from exc
