"""Tests for bounded verification of external scientific resources."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from enzynotation.cli import build_parser, main
from enzynotation.databases import (
    build_database_manifest,
    load_database_registry,
    manifest_has_required_failures,
    write_database_manifest,
)
from enzynotation.exceptions import ConfigurationError
from enzynotation.provenance import canonical_json_sha256, sha256_file


def _resource(
    resource_id: str,
    path: str,
    *,
    required: bool,
    strategy: str = "explicit_version",
    value: str = "1",
    version: str = "1",
    **fingerprint: object,
) -> dict[str, object]:
    return {
        "resource_id": resource_id,
        "type": "blast_database",
        "providers": ["blastp"],
        "root": "databases",
        "path": path,
        "version": version,
        "fingerprint": {"strategy": strategy, "value": value, **fingerprint},
        "required": required,
        "metadata_path": None,
        "read_only": True,
        "source": {"name": "test fixture", "url": None},
        "license": {"name": "test-only", "url": None},
        "citation": None,
    }


def _registry(path: Path, resources: list[dict[str, object]]) -> Path:
    document = {
        "schema_version": 1,
        "registry": {
            "roots": {
                "databases": {
                    "environment_variable": "ENZYNOTATION_DB_ROOT",
                    "container_path": "/databases",
                }
            },
            "resources": resources,
        },
    }
    path.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")
    return path


def test_resource_root_override_and_explicit_version(tmp_path: Path) -> None:
    root = tmp_path / "external"
    root.mkdir()
    (root / "database.pin").write_text("fixture", encoding="utf-8")
    config = _registry(
        tmp_path / "databases.yaml",
        [_resource("db", "database.pin", required=True)],
    )
    registry = load_database_registry(config)
    manifest = build_database_manifest(
        registry,
        environment={"ENZYNOTATION_DB_ROOT": "/wrong"},
        root_overrides={"databases": root},
    )
    verified = manifest["resources"][0]
    assert verified["resolved_path"] == str((root / "database.pin").resolve())
    assert verified["validation_status"] == "valid"
    assert verified["fingerprint"]["strategy"] == "explicit_version"


def test_required_and_optional_missing_resources_are_distinct(tmp_path: Path) -> None:
    root = tmp_path / "external"
    root.mkdir()
    config = _registry(
        tmp_path / "databases.yaml",
        [
            _resource("required", "required.db", required=True),
            _resource("optional", "optional.db", required=False),
        ],
    )
    manifest = build_database_manifest(
        load_database_registry(config), root_overrides={"databases": root}
    )
    resources = {value["resource_id"]: value for value in manifest["resources"]}
    assert resources["required"]["validation_status"] == "invalid"
    assert resources["required"]["reason"] == "required_resource_missing"
    assert resources["optional"]["validation_status"] == "missing_optional"
    assert resources["optional"]["reason"] == "optional_resource_missing"
    assert manifest_has_required_failures(manifest)


def test_placeholder_fingerprint_is_never_reported_as_verified(tmp_path: Path) -> None:
    root = tmp_path / "external"
    root.mkdir()
    (root / "db").write_text("data", encoding="utf-8")
    config = _registry(
        tmp_path / "databases.yaml",
        [
            _resource(
                "placeholder",
                "db",
                required=False,
                strategy="administrator_supplied_fingerprint",
                value="replace-on-server",
            )
        ],
    )
    manifest = build_database_manifest(
        load_database_registry(config), root_overrides={"databases": root}
    )
    verified = manifest["resources"][0]
    assert verified["validation_status"] == "invalid"
    assert verified["reason"] == "fingerprint_not_configured"
    assert not manifest_has_required_failures(manifest)


def test_full_small_file_sha256_and_size_limit(tmp_path: Path) -> None:
    root = tmp_path / "external"
    root.mkdir()
    resource = root / "small.db"
    resource.write_bytes(b"small deterministic database")
    expected = f"sha256:{sha256_file(resource)}"
    config = _registry(
        tmp_path / "databases.yaml",
        [
            _resource(
                "small",
                "small.db",
                required=True,
                strategy="full_sha256_for_small_file",
                value=expected,
                maximum_bytes=1024,
            )
        ],
    )
    registry = load_database_registry(config)
    valid = build_database_manifest(registry, root_overrides={"databases": root})[
        "resources"
    ][0]
    assert valid["validation_status"] == "valid"
    document = yaml.safe_load(config.read_text())
    document["registry"]["resources"][0]["fingerprint"]["maximum_bytes"] = 1
    config.write_text(yaml.safe_dump(document, sort_keys=False))
    limited = build_database_manifest(
        load_database_registry(config), root_overrides={"databases": root}
    )["resources"][0]
    assert limited["reason"] == "full_sha256_size_limit_exceeded"


def test_selected_file_fingerprint_is_bounded_and_deterministic(tmp_path: Path) -> None:
    root = tmp_path / "external"
    database = root / "selected"
    database.mkdir(parents=True)
    (database / "a.pin").write_text("a", encoding="utf-8")
    (database / "b.pin").write_text("b", encoding="utf-8")
    (database / "ignored.large").write_text("not fingerprinted", encoding="utf-8")
    checksums = {
        "a.pin": sha256_file(database / "a.pin"),
        "b.pin": sha256_file(database / "b.pin"),
    }
    expected = f"sha256:{canonical_json_sha256(checksums)}"
    config = _registry(
        tmp_path / "databases.yaml",
        [
            _resource(
                "selected",
                "selected",
                required=True,
                strategy="selected_file_checksums",
                value=expected,
                selected_files=["b.pin", "a.pin"],
            )
        ],
    )
    registry = load_database_registry(config)
    first = build_database_manifest(registry, root_overrides={"databases": root})
    second = build_database_manifest(registry, root_overrides={"databases": root})
    assert first == second
    observed = first["resources"][0]["fingerprint"]
    assert observed["selected_file_checksums"] == checksums
    (database / "ignored.large").write_text("changed", encoding="utf-8")
    third = build_database_manifest(registry, root_overrides={"databases": root})
    assert third["resources"][0]["fingerprint"] == observed


def test_manifest_checksum_strategy_and_mismatch(tmp_path: Path) -> None:
    root = tmp_path / "external"
    root.mkdir()
    (root / "database").mkdir()
    fingerprint_manifest = root / "release.manifest"
    fingerprint_manifest.write_text("release=1\n", encoding="utf-8")
    expected = f"sha256:{sha256_file(fingerprint_manifest)}"
    config = _registry(
        tmp_path / "databases.yaml",
        [
            _resource(
                "manifested",
                "database",
                required=True,
                strategy="manifest_checksum",
                value=expected,
                manifest_path="release.manifest",
            )
        ],
    )
    registry = load_database_registry(config)
    valid = build_database_manifest(registry, root_overrides={"databases": root})
    assert valid["resources"][0]["validation_status"] == "valid"
    fingerprint_manifest.write_text("release=2\n", encoding="utf-8")
    invalid = build_database_manifest(registry, root_overrides={"databases": root})
    assert invalid["resources"][0]["reason"] == "fingerprint_mismatch"


def test_manifest_serialization_is_byte_deterministic(tmp_path: Path) -> None:
    root = tmp_path / "external"
    root.mkdir()
    (root / "db").write_text("data", encoding="utf-8")
    config = _registry(
        tmp_path / "databases.yaml",
        [_resource("db", "db", required=True)],
    )
    manifest = build_database_manifest(
        load_database_registry(config), root_overrides={"databases": root}
    )
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"
    write_database_manifest(first, manifest)
    write_database_manifest(second, manifest)
    assert first.read_bytes() == second.read_bytes()


def test_relative_paths_cannot_escape_resource_root(tmp_path: Path) -> None:
    config = _registry(
        tmp_path / "databases.yaml",
        [_resource("escape", "../outside", required=True)],
    )
    with pytest.raises(ConfigurationError, match="escapes configured root"):
        build_database_manifest(
            load_database_registry(config),
            root_overrides={"databases": tmp_path / "root"},
        )


def test_database_cli_verify_and_manifest(tmp_path: Path, capsys) -> None:
    root = tmp_path / "external"
    root.mkdir()
    (root / "db").write_text("data", encoding="utf-8")
    config = _registry(
        tmp_path / "databases.yaml",
        [_resource("db", "db", required=True)],
    )
    common = ["--config", str(config), "--database-root", str(root)]
    assert main(["databases", "verify", *common]) == 0
    verified = json.loads(capsys.readouterr().out)
    assert verified["summary"]["valid_count"] == 1
    output = tmp_path / "databases-manifest.json"
    assert main(["databases", "manifest", *common, "--output", str(output)]) == 0
    assert json.loads(output.read_text()) == verified


def test_database_cli_reports_required_failure_and_help(tmp_path: Path) -> None:
    root = tmp_path / "external"
    root.mkdir()
    config = _registry(
        tmp_path / "databases.yaml",
        [_resource("missing", "missing.db", required=True)],
    )
    assert (
        main(
            [
                "databases",
                "verify",
                "--config",
                str(config),
                "--database-root",
                str(root),
            ]
        )
        == 1
    )
    choices = build_parser()._subparsers._group_actions[0].choices
    assert "databases" in choices
