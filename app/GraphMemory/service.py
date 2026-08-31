"""Application service for one graph-memory consolidation operation."""

from __future__ import annotations

from asyncio import AbstractEventLoop
from typing import Optional

from asyncpg import Pool

from app.GraphMemory.config import MemorySettings
from app.GraphMemory.llm import StructuredMemoryLLM
from app.GraphMemory.schemas import (
    ConsolidationApiRequest,
    ConsolidationApiResponse,
)
from app.GraphMemory.validator import validate_extraction
from app.RateLimit.rate_limit_service import schedule_token_usage_update
from app.RateLimit.token_pricing import calculate_token_cost_usd_float


class MemoryConsolidationService:
    """Coordinate extraction, validation, and non-blocking usage accounting."""

    def __init__(
        self,
        settings: MemorySettings,
        *,
        memory_llm: Optional[StructuredMemoryLLM] = None,
    ) -> None:
        self.settings = settings
        self.memory_llm = memory_llm or StructuredMemoryLLM(settings)

    async def consolidate(
        self,
        api_request: ConsolidationApiRequest,
        *,
        pool: Optional[Pool] = None,
        user_id: Optional[str] = None,
        event_loop: Optional[AbstractEventLoop] = None,
    ) -> ConsolidationApiResponse:
        """Make one LLM call and reject output that violates memory rules."""

        llm_result = await self.memory_llm.extract(api_request.request)
        validate_extraction(llm_result.extraction, api_request.request)

        usage_with_cost = _calculate_memory_usage_cost(
            usage=llm_result.usage,
            provider=self.settings.llm_config.provider,
            model_name=self.settings.llm_config.model_name,
        )

        # Consolidation does not perform a rate-limit check. Token accounting is
        # fire-and-forget, matching normal task LLM accounting. The shared
        # accounting worker updates both the current window and lifetime totals.
        schedule_token_usage_update(
            pool=pool,
            user_id=user_id,
            usage=usage_with_cost,
            details={
                "provider": self.settings.llm_config.provider,
                "model_name": self.settings.llm_config.model_name,
                "credential_source": "platform",
            },
            event_loop=event_loop,
        )
        return ConsolidationApiResponse(extraction=llm_result.extraction)


def _calculate_memory_usage_cost(
    *,
    usage: Optional[dict[str, int]],
    provider: str,
    model_name: str,
) -> Optional[dict[str, int | float]]:
    """Add total tokens and model-priced USD cost to provider token counts."""

    if usage is None:
        return None

    input_tokens = max(0, int(usage.get("input") or 0))
    output_tokens = max(0, int(usage.get("output") or 0))
    return {
        "input": input_tokens,
        "output": output_tokens,
        "total_tokens": input_tokens + output_tokens,
        "cost": calculate_token_cost_usd_float(
            provider=provider,
            model_name=model_name,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        ),
    }
