import asyncio
import logging
import os
from functools import partial

from google import genai
from openai import AsyncOpenAI

from app.Prompts.audio_input import AUDIO_INPUT_PROMPT
from app.Prompts.audio_transcribe import AUDIO_TRANSCRIBE_PROMPT
from app.STT.realtime_types import RealtimeSTTMode

logger = logging.getLogger(__name__)


class TranscriptPolisher:
    """Text-only LLM polishing with raw transcript fallback on provider errors."""

    def __init__(self) -> None:
        self.provider = os.getenv("STT_POLISH_PROVIDER", "openai").lower()
        self.model = os.getenv("STT_POLISH_MODEL", "gpt-4o-mini")
        self.enabled = os.getenv("STT_POLISH_ENABLED", "true").lower() == "true"
        self.openai_client = self._create_openai_client()
        self.gemini_client = self._create_gemini_client()

    async def polish(self, transcript: str, mode: RealtimeSTTMode) -> tuple[str, bool, str | None]:
        """
        Polish final utterance text.

        Returns (text, polished, error). On missing config or model errors,
        returns the original transcript with polished=False and an error reason.
        """

        clean_transcript = transcript.strip()
        if not clean_transcript:
            return "", False, None

        if not self.enabled:
            return clean_transcript, False, None

        prompt = self._prompt_for_mode(mode)

        try:
            polished = await self._polish_with_provider(prompt, clean_transcript)
            polished = polished.strip()
            return (polished or clean_transcript), bool(polished), None
        except Exception as exc:
            logger.warning("Realtime STT polish failed; using raw transcript: %s", exc)
            return clean_transcript, False, str(exc) or exc.__class__.__name__

    async def _polish_with_provider(self, prompt: str, transcript: str) -> str:
        """Route polishing to the configured low-latency text model provider."""

        if self.provider == "openai":
            if not self.openai_client:
                raise ValueError("OPENAI_API_KEY environment variable is not set.")
            return await self._polish_with_openai(prompt, transcript)

        if self.provider in {"gemini", "google"}:
            if not self.gemini_client:
                raise ValueError("GOOGLE_API_KEY environment variable is not set.")
            return await self._polish_with_gemini(prompt, transcript)

        raise ValueError(f"Unsupported STT_POLISH_PROVIDER: {self.provider}")

    async def _polish_with_openai(self, prompt: str, transcript: str) -> str:
        """Use OpenAI for fast text-only transcript polishing."""

        response = await self.openai_client.chat.completions.create(
            model=self.model,
            temperature=0,
            messages=[
                {"role": "system", "content": prompt},
                {
                    "role": "user",
                    "content": f"<TRANSCRIPT>\n{transcript}\n</TRANSCRIPT>",
                },
            ],
        )
        return response.choices[0].message.content or ""

    async def _polish_with_gemini(self, prompt: str, transcript: str) -> str:
        """Use Gemini for transcript polishing when explicitly configured."""

        content = f"{prompt}\n\n<TRANSCRIPT>\n{transcript}\n</TRANSCRIPT>"
        call = partial(
            self.gemini_client.models.generate_content,
            model=self.model,
            contents=content,
        )
        response = await asyncio.to_thread(call)
        return response.text or ""

    def _create_openai_client(self) -> AsyncOpenAI | None:
        """Create OpenAI client only when the API key exists."""

        api_key = os.getenv("OPENAI_API_KEY")
        return AsyncOpenAI(api_key=api_key) if api_key else None

    def _create_gemini_client(self):
        """Create Gemini client only when the API key exists."""

        api_key = os.getenv("GOOGLE_API_KEY")
        return genai.Client(api_key=api_key) if api_key else None

    def _prompt_for_mode(self, mode: RealtimeSTTMode) -> str:
        """Select the existing prompt that matches the requested STT mode."""

        if mode == RealtimeSTTMode.AUDIO_TRANSCRIBE:
            return AUDIO_TRANSCRIBE_PROMPT
        return AUDIO_INPUT_PROMPT
