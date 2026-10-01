"""Tests for InterProScan TSV parsing and normalization."""

from pathlib import Path

import pytest

from enzynotation.domains import interpro_observations
from enzynotation.parsers.interpro import InterProParseError, parse_interpro_tsv

FIXTURES = Path("tests/fixtures/domains")


def test_parse_interpro_fields_and_annotations() -> None:
    hits = parse_interpro_tsv(FIXTURES / "interpro.tsv")

    assert len(hits) == 4
    first = hits[0]
    assert first.query_id == "q1"
    assert first.member_database == "Pfam"
    assert first.member_signature == "PF00251"
    assert first.interpro_accession == "IPR001362"
    assert first.go_terms == ("GO:0004553", "GO:0016798")
    assert first.pathways == ("Reactome:R-HSA-000001",)
    assert first.query_coverage == pytest.approx(281 / 500)


def test_interpro_optional_values_and_repeated_domains_are_preserved() -> None:
    hits = parse_interpro_tsv(FIXTURES / "interpro.tsv")
    observations = interpro_observations(
        hits, database_name="InterPro", database_version="100.0"
    )

    repeated = [item for item in observations if item.signature_accession == "PF00251"]
    assert [item.occurrence for item in repeated] == [1, 2]
    no_score = next(
        item for item in observations if item.signature_accession == "PF08244"
    )
    assert no_score.bit_score is None
    assert no_score.go_terms == ("GO:0005975",)
    no_interpro = next(item for item in observations if item.query_id == "q2")
    assert no_interpro.interpro_accession is None


def test_empty_interpro_search_is_valid() -> None:
    assert parse_interpro_tsv(FIXTURES / "interpro_empty.tsv") == ()


def test_malformed_interpro_record_is_rejected() -> None:
    with pytest.raises(InterProParseError, match="expected 13 to 15"):
        parse_interpro_tsv(FIXTURES / "interpro_malformed.tsv")


def test_interpro_rejects_invalid_coordinates(tmp_path: Path) -> None:
    path = tmp_path / "bad.tsv"
    path.write_text(
        "q\tmd5\t10\tPfam\tPF1\tdesc\t9\t11\t-\tT\tdate\t-\t-\n",
        encoding="utf-8",
    )
    with pytest.raises(InterProParseError, match="invalid sequence length"):
        parse_interpro_tsv(path)
