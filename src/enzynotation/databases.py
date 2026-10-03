"""External database and model registry verification."""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from jsonschema import Draft202012Validator, FormatChecker

from enzynotation.exceptions import ConfigurationError
from enzynotation.provenance import canonical_json_sha256, sha256_file
from enzynotation.state import atomic_write_json


@dataclass(frozen=True, slots=True)
class ResourceRoot:
    """One host root and its canonical container mount point."""

    root_id: str
    environment_variable: str
    container_path: str


@dataclass(frozen=True, slots=True)
class ScientificResource:
    """One immutable external scientific resource declaration."""

    document: Mapping[str, Any]

    @property
    def resource_id(self) -> str:
        return str(self.document["resource_id"])

    @property
    def root_id(self) -> str:
        return str(self.document["root"])


@dataclass(frozen=True, slots=True)
class DatabaseRegistry:
    """Validated scientific resource registry."""

    roots: Mapping[str, ResourceRoot]
    resources: tuple[ScientificResource, ...]
    source_path: Path
    schema_path: Path


def _load_mapping(path: Path, label: str) -> dict[str, Any]:
    try:
        value = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ConfigurationError(f"Cannot load {label} {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ConfigurationError(f"{label} {path} must contain a mapping")
    return value


def load_database_registry(
    path: Path = Path("configs/databases.yaml"),
    *,
    schema_path: Path = Path("configs/schema/databases.schema.json"),
) -> DatabaseRegistry:
    """Load and schema-validate an external scientific resource registry."""

    source = Path(path).resolve()
    schema_source = Path(schema_path).resolve()
    document = _load_mapping(source, "database registry")
    try:
        schema = json.loads(schema_source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigurationError(
            f"Cannot load database registry schema {schema_source}: {exc}"
        ) from exc
    errors = sorted(
        Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(
            document
        ),
        key=lambda error: list(error.path),
    )
    if errors:
        location = ".".join(str(value) for value in errors[0].absolute_path)
        rendered = f" at {location}" if location else ""
        raise ConfigurationError(
            f"Invalid database registry{rendered}: {errors[0].message}"
        )

    registry = document["registry"]
    roots = {
        root_id: ResourceRoot(
            root_id=root_id,
            environment_variable=str(value["environment_variable"]),
            container_path=str(value["container_path"]),
        )
        for root_id, value in registry["roots"].items()
    }
    resources = tuple(
        ScientificResource(value)
        for value in sorted(
            registry["resources"], key=lambda item: str(item["resource_id"])
        )
    )
    identifiers = [resource.resource_id for resource in resources]
    if len(identifiers) != len(set(identifiers)):
        raise ConfigurationError("database registry contains duplicate resource_id")
    unknown_roots = sorted(
        {resource.root_id for resource in resources if resource.root_id not in roots}
    )
    if unknown_roots:
        raise ConfigurationError(
            "database registry references unknown roots: " + ", ".join(unknown_roots)
        )
    return DatabaseRegistry(roots, resources, source, schema_source)


def resolve_resource_roots(
    registry: DatabaseRegistry,
    *,
    environment: Mapping[str, str] | None = None,
    overrides: Mapping[str, Path] | None = None,
) -> dict[str, Path | None]:
    """Resolve registry roots without changing external directories."""

    selected_environment = os.environ if environment is None else environment
    selected_overrides = overrides or {}
    unknown = sorted(set(selected_overrides) - set(registry.roots))
    if unknown:
        raise ConfigurationError(
            "unknown resource root override: " + ", ".join(unknown)
        )
    result: dict[str, Path | None] = {}
    for root_id, root in sorted(registry.roots.items()):
        raw = selected_overrides.get(root_id)
        if raw is None:
            configured = selected_environment.get(root.environment_variable)
            raw = Path(configured) if configured else None
        result[root_id] = Path(raw).expanduser().resolve() if raw else None
    return result


def _resolved_path(root: Path | None, configured: str) -> Path | None:
    path = Path(configured).expanduser()
    if path.is_absolute():
        return path.resolve()
    if root is None:
        return None
    resolved_root = root.resolve()
    resolved = (resolved_root / path).resolve()
    if not resolved.is_relative_to(resolved_root):
        raise ConfigurationError(
            f"relative resource path escapes configured root: {configured}"
        )
    return resolved


def _fingerprint(
    resource: ScientificResource,
    *,
    root: Path | None,
    resolved_path: Path,
) -> tuple[str | None, dict[str, str], str | None]:
    document = resource.document
    configured = document["fingerprint"]
    strategy = configured["strategy"]
    selected_checksums: dict[str, str] = {}
    if str(configured["value"]).lower() in {"unknown", "replace-on-server"}:
        return None, selected_checksums, "fingerprint_not_configured"
    if strategy == "explicit_version":
        if str(document["version"]).lower() == "unknown":
            return None, selected_checksums, "resource_version_not_configured"
        return str(document["version"]), selected_checksums, None
    if strategy == "administrator_supplied_fingerprint":
        return str(configured["value"]), selected_checksums, None
    if strategy == "manifest_checksum":
        manifest = _resolved_path(root, str(configured["manifest_path"]))
        if manifest is None or not manifest.is_file():
            return None, selected_checksums, "fingerprint_manifest_missing"
        return f"sha256:{sha256_file(manifest)}", selected_checksums, None
    if strategy == "selected_file_checksums":
        base = resolved_path if resolved_path.is_dir() else resolved_path.parent
        for relative in sorted(str(value) for value in configured["selected_files"]):
            relative_path = Path(relative)
            selected = (base / relative_path).resolve()
            if relative_path.is_absolute() or not selected.is_relative_to(
                base.resolve()
            ):
                return None, selected_checksums, "selected_fingerprint_path_escape"
            if not selected.is_file():
                reason = f"selected_fingerprint_file_missing:{relative}"
                return None, selected_checksums, reason
            selected_checksums[relative] = sha256_file(selected)
        return (
            f"sha256:{canonical_json_sha256(selected_checksums)}",
            selected_checksums,
            None,
        )
    if strategy == "full_sha256_for_small_file":
        if not resolved_path.is_file():
            return None, selected_checksums, "full_sha256_requires_file"
        maximum = int(configured["maximum_bytes"])
        if resolved_path.stat().st_size > maximum:
            return None, selected_checksums, "full_sha256_size_limit_exceeded"
        return f"sha256:{sha256_file(resolved_path)}", selected_checksums, None
    raise AssertionError(f"unsupported fingerprint strategy: {strategy}")


def _status_for_missing(required: bool) -> tuple[str, str]:
    return (
        ("invalid", "required_resource_missing")
        if required
        else ("missing_optional", "optional_resource_missing")
    )


def verify_resource(
    resource: ScientificResource,
    *,
    root: Path | None,
) -> dict[str, Any]:
    """Verify one resource using only its configured bounded strategy."""

    document = resource.document
    configured_path = str(document["path"])
    resolved = _resolved_path(root, configured_path)
    metadata_configured = document.get("metadata_path")
    metadata = (
        _resolved_path(root, str(metadata_configured))
        if metadata_configured is not None
        else None
    )
    required = bool(document["required"])
    exists = resolved is not None and resolved.exists()
    status = "valid"
    reason: str | None = None
    observed: str | None = None
    selected_checksums: dict[str, str] = {}
    if resolved is None:
        status, reason = (
            ("invalid", "required_resource_root_unset")
            if required
            else ("missing_optional", "optional_resource_root_unset")
        )
    elif not exists:
        status, reason = _status_for_missing(required)
    elif metadata is not None and not metadata.is_file():
        status = "invalid"
        reason = "resource_metadata_missing"
    else:
        observed, selected_checksums, reason = _fingerprint(
            resource,
            root=root,
            resolved_path=resolved,
        )
        if reason is not None:
            status = "invalid"
        elif observed != document["fingerprint"]["value"]:
            status = "invalid"
            reason = "fingerprint_mismatch"

    return {
        "resource_id": resource.resource_id,
        "type": document["type"],
        "providers": list(document["providers"]),
        "root": resource.root_id,
        "expected_path": configured_path,
        "resolved_path": str(resolved) if resolved is not None else None,
        "required": required,
        "read_only": bool(document["read_only"]),
        "configured_version": document["version"],
        "metadata_path": metadata_configured,
        "resolved_metadata_path": str(metadata) if metadata is not None else None,
        "exists": exists,
        "fingerprint": {
            "strategy": document["fingerprint"]["strategy"],
            "configured": document["fingerprint"]["value"],
            "observed": observed,
            "selected_file_checksums": selected_checksums,
        },
        "validation_status": status,
        "reason": reason,
        "source": document["source"],
        "license": document["license"],
        "citation": document["citation"],
    }


def build_database_manifest(
    registry: DatabaseRegistry,
    *,
    environment: Mapping[str, str] | None = None,
    root_overrides: Mapping[str, Path] | None = None,
) -> dict[str, Any]:
    """Create a deterministic manifest without modifying registered resources."""

    resolved_roots = resolve_resource_roots(
        registry,
        environment=environment,
        overrides=root_overrides,
    )
    resources = [
        verify_resource(resource, root=resolved_roots[resource.root_id])
        for resource in registry.resources
    ]
    return {
        "schema_version": 1,
        "registry": {
            "path": str(registry.source_path),
            "sha256": sha256_file(registry.source_path),
            "schema_path": str(registry.schema_path),
            "schema_sha256": sha256_file(registry.schema_path),
        },
        "roots": [
            {
                "root_id": root_id,
                "environment_variable": root.environment_variable,
                "container_path": root.container_path,
                "resolved_path": (
                    str(resolved_roots[root_id])
                    if resolved_roots[root_id] is not None
                    else None
                ),
            }
            for root_id, root in sorted(registry.roots.items())
        ],
        "resources": resources,
        "summary": {
            "resource_count": len(resources),
            "valid_count": sum(
                item["validation_status"] == "valid" for item in resources
            ),
            "required_invalid_count": sum(
                item["required"] and item["validation_status"] != "valid"
                for item in resources
            ),
            "optional_missing_or_invalid_count": sum(
                not item["required"] and item["validation_status"] != "valid"
                for item in resources
            ),
        },
    }


def manifest_has_required_failures(manifest: Mapping[str, Any]) -> bool:
    """Return whether verification found any unusable required resource."""

    return bool(manifest["summary"]["required_invalid_count"])


def write_database_manifest(path: Path, manifest: Mapping[str, Any]) -> None:
    """Atomically write a deterministic resource manifest outside data roots."""

    atomic_write_json(Path(path), manifest)
