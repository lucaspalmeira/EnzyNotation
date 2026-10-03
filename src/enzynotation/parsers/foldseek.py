"""Foldseek tabular parsing, overlap-safe aggregation, filtering, and ranking."""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

FOLDSEEK_OUTPUT_FIELDS = (
    "query",
    "target",
    "fident",
    "alnlen",
    "qstart",
    "qend",
    "tstart",
    "tend",
    "evalue",
    "bits",
    "qlen",
    "tlen",
    "alntmscore",
    "qtmscore",
    "ttmscore",
    "lddt",
)


class FoldseekParseError(ValueError):
    """Raised when Foldseek output violates the explicit field contract."""


@dataclass(frozen=True, slots=True)
class FoldseekAlignment:
    query_id: str
    target_id: str
    percent_identity: float
    alignment_length: int
    query_start: int
    query_end: int
    target_start: int
    target_end: int
    evalue: float
    bit_score: float
    query_length: int
    target_length: int
    alignment_tm_score: float
    query_tm_score: float
    target_tm_score: float
    lddt: float
    raw_line_number: int

    @property
    def query_interval(self) -> tuple[int, int]:
        return min(self.query_start, self.query_end), max(
            self.query_start, self.query_end
        )

    @property
    def target_interval(self) -> tuple[int, int]:
        return min(self.target_start, self.target_end), max(
            self.target_start, self.target_end
        )


@dataclass(frozen=True, slots=True)
class FoldseekHit:
    query_id: str
    target_id: str
    percent_identity: float
    aligned_length: int
    query_coverage: float
    target_coverage: float
    evalue: float
    bit_score: float
    alignment_tm_score: float
    query_tm_score: float
    target_tm_score: float
    lddt: float
    query_length: int
    target_length: int
    alignment_count: int
    alignment_length_sum: int
    raw_line_numbers: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class FoldseekFilterSettings:
    maximum_evalue: float
    minimum_query_coverage: float
    minimum_target_coverage: float
    minimum_aligned_length: int
    maximum_retained_hits_per_query: int | None
    threshold_status: str


@dataclass(frozen=True, slots=True)
class FoldseekCriterion:
    criterion_id: str
    metric: str
    operator: str
    threshold: float | int
    observed_value: float | int
    passed: bool


@dataclass(frozen=True, slots=True)
class RankedFoldseekHit:
    hit: FoldseekHit
    rank: int
    retained_rank: int | None
    passed: bool
    filter_reasons: tuple[str, ...]
    criteria: tuple[FoldseekCriterion, ...]


def _float(value: str, field: str, line: int) -> float:
    try:
        parsed = float(value)
    except ValueError as exc:
        raise FoldseekParseError(
            f"line {line}: {field} must be numeric, got {value!r}"
        ) from exc
    if not math.isfinite(parsed):
        raise FoldseekParseError(f"line {line}: {field} must be finite")
    return parsed


def _int(value: str, field: str, line: int) -> int:
    try:
        return int(value)
    except ValueError as exc:
        raise FoldseekParseError(
            f"line {line}: {field} must be an integer, got {value!r}"
        ) from exc


def parse_foldseek_tabular(path: Path) -> tuple[FoldseekAlignment, ...]:
    """Parse the exact Foldseek `--format-output` contract; empty is valid."""

    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        raise FoldseekParseError(f"cannot read Foldseek output {path}: {exc}") from exc
    alignments: list[FoldseekAlignment] = []
    for line_number, line in enumerate(lines, start=1):
        if not line.strip() or line.startswith("#"):
            continue
        values = line.split("\t")
        if len(values) != len(FOLDSEEK_OUTPUT_FIELDS):
            raise FoldseekParseError(
                f"line {line_number}: expected {len(FOLDSEEK_OUTPUT_FIELDS)} "
                f"fields, found {len(values)}"
            )
        fields = dict(zip(FOLDSEEK_OUTPUT_FIELDS, values, strict=True))
        if not fields["query"] or not fields["target"]:
            raise FoldseekParseError(f"line {line_number}: IDs must be non-empty")
        identity_fraction = _float(fields["fident"], "fident", line_number)
        if not 0 <= identity_fraction <= 1:
            raise FoldseekParseError(
                f"line {line_number}: fident must be a fraction from 0 to 1"
            )
        item = FoldseekAlignment(
            query_id=fields["query"],
            target_id=fields["target"],
            percent_identity=identity_fraction * 100.0,
            alignment_length=_int(fields["alnlen"], "alnlen", line_number),
            query_start=_int(fields["qstart"], "qstart", line_number),
            query_end=_int(fields["qend"], "qend", line_number),
            target_start=_int(fields["tstart"], "tstart", line_number),
            target_end=_int(fields["tend"], "tend", line_number),
            evalue=_float(fields["evalue"], "evalue", line_number),
            bit_score=_float(fields["bits"], "bits", line_number),
            query_length=_int(fields["qlen"], "qlen", line_number),
            target_length=_int(fields["tlen"], "tlen", line_number),
            alignment_tm_score=_float(fields["alntmscore"], "alntmscore", line_number),
            query_tm_score=_float(fields["qtmscore"], "qtmscore", line_number),
            target_tm_score=_float(fields["ttmscore"], "ttmscore", line_number),
            lddt=_float(fields["lddt"], "lddt", line_number),
            raw_line_number=line_number,
        )
        if item.alignment_length < 1 or item.query_length < 1 or item.target_length < 1:
            raise FoldseekParseError(f"line {line_number}: lengths must be positive")
        if item.evalue < 0 or item.bit_score < 0:
            raise FoldseekParseError(
                f"line {line_number}: evalue and bits cannot be negative"
            )
        for field, score in (
            ("alntmscore", item.alignment_tm_score),
            ("qtmscore", item.query_tm_score),
            ("ttmscore", item.target_tm_score),
            ("lddt", item.lddt),
        ):
            if not 0 <= score <= 1:
                raise FoldseekParseError(
                    f"line {line_number}: {field} must be from 0 to 1"
                )
        qmin, qmax = item.query_interval
        tmin, tmax = item.target_interval
        if qmin < 1 or qmax > item.query_length:
            raise FoldseekParseError(
                f"line {line_number}: query coordinates exceed qlen"
            )
        if tmin < 1 or tmax > item.target_length:
            raise FoldseekParseError(
                f"line {line_number}: target coordinates exceed tlen"
            )
        alignments.append(item)
    return tuple(alignments)


def _union_length(intervals: list[tuple[int, int]]) -> int:
    if not intervals:
        return 0
    ordered = sorted(intervals)
    start, end = ordered[0]
    total = 0
    for next_start, next_end in ordered[1:]:
        if next_start <= end + 1:
            end = max(end, next_end)
        else:
            total += end - start + 1
            start, end = next_start, next_end
    return total + end - start + 1


def aggregate_foldseek_alignments(
    alignments: tuple[FoldseekAlignment, ...],
) -> tuple[FoldseekHit, ...]:
    """Aggregate query-target rows with interval-union coverage."""

    grouped: dict[tuple[str, str], list[FoldseekAlignment]] = defaultdict(list)
    for alignment in alignments:
        grouped[(alignment.query_id, alignment.target_id)].append(alignment)
    hits: list[FoldseekHit] = []
    for (query_id, target_id), group in sorted(grouped.items()):
        query_lengths = {item.query_length for item in group}
        target_lengths = {item.target_length for item in group}
        if len(query_lengths) != 1 or len(target_lengths) != 1:
            raise FoldseekParseError(
                f"inconsistent lengths for {query_id!r} and {target_id!r}"
            )
        query_length = query_lengths.pop()
        target_length = target_lengths.pop()
        query_covered = _union_length([item.query_interval for item in group])
        target_covered = _union_length([item.target_interval for item in group])
        length_sum = sum(item.alignment_length for item in group)
        hits.append(
            FoldseekHit(
                query_id=query_id,
                target_id=target_id,
                percent_identity=sum(
                    item.percent_identity * item.alignment_length for item in group
                )
                / length_sum,
                aligned_length=query_covered,
                query_coverage=min(1.0, query_covered / query_length),
                target_coverage=min(1.0, target_covered / target_length),
                evalue=min(item.evalue for item in group),
                bit_score=max(item.bit_score for item in group),
                alignment_tm_score=max(item.alignment_tm_score for item in group),
                query_tm_score=max(item.query_tm_score for item in group),
                target_tm_score=max(item.target_tm_score for item in group),
                lddt=max(item.lddt for item in group),
                query_length=query_length,
                target_length=target_length,
                alignment_count=len(group),
                alignment_length_sum=length_sum,
                raw_line_numbers=tuple(sorted(item.raw_line_number for item in group)),
            )
        )
    return tuple(hits)


def rank_and_filter_foldseek_hits(
    hits: tuple[FoldseekHit, ...], settings: FoldseekFilterSettings
) -> tuple[RankedFoldseekHit, ...]:
    """Apply operational filters and deterministic inspection ranking."""

    grouped: dict[str, list[FoldseekHit]] = defaultdict(list)
    for hit in hits:
        grouped[hit.query_id].append(hit)
    result: list[RankedFoldseekHit] = []
    for query_id in sorted(grouped):
        ordered = sorted(
            grouped[query_id],
            key=lambda item: (
                item.evalue,
                -item.bit_score,
                -item.query_coverage,
                -item.query_tm_score,
                item.target_id,
            ),
        )
        retained = 0
        for rank, hit in enumerate(ordered, start=1):
            criteria = (
                FoldseekCriterion(
                    "maximum_evalue",
                    "evalue",
                    "lte",
                    settings.maximum_evalue,
                    hit.evalue,
                    hit.evalue <= settings.maximum_evalue,
                ),
                FoldseekCriterion(
                    "minimum_query_coverage",
                    "query_coverage",
                    "gte",
                    settings.minimum_query_coverage,
                    hit.query_coverage,
                    hit.query_coverage >= settings.minimum_query_coverage,
                ),
                FoldseekCriterion(
                    "minimum_target_coverage",
                    "target_coverage",
                    "gte",
                    settings.minimum_target_coverage,
                    hit.target_coverage,
                    hit.target_coverage >= settings.minimum_target_coverage,
                ),
                FoldseekCriterion(
                    "minimum_aligned_length",
                    "aligned_length",
                    "gte",
                    settings.minimum_aligned_length,
                    hit.aligned_length,
                    hit.aligned_length >= settings.minimum_aligned_length,
                ),
            )
            passed = all(item.passed for item in criteria)
            reasons = [item.criterion_id for item in criteria if not item.passed]
            retained_rank = None
            if passed:
                if (
                    settings.maximum_retained_hits_per_query is None
                    or retained < settings.maximum_retained_hits_per_query
                ):
                    retained += 1
                    retained_rank = retained
                else:
                    passed = False
                    reasons.append("maximum_retained_hits_per_query")
            result.append(
                RankedFoldseekHit(
                    hit=hit,
                    rank=rank,
                    retained_rank=retained_rank,
                    passed=passed,
                    filter_reasons=tuple(reasons),
                    criteria=criteria,
                )
            )
    return tuple(result)
