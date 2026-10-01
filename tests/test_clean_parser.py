"""Tests for CLEAN CSV parsing, ranking, and filtering semantics."""

from pathlib import Path

import pytest

from enzynotation.parsers.clean import (
    CleanParseError,
    parse_clean_csv,
    rank_and_filter_predictions,
)

FIXTURES = Path("tests/fixtures/clean")


def _rank(path: str, *, direction: str, **overrides):
    parsed = parse_clean_csv(FIXTURES / path)
    settings = {
        "ranking_direction": direction,
        "maximum_retained_candidates": None,
        "threshold": None,
        "threshold_comparison": None,
        "allowed_ec_depths": None,
        "allow_partial_ec": True,
        **overrides,
    }
    return parsed, rank_and_filter_predictions(parsed.predictions, **settings)


def test_parser_preserves_complete_partial_duplicate_and_no_prediction_rows() -> None:
    parsed = parse_clean_csv(FIXTURES / "maxsep_distance.csv")

    assert parsed.query_ids == ("q1", "q2")
    assert [item.ec for item in parsed.predictions] == [
        "3.2.1.26",
        "3.2.1.-",
        "3.2.1.26",
    ]
    assert [item.original_rank for item in parsed.predictions] == [1, 2, 3]
    assert parsed.predictions[0].ec_complete
    assert parsed.predictions[1].ec_depth == 3
    assert not parsed.predictions[1].ec_complete
    assert parsed.predictions[0].raw_metric_value == "2.5000"
    assert parsed.predictions[0].metric_value == 2.5
    assert parsed.rejections == ()


def test_lower_is_better_ranking_does_not_replace_original_clean_rank() -> None:
    _, ranked = _rank("maxsep_distance.csv", direction="lower_is_better")

    assert [item.prediction.original_rank for item in ranked] == [1, 2, 3]
    assert [item.normalized_rank for item in ranked] == [1, 3, 2]
    assert [item.retained_rank for item in ranked] == [1, 3, 2]


def test_higher_is_better_ranking_and_maximum_retained_candidates() -> None:
    _, ranked = _rank(
        "gmm_confidence.csv",
        direction="higher_is_better",
        maximum_retained_candidates=2,
    )
    q1 = [item for item in ranked if item.prediction.query_id == "q1"]

    assert [item.normalized_rank for item in q1] == [2, 1, 3]
    assert [item.passed for item in q1] == [True, True, False]
    assert q1[2].filter_reasons == ("maximum_retained_candidates",)


def test_unknown_direction_preserves_order_without_normalized_rank() -> None:
    parsed = parse_clean_csv(FIXTURES / "gmm_confidence.csv")
    ranked = rank_and_filter_predictions(
        parsed.predictions,
        ranking_direction="unknown",
        maximum_retained_candidates=None,
        threshold=None,
        threshold_comparison=None,
        allowed_ec_depths=None,
        allow_partial_ec=True,
    )

    assert all(item.normalized_rank is None for item in ranked)
    assert [
        item.prediction.original_rank
        for item in ranked
        if item.prediction.query_id == "q1"
    ] == [1, 2, 3]


@pytest.mark.parametrize(
    ("direction", "comparison", "threshold", "expected"),
    [
        ("lower_is_better", "lte", 2.6, [True, False, False]),
        ("higher_is_better", "gte", 0.8, [True, True, False, False]),
    ],
)
def test_threshold_direction_is_explicit(
    direction: str, comparison: str, threshold: float, expected: list[bool]
) -> None:
    fixture = (
        "maxsep_distance.csv"
        if direction == "lower_is_better"
        else "gmm_confidence.csv"
    )
    _, ranked = _rank(
        fixture,
        direction=direction,
        threshold=threshold,
        threshold_comparison=comparison,
    )
    assert [item.passed for item in ranked] == expected


def test_depth_and_partial_filters_are_traceable() -> None:
    _, ranked = _rank(
        "maxsep_distance.csv",
        direction="lower_is_better",
        allowed_ec_depths=(4,),
        allow_partial_ec=False,
    )

    partial = ranked[1]
    assert not partial.passed
    assert set(partial.filter_reasons) == {"allowed_ec_depth", "partial_ec_allowed"}


def test_malformed_candidates_are_all_preserved_as_rejections() -> None:
    parsed = parse_clean_csv(FIXTURES / "malformed_candidates.csv")

    assert parsed.predictions == ()
    assert [item.reason_code for item in parsed.rejections] == [
        "invalid_ec",
        "invalid_metric_value",
        "malformed_prediction",
        "empty_prediction",
    ]
    assert all(item.raw_line_number == 1 for item in parsed.rejections)


def test_empty_result_is_valid() -> None:
    parsed = parse_clean_csv(FIXTURES / "empty.csv")
    assert parsed.predictions == ()
    assert parsed.rejections == ()
    assert parsed.query_ids == ()


def test_structurally_malformed_csv_fails() -> None:
    with pytest.raises(CleanParseError, match="malformed CLEAN CSV"):
        parse_clean_csv(FIXTURES / "malformed_csv.csv")


def test_opaque_metric_preserves_numeric_value_without_implied_direction() -> None:
    parsed = parse_clean_csv(FIXTURES / "opaque.csv")
    ranked = rank_and_filter_predictions(
        parsed.predictions,
        ranking_direction="unknown",
        maximum_retained_candidates=None,
        threshold=None,
        threshold_comparison=None,
        allowed_ec_depths=None,
        allow_partial_ec=True,
    )
    assert parsed.predictions[0].raw_metric_value == "0.7310"
    assert parsed.predictions[0].metric_value == 0.731
    assert ranked[0].normalized_rank is None
