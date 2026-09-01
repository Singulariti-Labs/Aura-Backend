"""Provider-neutral, single-request structured LLM extraction."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Optional

from app.GraphMemory.config import MemorySettings
from app.GraphMemory.errors import MemoryOutputValidationError, MemoryProviderError
from app.GraphMemory.provider_invoker import MemoryProviderInvoker
from app.GraphMemory.schemas import ConsolidationExtraction, ConsolidationInput
from app.GraphMemory.security import redact_secrets
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
        provider_invoker: Optional[MemoryProviderInvoker] = None,
    ) -> None:
        self.settings = settings
        self._provider_invoker = provider_invoker or MemoryProviderInvoker(settings)

    async def extract(self, source: ConsolidationInput) -> MemoryLLMResult:
        """Submit the episode once and return its parsed structured output."""

        provider_payload = _build_provider_payload(source)
        episode_json = json.dumps(
            provider_payload,
            ensure_ascii=False,
            separators=(",", ":"),
        )
        user_prompt = (
            "Extract durable graph memory from this completed episode JSON:\n"
            f"{episode_json}"
        )

        try:
            result = await self._provider_invoker.invoke(
                system_prompt=MEMORY_CONSOLIDATION_SYSTEM_PROMPT,
                user_prompt=user_prompt,
                output_schema=ConsolidationExtraction.model_json_schema(by_alias=True),
            )
        except MemoryOutputValidationError:
            raise
        except Exception as exc:
            raise MemoryProviderError("memory provider request failed") from exc

        try:
            extraction = ConsolidationExtraction.model_validate(result.payload)
        except Exception as exc:
            raise MemoryOutputValidationError(
                "the provider returned invalid structured output"
            ) from exc

        return MemoryLLMResult(
            extraction=extraction,
            usage=result.usage,
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
