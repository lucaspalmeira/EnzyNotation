"""Integration tests for the resumable BLAST evidence stage."""

from __future__ import annotations

import json
import shutil
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, FormatChecker

from enzynotation.backends.base import CommandSpec, ExecutionBackend
from enzynotation.backends.local import LocalBackend
from enzynotation.config import load_config
from enzynotation.paths import RunPaths
from enzynotation.provenance import CommandProvenance, SoftwareProvenance, utc_now
from enzynotation.runner import PipelineRunner
from enzynotation.stages.blast import BlastStage
from enzynotation.stages.validate import ValidationStage
from enzynotation.state import RunStatus, StageStateStore, StageStatus
from enzynotation.tools.blast import BlastConfig, blast_config_from_mapping
from enzynotation.workflow import Workflow

FIXTURES = Path("tests/fixtures/blast")


class FixtureBlastBackend(ExecutionBackend):
    """Write deterministic BLAST fixture output instead of invoking BLAST+."""

    name = "fixture-local"

    def __init__(self, fixture: Path, *, return_code: int = 0) -> None:
        self.fixture = fixture
        self.return_code = return_code
        self.execute_calls = 0
        self.version_calls = 0
        self.commands: list[tuple[str, ...]] = []

    def execute(
        self,
        command: CommandSpec,
        *,
        stdout_path: Path,
        stderr_path: Path,
    ) -> CommandProvenance:
        self.execute_calls += 1
        self.commands.append(command.argv)
        stdout_path.parent.mkdir(parents=True, exist_ok=True)
        stdout_path.write_text("fixture BLAST stdout\n", encoding="utf-8")
        stderr_path.write_text(
            "" if self.return_code == 0 else "fixture BLAST failure\n",
            encoding="utf-8",
        )
        if self.return_code == 0:
            output = Path(command.argv[command.argv.index("-out") + 1])
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(self.fixture.read_bytes())
        now = utc_now()
        return CommandProvenance(
            argv=command.argv,
            cwd=str((command.cwd or Path.cwd()).resolve()),
            backend=self.name,
            started_at=now,
            finished_at=now,
            duration_seconds=0.0,
            return_code=self.return_code,
            stdout_path=str(stdout_path.resolve()),
            stderr_path=str(stderr_path.resolve()),
        )

    def capture_version(
        self,
        executable: str,
        *,
        name: str | None = None,
        arguments: Sequence[str] = ("--version",),
    ) -> SoftwareProvenance:
        self.version_calls += 1
        return SoftwareProvenance(
            name=name or "blastp",
            version="blastp: 2.15.0+",
            executable=executable,
            version_command=(executable, *arguments),
            version_return_code=0,
        )


def _blast_config(tmp_path: Path, *, required: bool = True) -> BlastConfig:
    database = tmp_path / "external" / "db" / "proteins"
    database.parent.mkdir(parents=True, exist_ok=True)
    database.with_suffix(".pin").write_bytes(b"database-index-v1")
    metadata = tmp_path / "external" / "metadata.tsv"
    metadata.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(FIXTURES / "metadata.tsv", metadata)
    return blast_config_from_mapping(
        {
            "schema_version": 1,
            "blast": {
                "required": required,
                "thresholds_validated": False,
                "executable": "blastp-fixture",
                "database": {
                    "path": str(database),
                    "name": "fixture-curated-db",
                    "version": "v1",
                },
                "metadata": {"path": str(metadata)},
                "execution": {
                    "cpus": 2,
                    "evalue": 10.0,
                    "max_target_sequences": 50,
                },
                "filters": {
                    "maximum_evalue": 10.0,
                    "minimum_query_coverage": 0.0,
                    "minimum_subject_coverage": 0.0,
                    "minimum_percent_identity": 0.0,
                    "minimum_aligned_length": 1,
                    "maximum_retained_hits_per_query": None,
                },
            },
        }
    )


def _input(fasta_file) -> Path:
    return fasta_file(f">q1\n{'A' * 100}\n>q2\n{'C' * 80}\n")


def _runner(tmp_path: Path, backend: ExecutionBackend) -> PipelineRunner:
    return PipelineRunner(
        load_config(),
        results_root=tmp_path / "results",
        logs_root=tmp_path / "logs",
        backend=backend,
    )


def _workflow(config: BlastConfig) -> Workflow:
    return Workflow([ValidationStage(), BlastStage(config)])


def _records(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line]


def test_blast_stage_preserves_raw_and_emits_canonical_evidence(
    tmp_path: Path, fasta_file
) -> None:
    source = _input(fasta_file)
    config = _blast_config(tmp_path)
    backend = FixtureBlastBackend(FIXTURES / "hits.tsv")
    result = _runner(tmp_path, backend).run(
        source, workflow=_workflow(config), run_id="blast-success"
    )
    paths = RunPaths(tmp_path / "results", tmp_path / "logs", "blast-success")
    stage = paths.for_stage("blast")

    assert result.status is RunStatus.COMPLETED
    assert result.stage_outcomes == {
        "validate": StageStatus.COMPLETED,
        "blast": StageStatus.COMPLETED,
    }
    assert (stage.raw / "blast_hits.tsv").read_bytes() == (
        FIXTURES / "hits.tsv"
    ).read_bytes()
    assert (stage.normalized / "blast_hits.tsv").is_file()
    assert (stage.normalized / "blast_config.json").is_file()
    summary = json.loads((stage.normalized / "blast_summary.json").read_text())
    assert summary["counts"] == {
        "aggregated_hits": 5,
        "evidence_records": 8,
        "filtered_hits": 0,
        "queries_with_hits": 2,
        "raw_hsps": 6,
        "retained_hits": 5,
        "unmapped_retained_hits": 2,
    }
    assert summary["final_ec_prediction"] is None
    assert summary["thresholds_validated"] is False

    evidence_path = paths.evidence / "blast_evidence.jsonl"
    records = _records(evidence_path)
    schema = json.loads(Path("configs/schema/evidence.schema.json").read_text())
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    for record in records:
        validator.validate(record)

    unmapped = next(
        record
        for record in records
        if record["metrics"]["subject_accession"] == "UNMAPPED"
    )
    assert unmapped["metrics"]["metadata_mapped"] is False
    assert unmapped["source"]["evidence_class"] == "sequence_homology"
    assert "candidate_ec" not in unmapped["assertion"]["target"]

    p11111 = [
        record
        for record in records
        if record["metrics"]["subject_accession"] == "P11111"
    ]
    assert {record["source"]["evidence_class"] for record in p11111} == {
        "sequence_homology",
        "curated_annotation",
    }
    assert len({record["source"]["correlation_group"] for record in p11111}) == 1
    annotation = next(
        record
        for record in p11111
        if record["source"]["evidence_class"] == "curated_annotation"
    )
    assert annotation["assertion"]["target"]["candidate_ec"]["ec"] == "3.2.1.26"
    assert annotation["provenance"]["command"] == list(backend.commands[0])
    assert annotation["provenance"]["tool"]["version"] == "blastp: 2.15.0+"


def test_zero_hits_is_success_not_failure(tmp_path: Path, fasta_file) -> None:
    config = _blast_config(tmp_path)
    backend = FixtureBlastBackend(FIXTURES / "zero_hits.tsv")
    result = _runner(tmp_path, backend).run(
        _input(fasta_file), workflow=_workflow(config), run_id="blast-empty"
    )
    paths = RunPaths(tmp_path / "results", tmp_path / "logs", "blast-empty")
    summary = json.loads(
        (paths.for_stage("blast").normalized / "blast_summary.json").read_text()
    )

    assert result.status is RunStatus.COMPLETED
    assert summary["status"] == "completed"
    assert summary["empty_result"] is True
    assert summary["counts"]["evidence_records"] == 0
    assert (paths.evidence / "blast_evidence.jsonl").read_text() == ""


def test_malformed_output_fails_cleanly(tmp_path: Path, fasta_file) -> None:
    config = _blast_config(tmp_path)
    backend = FixtureBlastBackend(FIXTURES / "malformed.tsv")
    result = _runner(tmp_path, backend).run(
        _input(fasta_file), workflow=_workflow(config), run_id="blast-malformed"
    )
    paths = RunPaths(tmp_path / "results", tmp_path / "logs", "blast-malformed")
    state = StageStateStore(paths.for_stage("blast")).load()

    assert result.status is RunStatus.FAILED
    assert state is not None and state.status is StageStatus.FAILED
    assert "expected 14 fields" in state.message
    assert (paths.for_stage("blast").raw / "blast_hits.tsv").is_file()


@pytest.mark.parametrize(
    ("required", "expected_status"),
    [
        (True, RunStatus.FAILED),
        (False, RunStatus.COMPLETED_WITH_OPTIONAL_FAILURES),
    ],
)
def test_command_failure_respects_required_semantics(
    tmp_path: Path, fasta_file, required: bool, expected_status: RunStatus
) -> None:
    config = _blast_config(tmp_path, required=required)
    backend = FixtureBlastBackend(FIXTURES / "hits.tsv", return_code=2)
    result = _runner(tmp_path, backend).run(
        _input(fasta_file),
        workflow=_workflow(config),
        run_id=f"blast-failure-{required}",
    )

    assert result.status is expected_status
    assert result.stage_outcomes["blast"] is StageStatus.FAILED
    paths = RunPaths(
        tmp_path / "results", tmp_path / "logs", f"blast-failure-{required}"
    )
    state = StageStateStore(paths.for_stage("blast")).load()
    assert state is not None
    assert state.commands[0]["return_code"] == 2
    assert paths.for_stage("blast").stderr.read_text() == "fixture BLAST failure\n"


def test_resume_reuses_unchanged_blast_outputs(tmp_path: Path, fasta_file) -> None:
    config = _blast_config(tmp_path)
    backend = FixtureBlastBackend(FIXTURES / "hits.tsv")
    runner = _runner(tmp_path, backend)
    source = _input(fasta_file)
    workflow = _workflow(config)

    runner.run(source, workflow=workflow, run_id="blast-resume")
    second = runner.run(source, workflow=workflow, run_id="blast-resume")
    paths = RunPaths(tmp_path / "results", tmp_path / "logs", "blast-resume")
    state = StageStateStore(paths.for_stage("blast")).load()

    assert backend.execute_calls == 1
    assert backend.version_calls == 1
    assert second.stage_outcomes["blast"] is StageStatus.SKIPPED
    assert state is not None and state.attempt == 1


def test_query_change_invalidates_blast_cache(tmp_path: Path, fasta_file) -> None:
    config = _blast_config(tmp_path)
    backend = FixtureBlastBackend(FIXTURES / "hits.tsv")
    runner = _runner(tmp_path, backend)
    source = _input(fasta_file)
    workflow = _workflow(config)
    runner.run(source, workflow=workflow, run_id="blast-query-change")

    source.write_text(f">q1\n{'G' * 100}\n>q2\n{'C' * 80}\n", encoding="utf-8")
    second = runner.run(source, workflow=workflow, run_id="blast-query-change")

    assert backend.execute_calls == 2
    assert second.stage_outcomes["blast"] is StageStatus.COMPLETED


def test_database_and_metadata_changes_invalidate_cache(
    tmp_path: Path, fasta_file
) -> None:
    config = _blast_config(tmp_path)
    backend = FixtureBlastBackend(FIXTURES / "hits.tsv")
    runner = _runner(tmp_path, backend)
    source = _input(fasta_file)
    workflow = _workflow(config)
    runner.run(source, workflow=workflow, run_id="blast-resource-change")

    config.database.with_suffix(".pin").write_bytes(b"database-index-v2")
    database_run = runner.run(source, workflow=workflow, run_id="blast-resource-change")
    assert database_run.stage_outcomes["blast"] is StageStatus.COMPLETED

    metadata_text = config.metadata.read_text().replace("release-225", "release-226")
    config.metadata.write_text(metadata_text, encoding="utf-8")
    metadata_run = runner.run(source, workflow=workflow, run_id="blast-resource-change")
    assert metadata_run.stage_outcomes["blast"] is StageStatus.COMPLETED
    assert backend.execute_calls == 3


def test_missing_external_inputs_fail_during_stage_preparation(
    tmp_path: Path, fasta_file
) -> None:
    config = _blast_config(tmp_path)
    config.metadata.unlink()
    backend = FixtureBlastBackend(FIXTURES / "hits.tsv")
    result = _runner(tmp_path, backend).run(
        _input(fasta_file), workflow=_workflow(config), run_id="blast-no-metadata"
    )
    paths = RunPaths(tmp_path / "results", tmp_path / "logs", "blast-no-metadata")
    state = StageStateStore(paths.for_stage("blast")).load()

    assert result.status is RunStatus.FAILED
    assert state is not None
    assert "stage preparation failed" in state.message
    assert backend.execute_calls == 0


def test_missing_database_fails_during_stage_preparation(
    tmp_path: Path, fasta_file
) -> None:
    config = _blast_config(tmp_path)
    config.database.with_suffix(".pin").unlink()
    backend = FixtureBlastBackend(FIXTURES / "hits.tsv")
    result = _runner(tmp_path, backend).run(
        _input(fasta_file), workflow=_workflow(config), run_id="blast-no-database"
    )
    paths = RunPaths(tmp_path / "results", tmp_path / "logs", "blast-no-database")
    state = StageStateStore(paths.for_stage("blast")).load()

    assert result.status is RunStatus.FAILED
    assert state is not None
    assert "no BLAST database artifacts" in state.message
    assert backend.execute_calls == 0


def test_malformed_metadata_fails_before_blast_execution(
    tmp_path: Path, fasta_file
) -> None:
    config = _blast_config(tmp_path)
    config.metadata.write_text(
        "accession\tprotein_name\nA\tIncomplete\n", encoding="utf-8"
    )
    backend = FixtureBlastBackend(FIXTURES / "hits.tsv")
    result = _runner(tmp_path, backend).run(
        _input(fasta_file), workflow=_workflow(config), run_id="blast-bad-metadata"
    )
    paths = RunPaths(tmp_path / "results", tmp_path / "logs", "blast-bad-metadata")
    state = StageStateStore(paths.for_stage("blast")).load()

    assert result.status is RunStatus.FAILED
    assert state is not None and "curation metadata error" in state.message
    assert backend.execute_calls == 0
    summary = json.loads(
        (paths.for_stage("blast").normalized / "blast_summary.json").read_text()
    )
    assert summary["status"] == "failed"


def test_missing_blast_executable_is_a_recorded_command_failure(
    tmp_path: Path, fasta_file
) -> None:
    config = replace(
        _blast_config(tmp_path), executable="enzynotation-definitely-missing-blastp"
    )
    result = _runner(tmp_path, LocalBackend()).run(
        _input(fasta_file), workflow=_workflow(config), run_id="blast-no-executable"
    )
    paths = RunPaths(tmp_path / "results", tmp_path / "logs", "blast-no-executable")
    state = StageStateStore(paths.for_stage("blast")).load()

    assert result.status is RunStatus.FAILED
    assert state is not None
    assert state.commands[0]["return_code"] == 127
    assert state.software[0]["version"] == "unknown"
    assert state.software[0]["version_return_code"] == 127
