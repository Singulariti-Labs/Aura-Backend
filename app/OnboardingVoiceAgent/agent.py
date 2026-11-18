"""Fast text-agent -> ElevenLabs streaming voice onboarding pipeline."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from functools import lru_cache
from typing import Any, Optional
import asyncio
import json
import logging
import re
import time
import uuid

from asyncpg import Pool
from fastapi import WebSocket
from openai import AsyncOpenAI

from app.LLM.memory import Memory
from app.OnboardingVoiceAgent.config import OnboardingVoiceSettings
from app.OnboardingVoiceAgent.context import onboarding_runtime
from app.OnboardingVoiceAgent.elevenlabs_tts import ElevenLabsTTS
from app.OnboardingVoiceAgent.prompts import (
    ONBOARDING_SYSTEM_PROMPT,
    PROFILE_EXTRACTION_PROMPT,
)
from app.OnboardingVoiceAgent.protocol import (
    OnboardingProtocol,
    onboarding_memory_update_enabled,
)
from app.RateLimit.rate_limit_service import schedule_token_usage_update
from app.RateLimit.token_pricing import calculate_token_cost_usd_float
from app.Tools.memory_update import MemoryUpdateTool
from app.Types.agent_types import SystemInfo


logger = logging.getLogger(__name__)
TextCallback = Callable[[str], Awaitable[None]]


def _object_dict(value: Any) -> dict:
    if isinstance(value, dict):
        return value
    if hasattr(value, "model_dump"):
        dumped = value.model_dump()
        return dumped if isinstance(dumped, dict) else {}
    return {}


@lru_cache(maxsize=4)
def _shared_openai_client(api_key: str) -> AsyncOpenAI:
    """Reuse the HTTP connection pool across onboarding turns."""

    return AsyncOpenAI(api_key=api_key)


class SpeechTextBuffer:
    """Emit natural TTS chunks without waiting for the full LLM response."""

    def __init__(self, queue: "asyncio.Queue[Optional[str]]", max_chars: int = 120):
        self.queue = queue
        self.max_chars = max_chars
        self.buffer = ""

    async def feed(self, text: str) -> None:
        self.buffer += text
        while self.buffer:
            boundary = re.search(r"[.!?](?:\s|$)", self.buffer)
            if boundary:
                await self._emit(boundary.end())
                continue
            if len(self.buffer) >= self.max_chars:
                split_at = self.buffer.rfind(" ", 0, self.max_chars)
                await self._emit(split_at if split_at > 20 else self.max_chars)
                continue
            break

    async def finish(self) -> None:
        if self.buffer.strip():
            await self.queue.put(self.buffer.strip())
        self.buffer = ""
        await self.queue.put(None)

    async def _emit(self, end: int) -> None:
        chunk = self.buffer[:end].strip()
        self.buffer = self.buffer[end:].lstrip()
        if chunk:
            await self.queue.put(chunk)


class OnboardingVoiceAgent:
    """Stream onboarding speech, then optionally learn durable user facts."""

    def __init__(
        self,
        *,
        websocket: WebSocket,
        task_id: str,
        runtime_task_id: str,
        chat_id: str,
        session_id: str,
        query: str,
        payload: dict,
        system_info: SystemInfo,
        dbpool: Optional[Pool],
        user_id: Optional[str],
        rate_limit_loop: Optional[asyncio.AbstractEventLoop],
        settings: Optional[OnboardingVoiceSettings] = None,
    ) -> None:
        self.websocket = websocket
        self.task_id = task_id
        self.runtime_task_id = runtime_task_id
        self.chat_id = chat_id
        self.session_id = session_id
        self.query = query
        self.payload = payload
        self.system_info = system_info
        self.dbpool = dbpool
        self.user_id = user_id
        self.rate_limit_loop = rate_limit_loop
        self.settings = settings or OnboardingVoiceSettings.from_env()

        self.protocol = OnboardingProtocol.create(
            websocket=websocket,
            task_id=task_id,
            chat_id=chat_id,
            session_id=session_id,
        )
        self.openai = _shared_openai_client(self.settings.openai_api_key)
        self.tts = ElevenLabsTTS(self.settings)
        self.memory = Memory()
        self.usage = {"input": 0, "output": 0, "total_tokens": 0}

        self.memory_tool = None
        if (
            self.settings.fact_extraction_enabled
            and onboarding_memory_update_enabled(self.payload)
        ):
            self.memory_tool = MemoryUpdateTool(
                llm=None,
                memory=self.memory,
                task_id=self.runtime_task_id,
                chat_id=self.chat_id,
            ).to_tool()

    async def run(self) -> str:
        started = time.perf_counter()
        first_text_at: Optional[float] = None
        first_audio_at: Optional[float] = None
        audio_sequence = 0
        audio_queue: asyncio.Queue[Optional[str]] = asyncio.Queue()
        text_buffer = SpeechTextBuffer(audio_queue)

        async def on_audio(audio_base64: str) -> None:
            nonlocal audio_sequence, first_audio_at
            if first_audio_at is None:
                first_audio_at = time.perf_counter()
            await self.protocol.audio_delta(
                sequence=audio_sequence,
                audio_base64=audio_base64,
            )
            audio_sequence += 1

        await self.protocol.response_start(
            voice_name=self.settings.voice_name,
            audio_encoding="pcm_s16le",
            sample_rate_hz=self.settings.sample_rate_hz,
            channels=self.settings.channels,
        )

        with onboarding_runtime(self.session_id):
            tts_task = asyncio.create_task(self.tts.stream(audio_queue, on_audio))

            async def on_text(delta: str) -> None:
                nonlocal first_text_at
                if first_text_at is None:
                    first_text_at = time.perf_counter()
                await text_buffer.feed(delta)

            try:
                messages = self._conversation_messages()
                transcript = await self._stream_completion(
                    messages=messages,
                    on_text=on_text,
                )
                await text_buffer.finish()
                voice = await tts_task
                await self.protocol.audio_done(
                    last_sequence=max(audio_sequence - 1, -1),
                    voice_id=voice.voice_id,
                    voice_name=voice.name,
                )

                ended = time.perf_counter()
                latency = {
                    "llm_first_text": round(
                        ((first_text_at or ended) - started) * 1000,
                        2,
                    ),
                    "first_audio": round(
                        ((first_audio_at or ended) - started) * 1000,
                        2,
                    ),
                    "total": round((ended - started) * 1000, 2),
                }
                await self.protocol.response_done(
                    transcript=transcript,
                    latency_ms=latency,
                )
            except Exception as error:
                if not tts_task.done():
                    await audio_queue.put(None)
                    tts_task.cancel()
                await asyncio.gather(
                    tts_task,
                    return_exceptions=True,
                )
                await self.protocol.error(
                    code="ONBOARDING_VOICE_FAILED",
                    message=str(error),
                )
                raise

            # Hidden feature: a second LLM extracts durable facts only after the
            # voice response. It requires both the global environment flag and
            # the page-level opt-in, and remains fully dormant otherwise.
            if self.memory_tool is not None:
                try:
                    await asyncio.wait_for(
                        self._update_user_profile(),
                        timeout=max(
                            1.0,
                            self.settings.memory_update_timeout_seconds,
                        ),
                    )
                except TimeoutError:
                    logger.warning(
                        "Onboarding memory update timed out for session=%s",
                        self.session_id,
                    )
                except Exception:
                    logger.exception(
                        "Onboarding memory update failed for session=%s",
                        self.session_id,
                    )

            self._record_usage()
            return transcript

    async def _stream_completion(
        self,
        *,
        messages: list[dict],
        on_text: TextCallback,
    ) -> str:
        stream = await self.openai.chat.completions.create(
            model=self.settings.llm_model,
            messages=messages,
            reasoning_effort="none",
            max_completion_tokens=self.settings.llm_max_output_tokens,
            stream=True,
            stream_options={"include_usage": True},
        )

        text_parts: list[str] = []
        async for chunk in stream:
            usage = getattr(chunk, "usage", None)
            if usage is not None:
                self._add_usage(_object_dict(usage))
            choices = getattr(chunk, "choices", None) or []
            if not choices:
                continue
            delta = getattr(choices[0], "delta", None)
            content = getattr(delta, "content", None)
            if isinstance(content, str) and content:
                text_parts.append(content)
                await on_text(content)
        return "".join(text_parts).strip()

    async def _update_user_profile(self) -> None:
        if self.memory_tool is None:
            return

        existing = self._user_details_content()
        response = await self.openai.chat.completions.create(
            model=self.settings.llm_model,
            reasoning_effort="none",
            max_completion_tokens=180,
            response_format={
                "type": "json_schema",
                "json_schema": {
                    "name": "onboarding_profile_facts",
                    "strict": True,
                    "schema": {
                        "type": "object",
                        "properties": {
                            "facts": {
                                "type": "array",
                                "items": {
                                    "type": "string",
                                    "minLength": 1,
                                    "maxLength": 240,
                                },
                                "maxItems": 8,
                            }
                        },
                        "required": ["facts"],
                        "additionalProperties": False,
                    },
                },
            },
            messages=[
                {"role": "system", "content": PROFILE_EXTRACTION_PROMPT},
                {
                    "role": "user",
                    "content": (
                        f"Existing USER.md:\n{existing or '(empty)'}\n\n"
                        f"Latest user message:\n{self.query}"
                    ),
                },
            ],
        )
        if response.usage is not None:
            self._add_usage(_object_dict(response.usage))
        content = response.choices[0].message.content or "{}"
        extracted_facts = json.loads(content).get("facts") or []
        seen: set[str] = set()
        facts: list[str] = []
        for candidate in extracted_facts:
            fact = str(candidate).strip()
            normalized = fact.casefold()
            if fact and normalized not in seen:
                seen.add(normalized)
                facts.append(fact)
        if not facts:
            return

        result = await self.memory_tool.ainvoke(
            {
                "type": "tool_call",
                "id": f"profile_{uuid.uuid4().hex}",
                "name": "memory_update",
                "args": {
                    "name": "user",
                    "target": "user",
                    "operations": [
                        {"action": "add", "content": fact}
                        for fact in facts
                    ],
                },
            }
        )
        if isinstance(result, dict) and result.get("success") is True:
            return
        raise RuntimeError(f"Client memory update failed: {result}")

    def _conversation_messages(self) -> list[dict]:
        user_details = self._user_details_content()
        system_prompt = ONBOARDING_SYSTEM_PROMPT.format(
            user_details=user_details or "No durable user information is available yet.",
            workspace_context=(
                f"Operating system: {self.system_info.os} {self.system_info.version}\n"
                f"Workspace: {self.system_info.workspace or '(not provided)'}\n"
                f"Current directory: {self.system_info.cwd or '(not provided)'}"
            ),
        )
        messages: list[dict] = [{"role": "system", "content": system_prompt}]
        raw_messages = self.payload.get("messages")
        if isinstance(raw_messages, list):
            for message in raw_messages[-12:]:
                if not isinstance(message, dict):
                    continue
                role = message.get("role")
                if role not in {"user", "assistant"}:
                    continue
                content = message.get("content")
                if isinstance(content, list):
                    content = "\n".join(
                        str(block.get("text", ""))
                        for block in content
                        if isinstance(block, dict) and block.get("type") == "text"
                    )
                if isinstance(content, str) and content.strip():
                    messages.append({"role": role, "content": content.strip()})

        if not messages or messages[-1].get("role") != "user" or messages[-1].get("content") != self.query:
            messages.append({"role": "user", "content": self.query})
        return messages

    def _user_details_content(self) -> str:
        user_details = self.payload.get("user_details")
        if not isinstance(user_details, dict):
            return ""
        content = user_details.get("content")
        return str(content)[:8000] if content is not None else ""

    def _add_usage(self, usage: dict) -> None:
        input_tokens = int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0)
        output_tokens = int(
            usage.get("completion_tokens") or usage.get("output_tokens") or 0
        )
        self.usage["input"] += input_tokens
        self.usage["output"] += output_tokens
        self.usage["total_tokens"] += input_tokens + output_tokens

    def _record_usage(self) -> None:
        self.usage["cost"] = calculate_token_cost_usd_float(
            provider="openai",
            model_name=self.settings.llm_model,
            input_tokens=self.usage["input"],
            output_tokens=self.usage["output"],
        )
        schedule_token_usage_update(
            pool=self.dbpool,
            user_id=self.user_id,
            usage=self.usage,
            details={
                "provider": "openai",
                "model_name": self.settings.llm_model,
                "credential_source": "platform",
            },
            event_loop=self.rate_limit_loop,
        )
