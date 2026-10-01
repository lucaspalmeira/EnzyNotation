"""Generic enzyme-family configuration used by domain and motif providers."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from enzynotation.ec import normalize_ec
from enzynotation.exceptions import EnzyNotationError

_STABLE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_FAMILY_ID = re.compile(r"^[a-z0-9][a-z0-9._-]*$")
_REQUIREMENTS = {"required", "expected", "supporting", "optional", "forbidden"}


class FamilyConfigurationError(ValueError, EnzyNotationError):
    """Raised when a family profile is malformed or internally inconsistent."""


@dataclass(frozen=True, slots=True)
class SequenceAnalysis:
    """Sequence region and configured completeness heuristic."""

    region_start: int = 1
    region_end: int | None = None
    minimum_complete_length: int | None = None
    maximum_complete_length: int | None = None

    def completeness(self, sequence_length: int) -> str:
        """Classify sequence completeness from explicit family limits."""

        if self.minimum_complete_length is None:
            return "unknown"
        if sequence_length < self.minimum_complete_length:
            return "truncated"
        if (
            self.maximum_complete_length is not None
            and sequence_length > self.maximum_complete_length
        ):
            return "outside_expected_length"
        return "complete"


@dataclass(frozen=True, slots=True)
class DomainRule:
    """One configured domain-signature expectation."""

    rule_id: str
    source: str
    signature_id: str
    requirement: str
    min_count: int
    max_count: int | None
    minimum_query_coverage: float
    maximum_evalue: float | None
    architecture_order: int | None
    candidate_ecs: tuple[str, ...]
    description: str = ""


@dataclass(frozen=True, slots=True)
class PositionConstraint:
    """Allowed motif location relative to a sequence or domain boundary."""

    relative_to: str
    min_offset: int
    max_offset: int
    domain_rule_id: str | None = None


@dataclass(frozen=True, slots=True)
class CoordinateConstraint:
    """Absolute one-based inclusive motif coordinate limits."""

    minimum_start: int | None = None
    maximum_start: int | None = None
    minimum_end: int | None = None
    maximum_end: int | None = None


@dataclass(frozen=True, slots=True)
class CatalyticResidue:
    """Named residue at a zero-based offset within a motif match."""

    name: str
    motif_offset: int
    allowed_residues: frozenset[str]
    expected_position: int | None = None
    tolerance: int = 0


@dataclass(frozen=True, slots=True)
class MotifRule:
    """One configuration-driven catalytic motif rule."""

    rule_id: str
    pattern: str
    pattern_type: str
    requirement: str
    min_occurrences: int
    max_occurrences: int | None
    enabled: bool
    overlapping: bool
    position: PositionConstraint | None
    coordinates: CoordinateConstraint | None
    catalytic_residues: tuple[CatalyticResidue, ...]
    candidate_ecs: tuple[str, ...]
    description: str = ""


@dataclass(frozen=True, slots=True)
class MotifRelationship:
    """Ordering and distance constraint between two configured motifs."""

    relationship_id: str
    upstream_motif: str
    downstream_motif: str
    requirement: str
    minimum_distance: int | None = None
    maximum_distance: int | None = None


@dataclass(frozen=True, slots=True)
class FamilyProfile:
    """Validated family profile with generic domain and motif rules."""

    family_id: str
    name: str
    version: str
    sequence_analysis: SequenceAnalysis
    domains: tuple[DomainRule, ...]
    motifs: tuple[MotifRule, ...]
    motif_relationships: tuple[MotifRelationship, ...]
    document: dict[str, Any]
    source_path: Path

    def to_dict(self) -> dict[str, Any]:
        """Return the parsed configuration used for cache signatures."""

        return self.document


def _mapping(value: Any, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise FamilyConfigurationError(f"{field} must be a mapping")
    return value


def _list(value: Any, field: str) -> list[Any]:
    if not isinstance(value, list):
        raise FamilyConfigurationError(f"{field} must be a list")
    return value


def _identifier(value: Any, field: str, *, family: bool = False) -> str:
    pattern = _FAMILY_ID if family else _STABLE_ID
    if not isinstance(value, str) or not pattern.fullmatch(value):
        raise FamilyConfigurationError(f"{field} is not a valid stable identifier")
    return value


def _nonempty(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise FamilyConfigurationError(f"{field} must be a non-empty string")
    return value.strip()


def _integer(
    value: Any, field: str, *, minimum: int = 0, optional: bool = False
) -> int | None:
    if value is None and optional:
        return None
    if not isinstance(value, int) or isinstance(value, bool) or value < minimum:
        raise FamilyConfigurationError(
            f"{field} must be an integer greater than or equal to {minimum}"
        )
    return value


def _number(
    value: Any,
    field: str,
    *,
    minimum: float = 0.0,
    maximum: float | None = None,
    optional: bool = False,
) -> float | None:
    if value is None and optional:
        return None
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise FamilyConfigurationError(f"{field} must be numeric")
    number = float(value)
    if number < minimum or (maximum is not None and number > maximum):
        rendered = f" from {minimum} to {maximum}" if maximum is not None else ""
        raise FamilyConfigurationError(f"{field} must be{rendered}")
    return number


def _requirement(value: Any, field: str) -> str:
    if value not in _REQUIREMENTS:
        allowed = ", ".join(sorted(_REQUIREMENTS))
        raise FamilyConfigurationError(f"{field} must be one of: {allowed}")
    return str(value)


def _candidate_ecs(value: Any, field: str) -> tuple[str, ...]:
    if value is None:
        return ()
    values = _list(value, field)
    normalized = tuple(normalize_ec(_nonempty(ec, field)) for ec in values)
    if len(set(normalized)) != len(normalized):
        raise FamilyConfigurationError(f"{field} contains duplicate EC numbers")
    return normalized


def _sequence_analysis(document: dict[str, Any]) -> SequenceAnalysis:
    value = document.get("sequence_analysis", {})
    config = _mapping(value, "sequence_analysis")
    start = _integer(
        config.get("region_start", 1), "sequence_analysis.region_start", minimum=1
    )
    end = _integer(
        config.get("region_end"),
        "sequence_analysis.region_end",
        minimum=1,
        optional=True,
    )
    minimum_length = _integer(
        config.get("minimum_complete_length"),
        "sequence_analysis.minimum_complete_length",
        minimum=1,
        optional=True,
    )
    maximum_length = _integer(
        config.get("maximum_complete_length"),
        "sequence_analysis.maximum_complete_length",
        minimum=1,
        optional=True,
    )
    assert isinstance(start, int)
    if end is not None and end < start:
        raise FamilyConfigurationError(
            "sequence_analysis.region_end must not precede region_start"
        )
    if (
        minimum_length is not None
        and maximum_length is not None
        and maximum_length < minimum_length
    ):
        raise FamilyConfigurationError(
            "maximum_complete_length must not be below minimum_complete_length"
        )
    return SequenceAnalysis(start, end, minimum_length, maximum_length)


def _domain_rules(rules: dict[str, Any]) -> tuple[DomainRule, ...]:
    result: list[DomainRule] = []
    seen: set[str] = set()
    for index, raw in enumerate(_list(rules.get("domains"), "rules.domains")):
        field = f"rules.domains[{index}]"
        item = _mapping(raw, field)
        rule_id = _identifier(item.get("rule_id"), f"{field}.rule_id")
        if rule_id in seen:
            raise FamilyConfigurationError(f"duplicate domain rule_id {rule_id!r}")
        seen.add(rule_id)
        source = item.get("source")
        if source not in {"pfam", "hmmer", "interproscan"}:
            raise FamilyConfigurationError(f"{field}.source is unsupported")
        minimum_coverage = _number(
            item.get("minimum_query_coverage", 0.0),
            f"{field}.minimum_query_coverage",
            maximum=1.0,
        )
        maximum_evalue = _number(
            item.get("maximum_evalue"),
            f"{field}.maximum_evalue",
            optional=True,
        )
        min_count = _integer(item.get("min_count"), f"{field}.min_count")
        max_count = _integer(item.get("max_count"), f"{field}.max_count", optional=True)
        order = _integer(
            item.get("architecture_order"),
            f"{field}.architecture_order",
            minimum=1,
            optional=True,
        )
        assert isinstance(min_count, int)
        assert isinstance(minimum_coverage, float)
        if max_count is not None and max_count < min_count:
            raise FamilyConfigurationError(
                f"{field}.max_count must not be below min_count"
            )
        result.append(
            DomainRule(
                rule_id=rule_id,
                source=str(source),
                signature_id=_nonempty(
                    item.get("signature_id"), f"{field}.signature_id"
                ),
                requirement=_requirement(
                    item.get("requirement"), f"{field}.requirement"
                ),
                min_count=min_count,
                max_count=max_count,
                minimum_query_coverage=minimum_coverage,
                maximum_evalue=maximum_evalue,
                architecture_order=order,
                candidate_ecs=_candidate_ecs(
                    item.get("candidate_ecs"), f"{field}.candidate_ecs"
                ),
                description=str(item.get("description", "")),
            )
        )
    return tuple(result)


def _position(value: Any, field: str) -> PositionConstraint | None:
    if value is None:
        return None
    item = _mapping(value, field)
    relative_to = item.get("relative_to")
    if relative_to not in {
        "sequence_start",
        "sequence_end",
        "domain_start",
        "domain_end",
    }:
        raise FamilyConfigurationError(f"{field}.relative_to is unsupported")
    minimum = _integer(item.get("min_offset"), f"{field}.min_offset", minimum=-(10**9))
    maximum = _integer(item.get("max_offset"), f"{field}.max_offset", minimum=-(10**9))
    assert isinstance(minimum, int) and isinstance(maximum, int)
    if maximum < minimum:
        raise FamilyConfigurationError(
            f"{field}.max_offset must not precede min_offset"
        )
    domain_rule_id = item.get("domain_rule_id")
    if relative_to.startswith("domain_"):
        domain_rule_id = _identifier(domain_rule_id, f"{field}.domain_rule_id")
    elif domain_rule_id is not None:
        domain_rule_id = _identifier(domain_rule_id, f"{field}.domain_rule_id")
    return PositionConstraint(str(relative_to), minimum, maximum, domain_rule_id)


def _coordinates(value: Any, field: str) -> CoordinateConstraint | None:
    if value is None:
        return None
    item = _mapping(value, field)
    values = {
        name: _integer(item.get(name), f"{field}.{name}", minimum=1, optional=True)
        for name in (
            "minimum_start",
            "maximum_start",
            "minimum_end",
            "maximum_end",
        )
    }
    if (
        values["minimum_start"] is not None
        and values["maximum_start"] is not None
        and values["maximum_start"] < values["minimum_start"]
    ):
        raise FamilyConfigurationError(f"{field} start range is reversed")
    if (
        values["minimum_end"] is not None
        and values["maximum_end"] is not None
        and values["maximum_end"] < values["minimum_end"]
    ):
        raise FamilyConfigurationError(f"{field} end range is reversed")
    return CoordinateConstraint(**values)


def _catalytic_residues(value: Any, field: str) -> tuple[CatalyticResidue, ...]:
    if value is None:
        return ()
    result: list[CatalyticResidue] = []
    names: set[str] = set()
    for index, raw in enumerate(_list(value, field)):
        item_field = f"{field}[{index}]"
        item = _mapping(raw, item_field)
        name = _identifier(item.get("name"), f"{item_field}.name")
        if name in names:
            raise FamilyConfigurationError(f"duplicate catalytic residue {name!r}")
        names.add(name)
        residues = _nonempty(
            item.get("allowed_residues"), f"{item_field}.allowed_residues"
        ).upper()
        if any(not residue.isalpha() or len(residue) != 1 for residue in residues):
            raise FamilyConfigurationError(
                f"{item_field}.allowed_residues must contain one-letter residues"
            )
        offset = _integer(item.get("motif_offset"), f"{item_field}.motif_offset")
        expected = _integer(
            item.get("expected_position"),
            f"{item_field}.expected_position",
            minimum=1,
            optional=True,
        )
        tolerance = _integer(item.get("tolerance", 0), f"{item_field}.tolerance")
        assert isinstance(offset, int) and isinstance(tolerance, int)
        result.append(
            CatalyticResidue(
                name=name,
                motif_offset=offset,
                allowed_residues=frozenset(residues),
                expected_position=expected,
                tolerance=tolerance,
            )
        )
    return tuple(result)


def _motif_rules(rules: dict[str, Any]) -> tuple[MotifRule, ...]:
    result: list[MotifRule] = []
    seen: set[str] = set()
    for index, raw in enumerate(_list(rules.get("motifs"), "rules.motifs")):
        field = f"rules.motifs[{index}]"
        item = _mapping(raw, field)
        rule_id = _identifier(item.get("rule_id"), f"{field}.rule_id")
        if rule_id in seen:
            raise FamilyConfigurationError(f"duplicate motif rule_id {rule_id!r}")
        seen.add(rule_id)
        pattern_type = item.get("pattern_type", "regex")
        if pattern_type not in {"literal", "regex"}:
            raise FamilyConfigurationError(
                f"{field}.pattern_type must be literal or regex"
            )
        pattern = _nonempty(item.get("pattern"), f"{field}.pattern")
        if pattern_type == "regex":
            try:
                expression = re.compile(pattern)
            except re.error as exc:
                raise FamilyConfigurationError(
                    f"{field}.pattern is not a valid regex: {exc}"
                ) from exc
            if expression.match("") is not None:
                raise FamilyConfigurationError(
                    f"{field}.pattern must not match an empty sequence"
                )
        enabled = item.get("enabled", True)
        overlapping = item.get("overlapping", False)
        if not isinstance(enabled, bool) or not isinstance(overlapping, bool):
            raise FamilyConfigurationError(
                f"{field}.enabled and overlapping must be booleans"
            )
        minimum = _integer(item.get("min_occurrences"), f"{field}.min_occurrences")
        maximum = _integer(
            item.get("max_occurrences"),
            f"{field}.max_occurrences",
            optional=True,
        )
        assert isinstance(minimum, int)
        if maximum is not None and maximum < minimum:
            raise FamilyConfigurationError(
                f"{field}.max_occurrences must not be below min_occurrences"
            )
        result.append(
            MotifRule(
                rule_id=rule_id,
                pattern=pattern,
                pattern_type=str(pattern_type),
                requirement=_requirement(
                    item.get("requirement"), f"{field}.requirement"
                ),
                min_occurrences=minimum,
                max_occurrences=maximum,
                enabled=enabled,
                overlapping=overlapping,
                position=_position(item.get("position"), f"{field}.position"),
                coordinates=_coordinates(
                    item.get("coordinates"), f"{field}.coordinates"
                ),
                catalytic_residues=_catalytic_residues(
                    item.get("catalytic_residues"),
                    f"{field}.catalytic_residues",
                ),
                candidate_ecs=_candidate_ecs(
                    item.get("candidate_ecs"), f"{field}.candidate_ecs"
                ),
                description=str(item.get("description", "")),
            )
        )
    return tuple(result)


def _relationships(
    rules: dict[str, Any], motif_ids: set[str]
) -> tuple[MotifRelationship, ...]:
    result: list[MotifRelationship] = []
    seen: set[str] = set()
    values = rules.get("motif_relationships", [])
    for index, raw in enumerate(_list(values, "rules.motif_relationships")):
        field = f"rules.motif_relationships[{index}]"
        item = _mapping(raw, field)
        relationship_id = _identifier(
            item.get("relationship_id"), f"{field}.relationship_id"
        )
        if relationship_id in seen:
            raise FamilyConfigurationError(
                f"duplicate motif relationship {relationship_id!r}"
            )
        seen.add(relationship_id)
        upstream = _identifier(item.get("upstream_motif"), f"{field}.upstream_motif")
        downstream = _identifier(
            item.get("downstream_motif"), f"{field}.downstream_motif"
        )
        if upstream not in motif_ids or downstream not in motif_ids:
            raise FamilyConfigurationError(f"{field} references an unknown motif rule")
        minimum = _integer(
            item.get("minimum_distance"),
            f"{field}.minimum_distance",
            optional=True,
        )
        maximum = _integer(
            item.get("maximum_distance"),
            f"{field}.maximum_distance",
            optional=True,
        )
        if minimum is not None and maximum is not None and maximum < minimum:
            raise FamilyConfigurationError(
                f"{field}.maximum_distance must not be below minimum_distance"
            )
        result.append(
            MotifRelationship(
                relationship_id=relationship_id,
                upstream_motif=upstream,
                downstream_motif=downstream,
                requirement=_requirement(
                    item.get("requirement", "required"),
                    f"{field}.requirement",
                ),
                minimum_distance=minimum,
                maximum_distance=maximum,
            )
        )
    return tuple(result)


def load_family_profile(path: Path) -> FamilyProfile:
    """Load and validate a generic family profile from YAML."""

    source = Path(path)
    try:
        document = yaml.safe_load(source.read_text(encoding="utf-8"))
    except OSError as exc:
        raise FamilyConfigurationError(
            f"cannot read family profile {path}: {exc}"
        ) from exc
    except yaml.YAMLError as exc:
        raise FamilyConfigurationError(f"invalid family YAML {path}: {exc}") from exc
    if not isinstance(document, dict):
        raise FamilyConfigurationError("family profile must contain a mapping")
    if document.get("schema_version") != 1:
        raise FamilyConfigurationError("family schema_version must be 1")
    family = _mapping(document.get("family"), "family")
    rules = _mapping(document.get("rules"), "rules")
    family_id = _identifier(family.get("id"), "family.id", family=True)
    name = _nonempty(family.get("name"), "family.name")
    version = _nonempty(family.get("version"), "family.version")
    domains = _domain_rules(rules)
    motifs = _motif_rules(rules)
    domain_ids = {rule.rule_id for rule in domains}
    for motif in motifs:
        if (
            motif.position is not None
            and motif.position.domain_rule_id is not None
            and motif.position.domain_rule_id not in domain_ids
        ):
            raise FamilyConfigurationError(
                f"motif {motif.rule_id!r} references unknown domain rule "
                f"{motif.position.domain_rule_id!r}"
            )
        if motif.pattern_type == "literal" and any(
            residue.motif_offset >= len(motif.pattern)
            for residue in motif.catalytic_residues
        ):
            raise FamilyConfigurationError(
                f"motif {motif.rule_id!r} has a catalytic residue outside its "
                "literal pattern"
            )
    relationships = _relationships(rules, {motif.rule_id for motif in motifs})
    return FamilyProfile(
        family_id=family_id,
        name=name,
        version=version,
        sequence_analysis=_sequence_analysis(document),
        domains=domains,
        motifs=motifs,
        motif_relationships=relationships,
        document=document,
        source_path=source,
    )
