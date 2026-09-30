"""Tests for protein FASTA parsing and validation."""

from pathlib import Path

from enzynotation.config import load_config
from enzynotation.fasta import (
    FastaValidationOptions,
    validate_fasta,
    write_normalized_fasta,
)


def _options(**overrides: object) -> FastaValidationOptions:
    values = dict(load_config().section("input"))
    values.update(overrides)
    return FastaValidationOptions.from_mapping(values)


def test_valid_multirecord_fasta_is_normalized(fasta_file) -> None:
    path = fasta_file(">alpha first protein\nacde\nfg\n>beta\nMNPQ\n")
    result = validate_fasta(path, _options())

    assert result.is_valid
    assert [record.identifier for record in result.records] == ["alpha", "beta"]
    assert result.records[0].description == "first protein"
    assert result.records[0].sequence == "ACDEFG"
    assert result.to_dict()["record_count"] == 2


def test_tab_separates_identifier_and_description(fasta_file) -> None:
    result = validate_fasta(fasta_file(">alpha\tdescription\nACD\n"), _options())
    assert result.is_valid
    assert result.records[0].identifier == "alpha"
    assert result.records[0].description == "description"


def test_terminal_stop_is_removed_with_warning(fasta_file) -> None:
    result = validate_fasta(fasta_file(">protein\nACD*\n"), _options())
    assert result.is_valid
    assert result.records[0].sequence == "ACD"
    assert [issue.code for issue in result.warnings] == ["terminal_stop_removed"]


def test_nonterminal_stop_is_an_error(fasta_file) -> None:
    result = validate_fasta(fasta_file(">protein\nAC*D\n"), _options())
    assert not result.is_valid
    assert "invalid_stop_marker" in {issue.code for issue in result.errors}


def test_duplicate_identifiers_are_rejected(fasta_file) -> None:
    result = validate_fasta(fasta_file(">same one\nACD\n>same two\nEFG\n"), _options())
    assert not result.is_valid
    assert "duplicate_identifier" in {issue.code for issue in result.errors}


def test_unsupported_residue_is_rejected(fasta_file) -> None:
    result = validate_fasta(fasta_file(">protein\nACD?\n"), _options())
    assert not result.is_valid
    assert "invalid_residue" in {issue.code for issue in result.errors}


def test_sequence_before_header_is_rejected(fasta_file) -> None:
    result = validate_fasta(fasta_file("ACD\n>protein\nEFG\n"), _options())
    assert not result.is_valid
    assert "sequence_before_header" in {issue.code for issue in result.errors}


def test_empty_input_has_no_records(fasta_file) -> None:
    result = validate_fasta(fasta_file(""), _options())
    assert not result.is_valid
    assert result.records == ()
    assert "no_fasta_records" in {issue.code for issue in result.errors}


def test_empty_sequence_is_rejected(fasta_file) -> None:
    result = validate_fasta(fasta_file(">empty\n>present\nACD\n"), _options())
    assert not result.is_valid
    assert "empty_sequence" in {issue.code for issue in result.errors}


def test_internal_whitespace_is_rejected(fasta_file) -> None:
    result = validate_fasta(fasta_file(">protein\nAC D\n"), _options())
    codes = {issue.code for issue in result.errors}
    assert {"sequence_whitespace", "invalid_residue"} <= codes


def test_length_limits_are_enforced(fasta_file) -> None:
    short = validate_fasta(
        fasta_file(">short\nAC\n", "short.fasta"),
        _options(min_sequence_length=3),
    )
    long = validate_fasta(
        fasta_file(">long\nACDE\n", "long.fasta"),
        _options(max_sequence_length=3),
    )
    assert "sequence_too_short" in {issue.code for issue in short.errors}
    assert "sequence_too_long" in {issue.code for issue in long.errors}


def test_normalized_writer_wraps_sequences(tmp_path: Path, fasta_file) -> None:
    result = validate_fasta(fasta_file(">protein description\nacdef\n"), _options())
    output = tmp_path / "nested" / "normalized.fasta"
    write_normalized_fasta(result.records, output, line_width=3)
    assert output.read_text() == ">protein description\nACD\nEF\n"
