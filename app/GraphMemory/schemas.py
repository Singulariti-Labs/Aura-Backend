"""Validated request and response contracts for graph-memory consolidation."""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator


class MemorySchema(BaseModel):
    """Base model that rejects unknown fields at the public API boundary."""

    model_config = ConfigDict(
        extra="forbid",
        populate_by_name=True,
        str_strip_whitespace=True,
    )


class Episode(MemorySchema):
    id: str = Field(min_length=1, max_length=256)
    task_id: str = Field(alias="taskId", min_length=1, max_length=256)
    query: str = Field(min_length=1, max_length=100_000)
    status: Literal["completed"]
    started_at: AwareDatetime = Field(alias="startedAt")
    completed_at: Optional[AwareDatetime] = Field(default=None, alias="completedAt")

    @model_validator(mode="after")
    def validate_episode_identity_and_dates(self) -> "Episode":
        """Keep the memory episode tied to one completed frontend task."""

        if self.id != self.task_id:
            raise ValueError("episode.id must exactly match episode.taskId")
        if self.completed_at is not None and self.completed_at < self.started_at:
            raise ValueError("episode.completedAt cannot be earlier than startedAt")
        return self


class Observation(MemorySchema):
    id: str = Field(min_length=1, max_length=256)
    type: Literal[
        "user_request",
        "external_message",
        "document",
        "tool_result",
        "action",
        "tool_error",
    ]
    tool_name: Optional[str] = Field(default=None, alias="toolName", max_length=256)
    source_id: Optional[str] = Field(default=None, alias="sourceId", max_length=512)
    content: str = Field(min_length=1, max_length=500_000)
    status: Literal["success", "failed"]
    observed_at: AwareDatetime = Field(alias="observedAt")


class ExistingFact(MemorySchema):
    id: str = Field(min_length=1, max_length=256)
    subject_entity_id: str = Field(alias="subjectEntityId", min_length=1, max_length=512)
    subject: str = Field(min_length=1, max_length=1_000)
    predicate: str = Field(min_length=1, max_length=80)
    object_entity_id: Optional[str] = Field(
        default=None,
        alias="objectEntityId",
        max_length=512,
    )
    object_value: str = Field(alias="object", min_length=1, max_length=10_000)
    status: Literal["active", "superseded", "disputed", "retracted"]
    valid_from: Optional[AwareDatetime] = Field(default=None, alias="validFrom")
    valid_until: Optional[AwareDatetime] = Field(default=None, alias="validUntil")

    @model_validator(mode="after")
    def validate_validity_interval(self) -> "ExistingFact":
        if (
            self.valid_from is not None
            and self.valid_until is not None
            and self.valid_until < self.valid_from
        ):
            raise ValueError("validUntil cannot be earlier than validFrom")
        return self


class ConsolidationInput(MemorySchema):
    schema_version: Literal[1] = Field(alias="schemaVersion")
    episode: Episode
    observations: list[Observation]
    existing_facts: list[ExistingFact] = Field(alias="existingFacts")

    @model_validator(mode="after")
    def validate_unique_input_identifiers(self) -> "ConsolidationInput":
        """Reject ambiguous evidence and relationship target identifiers."""

        observation_ids = [item.id for item in self.observations]
        if len(observation_ids) != len(set(observation_ids)):
            raise ValueError("observation IDs must be unique")

        fact_ids = [item.id for item in self.existing_facts]
        if len(fact_ids) != len(set(fact_ids)):
            raise ValueError("existing fact IDs must be unique")
        return self


class ConsolidationApiRequest(MemorySchema):
    prompt_version: Literal["aura-memory-v1"]
    request: ConsolidationInput


class EntityAlias(MemorySchema):
    # Alias categories are intentionally open-ended so clients can introduce
    # domain-specific identifiers without requiring a server deployment.
    type: str = Field(min_length=1, max_length=80)
    value: str = Field(min_length=1, max_length=1_000)


class ExtractedEntity(MemorySchema):
    ref: str = Field(min_length=1, max_length=512)
    # Entity types such as product, event, asset, or any future category are
    # accepted. Only a non-empty bounded string is required.
    type: str = Field(min_length=1, max_length=80)
    canonical_name: str = Field(alias="canonicalName", min_length=1, max_length=1_000)
    aliases: list[EntityAlias] = Field(default_factory=list, max_length=30)


class FactRelation(MemorySchema):
    # Relationship vocabulary is owned by the graph client and may evolve.
    type: str = Field(min_length=1, max_length=80)
    target_fact_id: str = Field(alias="targetFactId", min_length=1, max_length=256)


class ExtractedFact(MemorySchema):
    subject_ref: str = Field(alias="subjectRef", min_length=1, max_length=512)
    predicate: str = Field(min_length=1, max_length=80)
    object_entity_ref: Optional[str] = Field(
        default=None,
        alias="objectEntityRef",
        max_length=512,
    )
    value: Optional[str] = Field(default=None, min_length=1, max_length=10_000)
    confidence: float = Field(ge=0, le=1)
    importance: float = Field(ge=0, le=1)
    valid_from: Optional[AwareDatetime] = Field(default=None, alias="validFrom")
    valid_until: Optional[AwareDatetime] = Field(default=None, alias="validUntil")
    source_observation_ids: list[str] = Field(
        alias="sourceObservationIds",
        min_length=1,
        max_length=20,
    )
    relation: Optional[FactRelation] = None

    @model_validator(mode="after")
    def validate_fact_object_and_dates(self) -> "ExtractedFact":
        """Require one unambiguous object and a valid temporal interval."""

        if (self.object_entity_ref is None) == (self.value is None):
            raise ValueError("a fact must provide exactly one of objectEntityRef or value")
        if (
            self.valid_from is not None
            and self.valid_until is not None
            and self.valid_until < self.valid_from
        ):
            raise ValueError("validUntil cannot be earlier than validFrom")
        return self


class ConsolidationExtraction(MemorySchema):
    episode_id: str = Field(alias="episodeId", min_length=1, max_length=256)
    summary: str = Field(max_length=4_000)
    entities: list[ExtractedEntity] = Field(default_factory=list, max_length=100)
    facts: list[ExtractedFact] = Field(default_factory=list, max_length=200)


class ConsolidationApiResponse(MemorySchema):
    extraction: ConsolidationExtraction


class ConsolidationError(MemorySchema):
    code: str
    message: str
    retryable: bool
    episode_id: str = Field(alias="episodeId")
    request_id: str = Field(alias="requestId")


class ConsolidationErrorResponse(MemorySchema):
    error: ConsolidationError
