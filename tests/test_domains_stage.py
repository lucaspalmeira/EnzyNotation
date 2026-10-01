"""Integration tests for HMMER and InterProScan domain evidence."""

from __future__ import annotations

import json
import shutil
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker

from enzynotation.backends.base import CommandSpec, ExecutionBackend
from enzynotation.backends.local import LocalBackend
from enzynotation.config import load_config
from enzynotation.family import load_family_profile
from enzynotation.paths import RunPaths
from enzynotation.provenance import CommandProvenance, SoftwareProvenance, utc_now
from enzynotation.runner import PipelineRunner
from enzynotation.stages.domains import DomainsStage
from enzynotation.stages.validate import ValidationStage
from enzynotation.state import RunStatus, StageStateStore, StageStatus
from enzynotation.tools.hmmer import HmmerConfig, hmmer_config_from_mapping
from enzynotation.tools.interpro import InterProConfig, interpro_config_from_mapping
from enzynotation.workflow import Workflow

FIXTURES = Path("tests/fixtures/domains")


class DomainFixtureBackend(ExecutionBackend):
    name = "domain-fixture"

    def __init__(
        self,
        *,
        hmmer_fixture: Path = FIXTURES / "hmmer.domtblout",
        interpro_fixture: Path = FIXTURES / "interpro.tsv",
        return_codes: dict[str, int] | None = None,
    ) -> None:
        self.hmmer_fixture = hmmer_fixture
        self.interpro_fixture = interpro_fixture
        self.return_codes = return_codes or {}
        self.calls: list[str] = []

    def execute(
        self,
        command: CommandSpec,
        *,
        stdout_path: Path,
        stderr_path: Path,
    ) -> CommandProvenance:
        provider = "hmmer" if "hmmscan" in command.argv[0] else "interproscan"
        self.calls.append(provider)
        return_code = self.return_codes.get(provider, 0)
        stdout_path.parent.mkdir(parents=True, exist_ok=True)
        stdout_path.write_text(f"{provider} fixture stdout\n", encoding="utf-8")
        stderr_path.write_text(
            "" if return_code == 0 else f"{provider} failed\n", encoding="utf-8"
        )
        if return_code == 0:
            if provider == "hmmer":
                output = Path(command.argv[command.argv.index("--domtblout") + 1])
                fixture = self.hmmer_fixture
            else:
                output = Path(command.argv[command.argv.index("-o") + 1])
                fixture = self.interpro_fixture
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(fixture.read_bytes())
        now = utc_now()
        return CommandProvenance(
            argv=command.argv,
            cwd=str(Path.cwd()),
            backend=self.name,
            started_at=now,
            finished_at=now,
            duration_seconds=0.0,
            return_code=return_code,
            stdout_path=str(stdout_path),
            stderr_path=str(stderr_path),
        )

    def capture_version(
        self,
        executable: str,
        *,
        name: str | None = None,
        arguments: Sequence[str] = ("--version",),
    ) -> SoftwareProvenance:
        provider = "hmmer" if "hmmscan" in executable else "interproscan"
        version = "HMMER 3.4" if provider == "hmmer" else "InterProScan 5.70"
        return SoftwareProvenance(
            name=name or provider,
            version=version,
            executable=executable,
            version_command=(executable, *arguments),
            version_return_code=0,
        )


def _profile(tmp_path: Path):
    path = tmp_path / "family.yaml"
    shutil.copyfile(FIXTURES / "family.yaml", path)
    return load_family_profile(path)


def _hmmer(tmp_path: Path, *, required: bool = True) -> HmmerConfig:
    database = tmp_path / "database" / "Pfam-A.hmm"
    database.parent.mkdir(parents=True, exist_ok=True)
    database.write_text("fixture HMM database\n", encoding="utf-8")
    return hmmer_config_from_mapping(
        {
            "schema_version": 1,
            "hmmer": {
                "enabled": True,
                "required": required,
                "executable": "hmmscan-fixture",
                "database": {
                    "path": str(database),
                    "name": "Pfam-A",
                    "version": "36.0",
                    "kind": "pfam",
                },
                "execution": {
                    "cpus": 1,
                    "sequence_evalue": 10.0,
                    "domain_evalue": 10.0,
                },
                "filters": {
                    "maximum_sequence_evalue": 10.0,
                    "maximum_domain_i_evalue": 10.0,
                    "minimum_bit_score": 0.0,
                    "minimum_query_coverage": 0.0,
                },
            },
        }
    )


def _interpro(tmp_path: Path, *, required: bool = False) -> InterProConfig:
    data = tmp_path / "interpro-data"
    data.mkdir(exist_ok=True)
    (data / "version.txt").write_text("100.0\n", encoding="utf-8")
    return interpro_config_from_mapping(
        {
            "schema_version": 1,
            "interproscan": {
                "enabled": True,
                "required": required,
                "executable": "interproscan-fixture",
                "database": {
                    "name": "InterPro",
                    "version": "100.0",
                    "path": str(data),
                },
                "execution": {
                    "cpus": 1,
                    "applications": ["Pfam", "CDD"],
                    "include_go_terms": True,
                    "include_pathways": True,
                },
            },
        }
    )


def _input(fasta_file) -> Path:
    return fasta_file(f">q1\n{'A' * 500}\n>q2\n{'C' * 200}\n")


def _runner(tmp_path: Path, backend: ExecutionBackend) -> PipelineRunner:
    return PipelineRunner(
        load_config(),
        results_root=tmp_path / "results",
        logs_root=tmp_path / "logs",
        backend=backend,
    )


def _workflow(profile, hmmer=None, interpro=None) -> Workflow:
    return Workflow(
        [ValidationStage(), DomainsStage(profile, hmmer=hmmer, interpro=interpro)]
    )


def _records(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line]


def test_domain_stage_runs_both_providers_and_emits_canonical_evidence(
    tmp_path: Path, fasta_file
) -> None:
    profile = _profile(tmp_path)
    backend = DomainFixtureBackend()
    result = _runner(tmp_path, backend).run(
        _input(fasta_file),
        workflow=_workflow(profile, _hmmer(tmp_path), _interpro(tmp_path)),
        run_id="domain-success",
    )
    paths = RunPaths(tmp_path / "results", tmp_path / "logs", "domain-success")
    stage = paths.for_stage("domains")
    summary = json.loads((stage.normalized / "domain_summary.json").read_text())
    evidence = _records(paths.evidence / "domain_evidence.jsonl")

    assert result.status is RunStatus.COMPLETED
    assert backend.calls == ["hmmer", "interproscan"]
    assert (stage.raw / "hmmer.domtblout").read_bytes() == (
        FIXTURES / "hmmer.domtblout"
    ).read_bytes()
    assert (stage.raw / "interpro.tsv").read_bytes() == (
        FIXTURES / "interpro.tsv"
    ).read_bytes()
    assert summary["counts"] == {
        "raw_domain_hits": 8,
        "retained_domain_hits": 8,
        "filtered_domain_hits": 0,
        "evidence_records": 12,
    }
    assert summary["final_ec_prediction"] is None

    schema = json.loads(Path("configs/schema/evidence.schema.json").read_text())
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    for record in evidence:
        validator.validate(record)
    assert {record["source"]["id"] for record in evidence} == {
        "hmmer",
        "interproscan",
    }
    assert all(
        record["source"]["evidence_class"] == "domain_architecture"
        for record in evidence
    )
    hmmer = next(
        record
        for record in evidence
        if record["source"]["id"] == "hmmer"
        and record["metrics"]["signature_accession"] == "PF00251.20"
        and record["metrics"]["domain_start"] == 20
    )
    interpro = next(
        record
        for record in evidence
        if record["source"]["id"] == "interproscan"
        and record["metrics"]["signature_accession"] == "PF00251"
        and record["metrics"]["domain_start"] == 20
    )
    assert (
        hmmer["source"]["correlation_group"] == interpro["source"]["correlation_group"]
    )
    assert hmmer["provenance"]["tool"]["version"] == "HMMER 3.4"
    assert interpro["metrics"]["go_terms"] == "GO:0004553;GO:0016798"


def test_empty_domain_search_is_success(tmp_path: Path, fasta_file) -> None:
    backend = DomainFixtureBackend(
        hmmer_fixture=FIXTURES / "hmmer_empty.domtblout",
        interpro_fixture=FIXTURES / "interpro_empty.tsv",
    )
    result = _runner(tmp_path, backend).run(
        _input(fasta_file),
        workflow=_workflow(_profile(tmp_path), _hmmer(tmp_path), _interpro(tmp_path)),
        run_id="domain-empty",
    )
    paths = RunPaths(tmp_path / "results", tmp_path / "logs", "domain-empty")
    summary = json.loads(
        (paths.for_stage("domains").normalized / "domain_summary.json").read_text()
    )

    assert result.status is RunStatus.COMPLETED
    assert summary["providers"]["hmmer"]["status"] == "empty"
    assert summary["providers"]["interproscan"]["status"] == "empty"
    assert summary["counts"]["evidence_records"] == 6


def test_optional_interpro_failure_does_not_break_hmmer(
    tmp_path: Path, fasta_file
) -> None:
    backend = DomainFixtureBackend(return_codes={"interproscan": 127})
    result = _runner(tmp_path, backend).run(
        _input(fasta_file),
        workflow=_workflow(_profile(tmp_path), _hmmer(tmp_path), _interpro(tmp_path)),
        run_id="optional-interpro",
    )
    paths = RunPaths(tmp_path / "results", tmp_path / "logs", "optional-interpro")
    summary = json.loads(
        (paths.for_stage("domains").normalized / "domain_summary.json").read_text()
    )

    assert result.status is RunStatus.COMPLETED
    assert summary["providers"]["hmmer"]["status"] == "completed"
    assert summary["providers"]["interproscan"]["status"] == "failed"
    assert summary["optional_provider_failures"] == ["interproscan"]


def test_required_hmmer_failure_fails_stage(tmp_path: Path, fasta_file) -> None:
    backend = DomainFixtureBackend(return_codes={"hmmer": 2})
    result = _runner(tmp_path, backend).run(
        _input(fasta_file),
        workflow=_workflow(_profile(tmp_path), _hmmer(tmp_path)),
        run_id="required-hmmer",
    )
    assert result.status is RunStatus.FAILED
    assert result.stage_outcomes["domains"] is StageStatus.FAILED


def test_malformed_hmmer_output_fails_required_provider(
    tmp_path: Path, fasta_file
) -> None:
    backend = DomainFixtureBackend(hmmer_fixture=FIXTURES / "hmmer_malformed.domtblout")
    result = _runner(tmp_path, backend).run(
        _input(fasta_file),
        workflow=_workflow(_profile(tmp_path), _hmmer(tmp_path)),
        run_id="malformed-hmmer",
    )
    paths = RunPaths(tmp_path / "results", tmp_path / "logs", "malformed-hmmer")
    summary = json.loads(
        (paths.for_stage("domains").normalized / "domain_summary.json").read_text()
    )
    assert result.status is RunStatus.FAILED
    assert summary["providers"]["hmmer"]["status"] == "failed"
    assert "expected at least 22" in summary["providers"]["hmmer"]["message"]


def test_missing_database_is_recorded_as_provider_failure(
    tmp_path: Path, fasta_file
) -> None:
    config = _hmmer(tmp_path)
    config.database.unlink()
    backend = DomainFixtureBackend()
    result = _runner(tmp_path, backend).run(
        _input(fasta_file),
        workflow=_workflow(_profile(tmp_path), config),
        run_id="missing-hmm-db",
    )
    paths = RunPaths(tmp_path / "results", tmp_path / "logs", "missing-hmm-db")
    summary = json.loads(
        (paths.for_stage("domains").normalized / "domain_summary.json").read_text()
    )
    assert result.status is RunStatus.FAILED
    assert "does not exist" in summary["providers"]["hmmer"]["message"]
    assert backend.calls == []


def test_missing_executable_is_recorded(tmp_path: Path, fasta_file) -> None:
    config = replace(_hmmer(tmp_path), executable="missing-hmmscan-enzynotation")
    result = _runner(tmp_path, LocalBackend()).run(
        _input(fasta_file),
        workflow=_workflow(_profile(tmp_path), config),
        run_id="missing-hmmscan",
    )
    paths = RunPaths(tmp_path / "results", tmp_path / "logs", "missing-hmmscan")
    state = StageStateStore(paths.for_stage("domains")).load()
    assert result.status is RunStatus.FAILED
    assert state is not None
    assert state.commands[0]["return_code"] == 127
    assert state.software[0]["version"] == "unknown"


def test_domain_resume_and_input_database_invalidation(
    tmp_path: Path, fasta_file
) -> None:
    profile = _profile(tmp_path)
    hmmer = _hmmer(tmp_path)
    backend = DomainFixtureBackend()
    runner = _runner(tmp_path, backend)
    source = _input(fasta_file)
    workflow = _workflow(profile, hmmer)
    runner.run(source, workflow=workflow, run_id="domain-cache")
    resumed = runner.run(source, workflow=workflow, run_id="domain-cache")
    assert resumed.stage_outcomes["domains"] is StageStatus.SKIPPED
    assert backend.calls == ["hmmer"]

    source.write_text(f">q1\n{'G' * 500}\n>q2\n{'C' * 200}\n", encoding="utf-8")
    runner.run(source, workflow=workflow, run_id="domain-cache")
    assert backend.calls == ["hmmer", "hmmer"]

    hmmer.database.write_text("changed database\n", encoding="utf-8")
    runner.run(source, workflow=workflow, run_id="domain-cache")
    assert backend.calls == ["hmmer", "hmmer", "hmmer"]


def test_family_and_provider_config_changes_invalidate_cache(
    tmp_path: Path, fasta_file
) -> None:
    profile = _profile(tmp_path)
    hmmer = _hmmer(tmp_path)
    backend = DomainFixtureBackend()
    runner = _runner(tmp_path, backend)
    source = _input(fasta_file)
    runner.run(
        source,
        workflow=_workflow(profile, hmmer),
        run_id="domain-config-change",
    )

    family_text = profile.source_path.read_text().replace("1.0.0", "1.0.1")
    profile.source_path.write_text(family_text, encoding="utf-8")
    changed_profile = load_family_profile(profile.source_path)
    runner.run(
        source,
        workflow=_workflow(changed_profile, hmmer),
        run_id="domain-config-change",
    )

    changed_hmmer = replace(hmmer, minimum_bit_score=50.0)
    runner.run(
        source,
        workflow=_workflow(changed_profile, changed_hmmer),
        run_id="domain-config-change",
    )
    assert backend.calls == ["hmmer", "hmmer", "hmmer"]
