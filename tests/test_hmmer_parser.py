"""Tests for HMMER domtblout parsing and normalization."""

from pathlib import Path

import pytest

from enzynotation.domains import hmmer_observations
from enzynotation.parsers.hmmer import HmmerParseError, parse_hmmer_domtblout
from enzynotation.tools.hmmer import hmmer_config_from_mapping

FIXTURES = Path("tests/fixtures/domains")


def _config(tmp_path: Path):
    return hmmer_config_from_mapping(
        {
            "schema_version": 1,
            "hmmer": {
                "enabled": True,
                "required": True,
                "executable": "hmmscan",
                "database": {
                    "path": str(tmp_path / "Pfam-A.hmm"),
                    "name": "Pfam-A",
                    "version": "36.0",
                    "kind": "pfam",
                },
                "execution": {
                    "cpus": 2,
                    "sequence_evalue": 10.0,
                    "domain_evalue": 10.0,
                },
                "filters": {
                    "maximum_sequence_evalue": 1e-7,
                    "maximum_domain_i_evalue": 1e-4,
                    "minimum_bit_score": 20.0,
                    "minimum_query_coverage": 0.5,
                },
            },
        }
    )


def test_parse_hmmer_scores_coordinates_and_description() -> None:
    hits = parse_hmmer_domtblout(FIXTURES / "hmmer.domtblout")

    assert len(hits) == 4
    first = hits[0]
    assert first.query_id == "q1"
    assert first.profile_accession == "PF00251.20"
    assert first.sequence_evalue == 1e-50
    assert first.independent_evalue == 1e-44
    assert first.bit_score == 170.0
    assert (first.query_start, first.query_end) == (20, 300)
    assert first.query_coverage == pytest.approx(281 / 500)
    assert first.description == "GH32 N-terminal catalytic domain"


def test_multiple_and_repeated_domains_are_preserved(tmp_path: Path) -> None:
    observations = hmmer_observations(
        parse_hmmer_domtblout(FIXTURES / "hmmer.domtblout"), _config(tmp_path)
    )
    repeated = [
        item for item in observations if item.signature_accession == "PF00251.20"
    ]

    assert len(repeated) == 2
    assert [item.occurrence for item in repeated] == [1, 2]
    assert {item.signature_accession for item in observations} == {
        "PF00251.20",
        "PF08244.12",
        "PF99999.1",
    }


def test_hmmer_filters_are_traceable(tmp_path: Path) -> None:
    config = _config(tmp_path)
    observations = hmmer_observations(
        parse_hmmer_domtblout(FIXTURES / "hmmer.domtblout"), config
    )
    q2 = next(item for item in observations if item.query_id == "q2")

    assert not q2.passed
    assert "maximum_sequence_evalue" in q2.filter_reasons
    assert "minimum_query_coverage" in q2.filter_reasons
    assert {criterion.criterion_id for criterion in q2.criteria} == {
        "maximum_sequence_evalue",
        "maximum_domain_i_evalue",
        "minimum_bit_score",
        "minimum_query_coverage",
    }


def test_empty_hmmer_search_is_valid() -> None:
    assert parse_hmmer_domtblout(FIXTURES / "hmmer_empty.domtblout") == ()


def test_malformed_hmmer_record_is_rejected() -> None:
    with pytest.raises(HmmerParseError, match="expected at least 22"):
        parse_hmmer_domtblout(FIXTURES / "hmmer_malformed.domtblout")


def test_hmmer_rejects_coordinates_outside_query(tmp_path: Path) -> None:
    path = tmp_path / "bad.domtblout"
    path.write_text(
        "D PF1 50 q - 20 1e-3 1 0 1 1 1e-3 1e-3 1 0 1 50 1 21 1 21 0.9 x\n",
        encoding="utf-8",
    )
    with pytest.raises(HmmerParseError, match="query coordinates"):
        parse_hmmer_domtblout(path)


def test_hmmer_rejects_reversed_coordinates(tmp_path: Path) -> None:
    path = tmp_path / "reversed.domtblout"
    path.write_text(
        "D PF1 50 q - 20 1e-3 1 0 1 1 1e-3 1e-3 1 0 1 50 15 5 5 15 0.9 x\n",
        encoding="utf-8",
    )
    with pytest.raises(HmmerParseError, match="query coordinates"):
        parse_hmmer_domtblout(path)
