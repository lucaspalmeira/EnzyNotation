"""Tests for orientation-aware TM-align parsing."""

from pathlib import Path

import pytest

from enzynotation.parsers.tmalign import TMAlignParseError, parse_tmalign_output

FIXTURES = Path("tests/fixtures/tmalign")


def test_parser_preserves_both_tm_score_directions() -> None:
    result = parse_tmalign_output(FIXTURES / "result.txt")

    assert result.query_name == "query.pdb"
    assert result.target_name == "reference.pdb"
    assert result.aligned_length == 80
    assert result.rmsd == 1.5
    assert result.sequence_identity == 0.25
    assert result.tm_score_normalized_by_query == 0.8
    assert result.tm_score_normalized_by_target == 0.7


def test_malformed_output_is_rejected() -> None:
    with pytest.raises(TMAlignParseError, match="missing TM-align fields"):
        parse_tmalign_output(FIXTURES / "malformed.txt")


def test_alignment_cannot_exceed_shorter_structure(tmp_path: Path) -> None:
    text = (FIXTURES / "result.txt").read_text(encoding="utf-8")
    path = tmp_path / "invalid.txt"
    path.write_text(text.replace("Aligned length= 80", "Aligned length= 110"))

    with pytest.raises(TMAlignParseError, match="outside valid bounds"):
        parse_tmalign_output(path)
