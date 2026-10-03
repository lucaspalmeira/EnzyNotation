"""Static tests for the future target-server Compose deployment."""

from __future__ import annotations

import re
from pathlib import Path

import yaml

COMPOSE_PATH = Path("compose.yaml")


def _compose() -> dict[str, object]:
    return yaml.safe_load(COMPOSE_PATH.read_text(encoding="utf-8"))


def _mounts(service: dict[str, object]) -> dict[str, dict[str, object]]:
    return {value["target"]: value for value in service["volumes"]}


def _interpolated_image(value: str, environment: dict[str, str]) -> str:
    match = re.fullmatch(r"\$\{([A-Z0-9_]+):-([^}]+)\}", value)
    assert match is not None
    variable, default = match.groups()
    return environment.get(variable, default)


def test_compose_yaml_has_expected_sibling_services() -> None:
    compose = _compose()
    assert compose["name"] == "enzynotation"
    assert set(compose["services"]) == {"enzynotation", "clean", "foldseek"}
    assert all("depends_on" not in value for value in compose["services"].values())


def test_core_has_project_build_without_large_resource_copy() -> None:
    service = _compose()["services"]["enzynotation"]
    build = service["build"]
    assert build["context"] == "."
    dockerfile = build["dockerfile_inline"]
    assert "FROM python:3.12-slim" in dockerfile
    assert "ncbi-blast+" in dockerfile
    assert "hmmer" in dockerfile
    assert "COPY ." not in dockerfile
    assert "COPY databases" not in dockerfile
    assert "COPY models" not in dockerfile
    assert 'ENTRYPOINT ["enzynotation"]' in dockerfile
    ignored = set(Path(".dockerignore").read_text(encoding="utf-8").splitlines())
    assert {"databases", "models", "input", "results", "logs"}.issubset(ignored)


def test_external_images_have_defaults_and_environment_overrides() -> None:
    services = _compose()["services"]
    assert _interpolated_image(services["clean"]["image"], {}) == (
        "moleculemaker/clean-image-amd64"
    )
    assert _interpolated_image(services["foldseek"]["image"], {}) == (
        "ghcr.io/steineggerlab/foldseek:10-941cd33"
    )
    assert (
        _interpolated_image(
            services["clean"]["image"], {"CLEAN_IMAGE": "registry/clean@sha256:abc"}
        )
        == "registry/clean@sha256:abc"
    )
    assert (
        _interpolated_image(
            services["foldseek"]["image"],
            {"FOLDSEEK_IMAGE": "registry/foldseek@sha256:def"},
        )
        == "registry/foldseek@sha256:def"
    )


def test_services_are_unprivileged_portless_and_have_no_docker_socket() -> None:
    compose = _compose()
    text = COMPOSE_PATH.read_text(encoding="utf-8")
    assert "/var/run/docker.sock" not in text
    for service in compose["services"].values():
        assert service.get("privileged") is not True
        assert "ports" not in service
        assert service["restart"] == "no"
        assert "no-new-privileges:true" in service["security_opt"]


def test_shared_mount_contract_and_access_modes_are_consistent() -> None:
    services = _compose()["services"]
    shared = {
        "/work/input",
        "/work/results",
        "/work/logs",
        "/databases",
        "/models",
        "/cache",
    }
    for service in services.values():
        mounts = _mounts(service)
        assert shared.issubset(mounts)
        assert mounts["/work/input"]["read_only"] is True
        assert mounts["/databases"]["read_only"] is True
        assert mounts["/models"]["read_only"] is True
        assert "read_only" not in mounts["/work/results"]
        assert "read_only" not in mounts["/work/logs"]
    clean_mounts = _mounts(services["clean"])
    assert "/root/.cache/torch/hub/checkpoints" in clean_mounts
    assert "/work/tmp" in _mounts(services["foldseek"])


def test_profiles_keep_batch_providers_opt_in_and_one_shot() -> None:
    services = _compose()["services"]
    assert services["enzynotation"]["profiles"] == ["core"]
    assert services["clean"]["profiles"] == ["clean"]
    assert services["foldseek"]["profiles"] == ["foldseek"]
    assert services["clean"]["restart"] == "no"
    assert services["foldseek"]["restart"] == "no"


def test_compose_contains_no_scientific_decision_configuration() -> None:
    compose = _compose()
    forbidden = {
        "evalue",
        "identity_threshold",
        "coverage_threshold",
        "predicted_ec",
        "confidence_threshold",
    }
    for service in compose["services"].values():
        environment = service.get("environment", {})
        assert forbidden.isdisjoint(environment)
        assert "command" not in service


def test_safe_environment_example_and_legacy_adapters_are_preserved() -> None:
    environment = Path(".env.example").read_text(encoding="utf-8")
    assert "ENZYNOTATION_DB_ROOT=" in environment
    assert "ENZYNOTATION_MODEL_ROOT=" in environment
    assert "CLEAN_IMAGE=moleculemaker/clean-image-amd64" in environment
    assert "FOLDSEEK_IMAGE=ghcr.io/steineggerlab/foldseek:10-941cd33" in environment
    assert "/home/" not in environment
    assert not any(value in environment.lower() for value in ("password=", "token="))
    assert ".env" in Path(".gitignore").read_text(encoding="utf-8").splitlines()
    assert Path("docker/clean.compose.yml").is_file()
    assert Path("docker/foldseek.compose.yml").is_file()
