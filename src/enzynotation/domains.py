"""Provider-neutral domain observations and family-rule evaluation."""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, replace

from enzynotation.family import DomainRule, FamilyProfile
from enzynotation.parsers.hmmer import HmmerDomainHit
from enzynotation.parsers.interpro import InterProHit
from enzynotation.tools.hmmer import HmmerConfig


@dataclass(frozen=True, slots=True)
class DomainCriterion:
    """One traceable provider-level domain filter decision."""

    criterion_id: str
    metric: str
    operator: str
    threshold: float
    observed_value: float
    passed: bool


@dataclass(frozen=True, slots=True)
class DomainObservation:
    """Normalized domain hit independent of its source tool format."""

    provider: str
    query_id: str
    query_length: int
    signature_accession: str
    signature_name: str
    database_name: str
    database_version: str
    start: int
    end: int
    query_coverage: float
    bit_score: float | None
    sequence_evalue: float | None
    domain_i_evalue: float | None
    description: str
    raw_line_number: int
    interpro_accession: str | None = None
    interpro_description: str | None = None
    member_database: str | None = None
    go_terms: tuple[str, ...] = ()
    pathways: tuple[str, ...] = ()
    occurrence: int = 0
    passed: bool = True
    filter_reasons: tuple[str, ...] = ()
    criteria: tuple[DomainCriterion, ...] = ()


@dataclass(frozen=True, slots=True)
class DomainRuleEvaluation:
    """Evaluation of one family domain rule for one query."""

    query_id: str
    rule: DomainRule
    observed_count: int
    count_satisfied: bool
    architecture_order_satisfied: bool | None

    @property
    def satisfied(self) -> bool:
        """Whether count and any configured architecture order are satisfied."""

        return self.count_satisfied and self.architecture_order_satisfied is not False


def _signature_key(value: str) -> str:
    return re.sub(r"\.\d+$", "", value).upper()


def hmmer_observations(
    hits: tuple[HmmerDomainHit, ...], config: HmmerConfig
) -> tuple[DomainObservation, ...]:
    """Normalize HMMER hits and apply configured operational filters."""

    observations: list[DomainObservation] = []
    for hit in hits:
        criteria = (
            DomainCriterion(
                "maximum_sequence_evalue",
                "sequence_evalue",
                "lte",
                config.maximum_sequence_evalue,
                hit.sequence_evalue,
                hit.sequence_evalue <= config.maximum_sequence_evalue,
            ),
            DomainCriterion(
                "maximum_domain_i_evalue",
                "domain_i_evalue",
                "lte",
                config.maximum_domain_i_evalue,
                hit.independent_evalue,
                hit.independent_evalue <= config.maximum_domain_i_evalue,
            ),
            DomainCriterion(
                "minimum_bit_score",
                "bit_score",
                "gte",
                config.minimum_bit_score,
                hit.bit_score,
                hit.bit_score >= config.minimum_bit_score,
            ),
            DomainCriterion(
                "minimum_query_coverage",
                "query_coverage",
                "gte",
                config.minimum_query_coverage,
                hit.query_coverage,
                hit.query_coverage >= config.minimum_query_coverage,
            ),
        )
        reasons = tuple(item.criterion_id for item in criteria if not item.passed)
        observations.append(
            DomainObservation(
                provider="hmmer",
                query_id=hit.query_id,
                query_length=hit.query_length,
                signature_accession=hit.profile_accession,
                signature_name=hit.profile_name,
                database_name=config.database_name,
                database_version=config.database_version,
                start=hit.query_start,
                end=hit.query_end,
                query_coverage=hit.query_coverage,
                bit_score=hit.bit_score,
                sequence_evalue=hit.sequence_evalue,
                domain_i_evalue=hit.independent_evalue,
                description=hit.description,
                raw_line_number=hit.raw_line_number,
                member_database=config.database_kind,
                passed=not reasons,
                filter_reasons=reasons,
                criteria=criteria,
            )
        )
    return assign_occurrences(tuple(observations))


def interpro_observations(
    hits: tuple[InterProHit, ...], *, database_name: str, database_version: str
) -> tuple[DomainObservation, ...]:
    """Normalize InterProScan signature hits without functional inference."""

    observations = tuple(
        DomainObservation(
            provider="interproscan",
            query_id=hit.query_id,
            query_length=hit.sequence_length,
            signature_accession=hit.member_signature,
            signature_name=hit.member_signature,
            database_name=database_name,
            database_version=database_version,
            start=hit.start,
            end=hit.end,
            query_coverage=hit.query_coverage,
            bit_score=hit.score,
            sequence_evalue=None,
            domain_i_evalue=None,
            description=hit.signature_description,
            raw_line_number=hit.raw_line_number,
            interpro_accession=hit.interpro_accession,
            interpro_description=hit.interpro_description,
            member_database=hit.member_database,
            go_terms=hit.go_terms,
            pathways=hit.pathways,
        )
        for hit in hits
    )
    return assign_occurrences(observations)


def assign_occurrences(
    observations: tuple[DomainObservation, ...],
) -> tuple[DomainObservation, ...]:
    """Assign deterministic occurrence indices to repeated domain signatures."""

    counters: dict[tuple[str, str, str], int] = defaultdict(int)
    result: list[DomainObservation] = []
    for observation in sorted(
        observations,
        key=lambda item: (
            item.query_id,
            item.start,
            item.end,
            item.provider,
            item.signature_accession,
            item.raw_line_number,
        ),
    ):
        key = (
            observation.query_id,
            observation.provider,
            _signature_key(observation.signature_accession),
        )
        counters[key] += 1
        result.append(replace(observation, occurrence=counters[key]))
    return tuple(result)


def matching_domain_rules(
    observation: DomainObservation, profile: FamilyProfile
) -> tuple[DomainRule, ...]:
    """Return family rules matching a normalized domain signature."""

    keys = {_signature_key(observation.signature_accession)}
    if observation.interpro_accession:
        keys.add(_signature_key(observation.interpro_accession))
    matches: list[DomainRule] = []
    for rule in profile.domains:
        source_matches = (
            observation.provider == "hmmer" and rule.source in {"hmmer", "pfam"}
        ) or (observation.provider == "interproscan" and rule.source == "interproscan")
        if source_matches and _signature_key(rule.signature_id) in keys:
            matches.append(rule)
    return tuple(matches)


def _passes_rule_thresholds(observation: DomainObservation, rule: DomainRule) -> bool:
    if not observation.passed:
        return False
    if observation.query_coverage < rule.minimum_query_coverage:
        return False
    if rule.maximum_evalue is None:
        return True
    evalue = observation.domain_i_evalue
    return evalue is not None and evalue <= rule.maximum_evalue


def evaluate_domain_rules(
    *,
    profile: FamilyProfile,
    query_ids: tuple[str, ...],
    observations: tuple[DomainObservation, ...],
) -> tuple[DomainRuleEvaluation, ...]:
    """Evaluate configured counts and architecture order for every query."""

    evaluations: list[DomainRuleEvaluation] = []
    for query_id in query_ids:
        query_observations = tuple(
            item for item in observations if item.query_id == query_id
        )
        first_positions: dict[str, int] = {}
        counts: dict[str, int] = {}
        for rule in profile.domains:
            matching = [
                item
                for item in query_observations
                if rule in matching_domain_rules(item, profile)
                and _passes_rule_thresholds(item, rule)
            ]
            counts[rule.rule_id] = len(matching)
            if matching:
                first_positions[rule.rule_id] = min(item.start for item in matching)

        ordered_rules = sorted(
            (
                rule
                for rule in profile.domains
                if rule.architecture_order is not None
                and rule.rule_id in first_positions
            ),
            key=lambda rule: rule.architecture_order or 0,
        )
        observed_positions = [first_positions[rule.rule_id] for rule in ordered_rules]
        global_order_ok = observed_positions == sorted(observed_positions)
        for rule in profile.domains:
            count = counts[rule.rule_id]
            if rule.requirement == "forbidden":
                count_satisfied = count == 0
            else:
                count_satisfied = count >= rule.min_count and (
                    rule.max_count is None or count <= rule.max_count
                )
            order_satisfied = (
                global_order_ok
                if rule.architecture_order is not None
                and rule.rule_id in first_positions
                else None
            )
            evaluations.append(
                DomainRuleEvaluation(
                    query_id=query_id,
                    rule=rule,
                    observed_count=count,
                    count_satisfied=count_satisfied,
                    architecture_order_satisfied=order_satisfied,
                )
            )
    return tuple(evaluations)
