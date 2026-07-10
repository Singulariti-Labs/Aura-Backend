from typing import Optional


MAX_OUTPUT_TOKEN_LIMIT_MESSAGE = (
    "Maximum output token limit hit. Please increase the max_tokens limit, "
    "or continue this session with retry."
)

DEFAULT_MAX_OUTPUT_TOKENS = 8192

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

MODEL_MAX_OUTPUT_TOKENS = {
    "openai:gpt-3.5-turbo": 4096,
    "openai:gpt-4": 4096,
    "openai:gpt-4-turbo": 4096,
    "openai:gpt-4o": 16384,
    "openai:gpt-4o-mini": 16384,
    "openai:gpt-4o-mini-high": 16384,
    "openai:gpt-4.1": 16384,
    "anthropic:claude-opus-4-7": 28000,
    "anthropic:claude-opus-4-6": 28000,
    "anthropic:claude-opus-4-8": 28000,
    "anthropic:claude-sonnet-4-6": 28000,
    "anthropic:claude-haiku-4-5-20251001": 8192,
    "open_router:kimi-k2": 8192,
    "open_router:deepseek": 8192,
    "open_router:z-ai": 8192,
    "open_router:x-ai": 8192,
    "open_router:openai": 16384,
    "open_router:xiaomi": 8192,
    "open_router:google": 8192,
    "open_router:qwen": 8192,
    "open_router:nvidia": 8192,
    "open_router:upstage": 8192,
    "google:gemini-2.0-flash": 8192,
    "google:gemini-2.5-flash": 8192,
    "google:gemini-2.5-flash-lite": 8192,
    "google:gemini-2.5-pro": 16384,
    "google:gemini-3-pro": 28000,
    "google:gemini-3-flash": 28000,
    "google:gemini-3-flash-preview": 28000,
    "agent_router:claude-opus-4-5-20251101": 28000,
    "agent_router:deepseek-r1-0528": 28000,
}


def get_model_max_output_tokens(
    provider: Optional[str],
    model_name: Optional[str],
) -> int:
    key = f"{(provider or '').lower()}:{model_name or ''}"
    return MODEL_MAX_OUTPUT_TOKENS.get(key, DEFAULT_MAX_OUTPUT_TOKENS)


def resolve_open_router_model(model_name: str) -> str:
    return OPEN_ROUTER_MODEL_ALIASES.get(model_name, model_name)
