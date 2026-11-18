"""Environment-backed configuration for the onboarding voice agent."""

from dataclasses import dataclass
import os


def _env_bool(name: str, default: bool = False) -> bool:
    """Read an opt-in environment flag; only explicit true values enable it."""

    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().casefold() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class OnboardingVoiceSettings:
    openai_api_key: str
    elevenlabs_api_key: str
    llm_model: str = "gpt-5.4-mini"
    llm_max_output_tokens: int = 320
    memory_update_timeout_seconds: float = 15.0
    fact_extraction_enabled: bool = False
    voice_name: str = "Zara"
    voice_id: str | None = None
    tts_model: str = "eleven_flash_v2_5"
    output_format: str = "pcm_24000"
    sample_rate_hz: int = 24000
    channels: int = 1
    elevenlabs_api_base: str = "https://api.elevenlabs.io"
    elevenlabs_ws_base: str = "wss://api.elevenlabs.io"

    @classmethod
    def from_env(cls) -> "OnboardingVoiceSettings":
        openai_key = os.getenv("OPENAI_API_KEY", "").strip()
        elevenlabs_key = os.getenv("ELEVENLABS_API_KEY", "").strip()
        if not openai_key:
            raise RuntimeError("OPENAI_API_KEY is required for onboarding voice")
        if not elevenlabs_key:
            raise RuntimeError("ELEVENLABS_API_KEY is required for onboarding voice")

        return cls(
            openai_api_key=openai_key,
            elevenlabs_api_key=elevenlabs_key,
            llm_model=os.getenv("ONBOARDING_VOICE_LLM_MODEL", "gpt-5.4-mini"),
            llm_max_output_tokens=int(
                os.getenv("ONBOARDING_VOICE_MAX_OUTPUT_TOKENS", "320")
            ),
            memory_update_timeout_seconds=float(
                os.getenv("ONBOARDING_MEMORY_UPDATE_TIMEOUT_SECONDS", "15")
            ),
            fact_extraction_enabled=_env_bool(
                "ONBOARDING_FACT_EXTRACTION_ENABLED"
            ),
            voice_name=os.getenv("ELEVENLABS_VOICE_NAME", "Zara"),
            voice_id=os.getenv("ELEVENLABS_VOICE_ID") or None,
            tts_model=os.getenv("ELEVENLABS_TTS_MODEL", "eleven_flash_v2_5"),
            output_format=os.getenv("ELEVENLABS_OUTPUT_FORMAT", "pcm_24000"),
            elevenlabs_api_base=os.getenv(
                "ELEVENLABS_API_BASE", "https://api.elevenlabs.io"
            ).rstrip("/"),
            elevenlabs_ws_base=os.getenv(
                "ELEVENLABS_WS_BASE", "wss://api.elevenlabs.io"
            ).rstrip("/"),
        )
