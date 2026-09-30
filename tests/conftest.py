"""Shared pytest fixtures."""

from pathlib import Path

import pytest


@pytest.fixture
def fasta_file(tmp_path: Path):
    """Return a helper that writes a temporary FASTA file."""

    def _write(content: str, name: str = "input.fasta") -> Path:
        path = tmp_path / name
        path.write_text(content, encoding="utf-8")
        return path

    return _write
