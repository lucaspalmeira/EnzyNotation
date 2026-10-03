"""Parser for classic TM-align pairwise output."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


class TMAlignParseError(ValueError):
    """Raised when TM-align output lacks required orientation-aware metrics."""


@dataclass(frozen=True, slots=True)
class TMAlignResult:
    query_name: str
    target_name: str
    query_length: int
    target_length: int
    aligned_length: int
    rmsd: float
    sequence_identity: float
    tm_score_normalized_by_query: float
    tm_score_normalized_by_target: float


_NAME = re.compile(r"Name of (?:Chain|Structure)_([12]):\s*(.+?)\s*$", re.MULTILINE)
_LENGTH = re.compile(r"Length of (?:Chain|Structure)_([12]):\s*(\d+)\s+residues")
_SUMMARY = re.compile(
    r"Aligned length=\s*(\d+),\s*RMSD=\s*([0-9.eE+-]+),\s*"
    r"Seq_ID=(?:n_identical/n_aligned=\s*)?([0-9.eE+-]+)"
)
_SCORE = re.compile(
    r"TM-score=\s*([0-9.eE+-]+).*normalized by length of "
    r"(?:Chain|Structure)_([12])"
)


def parse_tmalign_output(path: Path) -> TMAlignResult:
    """Parse both normalization directions from deterministic query-first output."""

    try:
        text = Path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise TMAlignParseError(f"cannot read TM-align output {path}: {exc}") from exc
    names = {match.group(1): match.group(2).strip() for match in _NAME.finditer(text)}
    lengths = {match.group(1): int(match.group(2)) for match in _LENGTH.finditer(text)}
    scores = {match.group(2): float(match.group(1)) for match in _SCORE.finditer(text)}
    summary = _SUMMARY.search(text)
    missing = [
        label
        for label, condition in (
            ("Structure_1 name", "1" not in names),
            ("Structure_2 name", "2" not in names),
            ("Structure_1 length", "1" not in lengths),
            ("Structure_2 length", "2" not in lengths),
            ("query-normalized TM-score", "1" not in scores),
            ("target-normalized TM-score", "2" not in scores),
            ("alignment summary", summary is None),
        )
        if condition
    ]
    if missing:
        raise TMAlignParseError("missing TM-align fields: " + ", ".join(missing))
    assert summary is not None
    result = TMAlignResult(
        query_name=names["1"],
        target_name=names["2"],
        query_length=lengths["1"],
        target_length=lengths["2"],
        aligned_length=int(summary.group(1)),
        rmsd=float(summary.group(2)),
        sequence_identity=float(summary.group(3)),
        tm_score_normalized_by_query=scores["1"],
        tm_score_normalized_by_target=scores["2"],
    )
    if result.query_length < 1 or result.target_length < 1:
        raise TMAlignParseError("TM-align structure lengths must be positive")
    if not 1 <= result.aligned_length <= min(result.query_length, result.target_length):
        raise TMAlignParseError("TM-align aligned length is outside valid bounds")
    if result.rmsd < 0:
        raise TMAlignParseError("TM-align RMSD cannot be negative")
    for name, value in (
        ("sequence identity", result.sequence_identity),
        ("query-normalized TM-score", result.tm_score_normalized_by_query),
        ("target-normalized TM-score", result.tm_score_normalized_by_target),
    ):
        if not 0 <= value <= 1:
            raise TMAlignParseError(f"TM-align {name} must be from 0 to 1")
    return result
