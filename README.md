Client request example:
```json
{
  // Unique identifier for this request (used to track response)
  "id": "req-20250616-001",           
  // Main prompt or query
  "query": "What is the capital of France?",  
  // (Optional) Client operating system info
  "os": "windows",                    
  // (Optional) OS version or application version
  "version": "11",                    
  // (Optional) Any additional metadata
  "meta": {                           
    "user_id": "user-1234",
    "timestamp": "2025-06-16T13:45:00Z",
    "source": "web-app"
  }
}
```

Realtime STT environment:

```env
DEEPGRAM_API_KEY=your_deepgram_api_key
STT_MAX_ACTIVE_SESSIONS=200
STT_MAX_SESSIONS_PER_USER=2
STT_AUDIO_QUEUE_SIZE=50
STT_MAX_SESSION_SECONDS=600
STT_POLISH_ENABLED=true
STT_POLISH_PROVIDER=openai
STT_POLISH_MODEL=gpt-4o-mini
STT_CLIENT_FINAL_CLOSE_DELAY_SECONDS=10
```

Realtime STT websocket:

```text
/audio/realtime?token=<AUTH0_TOKEN>&mode=audio_input
```

Use `mode=audio_input` for prompt cleanup and `mode=audio_transcribe` for intent-aware dictation. The existing `/ws` agent websocket stays separate; send the `polished_final.text` to `/ws` only when the user submits.
