"""Deterministic JSON, JSONL, and TSV serialization for final reports."""

from __future__ import annotations

import csv
import json
from collections.abc import Iterable, Mapping, Sequence
from io import StringIO
from pathlib import Path
from typing import Any

from enzynotation.state import atomic_write_json, atomic_write_text


def load_json(path: Path) -> dict[str, Any]:
    """Load one UTF-8 JSON object."""

    with Path(path).open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError(f"JSON document must contain an object: {path}")
    return value


def load_jsonl(paths: Iterable[Path]) -> list[dict[str, Any]]:
    """Load JSON objects from sorted JSONL paths, ignoring blank lines."""

    result: list[dict[str, Any]] = []
    for path in sorted((Path(value) for value in paths), key=lambda value: str(value)):
        with path.open("r", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                if not line.strip():
                    continue
                value = json.loads(line)
                if not isinstance(value, dict):
                    raise ValueError(
                        f"JSONL record must be an object: {path}:{line_number}"
                    )
                result.append(value)
    return result


def serialize_cell(value: Any) -> str:
    """Serialize a scalar or structured TSV cell using explicit stable rules."""

    if value is None:
        return ""
    if isinstance(value, (Mapping, list, tuple, set)):
        normalized = sorted(value) if isinstance(value, set) else value
        return json.dumps(
            normalized,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def tsv_text(columns: Sequence[str], rows: Iterable[Mapping[str, Any]]) -> str:
    """Return UTF-8-ready TSV text with fixed columns and LF newlines."""

    stream = StringIO(newline="")
    writer = csv.writer(
        stream,
        delimiter="\t",
        quotechar='"',
        quoting=csv.QUOTE_MINIMAL,
        lineterminator="\n",
    )
    writer.writerow(columns)
    for row in rows:
        writer.writerow(serialize_cell(row.get(column)) for column in columns)
    return stream.getvalue()


def write_tsv(
    path: Path, columns: Sequence[str], rows: Iterable[Mapping[str, Any]]
) -> None:
    """Atomically write deterministic UTF-8 TSV output."""

    atomic_write_text(Path(path), tsv_text(columns, rows))


def write_json(path: Path, value: Any) -> None:
    """Atomically write deterministic indented JSON."""

    atomic_write_json(Path(path), value)
