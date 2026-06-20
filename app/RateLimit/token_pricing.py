from decimal import Decimal, ROUND_HALF_UP
from typing import Optional, Tuple

TOKENS_PER_MILLION = Decimal("1000000")
USD_QUANTIZE = Decimal("0.000001")

# Pricing per 1M tokens: (input price USD, output price USD).
# Keep this table as the single source of truth for model cost estimates.
MODEL_PRICING_PER_1M_TOKENS: dict[str, Tuple[Decimal, Decimal]] = {
    "anthropic:claude-fable-5": (Decimal("10.00"), Decimal("50.00")),
    "anthropic:claude-opus-4-8": (Decimal("5.00"), Decimal("25.00")),
    "anthropic:claude-opus-4-7": (Decimal("5.00"), Decimal("25.00")),
    "anthropic:claude-opus-4-6": (Decimal("5.00"), Decimal("25.00")),
    "anthropic:claude-opus-4-5-20251101": (Decimal("5.00"), Decimal("25.00")),
    "anthropic:claude-sonnet-4-6": (Decimal("3.00"), Decimal("15.00")),
    "anthropic:claude-haiku-4-5-20251001": (Decimal("1.00"), Decimal("5.00")),
    "openai:gpt-5.5": (Decimal("5.00"), Decimal("30.00")),
    "openai:gpt-5.4": (Decimal("2.50"), Decimal("15.00")),
    "openai:gpt-5.4-mini": (Decimal("0.75"), Decimal("4.50")),
    "openai:gpt-5.4-nano": (Decimal("0.20"), Decimal("1.25")),
    "openai:gpt-4o": (Decimal("2.50"), Decimal("10.00")),
    "openai:gpt-4o-mini": (Decimal("0.15"), Decimal("0.60")),
    "openai:gpt-4.1": (Decimal("2.00"), Decimal("8.00")),
    "openai:gpt-4.1-mini": (Decimal("0.40"), Decimal("1.60")),
    "openai:gpt-4.1-nano": (Decimal("0.10"), Decimal("0.40")),
    "openai:o3": (Decimal("2.00"), Decimal("8.00")),
    "openai:o4-mini": (Decimal("1.10"), Decimal("4.40")),
    "google:gemini-3.1-pro-preview": (Decimal("2.00"), Decimal("12.00")),
    "google:gemini-3.1-flash-lite-preview": (Decimal("0.25"), Decimal("1.50")),
    "google:gemini-3-flash-preview": (Decimal("0.50"), Decimal("3.00")),
    "google:gemini-2.5-pro": (Decimal("1.25"), Decimal("10.00")),
    "google:gemini-2.5-flash": (Decimal("0.30"), Decimal("2.50")),
    "google:gemini-2.5-flash-lite": (Decimal("0.10"), Decimal("0.40")),
    "gemini:gemini-3.1-pro-preview": (Decimal("2.00"), Decimal("12.00")),
    "gemini:gemini-3.1-flash-lite-preview": (Decimal("0.25"), Decimal("1.50")),
    "gemini:gemini-3-flash-preview": (Decimal("0.50"), Decimal("3.00")),
    "gemini:gemini-2.5-pro": (Decimal("1.25"), Decimal("10.00")),
    "gemini:gemini-2.5-flash": (Decimal("0.30"), Decimal("2.50")),
    "gemini:gemini-2.5-flash-lite": (Decimal("0.10"), Decimal("0.40")),
}

# Preserve the previous handler fallback for unknown provider/model pairs.
DEFAULT_UNKNOWN_MODEL_PRICING_PER_1M_TOKENS = (Decimal("5.00"), Decimal("15.00"))


def calculate_token_cost_usd(
    *,
    provider: Optional[str],
    model_name: Optional[str],
    input_tokens: int,
    output_tokens: int,
) -> Decimal:
    """
    Calculates USD cost from token usage using per-million-token pricing.

    Unknown provider/model pairs intentionally use the historical fallback
    pricing so usage metadata remains non-zero when token counts are present.
    """
    input_price, output_price = get_model_pricing_per_1m(
        provider=provider,
        model_name=model_name,
    )
    cost = (
        Decimal(input_tokens) * input_price
        + Decimal(output_tokens) * output_price
    ) / TOKENS_PER_MILLION
    return cost.quantize(USD_QUANTIZE, rounding=ROUND_HALF_UP)


def calculate_token_cost_usd_float(
    *,
    provider: Optional[str],
    model_name: Optional[str],
    input_tokens: int,
    output_tokens: int,
) -> float:
    """
    Calculates token cost as a float for existing usage metadata consumers.

    The underlying calculation uses Decimal; this wrapper preserves the current
    handler contract where usage['cost'] is formatted and stored as a float.
    """
    return float(
        calculate_token_cost_usd(
            provider=provider,
            model_name=model_name,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )
    )


def get_model_pricing_per_1m(
    *,
    provider: Optional[str],
    model_name: Optional[str],
) -> Tuple[Decimal, Decimal]:
    """
    Returns pricing for a provider/model pair, falling back for unknown models.
    """
    return MODEL_PRICING_PER_1M_TOKENS.get(
        _pricing_key(provider=provider, model_name=model_name),
        DEFAULT_UNKNOWN_MODEL_PRICING_PER_1M_TOKENS,
    )


def _pricing_key(*, provider: Optional[str], model_name: Optional[str]) -> str:
    """
    Normalizes provider/model into the pricing-table lookup key.
    """
    provider_key = (provider or "unknown").strip().lower()
    model_key = (model_name or "unknown").strip().lower()
    return f"{provider_key}:{model_key}"
