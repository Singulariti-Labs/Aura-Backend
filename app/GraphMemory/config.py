"""Environment-backed settings for the independent memory LLM request."""

from __future__ import annotations

from dataclasses import dataclass
import os
from typing import Optional

from app.Types.agent_types import DEFAULT_MODELS, LLMConfig


DEFAULT_MEMORY_PROVIDER = "anthropic"
DEFAULT_MEMORY_MODEL = "claude-haiku-4-5-20251001"
DEFAULT_MAX_BODY_BYTES = 1_048_576
MAX_ALLOWED_BODY_BYTES = 10_485_760
MEMORY_REQUEST_TIMEOUT_SECONDS = 30.0


class MemoryConfigurationError(RuntimeError):
    """Raised when memory-consolidation settings are unsafe or invalid."""


@dataclass(frozen=True)
class MemorySettings:
    """Immutable settings shared safely by concurrent consolidation requests."""

    llm_config: LLMConfig
    api_key: Optional[str]
    max_body_bytes: int = DEFAULT_MAX_BODY_BYTES
    timeout_seconds: float = MEMORY_REQUEST_TIMEOUT_SECONDS

    @classmethod
    def from_env(cls) -> "MemorySettings":
        """Load and validate provider settings without exposing credentials."""

        provider = _normalize_provider(
            os.getenv("MEMORY_LLM_PROVIDER", DEFAULT_MEMORY_PROVIDER)
        )
        default_model = (
            DEFAULT_MEMORY_MODEL
            if provider == DEFAULT_MEMORY_PROVIDER
            else DEFAULT_MODELS.get(provider)
        )
        if default_model is None:
            raise MemoryConfigurationError(
                f"Unsupported MEMORY_LLM_PROVIDER: {provider!r}"
            )

        model = os.getenv("MEMORY_LLM_MODEL", default_model).strip()
        try:
            llm_config = LLMConfig(provider=provider, model_name=model)
        except Exception as exc:
            raise MemoryConfigurationError(
                "MEMORY_LLM_PROVIDER and MEMORY_LLM_MODEL are not a valid pair"
            ) from exc

        max_body_bytes = _read_max_body_bytes()
        api_key = os.getenv("MEMORY_LLM_API_KEY") or None
        return cls(
            llm_config=llm_config,
            api_key=api_key,
            max_body_bytes=max_body_bytes,
        )


def _normalize_provider(value: str) -> str:
    normalized = value.strip().lower().replace("-", "_").replace(" ", "_")
    aliases = {
        "gemini": "google",
        "openrouter": "open_router",
        "agentrouter": "agent_router",
    }
    return aliases.get(normalized, normalized)


def _read_max_body_bytes() -> int:
    raw_value = os.getenv("MEMORY_MAX_BODY_BYTES", str(DEFAULT_MAX_BODY_BYTES))
    try:
        value = int(raw_value)
    except ValueError as exc:
        raise MemoryConfigurationError("MEMORY_MAX_BODY_BYTES must be an integer") from exc

    if value < 1 or value > MAX_ALLOWED_BODY_BYTES:
        raise MemoryConfigurationError(
            f"MEMORY_MAX_BODY_BYTES must be between 1 and {MAX_ALLOWED_BODY_BYTES}"
        )
    return value
