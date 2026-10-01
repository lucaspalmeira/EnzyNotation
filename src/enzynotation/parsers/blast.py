"""Strict parsing, HSP aggregation, filtering, and ranking for BLAST outfmt 6."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

BLAST_OUTFMT_FIELDS = (
    "qseqid",
    "sseqid",
    "pident",
    "length",
    "mismatch",
    "gapopen",
    "qstart",
    "qend",
    "sstart",
    "send",
    "evalue",
    "bitscore",
    "qlen",
    "slen",
)


class BlastParseError(ValueError):
    """Raised when BLAST output violates the configured tabular contract."""


@dataclass(frozen=True, slots=True)
class BlastHSP:
    """One unmodified BLAST high-scoring segment pair."""

    qseqid: str
    sseqid: str
    percent_identity: float
    alignment_length: int
    mismatches: int
    gap_opens: int
    query_start: int
    query_end: int
    subject_start: int
    subject_end: int
    evalue: float
    bit_score: float
    query_length: int
    subject_length: int
    raw_line_number: int

    @property
    def query_interval(self) -> tuple[int, int]:
        return min(self.query_start, self.query_end), max(
            self.query_start, self.query_end
        )

    @property
    def subject_interval(self) -> tuple[int, int]:
        return min(self.subject_start, self.subject_end), max(
            self.subject_start, self.subject_end
        )


@dataclass(frozen=True, slots=True)
class BlastAggregatedHit:
    """A query-subject hit aggregated without double-counting coverage."""

    query_id: str
    subject_id: str
    percent_identity: float
    aligned_length: int
    query_covered_residues: int
    subject_covered_residues: int
    query_coverage: float
    subject_coverage: float
    evalue: float
    bit_score: float
    hsp_count: int
    hsp_alignment_length_sum: int
    hsp_bit_score_sum: float
    query_length: int
    subject_length: int
    raw_line_numbers: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class BlastFilterSettings:
    """Operational thresholds applied after HSP aggregation."""

    maximum_evalue: float
    minimum_query_coverage: float
    minimum_subject_coverage: float
    minimum_percent_identity: float
    minimum_aligned_length: int
    maximum_retained_hits_per_query: int | None


@dataclass(frozen=True, slots=True)
class FilterCriterion:
    """Traceable evaluation of one configured hit threshold."""

    criterion_id: str
    metric: str
    operator: str
    threshold: float | int | None
    observed_value: float | int
    passed: bool


@dataclass(frozen=True, slots=True)
class RankedBlastHit:
    """An aggregated hit with deterministic rank and filter decisions."""

    hit: BlastAggregatedHit
    rank: int
    retained_rank: int | None
    passed: bool
    filter_reasons: tuple[str, ...]
    criteria: tuple[FilterCriterion, ...]


def _parse_int(value: str, field: str, line_number: int) -> int:
    try:
        return int(value)
    except ValueError as exc:
        raise BlastParseError(
            f"line {line_number}: {field} must be an integer, got {value!r}"
        ) from exc


def _parse_float(value: str, field: str, line_number: int) -> float:
    try:
        parsed = float(value)
    except ValueError as exc:
        raise BlastParseError(
            f"line {line_number}: {field} must be numeric, got {value!r}"
        ) from exc
    if parsed != parsed or parsed in {float("inf"), float("-inf")}:
        raise BlastParseError(f"line {line_number}: {field} must be finite")
    return parsed


def parse_blast_tabular(path: Path) -> tuple[BlastHSP, ...]:
    """Parse the exact EnzyNotation BLAST outfmt 6 field list.

    Empty files and blank lines are valid and represent a successful zero-hit
    search. Comment lines are tolerated for fixture and diagnostic use.
    """

    hsps: list[BlastHSP] = []
    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        raise BlastParseError(f"cannot read BLAST output {path}: {exc}") from exc

    for line_number, line in enumerate(lines, start=1):
        if not line.strip() or line.startswith("#"):
            continue
        values = line.split("\t")
        if len(values) != len(BLAST_OUTFMT_FIELDS):
            raise BlastParseError(
                f"line {line_number}: expected {len(BLAST_OUTFMT_FIELDS)} fields, "
                f"found {len(values)}"
            )
        fields = dict(zip(BLAST_OUTFMT_FIELDS, values, strict=True))
        if not fields["qseqid"] or not fields["sseqid"]:
            raise BlastParseError(f"line {line_number}: sequence IDs must be non-empty")

        hsp = BlastHSP(
            qseqid=fields["qseqid"],
            sseqid=fields["sseqid"],
            percent_identity=_parse_float(fields["pident"], "pident", line_number),
            alignment_length=_parse_int(fields["length"], "length", line_number),
            mismatches=_parse_int(fields["mismatch"], "mismatch", line_number),
            gap_opens=_parse_int(fields["gapopen"], "gapopen", line_number),
            query_start=_parse_int(fields["qstart"], "qstart", line_number),
            query_end=_parse_int(fields["qend"], "qend", line_number),
            subject_start=_parse_int(fields["sstart"], "sstart", line_number),
            subject_end=_parse_int(fields["send"], "send", line_number),
            evalue=_parse_float(fields["evalue"], "evalue", line_number),
            bit_score=_parse_float(fields["bitscore"], "bitscore", line_number),
            query_length=_parse_int(fields["qlen"], "qlen", line_number),
            subject_length=_parse_int(fields["slen"], "slen", line_number),
            raw_line_number=line_number,
        )
        if not 0 <= hsp.percent_identity <= 100:
            raise BlastParseError(f"line {line_number}: pident must be from 0 to 100")
        if hsp.alignment_length < 1 or hsp.query_length < 1 or hsp.subject_length < 1:
            raise BlastParseError(
                f"line {line_number}: length, qlen, and slen must be positive"
            )
        if hsp.mismatches < 0 or hsp.gap_opens < 0:
            raise BlastParseError(
                f"line {line_number}: mismatch and gapopen cannot be negative"
            )
        if hsp.evalue < 0 or hsp.bit_score < 0:
            raise BlastParseError(
                f"line {line_number}: evalue and bitscore cannot be negative"
            )
        query_min, query_max = hsp.query_interval
        subject_min, subject_max = hsp.subject_interval
        if query_min < 1 or query_max > hsp.query_length:
            raise BlastParseError(f"line {line_number}: query coordinates exceed qlen")
        if subject_min < 1 or subject_max > hsp.subject_length:
            raise BlastParseError(
                f"line {line_number}: subject coordinates exceed slen"
            )
        hsps.append(hsp)
    return tuple(hsps)


def _union_length(intervals: list[tuple[int, int]]) -> int:
    if not intervals:
        return 0
    ordered = sorted(intervals)
    total = 0
    current_start, current_end = ordered[0]
    for start, end in ordered[1:]:
        if start <= current_end + 1:
            current_end = max(current_end, end)
        else:
            total += current_end - current_start + 1
            current_start, current_end = start, end
    return total + current_end - current_start + 1


def aggregate_hsps(hsps: tuple[BlastHSP, ...]) -> tuple[BlastAggregatedHit, ...]:
    """Aggregate HSPs by query/subject using union coverage.

    Query and subject coverage use the union of inclusive coordinate intervals,
    so overlapping HSPs cannot inflate coverage above 100%. Percent identity is
    the HSP-length-weighted mean; minimum E-value and maximum bit score are used
    for deterministic ranking. Raw HSP line numbers and sums remain available.
    """

    grouped: dict[tuple[str, str], list[BlastHSP]] = defaultdict(list)
    for hsp in hsps:
        grouped[(hsp.qseqid, hsp.sseqid)].append(hsp)

    aggregated: list[BlastAggregatedHit] = []
    for (query_id, subject_id), group in sorted(grouped.items()):
        query_lengths = {hsp.query_length for hsp in group}
        subject_lengths = {hsp.subject_length for hsp in group}
        if len(query_lengths) != 1 or len(subject_lengths) != 1:
            raise BlastParseError(
                f"inconsistent qlen/slen for query {query_id!r} and subject "
                f"{subject_id!r}"
            )
        query_length = query_lengths.pop()
        subject_length = subject_lengths.pop()
        query_covered = _union_length([hsp.query_interval for hsp in group])
        subject_covered = _union_length([hsp.subject_interval for hsp in group])
        length_sum = sum(hsp.alignment_length for hsp in group)
        weighted_identity = (
            sum(hsp.percent_identity * hsp.alignment_length for hsp in group)
            / length_sum
        )
        aggregated.append(
            BlastAggregatedHit(
                query_id=query_id,
                subject_id=subject_id,
                percent_identity=weighted_identity,
                aligned_length=query_covered,
                query_covered_residues=query_covered,
                subject_covered_residues=subject_covered,
                query_coverage=query_covered / query_length,
                subject_coverage=subject_covered / subject_length,
                evalue=min(hsp.evalue for hsp in group),
                bit_score=max(hsp.bit_score for hsp in group),
                hsp_count=len(group),
                hsp_alignment_length_sum=length_sum,
                hsp_bit_score_sum=sum(hsp.bit_score for hsp in group),
                query_length=query_length,
                subject_length=subject_length,
                raw_line_numbers=tuple(sorted(hsp.raw_line_number for hsp in group)),
            )
        )
    return tuple(aggregated)


def _ranking_key(hit: BlastAggregatedHit) -> tuple:
    return (
        hit.evalue,
        -hit.bit_score,
        -hit.query_coverage,
        -hit.percent_identity,
        hit.subject_id,
    )


def _threshold_criteria(
    hit: BlastAggregatedHit, settings: BlastFilterSettings
) -> tuple[FilterCriterion, ...]:
    return (
        FilterCriterion(
            "maximum_evalue",
            "evalue",
            "lte",
            settings.maximum_evalue,
            hit.evalue,
            hit.evalue <= settings.maximum_evalue,
        ),
        FilterCriterion(
            "minimum_query_coverage",
            "query_coverage",
            "gte",
            settings.minimum_query_coverage,
            hit.query_coverage,
            hit.query_coverage >= settings.minimum_query_coverage,
        ),
        FilterCriterion(
            "minimum_subject_coverage",
            "subject_coverage",
            "gte",
            settings.minimum_subject_coverage,
            hit.subject_coverage,
            hit.subject_coverage >= settings.minimum_subject_coverage,
        ),
        FilterCriterion(
            "minimum_percent_identity",
            "percent_identity",
            "gte",
            settings.minimum_percent_identity,
            hit.percent_identity,
            hit.percent_identity >= settings.minimum_percent_identity,
        ),
        FilterCriterion(
            "minimum_aligned_length",
            "aligned_length",
            "gte",
            settings.minimum_aligned_length,
            hit.aligned_length,
            hit.aligned_length >= settings.minimum_aligned_length,
        ),
    )


def filter_and_rank_hits(
    hits: tuple[BlastAggregatedHit, ...], settings: BlastFilterSettings
) -> tuple[RankedBlastHit, ...]:
    """Apply configured thresholds and deterministic per-query ranking."""

    grouped: dict[str, list[BlastAggregatedHit]] = defaultdict(list)
    for hit in hits:
        grouped[hit.query_id].append(hit)

    ranked: list[RankedBlastHit] = []
    for query_id in sorted(grouped):
        ordered = sorted(grouped[query_id], key=_ranking_key)
        threshold_pass_count = 0
        for rank, hit in enumerate(ordered, start=1):
            criteria = list(_threshold_criteria(hit, settings))
            reasons = [
                criterion.criterion_id for criterion in criteria if not criterion.passed
            ]
            retained_rank: int | None = None
            if not reasons:
                threshold_pass_count += 1
                retained_rank = threshold_pass_count
                limit = settings.maximum_retained_hits_per_query
                passes_limit = limit is None or retained_rank <= limit
                criteria.append(
                    FilterCriterion(
                        "maximum_retained_hits_per_query",
                        "retained_rank",
                        "lte",
                        limit,
                        retained_rank,
                        passes_limit,
                    )
                )
                if not passes_limit:
                    reasons.append("maximum_retained_hits_per_query")
            ranked.append(
                RankedBlastHit(
                    hit=hit,
                    rank=rank,
                    retained_rank=retained_rank,
                    passed=not reasons,
                    filter_reasons=tuple(reasons),
                    criteria=tuple(criteria),
                )
            )
    return tuple(ranked)
