import asyncio
import base64
import json
import logging
import os
import time
import uuid

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, status
from websockets.exceptions import ConnectionClosed

from app.DB.Queries.user import get_user_by_auth0_id
from app.DB.pool import get_pool
from app.STT.deepgram_realtime import DeepgramRealtimeClient
from app.STT.limits import stt_connection_limiter
from app.STT.polisher import TranscriptPolisher
from app.STT.realtime_types import RealtimeSTTConfig, RealtimeSTTMode
from app.STT.transcript_aggregator import TranscriptAggregator
from app.api.auth_utils import token_verifier

logger = logging.getLogger(__name__)

realtime_stt_router = APIRouter(prefix="/audio")


class STTLatencyMetrics:
    """Tracks realtime STT timings from the first received audio chunk."""

    def __init__(self, session_id: str) -> None:
        self.session_id = session_id
        self.first_chunk_at: float | None = None
        self.last_chunk_at: float | None = None
        self.stop_received_at: float | None = None
        self.audio_chunks = 0
        self.audio_bytes = 0
        self.final_results = 0

    def mark_audio_chunk(self, chunk_size: int) -> None:
        """Start the latency clock and track received audio volume."""

        if self.first_chunk_at is None:
            self.first_chunk_at = time.monotonic()
        self.last_chunk_at = time.monotonic()
        self.audio_chunks += 1
        self.audio_bytes += chunk_size

    def mark_final_result(self) -> None:
        """Track final Deepgram results for end-of-session diagnostics."""

        self.final_results += 1

    def mark_stop_received(self) -> None:
        """Record when client audio is complete."""

        self.stop_received_at = time.monotonic()

    def elapsed_ms(self) -> int | None:
        """Return elapsed milliseconds since first audio chunk."""

        if self.first_chunk_at is None:
            return None
        return int((time.monotonic() - self.first_chunk_at) * 1000)

    def elapsed_text(self) -> str:
        """Return formatted elapsed time since first audio chunk."""

        elapsed = self.elapsed_ms()
        return "NOT_STARTED" if elapsed is None else f"{elapsed}ms"

    def stt_elapsed_text(self) -> str:
        """Return STT conversion time from last audio chunk to current result."""

        anchor = self.last_chunk_at or self.stop_received_at
        if anchor is None:
            return "NOT_STARTED"
        return f"{int((time.monotonic() - anchor) * 1000)}ms"


class SessionTranscript:
    """Keeps rolling raw and polished transcript state for one recording."""

    def __init__(self) -> None:
        self._utterances: list[str] = []
        self._polished_text: str | None = None
        self._polished = False

    def add_for_polishing(self, text: str) -> str | None:
        """Add one raw utterance and return polished-prefix + new raw text."""

        clean_text = text.strip()
        if not clean_text:
            return None

        self._utterances.append(clean_text)
        if self._polished_text:
            return f"{self._polished_text} {clean_text}".strip()

        return self.raw_combined()

    def update_polished(self, text: str, polished: bool) -> None:
        """Store the latest rolling polished transcript."""

        clean_text = text.strip()
        if clean_text:
            self._polished_text = clean_text
            self._polished = polished

    def current_text(self) -> str | None:
        """Return the best available complete transcript without another LLM call."""

        return self._polished_text or self.raw_combined()

    @property
    def current_is_polished(self) -> bool:
        return self._polished

    def raw_combined(self) -> str | None:
        text = " ".join(self._utterances).strip()
        return text or None

    @property
    def count(self) -> int:
        return len(self._utterances)


@realtime_stt_router.websocket("/realtime")
async def realtime_audio(websocket: WebSocket):
    """
    Realtime speech-to-text gateway.

    This websocket is intentionally separate from app/api/websocket_routes.py so
    microphone streaming cannot add latency to agent task traffic.
    """

    await websocket.accept()
    session_id = str(uuid.uuid4())
    send_lock = asyncio.Lock()
    metrics = STTLatencyMetrics(session_id)

    try:
        user_id = await _authenticate_realtime_user(websocket)
    except Exception as exc:
        await _send_json(
            websocket,
            send_lock,
            {"type": "error", "code": "AUTH_FAILED", "message": str(exc)},
        )
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    async with stt_connection_limiter.session(user_id) as allowed:
        if not allowed:
            await _send_json(
                websocket,
                send_lock,
                {
                    "type": "error",
                    "code": "STT_RATE_LIMITED",
                    "message": "Too many active realtime transcription sessions.",
                },
            )
            await websocket.close(code=status.WS_1013_TRY_AGAIN_LATER)
            return

        await _run_realtime_session(websocket, send_lock, session_id, metrics)


async def _run_realtime_session(
    websocket: WebSocket,
    send_lock: asyncio.Lock,
    session_id: str,
    metrics: STTLatencyMetrics,
) -> None:
    """Start Deepgram streaming and bridge client audio to transcript events."""

    try:
        config, first_audio_chunk = await _read_start_config(websocket)
    except Exception as exc:
        await _send_json(
            websocket,
            send_lock,
            {
                "type": "error",
                "code": "STT_START_FAILED",
                "message": str(exc),
            },
        )
        await websocket.close(code=status.WS_1003_UNSUPPORTED_DATA)
        return

    client = DeepgramRealtimeClient(config)
    audio_queue: asyncio.Queue[bytes | None] = asyncio.Queue(maxsize=_audio_queue_size())
    polisher = TranscriptPolisher()
    aggregator = TranscriptAggregator()
    session_transcript = SessionTranscript()
    started_at = time.monotonic()

    try:
        deepgram_ws = await client.connect()
    except Exception as exc:
        logger.exception("Failed to connect Deepgram realtime session")
        await _send_json(
            websocket,
            send_lock,
            {
                "type": "error",
                "code": "DEEPGRAM_CONNECT_FAILED",
                "message": str(exc),
            },
        )
        await websocket.close(code=status.WS_1011_INTERNAL_ERROR)
        return

    await _send_json(
        websocket,
        send_lock,
        {
            "type": "stt_started",
            "session_id": session_id,
            "mode": config.mode.value,
            "model": config.model,
        },
    )

    if first_audio_chunk:
        metrics.mark_audio_chunk(len(first_audio_chunk))
        await _enqueue_audio(audio_queue, first_audio_chunk)

    receive_task = asyncio.create_task(
        _receive_client_audio(websocket, audio_queue, started_at, metrics)
    )
    audio_task = asyncio.create_task(_send_audio_to_deepgram(deepgram_ws, audio_queue))
    transcript_task = asyncio.create_task(
        _receive_deepgram_transcripts(
            websocket,
            send_lock,
            deepgram_ws,
            aggregator,
            session_transcript,
            polisher,
            config,
            session_id,
            metrics,
        )
    )

    done, _ = await asyncio.wait(
        {receive_task, transcript_task},
        return_when=asyncio.FIRST_COMPLETED,
    )

    final_sent = False
    if receive_task in done:
        await _signal_audio_end(audio_queue)
        await asyncio.gather(audio_task, return_exceptions=True)
        await transcript_task
    else:
        receive_task.cancel()
        audio_task.cancel()

    await asyncio.gather(receive_task, audio_task, transcript_task, return_exceptions=True)

    final_text = aggregator.flush()
    if final_text:
        polish_input = session_transcript.add_for_polishing(final_text)
        if polish_input:
            polished_text, polished, polish_error = await _polish_text(
                polisher,
                polish_input,
                config,
                session_id,
                metrics,
            )
            session_transcript.update_polished(polished_text, polished)
            await _send_transcript_event(
                websocket,
                send_lock,
                config,
                session_id,
                "polished_final",
                polished_text,
                session_transcript.raw_combined() or polish_input,
                polished,
                polish_error,
                metrics,
            )
            final_sent = True
    elif not final_sent and session_transcript.current_text():
        await _send_transcript_event(
            websocket,
            send_lock,
            config,
            session_id,
            "polished_final",
            session_transcript.current_text() or "",
            session_transcript.raw_combined() or "",
            session_transcript.current_is_polished,
            None,
            metrics,
        )
        final_sent = True

    await _close_deepgram(deepgram_ws)
    await _close_client_after_final(websocket, final_sent)


async def _authenticate_realtime_user(websocket: WebSocket) -> str:
    """Verify Auth0 token and return the internal user id for rate limiting."""

    token = websocket.query_params.get("token")
    if not token:
        raise ValueError("No token provided.")

    payload = token_verifier.verify(token)
    auth0_id = payload.get("sub")
    if not auth0_id:
        raise ValueError("Token missing subject.")

    pool = await get_pool()
    user = await get_user_by_auth0_id(pool, auth0_id)
    if not user:
        raise ValueError("User record not found.")

    return str(user["id"])


async def _read_start_config(websocket: WebSocket) -> tuple[RealtimeSTTConfig, bytes | None]:
    """Read optional start message and preserve any first audio frame."""

    defaults = {
        "mode": _normalize_mode(websocket.query_params.get("mode", "audio_input")),
        "model": websocket.query_params.get("model", "nova-3"),
        "language": websocket.query_params.get("language", "en"),
    }

    try:
        message = await asyncio.wait_for(websocket.receive(), timeout=5)
    except asyncio.TimeoutError:
        return RealtimeSTTConfig(**defaults), None

    if message.get("bytes") is not None:
        return RealtimeSTTConfig(**defaults), message["bytes"]

    if message.get("type") == "websocket.disconnect":
        raise WebSocketDisconnect()

    if message.get("text"):
        data = json.loads(message["text"])
        if data.get("type") == "start":
            payload = {**defaults, **{k: v for k, v in data.items() if k != "type"}}
            payload["mode"] = _normalize_mode(payload.get("mode", "audio_input"))
            return RealtimeSTTConfig(**payload), None

        if data.get("type") == "audio":
            return RealtimeSTTConfig(**defaults), _decode_audio_chunk(data)

    return RealtimeSTTConfig(**defaults), None


async def _receive_client_audio(
    websocket: WebSocket,
    audio_queue: asyncio.Queue[bytes | None],
    started_at: float,
    metrics: STTLatencyMetrics,
) -> None:
    """Receive client audio frames and apply bounded-queue backpressure."""

    max_seconds = _max_session_seconds()
    while True:
        if time.monotonic() - started_at > max_seconds:
            metrics.mark_stop_received()
            await _signal_audio_end(audio_queue)
            return

        message = await websocket.receive()

        if message.get("type") == "websocket.disconnect":
            metrics.mark_stop_received()
            await _signal_audio_end(audio_queue)
            return

        if message.get("bytes") is not None:
            chunk = message["bytes"]
            metrics.mark_audio_chunk(len(chunk))
            await _enqueue_audio(audio_queue, chunk)
            continue

        text = message.get("text")
        if text is None:
            continue

        data = json.loads(text)
        msg_type = data.get("type")

        if msg_type == "stop":
            metrics.mark_stop_received()
            await _signal_audio_end(audio_queue)
            return

        if msg_type == "audio":
            chunk = _decode_audio_chunk(data)
            metrics.mark_audio_chunk(len(chunk))
            await _enqueue_audio(audio_queue, chunk)


async def _send_audio_to_deepgram(deepgram_ws, audio_queue: asyncio.Queue[bytes | None]) -> None:
    """Forward queued audio chunks to Deepgram without blocking client receive."""

    while True:
        chunk = await audio_queue.get()
        if chunk is None:
            await deepgram_ws.send(json.dumps({"type": "CloseStream"}))
            return
        await deepgram_ws.send(chunk)


async def _receive_deepgram_transcripts(
    websocket: WebSocket,
    send_lock: asyncio.Lock,
    deepgram_ws,
    aggregator: TranscriptAggregator,
    session_transcript: SessionTranscript,
    polisher: TranscriptPolisher,
    config: RealtimeSTTConfig,
    session_id: str,
    metrics: STTLatencyMetrics,
) -> None:
    """Receive Deepgram transcript events and emit interim/final client events."""

    try:
        async for message in deepgram_ws:
            result = DeepgramRealtimeClient.parse_transcript(message)
            if result is None:
                continue

            event_type = "transcript_final" if result.is_final else "transcript_interim"
            if result.is_final:
                metrics.mark_final_result()

            await _send_json(
                websocket,
                send_lock,
                {
                    "type": event_type,
                    "session_id": session_id,
                    "text": result.text,
                    "is_final": result.is_final,
                    "speech_final": result.speech_final,
                },
            )

            utterance = aggregator.add_result(result)
            if utterance:
                polish_input = session_transcript.add_for_polishing(utterance)
                if not polish_input:
                    continue

                polished_text, polished, polish_error = await _polish_text(
                    polisher,
                    polish_input,
                    config,
                    session_id,
                    metrics,
                )
                session_transcript.update_polished(polished_text, polished)
                await _send_transcript_event(
                    websocket,
                    send_lock,
                    config,
                    session_id,
                    "polished_partial",
                    polished_text,
                    session_transcript.raw_combined() or polish_input,
                    polished,
                    polish_error,
                    metrics,
                )
    except ConnectionClosed:
        return


async def _polish_text(
    polisher: TranscriptPolisher,
    raw_text: str,
    config: RealtimeSTTConfig,
    session_id: str,
    metrics: STTLatencyMetrics,
) -> tuple[str, bool, str | None]:
    """Polish text and return the safest available transcript."""

    if config.mode == RealtimeSTTMode.AUDIO_INPUT:
        return raw_text, False, None

    polished_text, polished, error = await polisher.polish(raw_text, config.mode)
    return polished_text, polished, error


async def _send_transcript_event(
    websocket: WebSocket,
    send_lock: asyncio.Lock,
    config: RealtimeSTTConfig,
    session_id: str,
    event_type: str,
    text: str,
    raw_text: str,
    polished: bool,
    error: str | None,
    metrics: STTLatencyMetrics,
) -> None:
    """Send a polished partial or final transcript event."""

    payload = {
        "type": event_type,
        "session_id": session_id,
        "mode": config.mode.value,
        "text": text,
        "raw_text": raw_text,
        "polished": polished,
    }
    if error:
        payload["error"] = error

    await _send_json(
        websocket,
        send_lock,
        payload,
    )


async def _enqueue_audio(audio_queue: asyncio.Queue[bytes | None], chunk: bytes) -> None:
    """Put audio in a bounded queue while preserving stream continuity."""

    if not chunk:
        return

    await audio_queue.put(chunk)


async def _signal_audio_end(audio_queue: asyncio.Queue[bytes | None]) -> None:
    """Signal end-of-audio after queued audio has been forwarded."""

    await audio_queue.put(None)


def _decode_audio_chunk(data: dict) -> bytes:
    """Decode a JSON/base64 audio message into bytes."""

    chunk = data.get("chunk")
    if not chunk:
        return b""
    return base64.b64decode(chunk)


async def _send_json(websocket: WebSocket, send_lock: asyncio.Lock, payload: dict) -> None:
    """Serialize websocket sends so concurrent tasks never interleave messages."""

    try:
        async with send_lock:
            await websocket.send_json(payload)
    except (RuntimeError, WebSocketDisconnect):
        logger.debug("Client websocket closed before STT message could be sent")


async def _close_deepgram(deepgram_ws) -> None:
    """Close Deepgram connection quietly during normal cleanup."""

    try:
        await deepgram_ws.close()
    except Exception:
        logger.debug("Deepgram websocket already closed", exc_info=True)


async def _close_client_after_final(websocket: WebSocket, final_sent: bool) -> None:
    """Give the client a short window to receive final text, then close."""

    if not final_sent:
        return

    await asyncio.sleep(_client_final_close_delay_seconds())
    try:
        await websocket.close()
    except Exception:
        logger.debug("Client websocket already closed after final transcript", exc_info=True)


def _audio_queue_size() -> int:
    """Queue depth for short bursts; keep bounded for predictable memory."""

    return int(os.getenv("STT_AUDIO_QUEUE_SIZE", "50"))


def _max_session_seconds() -> int:
    """Maximum realtime STT session duration per websocket connection."""

    return int(os.getenv("STT_MAX_SESSION_SECONDS", "600"))


def _client_final_close_delay_seconds() -> float:
    """How long to keep the client websocket open after final delivery."""

    return float(os.getenv("STT_CLIENT_FINAL_CLOSE_DELAY_SECONDS", "10"))


def _normalize_mode(mode: str) -> str:
    """Accept old/frontend naming variants while storing one canonical mode."""

    if mode in {"audio_dectation", "audio_dictation", "dictation", "transcribe"}:
        return "audio_transcribe"
    return mode
