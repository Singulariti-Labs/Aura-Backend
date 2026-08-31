"""Provider-neutral, single-request structured LLM extraction."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Optional

from langchain_core.messages import HumanMessage, SystemMessage

from app.GraphMemory.config import MemorySettings
from app.GraphMemory.errors import MemoryOutputValidationError, MemoryProviderError
from app.GraphMemory.schemas import ConsolidationExtraction, ConsolidationInput
from app.GraphMemory.security import redact_secrets
from app.LLM.llm_factory import LLMFactory
from app.Prompts.memory_consolidation import MEMORY_CONSOLIDATION_SYSTEM_PROMPT


@dataclass(frozen=True)
class MemoryLLMResult:
    extraction: ConsolidationExtraction
    usage: Optional[dict[str, int]] = None


class StructuredMemoryLLM:
    """Call one configured model with no agent loop or application tools."""

    def __init__(
        self,
        settings: MemorySettings,
        *,
        structured_llm: Optional[Any] = None,
    ) -> None:
        self.settings = settings
        self._structured_llm = structured_llm

    def _get_structured_llm(self) -> Any:
        """Create the provider client lazily and reuse its connection pool."""

        if self._structured_llm is None:
            llm = LLMFactory.create_llm(
                self.settings.llm_config,
                user_api_key=self.settings.api_key,
            )
            self._structured_llm = llm.with_structured_output(
                ConsolidationExtraction,
                include_raw=True,
            )
        return self._structured_llm

    async def extract(self, source: ConsolidationInput) -> MemoryLLMResult:
        """Submit the episode once and return its parsed structured output."""

        provider_payload = _build_provider_payload(source)
        episode_json = json.dumps(
            provider_payload,
            ensure_ascii=False,
            separators=(",", ":"),
        )
        messages = [
            SystemMessage(content=MEMORY_CONSOLIDATION_SYSTEM_PROMPT),
            HumanMessage(
                content=(
                    "Extract durable graph memory from this completed episode JSON:\n"
                    f"{episode_json}"
                )
            ),
        ]

        try:
            result = await self._get_structured_llm().ainvoke(messages)
        except Exception as exc:
            raise MemoryProviderError("memory provider request failed") from exc

        if not isinstance(result, dict):
            raise MemoryOutputValidationError(
                "structured provider response must be an object"
            )
        parsing_error = result.get("parsing_error")
        if parsing_error is not None:
            error = MemoryOutputValidationError(
                "the provider returned invalid structured output"
            )
            if isinstance(parsing_error, BaseException):
                raise error from parsing_error
            raise error

        parsed = result.get("parsed")
        try:
            extraction = (
                parsed
                if isinstance(parsed, ConsolidationExtraction)
                else ConsolidationExtraction.model_validate(parsed)
            )
        except Exception as exc:
            raise MemoryOutputValidationError(
                "the provider returned invalid structured output"
            ) from exc

        return MemoryLLMResult(
            extraction=extraction,
            usage=_extract_usage(result.get("raw")),
        )


def _build_provider_payload(source: ConsolidationInput) -> dict[str, Any]:
    """Serialize the contract while redacting secret-bearing free-text fields."""

    payload = source.model_dump(mode="json", by_alias=True)
    payload["episode"]["query"] = redact_secrets(payload["episode"]["query"])
    for observation in payload["observations"]:
        observation["content"] = redact_secrets(observation["content"])
        if observation.get("sourceId"):
            observation["sourceId"] = redact_secrets(observation["sourceId"])
    for fact in payload["existingFacts"]:
        fact["subject"] = redact_secrets(fact["subject"])
        fact["object"] = redact_secrets(fact["object"])
    return payload


def _extract_usage(raw_response: Any) -> Optional[dict[str, int]]:
    """Normalize token counts exposed by supported LangChain providers."""

    if raw_response is None:
        return None

    usage = getattr(raw_response, "usage_metadata", None)
    if not isinstance(usage, dict):
        metadata = getattr(raw_response, "response_metadata", None)
        if isinstance(metadata, dict):
            usage = metadata.get("token_usage") or metadata.get("usage")
    if not isinstance(usage, dict):
        return None

    input_tokens = usage.get("input_tokens", usage.get("prompt_tokens", 0))
    output_tokens = usage.get("output_tokens", usage.get("completion_tokens", 0))
    try:
        return {
            "input": max(0, int(input_tokens or 0)),
            "output": max(0, int(output_tokens or 0)),
        }
    except (TypeError, ValueError):
        return None
