"""Tests for supplied-structure parsing and explicit mappings."""

from __future__ import annotations

from pathlib import Path

import pytest

from enzynotation.structure_mapping import (
    load_query_structure_manifest,
    load_structure_reference_metadata,
)
from enzynotation.structures import (
    StructureError,
    compare_structure_sequence,
    structural_correlation_group,
    validate_structure,
)

FIXTURES = Path("tests/fixtures/structures")


def test_pdb_chain_and_model_selection() -> None:
    first = validate_structure(
        FIXTURES / "query.pdb",
        structure_format="pdb",
        chain_id="A",
        model_index=1,
    )
    second = validate_structure(
        FIXTURES / "query.pdb",
        structure_format="pdb",
        chain_id="A",
        model_index=2,
    )

    assert first.sequence == "MAG"
    assert first.chain_id == "A"
    assert first.model_identifier == "1"
    assert second.sequence == "TY"
    assert " B " not in first.selected_content


def test_mmcif_chain_and_model_selection() -> None:
    first = validate_structure(
        FIXTURES / "query.cif",
        structure_format="mmcif",
        chain_id="A",
        model_index=1,
    )
    second = validate_structure(
        FIXTURES / "query.cif",
        structure_format="cif",
        chain_id="A",
        model_index=2,
    )

    assert first.sequence == "MA"
    assert first.atom_count == 2
    assert second.sequence == "T"


@pytest.mark.parametrize(
    ("chain", "model", "message"),
    [("Z", 1, "chain"), ("A", 3, "model")],
)
def test_missing_chain_or_model_is_rejected(
    chain: str, model: int, message: str
) -> None:
    with pytest.raises(StructureError, match=message):
        validate_structure(
            FIXTURES / "query.pdb",
            structure_format="pdb",
            chain_id=chain,
            model_index=model,
        )


def test_missing_malformed_and_unsupported_structures(tmp_path: Path) -> None:
    with pytest.raises(StructureError, match="does not exist"):
        validate_structure(
            tmp_path / "missing.pdb",
            structure_format="pdb",
            chain_id=None,
            model_index=None,
        )
    malformed = tmp_path / "bad.pdb"
    malformed.write_text("ATOM too short\n", encoding="utf-8")
    with pytest.raises(StructureError, match="malformed PDB"):
        validate_structure(
            malformed,
            structure_format="pdb",
            chain_id=None,
            model_index=None,
        )
    with pytest.raises(StructureError, match="unsupported"):
        validate_structure(
            FIXTURES / "query.pdb",
            structure_format="mol2",
            chain_id=None,
            model_index=None,
        )


def test_sequence_comparison_reports_mismatch_and_truncation() -> None:
    assert compare_structure_sequence("MAG", "MAG")[0] == "exact"
    assert compare_structure_sequence("XXMAGYY", "MAG")[0] == "compatible_partial"
    assert compare_structure_sequence("MAG", "XXMAGYY")[0] == "compatible_extension"
    assert compare_structure_sequence("MAG", "TY")[0] == "mismatch"


def test_manifest_and_reference_metadata_are_explicit(tmp_path: Path) -> None:
    manifest = tmp_path / "manifest.tsv"
    manifest.write_text(
        "query_id\tstructure_id\tstructure_path\tstructure_format\t"
        "chain_id\tmodel_index\tstructure_source\n"
        f"q1\tq-structure\t{(FIXTURES / 'query.pdb').resolve()}\tpdb\tA\t1\t"
        "user_supplied\n",
        encoding="utf-8",
    )
    records = load_query_structure_manifest(manifest)
    assert records[0].query_id == "q1"

    metadata = tmp_path / "metadata.tsv"
    metadata.write_text(
        "structure_id\tchain_id\tprotein_accession\tprotein_name\tec_numbers\t"
        "annotation_status\tsource_database\tdatabase_version\n"
        "refA\tA\tP1\tOne\t3.2.1.26\tcurated\tcustom\tv1\n"
        "refB\tB\tP2\tMany\t1.1.1.1;2.7.-.-\treviewed\tcustom\tv1\n"
        "refC\t\t\tNone\t\tunannotated\tcustom\tv1\n",
        encoding="utf-8",
    )
    index = load_structure_reference_metadata(metadata)
    assert index.lookup("refA.pdb").ec_numbers == ("3.2.1.26",)
    assert index.lookup("db|refB").ec_numbers == ("1.1.1.1", "2.7.-.-")
    assert index.lookup("refC").ec_numbers == ()


def test_manifest_rejects_unknown_structure_source(tmp_path: Path) -> None:
    manifest = tmp_path / "manifest.tsv"
    manifest.write_text(
        "query_id\tstructure_id\tstructure_path\tstructure_format\t"
        "structure_source\nq1\ts1\tquery.pdb\tpdb\tautomatic_magic\n",
        encoding="utf-8",
    )
    with pytest.raises(StructureError, match="structure_source"):
        load_query_structure_manifest(manifest)


def test_correlation_group_is_stable_and_bounded() -> None:
    expected = "structural:q1:refA:A"
    assert structural_correlation_group("q1", "refA", "A") == expected
    assert len(structural_correlation_group("q" * 400, "r" * 400, None)) < 100
