"""Integration tests for supplied structures, Foldseek, and selective TM-align."""

from __future__ import annotations

import json
import shutil
from collections.abc import Sequence
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, FormatChecker

from enzynotation.backends.base import CommandSpec, ExecutionBackend
from enzynotation.config import load_config
from enzynotation.paths import RunPaths
from enzynotation.provenance import CommandProvenance, SoftwareProvenance, utc_now
from enzynotation.runner import PipelineRunner
from enzynotation.stages.foldseek import FoldseekStage
from enzynotation.stages.structures import StructuresStage
from enzynotation.stages.tmalign import TMAlignStage
from enzynotation.stages.validate import ValidationStage
from enzynotation.state import RunStatus, StageStateStore, StageStatus
from enzynotation.structure_mapping import (
    StructuresConfig,
    structures_config_from_mapping,
)
from enzynotation.tools.foldseek import FoldseekConfig, foldseek_config_from_mapping
from enzynotation.tools.tmalign import TMAlignConfig, tmalign_config_from_mapping
from enzynotation.workflow import Workflow

STRUCTURES = Path("tests/fixtures/structures")
FOLDSEEK = Path("tests/fixtures/foldseek")
TMALIGN = Path("tests/fixtures/tmalign")


class StructuralFixtureBackend(ExecutionBackend):
    """Emulate external runtimes while preserving real runner behavior."""

    name = "fixture-local"

    def __init__(
        self,
        foldseek_fixture: Path = FOLDSEEK / "stage_hits.tsv",
        *,
        foldseek_return_code: int = 0,
        tmalign_return_code: int = 0,
        tmalign_available: bool = True,
        tmalign_fixture: Path = TMALIGN / "stage_result.txt",
        foldseek_stderr: str = "fixture Foldseek failure\n",
        unavailable_versions: frozenset[str] = frozenset(),
    ) -> None:
        self.foldseek_fixture = foldseek_fixture
        self.foldseek_return_code = foldseek_return_code
        self.tmalign_return_code = tmalign_return_code
        self.tmalign_available = tmalign_available
        self.tmalign_fixture = tmalign_fixture
        self.foldseek_stderr = foldseek_stderr
        self.unavailable_versions = unavailable_versions
        self.commands: list[tuple[str, ...]] = []
        self.version_names: list[str] = []

    @property
    def foldseek_calls(self) -> int:
        return sum("easy-search" in command for command in self.commands)

    @property
    def tmalign_calls(self) -> int:
        return sum(command[0] == "TMalign-fixture" for command in self.commands)

    def execute(
        self,
        command: CommandSpec,
        *,
        stdout_path: Path,
        stderr_path: Path,
    ) -> CommandProvenance:
        self.commands.append(command.argv)
        stdout_path.parent.mkdir(parents=True, exist_ok=True)
        stderr_path.parent.mkdir(parents=True, exist_ok=True)
        return_code = 0
        if "easy-search" in command.argv:
            return_code = self.foldseek_return_code
            stdout_path.write_text("fixture Foldseek stdout\n", encoding="utf-8")
            stderr_path.write_text(
                "" if return_code == 0 else self.foldseek_stderr,
                encoding="utf-8",
            )
            if return_code == 0:
                assert command.environment is not None
                output = Path(command.environment["FOLDSEEK_OUTPUT_DIR"])
                output.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(self.foldseek_fixture, output / "foldseek.tsv")
        else:
            return_code = self.tmalign_return_code
            if return_code == 0:
                shutil.copyfile(self.tmalign_fixture, stdout_path)
                stderr_path.write_text("", encoding="utf-8")
            else:
                stdout_path.write_text("", encoding="utf-8")
                stderr_path.write_text("fixture TM-align failure\n", encoding="utf-8")
        now = utc_now()
        return CommandProvenance(
            argv=command.argv,
            cwd=str((command.cwd or Path.cwd()).resolve()),
            backend=self.name,
            started_at=now,
            finished_at=now,
            duration_seconds=0.0,
            return_code=return_code,
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
        resolved_name = name or Path(executable).name
        self.version_names.append(resolved_name)
        unavailable = (
            resolved_name in self.unavailable_versions
            or resolved_name == "TMalign"
            and not self.tmalign_available
        )
        return SoftwareProvenance(
            name=resolved_name,
            version="unknown" if unavailable else f"{resolved_name} fixture-v1",
            executable=executable,
            version_command=(executable, *arguments),
            version_return_code=127 if unavailable else 0,
        )


def _configs(
    tmp_path: Path,
    *,
    foldseek_required: bool = True,
    tmalign_required: bool = True,
    top_n: int | None = 1,
) -> tuple[StructuresConfig, FoldseekConfig, TMAlignConfig, dict[str, Path]]:
    external = tmp_path / "external"
    external.mkdir(parents=True, exist_ok=True)
    query_structure = external / "query.pdb"
    reference = external / "reference.pdb"
    shutil.copyfile(STRUCTURES / "query.pdb", query_structure)
    shutil.copyfile(STRUCTURES / "query.pdb", reference)
    manifest = external / "manifest.tsv"
    manifest.write_text(
        "query_id\tstructure_id\tstructure_path\tstructure_format\tchain_id\t"
        "model_index\tstructure_source\n"
        f"q1\tq1-structure\t{query_structure}\tpdb\tA\t1\tuser_supplied\n",
        encoding="utf-8",
    )
    metadata = external / "metadata.tsv"
    metadata.write_text(
        "structure_id\tchain_id\tprotein_accession\tprotein_name\tec_numbers\t"
        "annotation_status\tsource_database\tdatabase_version\tstructure_method\t"
        "sequence_accession\tstructure_path\tstructure_format\tmodel_index\n"
        f"refA\tA\tP1\tReference A\t3.2.1.26\tcurated\tfixture-db\tv1\t"
        f"X-ray\tP1\t{reference}\tpdb\t1\n"
        f"refB\tA\tP2\tReference B\t1.1.1.1;2.7.-.-\treviewed\tfixture-db\t"
        f"v1\tcryo-EM\tP2\t{reference}\tpdb\t1\n"
        "refC\tA\t\tReference without EC\t\tunannotated\tfixture-db\tv1\t"
        "predicted\t\t\t\t\n",
        encoding="utf-8",
    )
    database = external / "foldseek-db"
    database.mkdir(exist_ok=True)
    fingerprint = external / "foldseek-db.sha256"
    fingerprint.write_text("fixture database fingerprint v1\n", encoding="utf-8")
    structures = structures_config_from_mapping(
        {
            "schema_version": 1,
            "structures": {"required": True, "manifest": str(manifest)},
        }
    )
    foldseek = foldseek_config_from_mapping(
        {
            "schema_version": 1,
            "foldseek": {
                "required": foldseek_required,
                "execution": {
                    "strategy": "docker_compose",
                    "docker_compose": {
                        "docker_executable": "docker-fixture",
                        "compose_file": str(
                            Path("docker/foldseek.compose.yml").resolve()
                        ),
                        "service": "foldseek",
                        "image": "ghcr.io/steineggerlab/foldseek:10-941cd33",
                        "image_digest": "sha256:1234abcd",
                    },
                },
                "database": {
                    "path": str(database),
                    "name": "curated_structures",
                    "version": "fixture-v1",
                    "fingerprint": str(fingerprint),
                    "metadata": str(metadata),
                },
                "parameters": {
                    "cpus": 2,
                    "evalue": 1.0,
                    "max_seqs": 20,
                    "sensitivity": None,
                },
                "filters": {
                    "maximum_evalue": 1.0,
                    "minimum_query_coverage": 0.0,
                    "minimum_target_coverage": 0.0,
                    "minimum_aligned_length": 1,
                    "maximum_retained_hits_per_query": None,
                    "threshold_status": "operational",
                },
                "software_version": "10-941cd33",
            },
        }
    )
    tmalign = tmalign_config_from_mapping(
        {
            "schema_version": 1,
            "tmalign": {
                "required": tmalign_required,
                "executable": "TMalign-fixture",
                "version_arguments": ["-version"],
                "selection": {
                    "top_hits_per_query": top_n,
                    "minimum_query_coverage": 0.0,
                    "minimum_target_coverage": 0.0,
                },
                "thresholds_validated": False,
            },
        }
    )
    return (
        structures,
        foldseek,
        tmalign,
        {
            "manifest": manifest,
            "metadata": metadata,
            "database": database,
            "fingerprint": fingerprint,
            "query_structure": query_structure,
        },
    )


def _workflow(
    structures: StructuresConfig,
    foldseek: FoldseekConfig,
    tmalign: TMAlignConfig | None = None,
) -> Workflow:
    stages = [ValidationStage(), StructuresStage(structures), FoldseekStage(foldseek)]
    if tmalign is not None:
        stages.append(TMAlignStage(tmalign, foldseek=foldseek))
    return Workflow(stages)


def _runner(tmp_path: Path, backend: ExecutionBackend) -> PipelineRunner:
    return PipelineRunner(
        load_config(),
        results_root=tmp_path / "results",
        logs_root=tmp_path / "logs",
        backend=backend,
    )


def _input(fasta_file, sequence: str = "MAG") -> Path:
    return fasta_file(f">q1\n{sequence}\n")


def _records(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line]


def _validate_evidence(records: list[dict]) -> None:
    schema = json.loads(Path("configs/schema/evidence.schema.json").read_text())
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    for record in records:
        validator.validate(record)


def test_structural_workflow_emits_correlated_canonical_evidence(
    tmp_path: Path, fasta_file
) -> None:
    structures, foldseek, tmalign, _ = _configs(tmp_path)
    backend = StructuralFixtureBackend()
    result = _runner(tmp_path, backend).run(
        _input(fasta_file),
        workflow=_workflow(structures, foldseek, tmalign),
        run_id="structural-success",
    )
    paths = RunPaths(tmp_path / "results", tmp_path / "logs", result.run_id)

    assert result.status is RunStatus.COMPLETED
    assert result.stage_outcomes == {
        "validate": StageStatus.COMPLETED,
        "structures": StageStatus.COMPLETED,
        "foldseek": StageStatus.COMPLETED,
        "tmalign": StageStatus.COMPLETED,
    }
    normalized_manifest = (
        paths.for_stage("structures").normalized / "structure_manifest.tsv"
    )
    assert "\texact\t" in normalized_manifest.read_text()
    assert (paths.for_stage("foldseek").raw / "foldseek.tsv").read_bytes() == (
        FOLDSEEK / "stage_hits.tsv"
    ).read_bytes()

    foldseek_records = _records(paths.evidence / "foldseek_evidence.jsonl")
    tmalign_records = _records(paths.evidence / "tmalign_evidence.jsonl")
    assert len(foldseek_records) == 7
    assert len(tmalign_records) == 1
    _validate_evidence(foldseek_records + tmalign_records)

    ref_b = [
        record
        for record in foldseek_records
        if record["metrics"]["reference_structure_id"] == "refB"
    ]
    assert {record["source"]["evidence_class"] for record in ref_b} == {
        "structure_homology",
        "curated_annotation",
    }
    assert {
        record["assertion"]["target"]["candidate_ec"]["ec"]
        for record in ref_b
        if record["assertion"]["target"]["type"] == "ec"
    } == {"1.1.1.1", "2.7.-.-"}
    groups = {record["source"]["correlation_group"] for record in ref_b}
    groups.add(tmalign_records[0]["source"]["correlation_group"])
    assert groups == {"structural:q1:refB:A"}
    assert tmalign_records[0]["metrics"]["tm_score_normalized_by_query"] == 0.9
    assert tmalign_records[0]["metrics"]["tm_score_normalized_by_target"] == 0.85
    no_ec = [
        record
        for record in foldseek_records
        if record["metrics"]["reference_structure_id"] == "refC"
    ]
    assert len(no_ec) == 1
    assert no_ec[0]["metrics"]["metadata_mapped"] is True
    assert no_ec[0]["assertion"]["target"]["type"] == "feature"
    unmapped = next(
        record
        for record in foldseek_records
        if record["metrics"]["reference_structure_id"] == "refD"
    )
    assert unmapped["metrics"]["metadata_mapped"] is False

    foldseek_command = next(
        command for command in backend.commands if "easy-search" in command
    )
    tmalign_command = next(
        command for command in backend.commands if command[0] == "TMalign-fixture"
    )
    assert foldseek_command[:7] == (
        "docker-fixture",
        "compose",
        "-f",
        str(Path("docker/foldseek.compose.yml").resolve()),
        "run",
        "--rm",
        "foldseek",
    )
    assert "query-structure-0001.pdb" in Path(tmalign_command[1]).name
    assert "reference-0001.pdb" == Path(tmalign_command[2]).name
    summary_names = {
        "structures": "structure_summary.json",
        "foldseek": "foldseek_summary.json",
        "tmalign": "tmalign_summary.json",
    }
    for stage_id, summary_name in summary_names.items():
        summary = json.loads(
            (paths.for_stage(stage_id).normalized / summary_name).read_text()
        )
        assert summary["final_ec_prediction"] is None


def test_resume_and_resource_changes_invalidate_dependents(
    tmp_path: Path, fasta_file
) -> None:
    structures, foldseek, tmalign, resources = _configs(tmp_path)
    backend = StructuralFixtureBackend()
    runner = _runner(tmp_path, backend)
    source = _input(fasta_file)
    workflow = _workflow(structures, foldseek, tmalign)

    runner.run(source, workflow=workflow, run_id="structural-cache")
    second = runner.run(source, workflow=workflow, run_id="structural-cache")
    assert backend.foldseek_calls == 1
    assert backend.tmalign_calls == 1
    assert second.stage_outcomes["structures"] is StageStatus.SKIPPED
    assert second.stage_outcomes["foldseek"] is StageStatus.SKIPPED
    assert second.stage_outcomes["tmalign"] is StageStatus.SKIPPED

    resources["fingerprint"].write_text("fixture database fingerprint v2\n")
    database_change = runner.run(source, workflow=workflow, run_id="structural-cache")
    assert database_change.stage_outcomes["structures"] is StageStatus.SKIPPED
    assert database_change.stage_outcomes["foldseek"] is StageStatus.COMPLETED
    assert database_change.stage_outcomes["tmalign"] is StageStatus.COMPLETED
    assert backend.foldseek_calls == 2
    assert backend.tmalign_calls == 2

    resources["metadata"].write_text(
        resources["metadata"].read_text().replace("fixture-db\tv1", "fixture-db\tv2")
    )
    metadata_change = runner.run(source, workflow=workflow, run_id="structural-cache")
    assert metadata_change.stage_outcomes["foldseek"] is StageStatus.COMPLETED
    assert metadata_change.stage_outcomes["tmalign"] is StageStatus.COMPLETED


def test_query_structure_change_invalidates_structural_stages(
    tmp_path: Path, fasta_file
) -> None:
    structures, foldseek, tmalign, resources = _configs(tmp_path)
    backend = StructuralFixtureBackend()
    runner = _runner(tmp_path, backend)
    source = _input(fasta_file)
    workflow = _workflow(structures, foldseek, tmalign)
    runner.run(source, workflow=workflow, run_id="structure-change")

    resources["query_structure"].write_text(
        resources["query_structure"].read_text() + "REMARK changed input\n"
    )
    changed = runner.run(source, workflow=workflow, run_id="structure-change")

    assert changed.stage_outcomes["structures"] is StageStatus.COMPLETED
    assert changed.stage_outcomes["foldseek"] is StageStatus.COMPLETED
    assert changed.stage_outcomes["tmalign"] is StageStatus.COMPLETED
    assert backend.foldseek_calls == 2
    assert backend.tmalign_calls == 2


def test_tmalign_selection_change_invalidates_only_tmalign(
    tmp_path: Path, fasta_file
) -> None:
    structures, foldseek, tmalign, _ = _configs(tmp_path, top_n=1)
    backend = StructuralFixtureBackend()
    runner = _runner(tmp_path, backend)
    source = _input(fasta_file)
    runner.run(
        source,
        workflow=_workflow(structures, foldseek, tmalign),
        run_id="tmalign-config-change",
    )
    _, _, changed_tmalign, _ = _configs(tmp_path, top_n=2)
    changed = runner.run(
        source,
        workflow=_workflow(structures, foldseek, changed_tmalign),
        run_id="tmalign-config-change",
    )

    assert changed.stage_outcomes["structures"] is StageStatus.SKIPPED
    assert changed.stage_outcomes["foldseek"] is StageStatus.SKIPPED
    assert changed.stage_outcomes["tmalign"] is StageStatus.COMPLETED
    assert backend.foldseek_calls == 1
    assert backend.tmalign_calls == 3


def test_zero_foldseek_hits_and_zero_tmalign_pairs_are_successful(
    tmp_path: Path, fasta_file
) -> None:
    structures, foldseek, tmalign, _ = _configs(tmp_path)
    backend = StructuralFixtureBackend(FOLDSEEK / "zero_hits.tsv")
    result = _runner(tmp_path, backend).run(
        _input(fasta_file),
        workflow=_workflow(structures, foldseek, tmalign),
        run_id="structural-zero",
    )
    paths = RunPaths(tmp_path / "results", tmp_path / "logs", result.run_id)
    foldseek_summary = json.loads(
        (paths.for_stage("foldseek").normalized / "foldseek_summary.json").read_text()
    )
    tmalign_summary = json.loads(
        (paths.for_stage("tmalign").normalized / "tmalign_summary.json").read_text()
    )

    assert result.status is RunStatus.COMPLETED
    assert foldseek_summary["empty_result"] is True
    assert tmalign_summary["zero_selected_pairs"] is True
    assert backend.tmalign_calls == 0
    assert "TMalign" not in backend.version_names


@pytest.mark.parametrize(
    ("required", "expected"),
    [
        (True, RunStatus.FAILED),
        (False, RunStatus.COMPLETED_WITH_OPTIONAL_FAILURES),
    ],
)
def test_foldseek_failure_respects_required_semantics(
    tmp_path: Path,
    fasta_file,
    required: bool,
    expected: RunStatus,
) -> None:
    structures, foldseek, _, _ = _configs(tmp_path, foldseek_required=required)
    backend = StructuralFixtureBackend(foldseek_return_code=2)
    result = _runner(tmp_path, backend).run(
        _input(fasta_file),
        workflow=_workflow(structures, foldseek),
        run_id=f"foldseek-failure-{required}",
    )

    assert result.status is expected
    assert result.stage_outcomes["foldseek"] is StageStatus.FAILED


def test_malformed_foldseek_output_preserves_raw_artifact(
    tmp_path: Path, fasta_file
) -> None:
    structures, foldseek, _, _ = _configs(tmp_path)
    backend = StructuralFixtureBackend(FOLDSEEK / "malformed.tsv")
    result = _runner(tmp_path, backend).run(
        _input(fasta_file),
        workflow=_workflow(structures, foldseek),
        run_id="foldseek-malformed",
    )
    paths = RunPaths(tmp_path / "results", tmp_path / "logs", result.run_id)
    state = StageStateStore(paths.for_stage("foldseek")).load()

    assert result.status is RunStatus.FAILED
    assert state is not None and "malformed_foldseek_output" in state.message
    assert (paths.for_stage("foldseek").raw / "foldseek.tsv").is_file()


def test_malformed_foldseek_database_has_distinct_failure_code(
    tmp_path: Path, fasta_file
) -> None:
    structures, foldseek, _, _ = _configs(tmp_path)
    backend = StructuralFixtureBackend(
        foldseek_return_code=1,
        foldseek_stderr="invalid database: cannot open index\n",
    )
    result = _runner(tmp_path, backend).run(
        _input(fasta_file),
        workflow=_workflow(structures, foldseek),
        run_id="foldseek-bad-db",
    )
    paths = RunPaths(tmp_path / "results", tmp_path / "logs", result.run_id)
    summary = json.loads(
        (paths.for_stage("foldseek").normalized / "foldseek_summary.json").read_text()
    )

    assert summary["reason_code"] == "foldseek_database_malformed"


@pytest.mark.parametrize(
    ("software_name", "reason"),
    [
        ("docker", "docker_runtime_unavailable"),
        ("docker-compose", "docker_compose_unavailable"),
        ("foldseek-container-image", "foldseek_image_unavailable"),
    ],
)
def test_foldseek_runtime_preflight_failures_are_distinct(
    tmp_path: Path,
    fasta_file,
    software_name: str,
    reason: str,
) -> None:
    structures, foldseek, _, _ = _configs(tmp_path)
    backend = StructuralFixtureBackend(unavailable_versions=frozenset({software_name}))
    result = _runner(tmp_path, backend).run(
        _input(fasta_file),
        workflow=_workflow(structures, foldseek),
        run_id=f"foldseek-unavailable-{software_name}",
    )
    paths = RunPaths(tmp_path / "results", tmp_path / "logs", result.run_id)
    summary = json.loads(
        (paths.for_stage("foldseek").normalized / "foldseek_summary.json").read_text()
    )

    assert result.status is RunStatus.FAILED
    assert summary["reason_code"] == reason
    assert backend.foldseek_calls == 0


def test_tmalign_command_failure_and_missing_executable_are_distinct(
    tmp_path: Path, fasta_file
) -> None:
    structures, foldseek, tmalign, _ = _configs(tmp_path)
    command_failure = StructuralFixtureBackend(tmalign_return_code=3)
    first = _runner(tmp_path, command_failure).run(
        _input(fasta_file),
        workflow=_workflow(structures, foldseek, tmalign),
        run_id="tmalign-command-failure",
    )
    first_paths = RunPaths(tmp_path / "results", tmp_path / "logs", first.run_id)
    first_summary = json.loads(
        (
            first_paths.for_stage("tmalign").normalized / "tmalign_summary.json"
        ).read_text()
    )
    assert first.status is RunStatus.FAILED
    assert first_summary["failures"][0]["reason_code"] == "tmalign_command_failed"

    second_root = tmp_path / "missing"
    structures, foldseek, tmalign, _ = _configs(second_root)
    unavailable = StructuralFixtureBackend(tmalign_available=False)
    second = _runner(second_root, unavailable).run(
        _input(fasta_file),
        workflow=_workflow(structures, foldseek, tmalign),
        run_id="tmalign-unavailable",
    )
    second_paths = RunPaths(
        second_root / "results", second_root / "logs", second.run_id
    )
    second_summary = json.loads(
        (
            second_paths.for_stage("tmalign").normalized / "tmalign_summary.json"
        ).read_text()
    )
    assert second.status is RunStatus.FAILED
    assert second_summary["reason_code"] == "tmalign_executable_unavailable"


def test_malformed_tmalign_output_fails_with_explicit_reason(
    tmp_path: Path, fasta_file
) -> None:
    structures, foldseek, tmalign, _ = _configs(tmp_path)
    backend = StructuralFixtureBackend(tmalign_fixture=TMALIGN / "malformed.txt")
    result = _runner(tmp_path, backend).run(
        _input(fasta_file),
        workflow=_workflow(structures, foldseek, tmalign),
        run_id="tmalign-malformed",
    )
    paths = RunPaths(tmp_path / "results", tmp_path / "logs", result.run_id)
    summary = json.loads(
        (paths.for_stage("tmalign").normalized / "tmalign_summary.json").read_text()
    )

    assert result.status is RunStatus.FAILED
    assert summary["failures"][0]["reason_code"] == "malformed_tmalign_output"


def test_missing_reference_structure_fails_selected_pair(
    tmp_path: Path, fasta_file
) -> None:
    structures, foldseek, tmalign, resources = _configs(tmp_path)
    resources["metadata"].write_text(
        resources["metadata"]
        .read_text()
        .replace(
            str(tmp_path / "external/reference.pdb"),
            str(tmp_path / "external/missing-reference.pdb"),
        )
    )
    result = _runner(tmp_path, StructuralFixtureBackend()).run(
        _input(fasta_file),
        workflow=_workflow(structures, foldseek, tmalign),
        run_id="missing-reference",
    )
    paths = RunPaths(tmp_path / "results", tmp_path / "logs", result.run_id)
    summary = json.loads(
        (paths.for_stage("tmalign").normalized / "tmalign_summary.json").read_text()
    )

    assert result.status is RunStatus.FAILED
    assert summary["failures"][0]["reason_code"] == ("reference_structure_unavailable")


@pytest.mark.parametrize(
    ("resource", "reason"),
    [
        ("database", "foldseek_database_unavailable"),
        ("metadata", "structure_metadata_unavailable"),
    ],
)
def test_missing_foldseek_resources_have_explicit_failure_codes(
    tmp_path: Path, fasta_file, resource: str, reason: str
) -> None:
    structures, foldseek, _, resources = _configs(tmp_path)
    target = resources[resource]
    if target.is_dir():
        target.rmdir()
    else:
        target.unlink()
    result = _runner(tmp_path, StructuralFixtureBackend()).run(
        _input(fasta_file),
        workflow=_workflow(structures, foldseek),
        run_id=f"missing-{resource}",
    )
    paths = RunPaths(tmp_path / "results", tmp_path / "logs", result.run_id)
    summary = json.loads(
        (paths.for_stage("foldseek").normalized / "foldseek_summary.json").read_text()
    )

    assert result.status is RunStatus.FAILED
    assert summary["reason_code"] == reason


def test_sequence_mismatch_remains_visible_without_coordinate_changes(
    tmp_path: Path, fasta_file
) -> None:
    structures, _, _, _ = _configs(tmp_path)
    result = _runner(tmp_path, StructuralFixtureBackend()).run(
        _input(fasta_file, "AAA"),
        workflow=Workflow([ValidationStage(), StructuresStage(structures)]),
        run_id="structure-mismatch",
    )
    paths = RunPaths(tmp_path / "results", tmp_path / "logs", result.run_id)
    summary = json.loads(
        (
            paths.for_stage("structures").normalized / "structure_summary.json"
        ).read_text()
    )

    assert result.status is RunStatus.COMPLETED
    assert summary["counts"]["mismatch"] == 1
    assert summary["coordinate_modification"] is False
