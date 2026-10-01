"""Parser and configuration-driven normalization for CLEAN CSV output."""

from __future__ import annotations

import csv
import math
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from enzynotation.ec import normalize_ec, parse_ec
from enzynotation.exceptions import ECNumberError


class CleanParseError(ValueError):
    """Raised when a CLEAN result cannot be parsed as CSV."""


@dataclass(frozen=True, slots=True)
class CleanPrediction:
    """One valid CLEAN EC candidate in its original serialized order."""

    query_id: str
    ec: str
    ec_depth: int
    ec_complete: bool
    raw_metric_value: str
    metric_value: float
    original_rank: int
    raw_line_number: int
    raw_token: str


@dataclass(frozen=True, slots=True)
class CleanRejectedPrediction:
    """One malformed CLEAN row or candidate retained for inspection."""

    query_id: str | None
    original_rank: int | None
    raw_line_number: int
    raw_token: str
    raw_row: str
    reason_code: str
    message: str


@dataclass(frozen=True, slots=True)
class CleanParseResult:
    """Valid candidates, rejected records, and query rows from CLEAN output."""

    predictions: tuple[CleanPrediction, ...]
    rejections: tuple[CleanRejectedPrediction, ...]
    query_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CleanFilterCriterion:
    """One traceable filtering decision for a CLEAN candidate."""

    criterion_id: str
    metric: str
    operator: str
    threshold: str | int | float | bool | None
    observed_value: str | int | float | bool | None
    passed: bool


@dataclass(frozen=True, slots=True)
class RankedCleanPrediction:
    """A CLEAN candidate with optional normalized rank and filter decisions."""

    prediction: CleanPrediction
    normalized_rank: int | None
    retained_rank: int | None
    passed: bool
    filter_reasons: tuple[str, ...]
    criteria: tuple[CleanFilterCriterion, ...]


def _reject(
    *,
    query_id: str | None,
    rank: int | None,
    line_number: int,
    token: str,
    row: list[str],
    reason: str,
    message: str,
) -> CleanRejectedPrediction:
    return CleanRejectedPrediction(
        query_id=query_id,
        original_rank=rank,
        raw_line_number=line_number,
        raw_token=token,
        raw_row=",".join(row),
        reason_code=reason,
        message=message,
    )


def parse_clean_csv(path: Path) -> CleanParseResult:
    """Parse upstream CLEAN rows of ``query,EC:number/value,...``.

    Empty files and query-only rows are valid zero-prediction results. Invalid
    candidates are returned as rejections instead of being silently discarded.
    """

    source = Path(path)
    try:
        handle = source.open("r", encoding="utf-8", newline="")
    except (OSError, UnicodeError) as exc:
        raise CleanParseError(f"cannot read CLEAN output {path}: {exc}") from exc

    predictions: list[CleanPrediction] = []
    rejections: list[CleanRejectedPrediction] = []
    query_ids: list[str] = []
    try:
        with handle:
            reader = csv.reader(handle, strict=True)
            for line_number, row in enumerate(reader, start=1):
                if not row or all(not field.strip() for field in row):
                    continue
                query_id = row[0].strip()
                if not query_id:
                    rejections.append(
                        _reject(
                            query_id=None,
                            rank=None,
                            line_number=line_number,
                            token="",
                            row=row,
                            reason="missing_query_id",
                            message="CLEAN row has an empty query identifier",
                        )
                    )
                    continue
                query_ids.append(query_id)
                for rank, field in enumerate(row[1:], start=1):
                    token = field.strip()
                    if not token:
                        rejections.append(
                            _reject(
                                query_id=query_id,
                                rank=rank,
                                line_number=line_number,
                                token=token,
                                row=row,
                                reason="empty_prediction",
                                message="CLEAN prediction field is empty",
                            )
                        )
                        continue
                    if "/" not in token:
                        rejections.append(
                            _reject(
                                query_id=query_id,
                                rank=rank,
                                line_number=line_number,
                                token=token,
                                row=row,
                                reason="malformed_prediction",
                                message="CLEAN prediction must contain EC/value",
                            )
                        )
                        continue
                    ec_text, raw_metric = token.rsplit("/", 1)
                    try:
                        ec = normalize_ec(ec_text)
                        parsed_ec = parse_ec(ec)
                    except ECNumberError as exc:
                        rejections.append(
                            _reject(
                                query_id=query_id,
                                rank=rank,
                                line_number=line_number,
                                token=token,
                                row=row,
                                reason="invalid_ec",
                                message=str(exc),
                            )
                        )
                        continue
                    try:
                        metric_value = float(raw_metric)
                    except ValueError:
                        metric_value = math.nan
                    if not math.isfinite(metric_value):
                        rejections.append(
                            _reject(
                                query_id=query_id,
                                rank=rank,
                                line_number=line_number,
                                token=token,
                                row=row,
                                reason="invalid_metric_value",
                                message=(
                                    "CLEAN metric must be a finite numeric value, got "
                                    f"{raw_metric!r}"
                                ),
                            )
                        )
                        continue
                    predictions.append(
                        CleanPrediction(
                            query_id=query_id,
                            ec=ec,
                            ec_depth=parsed_ec.depth,
                            ec_complete=parsed_ec.is_complete,
                            raw_metric_value=raw_metric,
                            metric_value=metric_value,
                            original_rank=rank,
                            raw_line_number=line_number,
                            raw_token=token,
                        )
                    )
    except (csv.Error, UnicodeError) as exc:
        raise CleanParseError(f"malformed CLEAN CSV {path}: {exc}") from exc
    return CleanParseResult(
        predictions=tuple(predictions),
        rejections=tuple(rejections),
        query_ids=tuple(query_ids),
    )


def rank_and_filter_predictions(
    predictions: tuple[CleanPrediction, ...],
    *,
    ranking_direction: str,
    maximum_retained_candidates: int | None,
    threshold: float | None,
    threshold_comparison: str | None,
    allowed_ec_depths: tuple[int, ...] | None,
    allow_partial_ec: bool,
) -> tuple[RankedCleanPrediction, ...]:
    """Apply declared metric semantics without replacing CLEAN's own ranking."""

    grouped: dict[str, list[CleanPrediction]] = defaultdict(list)
    for prediction in predictions:
        grouped[prediction.query_id].append(prediction)

    ranked: list[RankedCleanPrediction] = []
    for query_id in dict.fromkeys(prediction.query_id for prediction in predictions):
        values = grouped[query_id]
        if ranking_direction == "lower_is_better":
            normalized = sorted(
                values, key=lambda item: (item.metric_value, item.original_rank)
            )
        elif ranking_direction == "higher_is_better":
            normalized = sorted(
                values, key=lambda item: (-item.metric_value, item.original_rank)
            )
        else:
            normalized = list(values)
        normalized_ranks = {
            id(item): index for index, item in enumerate(normalized, start=1)
        }
        retained = 0
        interim: list[RankedCleanPrediction] = []
        for prediction in normalized:
            criteria: list[CleanFilterCriterion] = []
            if allowed_ec_depths is not None:
                criteria.append(
                    CleanFilterCriterion(
                        "allowed_ec_depth",
                        "ec_depth",
                        "in",
                        ",".join(str(value) for value in allowed_ec_depths),
                        prediction.ec_depth,
                        prediction.ec_depth in allowed_ec_depths,
                    )
                )
            if not allow_partial_ec:
                criteria.append(
                    CleanFilterCriterion(
                        "partial_ec_allowed",
                        "ec_complete",
                        "eq",
                        True,
                        prediction.ec_complete,
                        prediction.ec_complete,
                    )
                )
            if threshold is not None and threshold_comparison is not None:
                threshold_passed = (
                    prediction.metric_value <= threshold
                    if threshold_comparison == "lte"
                    else prediction.metric_value >= threshold
                )
                criteria.append(
                    CleanFilterCriterion(
                        "metric_threshold",
                        "metric_value",
                        threshold_comparison,
                        threshold,
                        prediction.metric_value,
                        threshold_passed,
                    )
                )
            passed = all(criterion.passed for criterion in criteria)
            reasons = [
                criterion.criterion_id for criterion in criteria if not criterion.passed
            ]
            retained_rank: int | None = None
            if passed:
                if (
                    maximum_retained_candidates is None
                    or retained < maximum_retained_candidates
                ):
                    retained += 1
                    retained_rank = retained
                else:
                    passed = False
                    reasons.append("maximum_retained_candidates")
                    criteria.append(
                        CleanFilterCriterion(
                            "maximum_retained_candidates",
                            "retained_rank",
                            "lte",
                            maximum_retained_candidates,
                            retained + 1,
                            False,
                        )
                    )
            interim.append(
                RankedCleanPrediction(
                    prediction=prediction,
                    normalized_rank=(
                        normalized_ranks[id(prediction)]
                        if ranking_direction != "unknown"
                        else None
                    ),
                    retained_rank=retained_rank,
                    passed=passed,
                    filter_reasons=tuple(reasons),
                    criteria=tuple(criteria),
                )
            )
        ranked.extend(sorted(interim, key=lambda item: item.prediction.original_rank))
    return tuple(ranked)
