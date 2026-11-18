import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.OnboardingVoiceAgent.agent import OnboardingVoiceAgent, SpeechTextBuffer
from app.OnboardingVoiceAgent.config import _env_bool
from app.OnboardingVoiceAgent.elevenlabs_tts import select_voice
from app.OnboardingVoiceAgent.prompts import PROFILE_EXTRACTION_PROMPT


class VoiceSelectionTests(unittest.TestCase):
    def test_fact_extraction_feature_flag_defaults_off(self):
        with patch.dict("os.environ", {}, clear=True):
            self.assertFalse(_env_bool("ONBOARDING_FACT_EXTRACTION_ENABLED"))

        for enabled_value in ("true", "TRUE", "1", "yes", "on"):
            with self.subTest(enabled_value=enabled_value):
                with patch.dict(
                    "os.environ",
                    {"ONBOARDING_FACT_EXTRACTION_ENABLED": enabled_value},
                    clear=True,
                ):
                    self.assertTrue(
                        _env_bool("ONBOARDING_FACT_EXTRACTION_ENABLED")
                    )

    def test_profile_prompt_fixes_onboarding_memory_destination(self):
        self.assertIn("memory tool: memory_update", PROFILE_EXTRACTION_PROMPT)
        self.assertIn("name: user", PROFILE_EXTRACTION_PROMPT)
        self.assertIn("target: user", PROFILE_EXTRACTION_PROMPT)
        self.assertIn("local destination: USER.md", PROFILE_EXTRACTION_PROMPT)

    def test_prefers_exact_zara_case_insensitively(self):
        voice = select_voice(
            [
                {"voice_id": "other", "name": "Alice"},
                {"voice_id": "zara-id", "name": "ZARA"},
            ],
            "Zara",
        )
        self.assertIsNotNone(voice)
        self.assertEqual(voice.voice_id, "zara-id")

    def test_falls_back_to_available_voice(self):
        voice = select_voice(
            [{"voice_id": "fallback-id", "name": "Alice"}],
            "Zara",
        )
        self.assertEqual(voice.voice_id, "fallback-id")


class SpeechTextBufferTests(unittest.IsolatedAsyncioTestCase):
    async def test_emits_sentence_before_completion(self):
        queue = asyncio.Queue()
        buffer = SpeechTextBuffer(queue)
        await buffer.feed("Hello there. What are you building")
        self.assertEqual(await queue.get(), "Hello there.")
        await buffer.finish()
        self.assertEqual(await queue.get(), "What are you building")
        self.assertIsNone(await queue.get())


class VoiceCompletionTests(unittest.IsolatedAsyncioTestCase):
    async def test_spoken_response_does_not_expose_tools(self):
        class FakeStream:
            def __init__(self, chunks):
                self._chunks = iter(chunks)

            def __aiter__(self):
                return self

            async def __anext__(self):
                try:
                    return next(self._chunks)
                except StopIteration as error:
                    raise StopAsyncIteration from error

        create = AsyncMock(
            return_value=FakeStream(
                [
                    SimpleNamespace(
                        usage=None,
                        choices=[
                            SimpleNamespace(
                                delta=SimpleNamespace(content="Hello there.")
                            )
                        ],
                    )
                ]
            )
        )
        agent = object.__new__(OnboardingVoiceAgent)
        agent.openai = SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=create))
        )
        agent.settings = SimpleNamespace(
            llm_model="voice-model",
            llm_max_output_tokens=100,
        )
        agent.usage = {"input": 0, "output": 0, "total_tokens": 0}
        on_text = AsyncMock()

        result = await agent._stream_completion(
            messages=[{"role": "user", "content": "Hi"}],
            on_text=on_text,
        )

        self.assertEqual(result, "Hello there.")
        request = create.await_args.kwargs
        self.assertNotIn("tools", request)
        self.assertNotIn("tool_choice", request)
        self.assertNotIn("parallel_tool_calls", request)
        on_text.assert_awaited_once_with("Hello there.")

    async def test_memory_learning_starts_after_response_done(self):
        events = []

        class FakeProtocol:
            async def response_start(self, **_kwargs):
                events.append("response.start")

            async def audio_delta(self, **_kwargs):
                events.append("audio.delta")

            async def audio_done(self, **_kwargs):
                events.append("audio.done")

            async def response_done(self, **_kwargs):
                events.append("response.done")

            async def error(self, **_kwargs):
                events.append("response.error")

        class FakeTTS:
            async def stream(self, queue, _on_audio):
                while await queue.get() is not None:
                    pass
                return SimpleNamespace(voice_id="voice-1", name="Zara")

        agent = object.__new__(OnboardingVoiceAgent)
        agent.protocol = FakeProtocol()
        agent.tts = FakeTTS()
        agent.session_id = "session-1"
        agent.payload = {"memory_update": {"enabled": True}}
        agent.settings = SimpleNamespace(
            voice_name="Zara",
            sample_rate_hz=24000,
            channels=1,
            memory_update_timeout_seconds=1,
        )
        agent.memory_tool = SimpleNamespace()
        agent._conversation_messages = lambda: []

        async def stream_completion(**kwargs):
            await kwargs["on_text"]("Welcome.")
            return "Welcome."

        async def update_profile():
            events.append("memory.update")

        agent._stream_completion = stream_completion
        agent._update_user_profile = update_profile
        agent._record_usage = lambda: events.append("usage.recorded")

        result = await agent.run()

        self.assertEqual(result, "Welcome.")
        self.assertLess(events.index("response.done"), events.index("memory.update"))
        self.assertNotIn("response.error", events)


if __name__ == "__main__":
    unittest.main()
