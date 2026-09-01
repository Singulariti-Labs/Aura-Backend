"""Cross-reference and security validation for structured LLM output."""

from __future__ import annotations

from collections.abc import Iterable

from app.GraphMemory.errors import MemoryOutputValidationError
from app.GraphMemory.schemas import ConsolidationExtraction, ConsolidationInput
from app.GraphMemory.security import contains_secret_like_content


def validate_extraction(
    extraction: ConsolidationExtraction,
    source: ConsolidationInput,
) -> None:
    """Validate evidence, identity, relationships, and secret-safe output.

    Pydantic validates local field constraints. This function handles rules
    involving multiple arrays or comparisons with the original request.
    """

    if extraction.episode_id != source.episode.id:
        raise MemoryOutputValidationError(
            "the extraction episodeId does not match the request episode"
        )

    entities_by_ref = {}
    for entity in extraction.entities:
        if entity.ref in entities_by_ref:
            raise MemoryOutputValidationError("entity refs must be unique")
        entities_by_ref[entity.ref] = entity

        seen_aliases: set[tuple[str, str]] = set()
        for alias in entity.aliases:
            alias_key = (alias.type, alias.value.casefold())
            if alias_key in seen_aliases:
                raise MemoryOutputValidationError("entity aliases must be unique")
            seen_aliases.add(alias_key)

    observation_ids = {observation.id for observation in source.observations}
    existing_facts = {fact.id: fact for fact in source.existing_facts}

    for fact in extraction.facts:
        if fact.subject_ref not in entities_by_ref:
            raise MemoryOutputValidationError(
                f"unknown fact subjectRef: {fact.subject_ref}"
            )
        if (
            fact.object_entity_ref is not None
            and fact.object_entity_ref not in entities_by_ref
        ):
            raise MemoryOutputValidationError(
                f"unknown fact objectEntityRef: {fact.object_entity_ref}"
            )

        evidence_ids = fact.source_observation_ids
        if len(evidence_ids) != len(set(evidence_ids)):
            raise MemoryOutputValidationError(
                "sourceObservationIds must not contain duplicates"
            )
        if not set(evidence_ids).issubset(observation_ids):
            raise MemoryOutputValidationError(
                "a fact cites an observation that was not supplied"
            )

        if fact.relation is not None:
            target = existing_facts.get(fact.relation.target_fact_id)
            if target is None:
                raise MemoryOutputValidationError(
                    "a relationship targets a fact that was not supplied"
                )

    _reject_secret_like_output(_iter_output_strings(extraction))


def _iter_output_strings(extraction: ConsolidationExtraction) -> Iterable[str]:
    yield extraction.summary
    for entity in extraction.entities:
        yield entity.canonical_name
        yield entity.ref
        for alias in entity.aliases:
            yield alias.value
    for fact in extraction.facts:
        yield fact.subject_ref
        yield fact.predicate
        if fact.object_entity_ref is not None:
            yield fact.object_entity_ref
        if fact.value is not None:
            yield fact.value


def _reject_secret_like_output(values: Iterable[str]) -> None:
    if contains_secret_like_content(values):
        raise MemoryOutputValidationError(
            "the extraction contains credential-like content"
        )
