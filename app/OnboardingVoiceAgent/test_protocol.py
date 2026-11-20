import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app.OnboardingVoiceAgent.context import onboarding_runtime
from app.OnboardingVoiceAgent.protocol import (
    OnboardingProtocol,
    extract_onboarding_query,
    onboarding_memory_update_enabled,
)
from app.api.websocket_utils import send_ws_message


class FakeWebSocket:
    def __init__(self):
        self.state = SimpleNamespace()
        self.messages = []

    async def send_json(self, message):
        self.messages.append(message)


class OnboardingProtocolTests(unittest.IsolatedAsyncioTestCase):
    def test_extracts_latest_user_text(self):
        payload = {
            "messages": [
                {"role": "user", "content": "older"},
                {"role": "assistant", "content": "reply"},
                {
                    "role": "user",
                    "content": [{"type": "text", "text": "latest"}],
                },
            ]
        }
        self.assertEqual(extract_onboarding_query(payload), "latest")

    def test_query_field_wins(self):
        self.assertEqual(
            extract_onboarding_query(
                {
                    "query": "explicit",
                    "messages": [{"role": "user", "content": "history"}],
                }
            ),
            "explicit",
        )

    def test_memory_update_requires_explicit_boolean_opt_in(self):
        self.assertTrue(
            onboarding_memory_update_enabled(
                {"memory_update": {"enabled": True, "page_id": "about-you"}}
            )
        )
        self.assertFalse(onboarding_memory_update_enabled({}))
        self.assertFalse(
            onboarding_memory_update_enabled(
                {"memory_update": {"enabled": "true"}}
            )
        )
        self.assertFalse(
            onboarding_memory_update_enabled(
                {"memory_update": {"enabled": False}}
            )
        )

    async def test_structured_event_keeps_all_routing_ids(self):
        websocket = FakeWebSocket()
        protocol = OnboardingProtocol.create(
            websocket=websocket,
            task_id="task-1",
            chat_id="chat-1",
            session_id="session-1",
        )
        await protocol.audio_delta(sequence=3, audio_base64="abc")
        message = websocket.messages[0]
        self.assertEqual(message["type"], "onboarding_response")
        self.assertEqual(message["task_id"], "task-1")
        self.assertEqual(message["chat_id"], "chat-1")
        self.assertEqual(message["session_id"], "session-1")
        self.assertEqual(message["payload"]["event"], "audio.delta")
        self.assertEqual(message["payload"]["sequence"], 3)

    async def test_onboarding_memory_tool_request_is_hidden(self):
        websocket = FakeWebSocket()
        with (
            patch("app.Task.task_manager.task_manager.register_tool_call"),
            onboarding_runtime("session-1"),
        ):
            await send_ws_message(
                websocket,
                type="client_tool_request",
                task_id="task-1",
                chat_id="chat-1",
                payload={
                    "tool": "memory_update",
                    "tool_call_id": "memory-1",
                    "input": {"name": "user", "target": "user"},
                },
            )

        message = websocket.messages[0]
        self.assertEqual(message["session_id"], "session-1")
        self.assertIs(message["payload"]["silent"], True)
        self.assertEqual(message["payload"]["input"]["hide"], "true")


if __name__ == "__main__":
    unittest.main()
