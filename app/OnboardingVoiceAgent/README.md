# OnboardingVoiceAgent

Low-latency onboarding pipeline:

1. Accept a text `onboarding` request over the existing task WebSocket.
2. Stream `gpt-5.4-mini` output without exposing any tools to the voice model.
3. Stream text into ElevenLabs `eleven_flash_v2_5` over WebSocket.
4. Return ordered `onboarding_response` audio events to the frontend.
5. After `response.done`, optionally extract durable USER.md facts and send one
   hidden `memory_update` request to the client.

The post-response fact-extraction path is a hidden, unfinished feature and is
disabled by default. `ONBOARDING_FACT_EXTRACTION_ENABLED=true` is required in
addition to the page-level opt-in below. Keep it disabled until onboarding uses
the intended `conscious/USER.md` file-editor flow.

## Page-level memory contract

Memory learning is disabled by default. Pages that are allowed to update the
local user profile must include the literal boolean below in the onboarding
payload:

```json
{
  "type": "onboarding",
  "session_id": "onboarding_...",
  "payload": {
    "query": "Call me Yash.",
    "memory_update": {
      "enabled": true,
      "page_id": "about-you"
    }
  }
}
```

Missing or malformed configuration, including `"enabled": "true"`, does not
permit a write. The server always fixes the update target to the local user
memory file and never exposes general file tools to the voice model.

When durable facts are found, the post-response request uses the existing
`client_tool_request` contract with `payload.silent=true` and
`payload.input.hide="true"`. A failure or timeout is logged but does not change
the already-completed voice response.

## Required environment

```text
OPENAI_API_KEY=...
ELEVENLABS_API_KEY=...
```

Optional overrides are documented in `.env.example`. `Zara` is requested by
default. The resolver uses an exact Zara match from the account, then falls back
to the first available account voice and caches the result. Set
`ELEVENLABS_VOICE_ID` to Zara's ID to bypass lookup and minimize first-turn
latency.

## Frontend event sequence

```text
response.start
audio.delta (repeated, ordered by sequence)
audio.done
response.done
```

Audio is Base64-encoded signed 16-bit little-endian PCM at 24 kHz mono. The
frontend should enqueue each decoded delta immediately instead of waiting for
`audio.done`.

Every event keeps the request routing IDs:

```json
{
  "type": "onboarding_response",
  "task_id": "onboarding-turn-...",
  "chat_id": "onboarding-chat-...",
  "session_id": "onboarding_...",
  "payload": {
    "event": "response.start",
    "response_id": "onboarding-response-...",
    "output_mode": "voice",
    "voice": { "requested_name": "Zara" },
    "audio": {
      "encoding": "pcm_s16le",
      "sample_rate_hz": 24000,
      "channels": 1
    }
  }
}
```

Audio chunks use the same envelope with this payload:

```json
{
  "event": "audio.delta",
  "response_id": "onboarding-response-...",
  "sequence": 0,
  "audio_base64": "..."
}
```

The terminal payload is:

```json
{
  "event": "response.done",
  "response_id": "onboarding-response-...",
  "status": "completed",
  "transcript": "Spoken assistant response",
  "latency_ms": {
    "llm_first_text": 0,
    "first_audio": 0,
    "total": 0
  }
}
```

Failures use `event: "response.error"` with `status: "failed"` and an
`error` object containing `code` and `message`.

Tool events sent while this agent runs include `payload.silent=true` and
`payload.input.hide="true"`. Only the post-response `memory_update` event is
sent. The client must execute it and return the usual `client_tool_response`,
but must not render it.
