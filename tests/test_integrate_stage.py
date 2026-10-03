"""Workflow and cache tests for the integration stage."""

import json
from pathlib import Path

import yaml

from enzynotation.cli import build_parser, main
from enzynotation.provenance import sha256_bytes


def _run(tmp_path: Path, source: Path, *extra: str, run_id: str = "integration") -> int:
    return main(
        [
            "run",
            str(source),
            "--run-id",
            run_id,
            "--results-dir",
            str(tmp_path / "results"),
            "--logs-dir",
            str(tmp_path / "logs"),
            "--integration-config",
            "configs/integration/default.yaml",
            *extra,
        ]
    )


def _write_evidence(path: Path, canonical_record, sequence: str) -> None:
    checksum = sha256_bytes(sequence.encode("ascii"))
    values = [
        canonical_record(
            "blast",
            sequence_sha256=checksum,
            sequence_length=len(sequence),
        ),
        canonical_record(
            "clean",
            source="clean",
            evidence_class="learned_sequence_model",
            group="clean",
            sequence_sha256=checksum,
            sequence_length=len(sequence),
        ),
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(value) + "\n" for value in values))


def test_integration_stage_emits_first_class_unresolved_result(
    tmp_path: Path, fasta_file
) -> None:
    source = fasta_file(">q1\nACDEFGHIKL\n")
    assert _run(tmp_path, source) == 0
    root = tmp_path / "results/integration"
    final = json.loads((root / "final_annotation.json").read_text())
    assert final["annotations"][0]["predicted_ec"] is None
    assert final["annotations"][0]["confidence"] == "unresolved"
    assert (root / "stages/integrate/normalized/candidate_scorecards.jsonl").is_file()


def test_identical_integration_run_reuses_cache(tmp_path: Path, fasta_file) -> None:
    source = fasta_file(">q1\nACDEFGHIKL\n")
    assert _run(tmp_path, source) == 0
    assert _run(tmp_path, source) == 0
    root = tmp_path / "results/integration"
    status = json.loads((root / "stages/integrate/status.json").read_text())
    manifest = json.loads((root / "manifest.json").read_text())
    assert status["attempt"] == 1
    assert manifest["stages"]["integrate"]["status"] == "skipped"


def test_new_canonical_evidence_invalidates_integration_cache(
    tmp_path: Path, fasta_file, canonical_record
) -> None:
    sequence = "ACDEFGHIKL"
    source = fasta_file(f">q1\n{sequence}\n")
    assert _run(tmp_path, source) == 0
    root = tmp_path / "results/integration"
    _write_evidence(root / "evidence/blast_evidence.jsonl", canonical_record, sequence)
    assert _run(tmp_path, source) == 0
    status = json.loads((root / "stages/integrate/status.json").read_text())
    final = json.loads((root / "final_annotation.json").read_text())
    assert status["attempt"] == 2
    assert final["annotations"][0]["predicted_ec"] == "1.1.1.1"


def test_malformed_evidence_is_stage_failure(tmp_path: Path, fasta_file) -> None:
    source = fasta_file(">q1\nACDEFGHIKL\n")
    root = tmp_path / "results/integration"
    evidence = root / "evidence/blast_evidence.jsonl"
    evidence.parent.mkdir(parents=True)
    evidence.write_text('{"schema_version": 1}\n')
    assert _run(tmp_path, source) == 1
    status = json.loads((root / "stages/integrate/status.json").read_text())
    assert status["status"] == "failed"
    assert "Invalid evidence record" in status["message"]


def test_rule_change_invalidates_integration_cache(tmp_path: Path, fasta_file) -> None:
    source = fasta_file(">q1\nACDEFGHIKL\n")
    rules_path = tmp_path / "rules.yaml"
    rules = yaml.safe_load(Path("configs/ec_rules/example.yaml").read_text())
    rules_path.write_text(yaml.safe_dump(rules, sort_keys=False))
    assert _run(tmp_path, source, "--ec-rules", str(rules_path)) == 0
    rules["ruleset"]["description"] = "changed"
    rules_path.write_text(yaml.safe_dump(rules, sort_keys=False))
    assert _run(tmp_path, source, "--ec-rules", str(rules_path)) == 0
    status = json.loads(
        (tmp_path / "results/integration/stages/integrate/status.json").read_text()
    )
    assert status["attempt"] == 2


def test_integration_policy_change_invalidates_cache(
    tmp_path: Path, fasta_file
) -> None:
    source = fasta_file(">q1\nACDEFGHIKL\n")
    policy_dir = tmp_path / "policy"
    policy_dir.mkdir()
    confidence = Path("configs/integration/confidence.yaml").read_text()
    (policy_dir / "confidence.yaml").write_text(confidence)
    document = yaml.safe_load(Path("configs/integration/default.yaml").read_text())
    policy_path = policy_dir / "integration.yaml"
    policy_path.write_text(yaml.safe_dump(document, sort_keys=False))
    command = [
        "run",
        str(source),
        "--run-id",
        "policy-change",
        "--results-dir",
        str(tmp_path / "results"),
        "--logs-dir",
        str(tmp_path / "logs"),
        "--integration-config",
        str(policy_path),
    ]
    assert main(command) == 0
    document["integration"]["policy_version"] = "2"
    policy_path.write_text(yaml.safe_dump(document, sort_keys=False))
    assert main(command) == 0
    status = json.loads(
        (tmp_path / "results/policy-change/stages/integrate/status.json").read_text()
    )
    assert status["attempt"] == 2


def test_final_annotation_captures_integration_provenance(
    tmp_path: Path, fasta_file
) -> None:
    source = fasta_file(">q1\nACDEFGHIKL\n")
    assert _run(tmp_path, source, run_id="provenance") == 0
    final = json.loads(
        (tmp_path / "results/provenance/final_annotation.json").read_text()
    )
    provenance = final["provenance"]
    assert provenance["policy"] == {
        "id": "conservative-default",
        "version": "1",
        "calibration": "heuristic",
    }
    assert len(provenance["integration_configuration_sha256"]) == 64
    assert len(provenance["evidence_schema_sha256"]) == 64


def test_sequence_mismatched_evidence_fails_integration(
    tmp_path: Path, fasta_file, canonical_record
) -> None:
    source = fasta_file(">q1\nACDEFGHIKL\n")
    root = tmp_path / "results/mismatch"
    evidence = root / "evidence/blast_evidence.jsonl"
    evidence.parent.mkdir(parents=True)
    evidence.write_text(json.dumps(canonical_record("wrong-sequence")) + "\n")
    assert _run(tmp_path, source, run_id="mismatch") == 1
    status = json.loads((root / "stages/integrate/status.json").read_text())
    assert "does not match the validated sequence" in status["message"]


def test_cli_help_lists_integration_options() -> None:
    help_text = (
        build_parser()._subparsers._group_actions[0].choices["run"].format_help()
    )
    assert "--integration-config" in help_text
    assert "--confidence-config" in help_text
    assert "--ec-rules" in help_text
