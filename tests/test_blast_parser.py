"""Tests for strict BLAST parsing, aggregation, filtering, and ranking."""

from __future__ import annotations

from pathlib import Path

import pytest

from enzynotation.parsers.blast import (
    BlastFilterSettings,
    BlastParseError,
    aggregate_hsps,
    filter_and_rank_hits,
    parse_blast_tabular,
)

FIXTURES = Path("tests/fixtures/blast")


def _settings(**overrides) -> BlastFilterSettings:
    values = {
        "maximum_evalue": 10.0,
        "minimum_query_coverage": 0.0,
        "minimum_subject_coverage": 0.0,
        "minimum_percent_identity": 0.0,
        "minimum_aligned_length": 1,
        "maximum_retained_hits_per_query": None,
    }
    values.update(overrides)
    return BlastFilterSettings(**values)


def test_parser_reads_multiple_queries_and_hits() -> None:
    hsps = parse_blast_tabular(FIXTURES / "hits.tsv")

    assert len(hsps) == 6
    assert {hsp.qseqid for hsp in hsps} == {"q1", "q2"}
    assert len({hsp.sseqid for hsp in hsps if hsp.qseqid == "q1"}) == 3
    assert hsps[0].percent_identity == 80.0
    assert hsps[0].evalue == 1e-40


def test_zero_hit_file_is_valid(tmp_path: Path) -> None:
    empty = tmp_path / "empty.tsv"
    empty.write_text("", encoding="utf-8")

    assert parse_blast_tabular(empty) == ()
    assert parse_blast_tabular(FIXTURES / "zero_hits.tsv") == ()


def test_malformed_rows_are_rejected() -> None:
    with pytest.raises(BlastParseError, match="expected 14 fields"):
        parse_blast_tabular(FIXTURES / "malformed.tsv")


def test_invalid_numeric_and_coordinate_values_are_rejected(tmp_path: Path) -> None:
    invalid = tmp_path / "invalid.tsv"
    invalid.write_text(
        "q\ts\t101\t5\t0\t0\t1\t5\t1\t5\t0\t2\t5\t5\n",
        encoding="utf-8",
    )
    with pytest.raises(BlastParseError, match="pident"):
        parse_blast_tabular(invalid)

    invalid.write_text(
        "q\ts\t50\t5\t0\t0\t1\t6\t1\t5\t0\t2\t5\t5\n",
        encoding="utf-8",
    )
    with pytest.raises(BlastParseError, match="query coordinates"):
        parse_blast_tabular(invalid)


def test_overlapping_hsps_use_union_coverage() -> None:
    hits = aggregate_hsps(parse_blast_tabular(FIXTURES / "hits.tsv"))
    hit = next(item for item in hits if item.subject_id.startswith("sp|P11111"))

    assert hit.hsp_count == 2
    assert hit.hsp_alignment_length_sum == 111
    assert hit.aligned_length == 100
    assert hit.query_covered_residues == 100
    assert hit.subject_covered_residues == 100
    assert hit.query_coverage == 1.0
    assert hit.subject_coverage == pytest.approx(100 / 120)
    assert hit.percent_identity == pytest.approx((80 * 60 + 70 * 51) / 111)
    assert hit.raw_line_numbers == (1, 2)


def test_reversed_subject_coordinates_are_normalized() -> None:
    hits = aggregate_hsps(parse_blast_tabular(FIXTURES / "hits.tsv"))
    hit = next(item for item in hits if item.subject_id.startswith("ref|NP_333"))

    assert hit.subject_covered_residues == 50
    assert hit.subject_coverage == pytest.approx(50 / 90)


def test_ranking_is_deterministic_and_per_query() -> None:
    hits = aggregate_hsps(parse_blast_tabular(FIXTURES / "hits.tsv"))
    ranked = filter_and_rank_hits(hits, _settings())
    q1 = [item for item in ranked if item.hit.query_id == "q1"]
    q2 = [item for item in ranked if item.hit.query_id == "q2"]

    assert [item.rank for item in q1] == [1, 2, 3]
    assert q1[0].hit.subject_id.startswith("sp|P11111")
    assert [item.hit.subject_id for item in q2] == [
        "custom_subject",
        "ref|NP_333.1|",
    ]


def test_configurable_filters_preserve_traceable_decisions() -> None:
    hits = aggregate_hsps(parse_blast_tabular(FIXTURES / "hits.tsv"))
    ranked = filter_and_rank_hits(
        hits,
        _settings(
            maximum_evalue=1e-15,
            minimum_query_coverage=0.5,
            minimum_subject_coverage=0.5,
            minimum_percent_identity=60,
            minimum_aligned_length=40,
            maximum_retained_hits_per_query=1,
        ),
    )

    retained = [item for item in ranked if item.passed]
    assert [(item.hit.query_id, item.rank) for item in retained] == [("q1", 1)]
    unmapped = next(item for item in ranked if item.hit.subject_id == "lcl|UNMAPPED")
    assert "maximum_evalue" in unmapped.filter_reasons
    assert "minimum_query_coverage" in unmapped.filter_reasons
    second_q1 = next(
        item
        for item in ranked
        if item.hit.query_id == "q1" and item.hit.subject_id.startswith("tr|")
    )
    assert "maximum_retained_hits_per_query" in second_q1.filter_reasons
    assert all(criterion.criterion_id for criterion in second_q1.criteria)
