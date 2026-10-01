"""Strict parser for HMMER 3 domain-table output."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


class HmmerParseError(ValueError):
    """Raised when HMMER domtblout is malformed."""


@dataclass(frozen=True, slots=True)
class HmmerDomainHit:
    """One HMMER domain hit preserving sequence- and domain-level scores."""

    query_id: str
    query_length: int
    profile_name: str
    profile_accession: str
    profile_length: int
    sequence_evalue: float
    sequence_score: float
    domain_index: int
    domain_count: int
    conditional_evalue: float
    independent_evalue: float
    bit_score: float
    hmm_start: int
    hmm_end: int
    query_start: int
    query_end: int
    envelope_start: int
    envelope_end: int
    accuracy: float
    description: str
    raw_line_number: int

    @property
    def query_coverage(self) -> float:
        """Fraction of the query covered by this aligned domain."""

        return (abs(self.query_end - self.query_start) + 1) / self.query_length


def _integer(value: str, field: str, line_number: int) -> int:
    try:
        return int(value)
    except ValueError as exc:
        raise HmmerParseError(
            f"line {line_number}: {field} must be an integer, got {value!r}"
        ) from exc


def _number(value: str, field: str, line_number: int) -> float:
    try:
        number = float(value)
    except ValueError as exc:
        raise HmmerParseError(
            f"line {line_number}: {field} must be numeric, got {value!r}"
        ) from exc
    if number != number or number in {float("inf"), float("-inf")}:
        raise HmmerParseError(f"line {line_number}: {field} must be finite")
    return number


def parse_hmmer_domtblout(path: Path) -> tuple[HmmerDomainHit, ...]:
    """Parse HMMER 3 ``--domtblout`` output.

    Comment-only and empty files are valid successful zero-hit searches.
    """

    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        raise HmmerParseError(f"cannot read HMMER output {path}: {exc}") from exc

    hits: list[HmmerDomainHit] = []
    for line_number, line in enumerate(lines, start=1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        fields = line.split(maxsplit=22)
        if len(fields) < 22:
            raise HmmerParseError(
                f"line {line_number}: expected at least 22 HMMER fields, "
                f"found {len(fields)}"
            )
        profile_accession = fields[1] if fields[1] != "-" else fields[0]
        hit = HmmerDomainHit(
            query_id=fields[3],
            query_length=_integer(fields[5], "qlen", line_number),
            profile_name=fields[0],
            profile_accession=profile_accession,
            profile_length=_integer(fields[2], "tlen", line_number),
            sequence_evalue=_number(fields[6], "sequence E-value", line_number),
            sequence_score=_number(fields[7], "sequence score", line_number),
            domain_index=_integer(fields[9], "domain index", line_number),
            domain_count=_integer(fields[10], "domain count", line_number),
            conditional_evalue=_number(fields[11], "c-Evalue", line_number),
            independent_evalue=_number(fields[12], "i-Evalue", line_number),
            bit_score=_number(fields[13], "domain score", line_number),
            hmm_start=_integer(fields[15], "hmm from", line_number),
            hmm_end=_integer(fields[16], "hmm to", line_number),
            query_start=_integer(fields[17], "ali from", line_number),
            query_end=_integer(fields[18], "ali to", line_number),
            envelope_start=_integer(fields[19], "env from", line_number),
            envelope_end=_integer(fields[20], "env to", line_number),
            accuracy=_number(fields[21], "accuracy", line_number),
            description=fields[22] if len(fields) == 23 else "",
            raw_line_number=line_number,
        )
        if not hit.query_id or not hit.profile_name or not hit.profile_accession:
            raise HmmerParseError(f"line {line_number}: identifiers must be non-empty")
        if hit.query_length < 1 or hit.profile_length < 1:
            raise HmmerParseError(f"line {line_number}: qlen and tlen must be positive")
        if hit.domain_index < 1 or hit.domain_count < hit.domain_index:
            raise HmmerParseError(f"line {line_number}: invalid domain index/count")
        if (
            hit.sequence_evalue < 0
            or hit.conditional_evalue < 0
            or hit.independent_evalue < 0
        ):
            raise HmmerParseError(f"line {line_number}: E-values cannot be negative")
        if (
            hit.query_start < 1
            or hit.query_end < hit.query_start
            or hit.query_end > hit.query_length
        ):
            raise HmmerParseError(
                f"line {line_number}: invalid aligned query coordinates for qlen"
            )
        if (
            hit.hmm_start < 1
            or hit.hmm_end < hit.hmm_start
            or hit.hmm_end > hit.profile_length
        ):
            raise HmmerParseError(
                f"line {line_number}: invalid HMM coordinates for tlen"
            )
        if (
            hit.envelope_start < 1
            or hit.envelope_end < hit.envelope_start
            or hit.envelope_end > hit.query_length
        ):
            raise HmmerParseError(
                f"line {line_number}: invalid envelope coordinates for qlen"
            )
        if not 0 <= hit.accuracy <= 1:
            raise HmmerParseError(f"line {line_number}: accuracy must be from 0 to 1")
        hits.append(hit)
    return tuple(hits)
