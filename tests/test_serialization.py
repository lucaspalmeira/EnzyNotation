"""Deterministic serialization tests for final report artifacts."""

import csv
import json
from io import StringIO
from pathlib import Path

from enzynotation.serialization import serialize_cell, tsv_text, write_tsv


def test_cell_serialization_has_explicit_null_boolean_and_json_rules() -> None:
    assert serialize_cell(None) == ""
    assert serialize_cell(True) == "true"
    assert serialize_cell(False) == "false"
    assert serialize_cell(["b", "a"]) == '["b","a"]'
    assert serialize_cell({"z": 1, "a": [2]}) == '{"a":[2],"z":1}'


def test_tsv_has_fixed_header_lf_newlines_and_safe_external_text() -> None:
    text = tsv_text(
        ("query_id", "description", "metrics"),
        (
            {
                "query_id": "q2",
                "description": "alpha\tbeta\nCafé",
                "metrics": {"score": 2, "label": "β"},
            },
        ),
    )
    assert text.startswith("query_id\tdescription\tmetrics\n")
    assert "\r" not in text
    rows = list(csv.reader(StringIO(text), delimiter="\t"))
    assert rows == [
        ["query_id", "description", "metrics"],
        ["q2", "alpha\tbeta\nCafé", '{"label":"β","score":2}'],
    ]


def test_write_tsv_is_utf8_and_byte_deterministic(tmp_path: Path) -> None:
    first = tmp_path / "first.tsv"
    second = tmp_path / "second.tsv"
    rows = ({"name": "enzima β", "values": [3, 1]},)
    write_tsv(first, ("name", "values"), rows)
    write_tsv(second, ("name", "values"), rows)
    assert first.read_bytes() == second.read_bytes()
    parsed = list(csv.reader(StringIO(first.read_text()), delimiter="\t"))
    assert json.loads(parsed[1][1]) == [3, 1]
