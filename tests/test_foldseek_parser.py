"""Tests for Foldseek parsing, interval aggregation, filtering, and ranking."""

from __future__ import annotations

from pathlib import Path

import pytest

from enzynotation.parsers.foldseek import (
    FoldseekFilterSettings,
    FoldseekParseError,
    aggregate_foldseek_alignments,
    parse_foldseek_tabular,
    rank_and_filter_foldseek_hits,
)

FIXTURES = Path("tests/fixtures/foldseek")


def _settings(**overrides) -> FoldseekFilterSettings:
    values = {
        "maximum_evalue": 1.0,
        "minimum_query_coverage": 0.0,
        "minimum_target_coverage": 0.0,
        "minimum_aligned_length": 1,
        "maximum_retained_hits_per_query": None,
        "threshold_status": "operational",
    }
    values.update(overrides)
    return FoldseekFilterSettings(**values)


def test_parser_reads_explicit_metrics_and_multiple_hits() -> None:
    rows = parse_foldseek_tabular(FIXTURES / "hits.tsv")

    assert len(rows) == 4
    assert rows[0].query_id == "query-structure-0001"
    assert rows[0].percent_identity == 40.0
    assert rows[0].query_tm_score == 0.60
    assert {row.target_id for row in rows} == {"refA", "refB", "refC"}


def test_zero_hits_are_valid_and_malformed_rows_fail() -> None:
    assert parse_foldseek_tabular(FIXTURES / "zero_hits.tsv") == ()
    with pytest.raises(FoldseekParseError, match="expected 16 fields"):
        parse_foldseek_tabular(FIXTURES / "malformed.tsv")


def test_overlapping_alignments_use_interval_union() -> None:
    hits = aggregate_foldseek_alignments(parse_foldseek_tabular(FIXTURES / "hits.tsv"))
    hit = next(item for item in hits if item.target_id == "refA")

    assert hit.alignment_count == 2
    assert hit.alignment_length_sum == 101
    assert hit.aligned_length == 90
    assert hit.query_coverage == 0.9
    assert hit.target_coverage == pytest.approx(95 / 120)
    assert hit.query_coverage <= 1.0
    assert hit.target_coverage <= 1.0


def test_ranking_and_filters_are_deterministic_and_traceable() -> None:
    hits = aggregate_foldseek_alignments(parse_foldseek_tabular(FIXTURES / "hits.tsv"))
    ranked = rank_and_filter_foldseek_hits(
        hits,
        _settings(
            maximum_evalue=1e-5,
            minimum_query_coverage=0.5,
            minimum_target_coverage=0.5,
            minimum_aligned_length=40,
            maximum_retained_hits_per_query=1,
        ),
    )
    first_query = [
        item for item in ranked if item.hit.query_id == "query-structure-0001"
    ]

    assert [item.hit.target_id for item in first_query] == ["refB", "refA"]
    assert first_query[0].passed
    assert not first_query[1].passed
    assert "maximum_retained_hits_per_query" in first_query[1].filter_reasons
    ref_c = next(item for item in ranked if item.hit.target_id == "refC")
    assert {criterion.criterion_id for criterion in ref_c.criteria} == {
        "maximum_evalue",
        "minimum_query_coverage",
        "minimum_target_coverage",
        "minimum_aligned_length",
    }


@pytest.mark.parametrize(
    "replacement, message",
    [
        ("1.2", "fident"),
        ("nan", "finite"),
    ],
)
def test_invalid_scores_are_rejected(
    tmp_path: Path, replacement: str, message: str
) -> None:
    values = (FIXTURES / "hits.tsv").read_text(encoding="utf-8").splitlines()[0]
    fields = values.split("\t")
    fields[2] = replacement
    path = tmp_path / "invalid.tsv"
    path.write_text("\t".join(fields) + "\n", encoding="utf-8")

    with pytest.raises(FoldseekParseError, match=message):
        parse_foldseek_tabular(path)
