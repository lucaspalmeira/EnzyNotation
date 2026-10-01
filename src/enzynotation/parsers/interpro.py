"""Parser for InterProScan TSV output."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


class InterProParseError(ValueError):
    """Raised when InterProScan TSV output is malformed."""


@dataclass(frozen=True, slots=True)
class InterProHit:
    """One member-signature match from InterProScan TSV."""

    query_id: str
    sequence_md5: str
    sequence_length: int
    member_database: str
    member_signature: str
    signature_description: str
    start: int
    end: int
    score: float | None
    status: str
    run_date: str
    interpro_accession: str | None
    interpro_description: str | None
    go_terms: tuple[str, ...]
    pathways: tuple[str, ...]
    raw_line_number: int

    @property
    def query_coverage(self) -> float:
        """Fraction of the query covered by the signature match."""

        return (self.end - self.start + 1) / self.sequence_length


def _optional(value: str) -> str | None:
    stripped = value.strip()
    return None if stripped in {"", "-"} else stripped


def _items(value: str) -> tuple[str, ...]:
    optional = _optional(value)
    if optional is None:
        return ()
    return tuple(item.strip() for item in optional.split("|") if item.strip())


def parse_interpro_tsv(path: Path) -> tuple[InterProHit, ...]:
    """Parse standard 13- to 15-column InterProScan TSV output."""

    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        raise InterProParseError(f"cannot read InterPro output {path}: {exc}") from exc

    hits: list[InterProHit] = []
    for line_number, line in enumerate(lines, start=1):
        if not line.strip() or line.startswith("#"):
            continue
        fields = line.split("\t")
        if not 13 <= len(fields) <= 15:
            raise InterProParseError(
                f"line {line_number}: expected 13 to 15 fields, found {len(fields)}"
            )
        try:
            sequence_length = int(fields[2])
            start = int(fields[6])
            end = int(fields[7])
            score = None if fields[8] in {"", "-"} else float(fields[8])
        except ValueError as exc:
            raise InterProParseError(
                f"line {line_number}: invalid numeric field"
            ) from exc
        if sequence_length < 1 or start < 1 or end < start or end > sequence_length:
            raise InterProParseError(
                f"line {line_number}: invalid sequence length or coordinates"
            )
        if score is not None and (
            score != score or score in {float("inf"), float("-inf")}
        ):
            raise InterProParseError(f"line {line_number}: score must be finite")
        required = (fields[0], fields[1], fields[3], fields[4], fields[9], fields[10])
        if any(not value for value in required):
            raise InterProParseError(
                f"line {line_number}: required identifiers must be non-empty"
            )
        hits.append(
            InterProHit(
                query_id=fields[0],
                sequence_md5=fields[1],
                sequence_length=sequence_length,
                member_database=fields[3],
                member_signature=fields[4],
                signature_description=fields[5],
                start=start,
                end=end,
                score=score,
                status=fields[9],
                run_date=fields[10],
                interpro_accession=_optional(fields[11]),
                interpro_description=_optional(fields[12]),
                go_terms=_items(fields[13]) if len(fields) >= 14 else (),
                pathways=_items(fields[14]) if len(fields) >= 15 else (),
                raw_line_number=line_number,
            )
        )
    return tuple(hits)
