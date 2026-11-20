from __future__ import annotations

import os
from typing import Any, Optional, Protocol

from app.Context.models import CompressionProvider, CompressionSummary
from app.Prompts.compression import COMPRESSION_SYSTEM_PROMPT


class CompressionService(Protocol):
    """Provider-neutral contract for the dedicated context compressor."""

    provider: CompressionProvider

    async def summarize(
        self,
        compressor_input: str,
        *,
        model: str,
        max_output_tokens: int,
    ) -> CompressionSummary: ...


class AnthropicCompressionService:
    """Dedicated, tool-free Anthropic client for context compression."""

    provider: CompressionProvider = "anthropic"

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


class OpenAICompressionService:
    """Dedicated, tool-free OpenAI Responses API context compressor."""

    provider: CompressionProvider = "openai"

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
            or os.getenv("COMPRESSION_OPENAI_API_KEY")
            or os.getenv("OPENAI_API_KEY")
        )
        if not api_key:
            raise ValueError(
                "COMPRESSION_OPENAI_API_KEY or OPENAI_API_KEY must be set "
                "to use OpenAI context compression"
            )

        from openai import AsyncOpenAI

        self._client = AsyncOpenAI(api_key=api_key)
        return self._client

    async def summarize(
        self,
        compressor_input: str,
        *,
        model: str,
        max_output_tokens: int,
    ) -> CompressionSummary:
        """Make one standalone Responses API request with no agent tools."""

        response = await self._get_client().responses.create(
            model=model,
            instructions=COMPRESSION_SYSTEM_PROMPT,
            input=compressor_input,
            max_output_tokens=max_output_tokens,
            store=False,
        )

        status = str(getattr(response, "status", "") or "").lower()
        incomplete_details = getattr(response, "incomplete_details", None)
        if isinstance(incomplete_details, dict):
            incomplete_reason = incomplete_details.get("reason")
        else:
            incomplete_reason = getattr(incomplete_details, "reason", None)
        if status == "incomplete" and incomplete_reason == "max_output_tokens":
            raise ValueError("Dedicated compressor reached its maximum output limit")

        text_parts: list[str] = []
        for item in getattr(response, "output", []) or []:
            item_type = (
                item.get("type") if isinstance(item, dict) else getattr(item, "type", None)
            )
            if item_type != "message":
                continue
            content = (
                item.get("content", [])
                if isinstance(item, dict)
                else getattr(item, "content", [])
            )
            for block in content or []:
                block_type = (
                    block.get("type")
                    if isinstance(block, dict)
                    else getattr(block, "type", None)
                )
                if block_type != "output_text":
                    continue
                value = (
                    block.get("text", "")
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


openai_compression_service = OpenAICompressionService()
anthropic_compression_service = AnthropicCompressionService()
COMPRESSION_LLM_SERVICES: dict[CompressionProvider, CompressionService] = {
    "openai": openai_compression_service,
    "anthropic": anthropic_compression_service,
}


def get_compression_service(provider: CompressionProvider) -> CompressionService:
    """Return the configured dedicated compressor implementation."""

    return COMPRESSION_LLM_SERVICES[provider]


# Backward-compatible alias. New callers should select by provider.
compression_llm_service = openai_compression_service
