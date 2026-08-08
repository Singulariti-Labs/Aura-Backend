"""Resolve per-task LLM settings received from the WebSocket client.

The WebSocket route should not contain provider/model validation or credential
precedence rules. This module keeps those rules in one testable place.
"""

from collections.abc import Mapping
from typing import Any, Optional
import logging

from app.Types.agent_types import (
    DEFAULT_MODELS,
    MODEL_NAMES_BY_PROVIDER,
    PROVIDER_MAPPING,
    LLMConfig,
)

logger = logging.getLogger(__name__)


def _canonical_provider(raw_provider: Any) -> Optional[str]:
    """Return the backend provider id for a UI/provider-settings value."""
    if not isinstance(raw_provider, str) or not raw_provider.strip():
        return None

    value = raw_provider.strip()
    normalized = value.lower().replace("-", "_").replace(" ", "_")
    normalized_aliases = {
        "open_ai": "openai",
        "openai": "openai",
        "anthropic": "anthropic",
        "open_router": "open_router",
        "openrouter": "open_router",
        "gemini": "google",
        "google": "google",
        "agent_router": "agent_router",
        "agentrouter": "agent_router",
    }
    return (
        PROVIDER_MAPPING.get(value)
        or PROVIDER_MAPPING.get(value.lower())
        or PROVIDER_MAPPING.get(normalized)
        or normalized_aliases.get(normalized)
    )


def _model_for_provider(provider: str, requested_model: Any) -> str:
    """Use the requested model when valid; otherwise use that provider's default."""
    allowed_models = MODEL_NAMES_BY_PROVIDER[provider]
    if isinstance(requested_model, str) and requested_model in allowed_models:
        return requested_model

    default_model = DEFAULT_MODELS[provider]
    if isinstance(requested_model, str) and requested_model:
        logger.warning(
            "Unsupported model '%s' for provider '%s'; using default model '%s'",
            requested_model,
            provider,
            default_model,
        )
    return default_model


def _reasoning_effort_or_none(value: Any) -> Optional[str]:
    """Keep only the normalized reasoning levels supported by the UI contract."""
    return value if value in {"low", "medium", "high"} else None


def _resolve_api_key(
    api_config: Mapping[str, Any],
    credential_source: str,
) -> Optional[str]:
    """Resolve a client-provided key for custom credentials.

    Platform credentials deliberately return ``None`` so the LLM factory uses
    the provider's server-side environment variable.
    """
    if credential_source != "custom":
        return None

    payload_key = api_config.get("api_key")
    if isinstance(payload_key, str) and payload_key.strip():
        return payload_key
    return None


def _build_config(
    *,
    provider_value: Any,
    model_value: Any,
    reasoning_effort_value: Any,
    api_key: Optional[str],
    credential_source: Optional[str],
) -> Optional[LLMConfig]:
    """Build one validated config, falling back to the provider default model."""
    provider = _canonical_provider(provider_value)
    if provider is None:
        return None

    model_name = _model_for_provider(provider, model_value)
    return LLMConfig(
        provider=provider,
        model_name=model_name,
        api_key=api_key,
        reasoning_effort=_reasoning_effort_or_none(reasoning_effort_value),
        credential_source=credential_source,
    )


def resolve_llm_config(
    *,
    api_config: Any,
    default_config: LLMConfig,
) -> tuple[LLMConfig, bool]:
    """Resolve the LLM configuration for one task request.

    An active per-request api_config takes precedence over the server default.
    A valid provider with a missing or invalid model always
    receives that provider's configured default model. The boolean return value
    indicates whether a non-default configuration was selected.
    """
    if isinstance(api_config, Mapping) and api_config.get("is_active", False):
        provider = _canonical_provider(api_config.get("provider"))
        if provider is not None:
            source = api_config.get("credential_source")
            if source not in {"platform", "custom"}:
                source = "custom" if api_config.get("api_key") else "platform"

            api_key = _resolve_api_key(api_config, source)
            if source == "custom" and api_key is None:
                logger.warning("Ignoring custom api_config without a valid api_key")
            else:
                config = _build_config(
                    provider_value=provider,
                    model_value=api_config.get("model_name"),
                    reasoning_effort_value=api_config.get("reasoning_effort"),
                    api_key=api_key,
                    credential_source=source,
                )
                if config is not None:
                    return config, True

        logger.warning("Ignoring invalid per-request api_config; using server default")

    return default_config, False
