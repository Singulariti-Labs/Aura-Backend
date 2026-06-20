import json
import os
from typing import Any
from urllib.parse import urlencode

import websockets

from app.STT.realtime_types import RealtimeSTTConfig, TranscriptResult


class DeepgramRealtimeClient:
    """Small direct WebSocket client for Deepgram realtime STT."""

    BASE_URL = "wss://api.deepgram.com/v1/listen"

    def __init__(self, config: RealtimeSTTConfig) -> None:
        self.config = config
        self.api_key = os.getenv("DEEPGRAM_API_KEY")
        if not self.api_key:
            raise ValueError("DEEPGRAM_API_KEY environment variable is not set.")

    def build_url(self) -> str:
        """Build Deepgram listen URL with latency-safe realtime options."""

        params: dict[str, Any] = {
            "model": self.config.model,
            "language": self.config.language,
            "channels": self.config.channels,
            "interim_results": str(self.config.interim_results).lower(),
            "smart_format": str(self.config.smart_format).lower(),
            "punctuate": str(self.config.punctuate).lower(),
            "endpointing": self.config.endpointing,
        }
        if self.config.encoding:
            params["encoding"] = self.config.encoding
        if self.config.sample_rate:
            params["sample_rate"] = self.config.sample_rate

        return f"{self.BASE_URL}?{urlencode(params)}"

    async def connect(self):
        """Open the authenticated Deepgram WebSocket connection."""

        return await websockets.connect(
            self.build_url(),
            additional_headers={"Authorization": f"Token {self.api_key}"},
            ping_interval=20,
            ping_timeout=10,
            close_timeout=5,
            max_queue=32,
        )

    @staticmethod
    def parse_transcript(message: str) -> TranscriptResult | None:
        """Normalize Deepgram messages into transcript events used by the route."""

        try:
            payload = json.loads(message)
        except json.JSONDecodeError:
            return None

        if payload.get("type") != "Results":
            return None

        channel = payload.get("channel") or {}
        alternatives = channel.get("alternatives") or []
        if not alternatives:
            return None

        transcript = (alternatives[0].get("transcript") or "").strip()
        if not transcript:
            return None

        return TranscriptResult(
            text=transcript,
            is_final=bool(payload.get("is_final")),
            speech_final=bool(payload.get("speech_final")),
        )
