"""Native provider calls dedicated to graph-memory consolidation.

This module deliberately does not use LangChain or Aura's main agent loop.
Memory consolidation is one isolated structured request routed directly to the
configured provider SDK.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import json
import os
from typing import Any, Optional

from anthropic import AsyncAnthropic
from google import genai

from app.GraphMemory.config import MemorySettings
from app.GraphMemory.errors import MemoryOutputValidationError
from app.LLM.model_token_limits import get_model_max_output_tokens


@dataclass(frozen=True)
class NativeMemoryResponse:
    """Provider-neutral payload and token counts from one native LLM call."""

    payload: Any
    usage: Optional[dict[str, int]]


class MemoryProviderInvoker:
    """Invoke Anthropic or Gemini without entering the main Aura agent loop."""

    def __init__(
        self,
        settings: MemorySettings,
        *,
        anthropic_client: Optional[Any] = None,
        gemini_client: Optional[Any] = None,
    ) -> None:
        self.settings = settings
        self._anthropic_client = anthropic_client
        self._gemini_client = gemini_client

    async def invoke(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        output_schema: dict[str, Any],
    ) -> NativeMemoryResponse:
        """Route exactly one structured request to the configured native SDK."""

        provider = self.settings.llm_config.provider
        provider_schema = _build_provider_json_schema(
            output_schema,
            provider=provider,
        )
        if provider == "anthropic":
            return await self._invoke_anthropic(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                output_schema=provider_schema,
            )
        if provider == "google":
            return await self._invoke_gemini(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                output_schema=provider_schema,
            )
        raise ValueError(f"Unsupported memory LLM provider: {provider!r}")

    async def _invoke_anthropic(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        output_schema: dict[str, Any],
    ) -> NativeMemoryResponse:
        """Call Anthropic Messages with its native JSON Schema output mode."""

        client = self._anthropic_client
        if client is None:
            client = AsyncAnthropic(api_key=self._provider_api_key("ANTHROPIC_API_KEY"))
            self._anthropic_client = client

        response = await client.messages.create(
            model=self.settings.llm_config.model_name,
            max_tokens=self._max_output_tokens(),
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
            output_config={
                "format": {
                    "type": "json_schema",
                    "schema": output_schema,
                }
            },
        )
        text = "".join(
            str(getattr(block, "text", ""))
            for block in (getattr(response, "content", None) or [])
            if getattr(block, "type", None) == "text"
        )
        payload = _parse_json_object(text)
        usage = getattr(response, "usage", None)
        return NativeMemoryResponse(
            payload=payload,
            usage=_normalize_usage(
                getattr(usage, "input_tokens", 0),
                getattr(usage, "output_tokens", 0),
            ),
        )

    async def _invoke_gemini(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        output_schema: dict[str, Any],
    ) -> NativeMemoryResponse:
        """Call Gemini generateContent with its native JSON response schema."""

        client = self._gemini_client
        if client is None:
            client = genai.Client(api_key=self._provider_api_key("GOOGLE_API_KEY")).aio
            self._gemini_client = client

        response = await client.models.generate_content(
            model=self.settings.llm_config.model_name,
            contents=user_prompt,
            config={
                "system_instruction": system_prompt,
                "max_output_tokens": self._max_output_tokens(),
                "response_mime_type": "application/json",
                "response_json_schema": output_schema,
            },
        )
        payload = _parse_json_object(str(getattr(response, "text", "") or ""))
        usage = getattr(response, "usage_metadata", None)
        return NativeMemoryResponse(
            payload=payload,
            usage=_normalize_usage(
                getattr(usage, "prompt_token_count", 0),
                getattr(usage, "candidates_token_count", 0),
            ),
        )

    def _provider_api_key(self, provider_environment_name: str) -> Optional[str]:
        """Prefer the memory-specific credential, then the provider credential."""

        return self.settings.api_key or os.getenv(provider_environment_name) or None

    def _max_output_tokens(self) -> int:
        """Use the application's trusted model output limit table."""

        return get_model_max_output_tokens(
            self.settings.llm_config.provider,
            self.settings.llm_config.model_name,
        )


def _parse_json_object(text: str) -> dict[str, Any]:
    """Decode one provider JSON document and require a top-level object."""

    try:
        payload = json.loads(text)
    except (json.JSONDecodeError, TypeError) as exc:
        raise MemoryOutputValidationError(
            "memory provider response is not valid JSON"
        ) from exc
    if not isinstance(payload, dict):
        raise MemoryOutputValidationError(
            "memory provider response must be a JSON object"
        )
    return payload


def _normalize_usage(input_tokens: Any, output_tokens: Any) -> dict[str, int]:
    """Normalize native provider token counters to Aura's accounting shape."""

    return {
        "input": max(0, int(input_tokens or 0)),
        "output": max(0, int(output_tokens or 0)),
    }


def _build_provider_json_schema(
    schema: dict[str, Any],
    *,
    provider: str,
) -> dict[str, Any]:
    """Create a strict schema using the selected provider's supported subset.

    Native structured-output APIs require every object property to be listed in
    ``required``. Nullable Pydantic fields remain nullable through ``anyOf``.
    Anthropic does not accept collection-size keywords, so those limits remain
    in the system prompt and are authoritatively enforced by Pydantic after the
    response is decoded. Gemini accepts and retains them in its request schema.
    """

    supported_keywords = {
        "$defs",
        "$id",
        "$ref",
        "$anchor",
        "type",
        "format",
        "description",
        "enum",
        "items",
        "prefixItems",
        "minimum",
        "maximum",
        "anyOf",
        "oneOf",
        "properties",
        "additionalProperties",
        "required",
    }
    if provider == "google":
        supported_keywords.update({"minItems", "maxItems"})

    def normalize(node: Any) -> Any:
        if isinstance(node, list):
            return [normalize(item) for item in node]
        if not isinstance(node, dict):
            return node

        normalized: dict[str, Any] = {}
        for key, value in node.items():
            if key not in supported_keywords:
                continue
            if key in {"properties", "$defs"} and isinstance(value, dict):
                normalized[key] = {
                    name: normalize(child_schema)
                    for name, child_schema in value.items()
                }
            else:
                normalized[key] = normalize(value)
        properties = normalized.get("properties")
        if isinstance(properties, dict):
            normalized["required"] = list(properties)
        return normalized

    return normalize(deepcopy(schema))
