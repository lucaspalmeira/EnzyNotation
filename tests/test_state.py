"""Tests for atomic stage state and run manifest persistence."""

from __future__ import annotations

from pathlib import Path

from enzynotation.paths import RunPaths
from enzynotation.state import (
    ManifestStore,
    RunManifest,
    RunStatus,
    StageState,
    StageStateStore,
    StageStatus,
    atomic_write_text,
    outputs_are_intact,
)


def _stage_state(output_path: str, output_checksum: str) -> StageState:
    return StageState(
        stage_id="validate",
        implementation_version="1",
        status=StageStatus.COMPLETED,
        required=True,
        attempt=1,
        signature="a" * 64,
        started_at="2026-09-30T15:00:00Z",
        finished_at="2026-09-30T15:00:01Z",
        input_checksums={"input_fasta": "b" * 64},
        configuration_checksum="c" * 64,
        dependency_signatures={},
        output_checksums={output_path: output_checksum},
        message="complete",
    )


def test_atomic_text_write_replaces_complete_file(tmp_path: Path) -> None:
    path = tmp_path / "state.txt"
    atomic_write_text(path, "first")
    atomic_write_text(path, "second")
    assert path.read_text() == "second"
    assert list(tmp_path.glob(".state.txt.*")) == []


def test_stage_state_round_trip_and_output_integrity(tmp_path: Path) -> None:
    paths = RunPaths(tmp_path / "results", tmp_path / "logs", "state-run")
    paths.prepare()
    output = paths.input / "output.txt"
    output.write_text("output\n", encoding="utf-8")

    from enzynotation.provenance import sha256_file

    state = _stage_state("input/output.txt", sha256_file(output))
    stage_paths = paths.for_stage("validate")
    stage_paths.prepare()
    store = StageStateStore(stage_paths)
    store.mark_running(
        stage_id="validate",
        attempt=1,
        signature=state.signature,
        started_at=state.started_at,
    )
    store.publish(state)

    assert store.load() == state
    assert not stage_paths.running.exists()
    assert outputs_are_intact(state, paths.run_root)

    output.write_text("tampered\n", encoding="utf-8")
    assert not outputs_are_intact(state, paths.run_root)


def test_interrupted_attempt_is_counted_and_archived(tmp_path: Path) -> None:
    paths = RunPaths(tmp_path / "results", tmp_path / "logs", "interrupted-run")
    paths.prepare()
    stage_paths = paths.for_stage("validate")
    stage_paths.prepare()
    store = StageStateStore(stage_paths)
    store.mark_running(
        stage_id="validate",
        attempt=1,
        signature="a" * 64,
        started_at="2026-09-30T15:00:00Z",
    )

    assert store.has_unfinished_attempt()
    assert store.next_attempt() == 2
    store.prepare_attempt()
    assert (stage_paths.history / "attempt-0001-interrupted.json").is_file()
    assert not stage_paths.running.exists()


def test_manifest_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "manifest.json"
    manifest = RunManifest(
        run_id="manifest-run",
        created_at="2026-09-30T15:00:00Z",
        updated_at="2026-09-30T15:00:01Z",
        status=RunStatus.RUNNING,
        software={"name": "enzynotation", "version": "0.1.0"},
        input={"source": "input.fasta", "sha256": "a" * 64},
        configuration={"sha256": "b" * 64},
        workflow=[],
    )
    store = ManifestStore(path)
    store.write(manifest)
    assert store.load() == manifest
