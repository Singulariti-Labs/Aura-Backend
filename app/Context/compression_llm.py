from __future__ import annotations

import os
from typing import Any, Optional

from app.Context.models import CompressionSummary
from app.Prompts.compression import COMPRESSION_SYSTEM_PROMPT


class AnthropicCompressionService:
    """Dedicated, tool-free Anthropic client for context compression."""

    provider = "anthropic"

    def __init__(
        self,
        *,
        api_key: Optional[str] = None,
        client: Optional[Any] = None,
    ) -> None:
        self._api_key = api_key
        self._client = client

    def _get_client(self) -> Any:
        if self._client is not None:
            return self._client

        api_key = (
            self._api_key
            or os.getenv("COMPRESSION_ANTHROPIC_API_KEY")
            or os.getenv("ANTHROPIC_API_KEY")
        )
        if not api_key:
            raise ValueError(
                "COMPRESSION_ANTHROPIC_API_KEY or ANTHROPIC_API_KEY must be set "
                "to use context compression"
            )

        from anthropic import AsyncAnthropic

        self._client = AsyncAnthropic(api_key=api_key)
        return self._client

    async def summarize(
        self,
        compressor_input: str,
        *,
        model: str,
        max_output_tokens: int,
    ) -> CompressionSummary:
        """Make one standalone compression request with no agent tools."""

        response = await self._get_client().messages.create(
            model=model,
            system=COMPRESSION_SYSTEM_PROMPT,
            messages=[
                {
                    "role": "user",
                    "content": [{"type": "text", "text": compressor_input}],
                }
            ],
            max_tokens=max_output_tokens,
        )

        if str(getattr(response, "stop_reason", "") or "").lower() == "max_tokens":
            raise ValueError("Dedicated compressor reached its maximum output limit")

        text_parts: list[str] = []
        for block in getattr(response, "content", []) or []:
            block_type = (
                block.get("type")
                if isinstance(block, dict)
                else getattr(block, "type", None)
            )
            if block_type != "text":
                continue
            value = (
                block.get("text")
                if isinstance(block, dict)
                else getattr(block, "text", "")
            )
            if value:
                text_parts.append(str(value))

        usage = getattr(response, "usage", None)
        if isinstance(usage, dict):
            input_tokens = int(usage.get("input_tokens") or 0)
            output_tokens = int(usage.get("output_tokens") or 0)
        else:
            input_tokens = int(getattr(usage, "input_tokens", 0) or 0)
            output_tokens = int(getattr(usage, "output_tokens", 0) or 0)

        return CompressionSummary(
            summary="\n".join(text_parts).strip(),
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )


compression_llm_service = AnthropicCompressionService()
