"""Tests for checksums, run IDs, commands, and software provenance."""

from __future__ import annotations

import re
import sys
from datetime import UTC, datetime
from pathlib import Path

from enzynotation.backends.base import CommandSpec
from enzynotation.backends.local import LocalBackend
from enzynotation.provenance import (
    canonical_json_sha256,
    create_run_id,
    sha256_file,
    stage_signature,
)


def test_file_checksum_is_stable_and_content_sensitive(tmp_path: Path) -> None:
    path = tmp_path / "input.txt"
    path.write_text("same content\n", encoding="utf-8")
    first = sha256_file(path)
    second = sha256_file(path)
    assert first == second
    assert re.fullmatch(r"[a-f0-9]{64}", first)

    path.write_text("changed content\n", encoding="utf-8")
    assert sha256_file(path) != first


def test_canonical_checksum_ignores_mapping_order() -> None:
    assert canonical_json_sha256({"a": 1, "b": 2}) == canonical_json_sha256(
        {"b": 2, "a": 1}
    )


def test_stage_signature_captures_inputs_config_and_dependencies() -> None:
    baseline = stage_signature(
        stage_id="example",
        implementation_version="1",
        input_checksums={"input": "a" * 64},
        configuration_checksum="b" * 64,
        dependency_signatures={"validate": "c" * 64},
    )
    changed = stage_signature(
        stage_id="example",
        implementation_version="1",
        input_checksums={"input": "d" * 64},
        configuration_checksum="b" * 64,
        dependency_signatures={"validate": "c" * 64},
    )
    assert baseline != changed


def test_run_id_is_sortable_and_valid() -> None:
    run_id = create_run_id(
        timestamp=datetime(2026, 9, 30, 15, 0, tzinfo=UTC),
        suffix="deadbeef",
    )
    assert run_id == "run-20260930T150000Z-deadbeef"


def test_local_backend_captures_command_and_logs(tmp_path: Path) -> None:
    stdout = tmp_path / "stdout.log"
    stderr = tmp_path / "stderr.log"
    backend = LocalBackend()
    result = backend.execute(
        CommandSpec.from_sequence(
            [
                sys.executable,
                "-c",
                "import sys; print('out'); print('err', file=sys.stderr)",
            ],
            cwd=tmp_path,
        ),
        stdout_path=stdout,
        stderr_path=stderr,
    )

    assert result.return_code == 0
    assert result.argv[0] == sys.executable
    assert result.cwd == str(tmp_path)
    assert result.backend == "local"
    assert stdout.read_text() == "out\n"
    assert stderr.read_text() == "err\n"


def test_local_backend_records_failed_command(tmp_path: Path) -> None:
    stdout = tmp_path / "stdout.log"
    stderr = tmp_path / "stderr.log"
    result = LocalBackend().execute(
        CommandSpec.from_sequence([sys.executable, "-c", "import sys; sys.exit(7)"]),
        stdout_path=stdout,
        stderr_path=stderr,
    )
    assert result.return_code == 7
    assert stdout.is_file()
    assert stderr.is_file()


def test_local_backend_captures_software_version() -> None:
    software = LocalBackend().capture_version(sys.executable, name="python")
    assert software.name == "python"
    assert software.version.startswith("Python ")
    assert software.version_return_code == 0
    assert software.version_command == (sys.executable, "--version")
