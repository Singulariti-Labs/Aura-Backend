import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from langchain_core.messages import HumanMessage, ToolMessage
from pydantic import ValidationError

from app.Agentic_Tools.browser_tools import BrowserTools
from app.LLM.memory import Memory
from app.LLM.model_bridge.anthropic import anthropic_message_formater
from app.LLM.model_bridge.common import canonical_tool_result
from app.LLM.model_bridge.gemini import gemini_message_formater
from app.LLM.model_bridge.openai import openai_message_formater
from app.Types.agent_types import BrowserVisionInput
from app.utils.tool_message_formatter import _create_tool_messages


VISION_RESULT = {
    "success": True,
    "question": "Is there a CAPTCHA?",
    "screenshot_path": r"C:\screenshots\browser.png",
    "image_data_url": "data:image/png;base64,PNG_BYTES",
    "mime_type": "image/png",
    "image_size_bytes": 248133,
    "native_vision": True,
    "annotations": [{"ref": "@e1", "label": 1}],
}


class BrowserVisionInputTests(unittest.TestCase):
    def test_question_is_required_and_annotate_defaults_false(self):
        inputs = BrowserVisionInput(question="What is visible?")

        self.assertEqual(inputs.question, "What is visible?")
        self.assertFalse(inputs.annotate)
        with self.assertRaises(ValidationError):
            BrowserVisionInput()


class BrowserVisionBridgeTests(unittest.IsolatedAsyncioTestCase):
    @patch("app.Agentic_Tools.browser_tools.update_memory")
    @patch("app.Agentic_Tools.browser_tools.create_agent_event", new_callable=AsyncMock)
    @patch("app.Agentic_Tools.browser_tools.send_ws_message", new_callable=AsyncMock)
    @patch("app.Agentic_Tools.browser_tools.task_manager")
    async def test_success_preserves_contract_and_sends_native_image_memory(
        self,
        task_manager_mock,
        send_ws_message_mock,
        create_agent_event_mock,
        update_memory_mock,
    ):
        state = SimpleNamespace(
            websocket=object(),
            dbpool=object(),
            get_next_seq=MagicMock(return_value=7),
        )
        task_manager_mock.get_state.return_value = state
        task_manager_mock.wait_for_tool_response = AsyncMock(
            return_value={
                "type": "client_tool_response",
                "payload": {
                    "tool": "browser_vision",
                    "tool_call_id": "call-vision",
                    "result": dict(VISION_RESULT),
                },
            }
        )
        browser = BrowserTools(
            llm=SimpleNamespace(),
            task_id="task-1",
            chat_id="chat-1",
            memory=Memory(),
        )

        result = await browser.browser_vision(
            question="Is there a CAPTCHA?",
            annotate=True,
            tool_call_id="call-vision",
        )

        self.assertEqual(result, VISION_RESULT)
        request = send_ws_message_mock.await_args.kwargs
        self.assertEqual(request["payload"]["tool"], "browser_vision")
        self.assertEqual(
            request["payload"]["input"],
            {"question": "Is there a CAPTCHA?", "annotate": True},
        )
        create_agent_event_mock.assert_awaited_once()

        tool_memory_call = update_memory_mock.call_args_list[-1].kwargs
        self.assertEqual(tool_memory_call["name"], "browser_vision")
        memory_content = tool_memory_call["content"]
        self.assertIsInstance(memory_content, list)
        self.assertNotIn("data:image/png;base64", memory_content[0]["text"])
        self.assertEqual(memory_content[-1]["type"], "image")
        self.assertEqual(memory_content[-1]["data"], "PNG_BYTES")

    @patch("app.Agentic_Tools.browser_tools.update_memory")
    @patch("app.Agentic_Tools.browser_tools.create_agent_event", new_callable=AsyncMock)
    @patch("app.Agentic_Tools.browser_tools.send_ws_message", new_callable=AsyncMock)
    @patch("app.Agentic_Tools.browser_tools.task_manager")
    async def test_failure_preserves_optional_path_and_warning(
        self,
        task_manager_mock,
        _send_ws_message_mock,
        _create_agent_event_mock,
        _update_memory_mock,
    ):
        task_manager_mock.get_state.return_value = SimpleNamespace(
            websocket=object(),
            dbpool=object(),
            get_next_seq=MagicMock(return_value=1),
        )
        task_manager_mock.wait_for_tool_response = AsyncMock(
            return_value={
                "type": "client_tool_response",
                "payload": {
                    "tool": "browser_vision",
                    "tool_call_id": "call-failure",
                    "result": {
                        "success": False,
                        "error": "Capture failed",
                        "screenshot_path": r"C:\screenshots\partial.png",
                        "fallback_warning": "Fallback browser used",
                    },
                },
            }
        )
        browser = BrowserTools(
            llm=SimpleNamespace(),
            task_id="task-2",
            chat_id="chat-2",
            memory=Memory(),
        )

        result = await browser.browser_vision(
            question="What failed?",
            tool_call_id="call-failure",
        )

        self.assertEqual(
            result,
            {
                "success": False,
                "error": "Capture failed",
                "screenshot_path": r"C:\screenshots\partial.png",
                "fallback_warning": "Fallback browser used",
            },
        )


class BrowserVisionMultimodalTests(unittest.TestCase):
    def test_canonical_result_promotes_data_url_out_of_text(self):
        result = canonical_tool_result(
            tool_call={
                "tool_call_id": "call-vision",
                "name": "browser_vision",
            },
            result=dict(VISION_RESULT),
        )

        text = "\n".join(
            block["text"]
            for block in result["content"]
            if block["type"] == "text"
        )
        image = next(
            block for block in result["content"] if block["type"] == "image"
        )
        self.assertNotIn("PNG_BYTES", text)
        self.assertIn("Screenshot attached as native image content", text)
        self.assertIn(
            "Analyze this browser screenshot and answer: Is there a CAPTCHA?",
            text,
        )
        self.assertEqual(
            image["image_url"],
            "data:image/png;base64,PNG_BYTES",
        )

        history = [
            {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_call",
                        "tool_call_id": "call-vision",
                        "name": "browser_vision",
                        "input": {"question": "Is there a CAPTCHA?"},
                    }
                ],
            },
            result,
        ]
        messages = openai_message_formater(history)
        tool_message = next(message for message in messages if message["role"] == "tool")
        rich_message = messages[-1]
        self.assertNotIn("PNG_BYTES", tool_message["content"])
        self.assertNotIn("Analyze this browser screenshot", tool_message["content"])
        self.assertEqual(rich_message["role"], "user")
        self.assertIn("Is there a CAPTCHA?", rich_message["content"][1]["text"])
        self.assertEqual(rich_message["content"][-1]["type"], "image_url")

    def test_anthropic_uses_separate_tool_result_and_vision_message(self):
        result = canonical_tool_result(
            tool_call={"tool_call_id": "call-vision", "name": "browser_vision"},
            result=dict(VISION_RESULT),
        )
        messages = anthropic_message_formater(
            [
                {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "tool_call",
                            "tool_call_id": "call-vision",
                            "name": "browser_vision",
                            "input": {"question": "Is there a CAPTCHA?"},
                        }
                    ],
                },
                result,
            ]
        )

        tool_result = messages[-2]["content"][0]
        vision_message = messages[-1]
        self.assertEqual(tool_result["type"], "tool_result")
        self.assertEqual(len(tool_result["content"]), 1)
        self.assertEqual(tool_result["content"][0]["type"], "text")
        self.assertEqual(vision_message["role"], "user")
        self.assertIn("Is there a CAPTCHA?", vision_message["content"][0]["text"])
        self.assertEqual(vision_message["content"][1]["type"], "image")
        self.assertEqual(vision_message["content"][1]["source"]["data"], "PNG_BYTES")

    def test_gemini_uses_separate_function_response_and_vision_content(self):
        result = canonical_tool_result(
            tool_call={"tool_call_id": "call-vision", "name": "browser_vision"},
            result=dict(VISION_RESULT),
        )
        messages = gemini_message_formater(
            [
                {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "tool_call",
                            "tool_call_id": "call-vision",
                            "name": "browser_vision",
                            "input": {"question": "Is there a CAPTCHA?"},
                        }
                    ],
                },
                result,
            ]
        )

        function_message = messages[-2]
        vision_message = messages[-1]
        function_response = function_message["parts"][0]["function_response"]
        self.assertNotIn("parts", function_response)
        self.assertEqual(vision_message["role"], "user")
        self.assertIn("Is there a CAPTCHA?", vision_message["parts"][0]["text"])
        self.assertEqual(
            vision_message["parts"][1]["inline_data"]["data"],
            "PNG_BYTES",
        )

    def test_legacy_formatter_uses_two_messages_for_every_provider(self):
        action = SimpleNamespace(
            tool="browser_vision",
            tool_call_id="call-vision",
            id="call-vision",
        )

        for provider in ("openai", "anthropic", "google"):
            with self.subTest(provider=provider):
                messages = _create_tool_messages(
                    action,
                    dict(VISION_RESULT),
                    provider,
                )

                self.assertEqual(len(messages), 2)
                self.assertIsInstance(messages[0], ToolMessage)
                self.assertNotIn("PNG_BYTES", messages[0].content)
                self.assertIsInstance(messages[1], HumanMessage)
                self.assertIn("Is there a CAPTCHA?", messages[1].content[0]["text"])
                self.assertEqual(messages[1].content[-1]["type"], "image")
                self.assertEqual(messages[1].content[-1]["data"], "PNG_BYTES")


if __name__ == "__main__":
    unittest.main()