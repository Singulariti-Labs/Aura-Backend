from typing import Optional

from pydantic import BaseModel, ConfigDict


MAX_OUTPUT_TOKEN_LIMIT_MESSAGE = (
    "Maximum output token limit hit. Please increase the max_tokens limit, "
    "or continue this session with retry."
)

DEFAULT_MAX_OUTPUT_TOKENS = 8192
DEFAULT_CONTEXT_WINDOW = 128000


class ModelContextProfile(BaseModel):
    """Single source of truth for a model's context-related capabilities."""

    model_config = ConfigDict(frozen=True)

    provider: str
    model: str
    context_window: int
    max_output_tokens: int
    supports_vision: bool = False
    supports_native_compaction: bool = False

OPEN_ROUTER_MODEL_ALIASES = {
    "z-ai": "z-ai/glm-4.5-air:free",
    "x-ai": "x-ai/grok-4.1-fast:free",
    "openai": "openai/gpt-oss-120b:free",
    "xiaomi": "xiaomi/mimo-v2-flash:free",
    "google": "google/gemini-2.0-flash-exp:free",
    "qwen": "qwen/qwen3-next-80b-a3b-instruct:free",
    "nvidia": "nvidia/nemotron-3-nano-30b-a3b:free",
    "upstage": "upstage/solar-pro-3:free",
}

# -----------------------------------------------------------------------------
# Easy-to-read Model Definitions by Provider:
# Format: "model_name": (context_window, max_output_tokens, supports_vision)
# -----------------------------------------------------------------------------

OPENAI_MODELS = {
    "gpt-3.5-turbo": (16385, 4096, False),
    "gpt-4": (8192, 4096, False),
    "gpt-4-turbo": (128000, 4096, True),
    "gpt-4o": (128000, 16384, True),
    "gpt-4o-mini": (128000, 16384, True),
    "gpt-4o-mini-high": (128000, 16384, True),
    "gpt-4.1": (1047576, 16384, True),
    "gpt-5": (400000, 8192, True),
    "gpt-5.4": (1050000, 8192, True),
    "gpt-5.4-mini": (400000, 8192, True),
    "gpt-5.5": (1050000, 8192, True),
    "gpt-5.6-terra": (1050000, 8192, True),
    "gpt-5.6-luna": (1050000, 8192, True),
}

ANTHROPIC_MODELS = {
    "claude-3-5-haiku-20241022": (200000, 8192, True),
    "claude-opus-4-7": (200000, 28000, True),
    "claude-opus-4-6": (200000, 28000, True),
    "claude-opus-4-8": (200000, 28000, True),
    "claude-sonnet-4-6": (200000, 28000, True),
    "claude-haiku-4-5-20251001": (200000, 8192, True),
    "claude-fable-5": (200000, 8192, True),
    "claude-sonnet-5": (200000, 8192, True),
    "claude-sonnet-4-5-20250929": (200000, 8192, True),
}

OPEN_ROUTER_MODELS = {
    "kimi-k2": (128000, 8192, True),
    "deepseek": (128000, 8192, True),
    "z-ai": (128000, 8192, True),
    "x-ai": (128000, 8192, True),
    "openai": (128000, 16384, True),
    "xiaomi": (128000, 8192, True),
    "google": (128000, 8192, True),
    "qwen": (128000, 8192, True),
    "nvidia": (128000, 8192, True),
    "upstage": (128000, 8192, True),
}

GOOGLE_MODELS = {
    "gemini-2.0-flash": (1000000, 8192, True),
    "gemini-2.5-flash": (1000000, 8192, True),
    "gemini-2.5-flash-lite": (1000000, 8192, True),
    "gemini-2.5-pro": (1000000, 16384, True),
    "gemini-3-pro": (1000000, 28000, True),
    "gemini-3-flash": (1000000, 28000, True),
    "gemini-3-flash-preview": (1000000, 28000, True),
    "gemini-flash-latest": (1000000, 8192, True),
    "gemini-3.1-pro-preview": (1000000, 8192, True),
    "gemini-3.1-flash-lite": (1000000, 8192, True),
}

AGENT_ROUTER_MODELS = {
    "claude-opus-4-5-20251101": (200000, 28000, True),
    "deepseek-r1-0528": (128000, 28000, False),
}

ALL_PROVIDER_MODELS = {
    "openai": OPENAI_MODELS,
    "anthropic": ANTHROPIC_MODELS,
    "open_router": OPEN_ROUTER_MODELS,
    "google": GOOGLE_MODELS,
    "agent_router": AGENT_ROUTER_MODELS,
}

# Automatically build MODEL_CONTEXT_PROFILES map
MODEL_CONTEXT_PROFILES = {}
for provider, models in ALL_PROVIDER_MODELS.items():
    for model_name, (context_window, max_output, vision) in models.items():
        key = f"{provider}:{model_name}"
        MODEL_CONTEXT_PROFILES[key] = ModelContextProfile(
            provider=provider,
            model=model_name,
            context_window=context_window,
            max_output_tokens=max_output,
            supports_vision=vision,
        )

# Backward-compatible view for callers/tests that still import the old map.
MODEL_MAX_OUTPUT_TOKENS = {
    key: profile.max_output_tokens
    for key, profile in MODEL_CONTEXT_PROFILES.items()
}


def get_model_context_profile(
    provider: Optional[str],
    model_name: Optional[str],
) -> ModelContextProfile:
    normalized_provider = (provider or "").lower()
    normalized_model = model_name or ""
    key = f"{normalized_provider}:{normalized_model}"
    profile = MODEL_CONTEXT_PROFILES.get(key)
    if profile is not None:
        return profile

    # LangChain exposes the resolved OpenRouter slug on the model object.
    if normalized_provider == "open_router":
        for alias, resolved in OPEN_ROUTER_MODEL_ALIASES.items():
            if normalized_model == resolved:
                return MODEL_CONTEXT_PROFILES[f"open_router:{alias}"]

    return ModelContextProfile(
        provider=normalized_provider or "unknown",
        model=normalized_model or "unknown",
        context_window=DEFAULT_CONTEXT_WINDOW,
        max_output_tokens=DEFAULT_MAX_OUTPUT_TOKENS,
    )


def get_model_max_output_tokens(
    provider: Optional[str],
    model_name: Optional[str],
) -> int:
    return get_model_context_profile(provider, model_name).max_output_tokens


def resolve_open_router_model(model_name: str) -> str:
    return OPEN_ROUTER_MODEL_ALIASES.get(model_name, model_name)
