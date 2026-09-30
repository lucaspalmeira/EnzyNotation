"""Tests for EC-number parsing and normalization."""

import pytest

from enzynotation.ec import normalize_ec, parse_ec
from enzynotation.exceptions import ECNumberError


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("1.2.3.4", "1.2.3.4"),
        ("EC 1.2.3.4", "1.2.3.4"),
        ("ec: 1.2.3.4", "1.2.3.4"),
        ("01.002.3.4", "1.2.3.4"),
        ("1", "1.-.-.-"),
        ("1.2", "1.2.-.-"),
        ("1.2.3", "1.2.3.-"),
        ("1.2.-.-", "1.2.-.-"),
    ],
)
def test_normalize_ec(raw: str, expected: str) -> None:
    assert normalize_ec(raw) == expected


def test_ec_properties() -> None:
    complete = parse_ec("1.2.3.4")
    partial = parse_ec("1.2")
    assert complete.is_complete is True
    assert complete.depth == 4
    assert partial.is_complete is False
    assert partial.depth == 2


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "EC",
        "1..2.3",
        "1.2.3.4.5",
        "1.2.-.4",
        "1.2.a.4",
        "0.1.2.3",
        "-1.2.3.4",
        "-",
        "-.-.-.-",
    ],
)
def test_invalid_ec_numbers(raw: str) -> None:
    with pytest.raises(ECNumberError):
        normalize_ec(raw)


@pytest.mark.parametrize("raw", ["1", "1.2.3", "1.2.3.-"])
def test_partial_ec_can_be_disallowed(raw: str) -> None:
    with pytest.raises(ECNumberError, match="incomplete"):
        normalize_ec(raw, allow_partial=False)
