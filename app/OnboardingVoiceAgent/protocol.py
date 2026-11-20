"""Validation and structured WebSocket events for onboarding voice."""

from dataclasses import dataclass
from typing import Any, Optional
import uuid

from fastapi import WebSocket

from app.api.websocket_utils import send_ws_message


def _text_from_content(content: Any) -> str:
    if isinstance(content, str):
        return content.strip()
    if not isinstance(content, list):
        return ""
    return "\n".join(
        str(block.get("text", "")).strip()
        for block in content
        if isinstance(block, dict) and block.get("type") == "text"
    ).strip()


def extract_onboarding_query(payload: dict) -> str:
    """Prefer payload.query, otherwise use the latest user text message."""

    query = payload.get("query")
    if isinstance(query, str) and query.strip():
        return query.strip()

    messages = payload.get("messages")
    if isinstance(messages, list):
        for message in reversed(messages):
            if isinstance(message, dict) and message.get("role") == "user":
                query = _text_from_content(message.get("content"))
                if query:
                    return query
    raise ValueError("A non-empty payload.query or user text message is required")


def onboarding_memory_update_enabled(payload: dict) -> bool:
    """Return whether this onboarding page explicitly permits profile learning.

    Memory learning is opt-in per request.  Requiring the literal boolean ``True``
    avoids accidentally enabling writes for older clients or malformed values such
    as the strings ``"true"`` and ``"false"``.
    """

    config = payload.get("memory_update")
    return isinstance(config, dict) and config.get("enabled") is True


@dataclass(slots=True)
class OnboardingProtocol:
    websocket: WebSocket
    task_id: str
    chat_id: str
    session_id: str
    response_id: str

    @classmethod
    def create(
        cls,
        *,
        websocket: WebSocket,
        task_id: str,
        chat_id: str,
        session_id: str,
    ) -> "OnboardingProtocol":
        return cls(
            websocket=websocket,
            task_id=task_id,
            chat_id=chat_id,
            session_id=session_id,
            response_id=f"onboarding-response-{uuid.uuid4()}",
        )

    async def _send(self, event: str, **payload: Any) -> None:
        await send_ws_message(
            self.websocket,
            type="onboarding_response",
            task_id=self.task_id,
            chat_id=self.chat_id,
            session_id=self.session_id,
            payload={
                "event": event,
                "response_id": self.response_id,
                **payload,
            },
        )

    async def response_start(
        self,
        *,
        voice_name: str,
        audio_encoding: str,
        sample_rate_hz: int,
        channels: int,
    ) -> None:
        await self._send(
            "response.start",
            output_mode="voice",
            voice={"requested_name": voice_name},
            audio={
                "encoding": audio_encoding,
                "sample_rate_hz": sample_rate_hz,
                "channels": channels,
            },
        )

    async def audio_delta(self, *, sequence: int, audio_base64: str) -> None:
        await self._send(
            "audio.delta",
            sequence=sequence,
            audio_base64=audio_base64,
        )

    async def audio_done(
        self,
        *,
        last_sequence: int,
        voice_id: str,
        voice_name: str,
    ) -> None:
        await self._send(
            "audio.done",
            last_sequence=last_sequence,
            voice={"id": voice_id, "name": voice_name},
        )

    async def response_done(
        self,
        *,
        transcript: str,
        latency_ms: dict[str, float],
    ) -> None:
        await self._send(
            "response.done",
            status="completed",
            transcript=transcript,
            latency_ms=latency_ms,
        )

    async def error(self, *, code: str, message: str) -> None:
        await self._send(
            "response.error",
            status="failed",
            error={"code": code, "message": message},
        )
