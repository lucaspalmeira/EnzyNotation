"""Tests for explicit accession-to-function metadata."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from enzynotation.curation import (
    CurationError,
    load_curation_metadata,
    normalize_subject_accession,
)

FIXTURES = Path("tests/fixtures/blast")


@pytest.mark.parametrize(
    ("subject_id", "expected"),
    [
        ("sp|P11111|ENZYME_A", "P11111"),
        ("tr|Q22222|ENZYME_B", "Q22222"),
        ("ref|NP_333.1|", "NP_333.1"),
        ("UniProtKB:P11111", "P11111"),
        ("custom_accession description", "custom_accession"),
    ],
)
def test_subject_accession_normalization(subject_id: str, expected: str) -> None:
    assert normalize_subject_accession(subject_id) == expected


def test_tsv_metadata_supports_zero_one_multiple_and_partial_ecs() -> None:
    metadata = load_curation_metadata(FIXTURES / "metadata.tsv")

    single = metadata.lookup("sp|P11111|ENZYME_A")
    multiple = metadata.lookup("tr|Q22222|ENZYME_B")
    no_ec = metadata.lookup("ref|NP_333.1|")
    assert single is not None and single.ec_numbers == ("3.2.1.26",)
    assert single.is_curated
    assert multiple is not None
    assert multiple.ec_numbers == ("1.2.3.4", "2.3.-.-")
    assert no_ec is not None and no_ec.ec_numbers == ()


def test_unmapped_accession_does_not_invent_metadata() -> None:
    metadata = load_curation_metadata(FIXTURES / "metadata.tsv")
    assert metadata.lookup("lcl|UNMAPPED") is None


def test_csv_and_json_metadata_are_supported(tmp_path: Path) -> None:
    csv_path = tmp_path / "metadata.csv"
    csv_path.write_text(
        "accession,protein_name,ec_numbers,annotation_status,"
        "source_database,database_version\n"
        "A1,Example,EC: 1.2.3.-,reviewed,Custom,v1\n",
        encoding="utf-8",
    )
    assert load_curation_metadata(csv_path).lookup("A1").ec_numbers == ("1.2.3.-",)

    json_path = tmp_path / "metadata.json"
    json_path.write_text(
        json.dumps(
            {
                "records": [
                    {
                        "accession": "B2",
                        "protein_name": "Example B",
                        "ec_numbers": ["4.2.1.1", "4.2.1.1"],
                        "annotation_status": "curated",
                        "source_database": "Custom",
                        "database_version": "v2",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    assert load_curation_metadata(json_path).lookup("B2").ec_numbers == ("4.2.1.1",)


@pytest.mark.parametrize(
    "content",
    [
        "accession\tprotein_name\nA\tMissing columns\n",
        (
            "accession\tprotein_name\tec_numbers\tannotation_status\t"
            "source_database\tdatabase_version\n"
            "A\tBad EC\t1.x.3.4\tcurated\tCustom\tv1\n"
        ),
    ],
)
def test_malformed_metadata_is_rejected(tmp_path: Path, content: str) -> None:
    path = tmp_path / "bad.tsv"
    path.write_text(content, encoding="utf-8")
    with pytest.raises(CurationError, match="metadata row"):
        load_curation_metadata(path)


def test_missing_and_unsupported_metadata_files_are_rejected(tmp_path: Path) -> None:
    with pytest.raises(CurationError, match="does not exist"):
        load_curation_metadata(tmp_path / "missing.tsv")

    unsupported = tmp_path / "metadata.yaml"
    unsupported.write_text("records: []\n", encoding="utf-8")
    with pytest.raises(CurationError, match="unsupported metadata format"):
        load_curation_metadata(unsupported)
