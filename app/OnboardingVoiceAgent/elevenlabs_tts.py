"""ElevenLabs voice resolution and low-latency WebSocket TTS streaming."""

from dataclasses import dataclass
from typing import Awaitable, Callable, Optional
from urllib.parse import urlencode
import asyncio
import json

import aiohttp
import websockets

from app.OnboardingVoiceAgent.config import OnboardingVoiceSettings


AudioCallback = Callable[[str], Awaitable[None]]
_VOICE_CACHE: dict[tuple[str, str], "VoiceSelection"] = {}


@dataclass(frozen=True, slots=True)
class VoiceSelection:
    voice_id: str
    name: str


def select_voice(voices: list[dict], preferred_name: str) -> Optional[VoiceSelection]:
    usable = [
        voice
        for voice in voices
        if isinstance(voice, dict) and voice.get("voice_id")
    ]
    preferred = preferred_name.casefold()
    for voice in usable:
        if str(voice.get("name") or "").casefold() == preferred:
            return VoiceSelection(str(voice["voice_id"]), str(voice.get("name") or preferred_name))
    if usable:
        voice = usable[0]
        return VoiceSelection(str(voice["voice_id"]), str(voice.get("name") or "Available voice"))
    return None


class ElevenLabsTTS:
    def __init__(self, settings: OnboardingVoiceSettings):
        self.settings = settings
        self._voice: Optional[VoiceSelection] = None
        self._voice_lock = asyncio.Lock()

    async def resolve_voice(self) -> VoiceSelection:
        if self._voice is not None:
            return self._voice
        if self.settings.voice_id:
            self._voice = VoiceSelection(
                self.settings.voice_id,
                self.settings.voice_name,
            )
            return self._voice

        cache_key = (
            self.settings.elevenlabs_api_base,
            self.settings.voice_name.casefold(),
        )
        cached = _VOICE_CACHE.get(cache_key)
        if cached is not None:
            self._voice = cached
            return cached

        async with self._voice_lock:
            if self._voice is not None:
                return self._voice
            headers = {"xi-api-key": self.settings.elevenlabs_api_key}
            timeout = aiohttp.ClientTimeout(total=10)
            async with aiohttp.ClientSession(headers=headers, timeout=timeout) as session:
                search_url = f"{self.settings.elevenlabs_api_base}/v2/voices"
                async with session.get(
                    search_url,
                    params={"search": self.settings.voice_name, "page_size": 100},
                ) as response:
                    response.raise_for_status()
                    search_data = await response.json()
                exact = select_voice(search_data.get("voices", []), self.settings.voice_name)
                if exact and exact.name.casefold() == self.settings.voice_name.casefold():
                    self._voice = exact
                    _VOICE_CACHE[cache_key] = exact
                    return exact

                async with session.get(search_url, params={"page_size": 100}) as response:
                    response.raise_for_status()
                    available_data = await response.json()
                fallback = select_voice(
                    available_data.get("voices", []),
                    self.settings.voice_name,
                )
                if fallback is None:
                    raise RuntimeError("No ElevenLabs voice is available for this account")
                self._voice = fallback
                _VOICE_CACHE[cache_key] = fallback
                return fallback

    async def stream(
        self,
        text_queue: "asyncio.Queue[Optional[str]]",
        on_audio: AudioCallback,
    ) -> VoiceSelection:
        voice = await self.resolve_voice()
        query = urlencode(
            {
                "model_id": self.settings.tts_model,
                "output_format": self.settings.output_format,
                "inactivity_timeout": 60,
            }
        )
        url = (
            f"{self.settings.elevenlabs_ws_base}/v1/text-to-speech/"
            f"{voice.voice_id}/stream-input?{query}"
        )

        async with websockets.connect(
            url,
            max_size=2**22,
            ping_interval=20,
            open_timeout=10,
        ) as socket:
            await socket.send(
                json.dumps(
                    {
                        "text": " ",
                        "xi_api_key": self.settings.elevenlabs_api_key,
                        "voice_settings": {
                            "stability": 0.45,
                            "similarity_boost": 0.8,
                            "use_speaker_boost": False,
                            "speed": 1.0,
                        },
                        "generation_config": {
                            "chunk_length_schedule": [50, 90, 140, 200]
                        },
                    }
                )
            )

            async def send_text() -> None:
                while True:
                    chunk = await text_queue.get()
                    if chunk is None:
                        await socket.send(json.dumps({"text": ""}))
                        return
                    normalized = chunk.strip()
                    if normalized:
                        await socket.send(
                            json.dumps(
                                {
                                    "text": normalized + " ",
                                    "try_trigger_generation": True,
                                }
                            )
                        )

            sender = asyncio.create_task(send_text())
            try:
                async for raw_message in socket:
                    message = json.loads(raw_message)
                    audio = message.get("audio")
                    if isinstance(audio, str) and audio:
                        await on_audio(audio)
                    if message.get("is_final") is True:
                        break
                await sender
            finally:
                if not sender.done():
                    sender.cancel()
                    await asyncio.gather(sender, return_exceptions=True)

        return voice
