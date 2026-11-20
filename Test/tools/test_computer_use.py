import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from langchain_core.messages import HumanMessage, ToolMessage

from app.Agentic_Tools.computer_use_tool import ComputerUseToolExecutor
from app.LLM.memory import Memory
from app.LLM.model_bridge.common import canonical_tool_result
from app.utils.tool_message_formatter import _create_tool_messages


CAPTURE_CLIENT_RESULT = {
    "ok": True,
    "action": "capture",
    "mode": "som",
    "screenshot_path": r"C:\screenshots\capture.png",
    "image": {
        "mime_type": "image/png",
        "data": "PNG_BYTES",
        "width": 1280,
        "height": 720,
    },
    "elements": [
        {
            "element": 1,
            "role": "Button",
            "label": "Save",
        }
    ],
    "total_elements": 1,
    "summary": "Captured one element.",
}


class ComputerUseExecutorTests(unittest.IsolatedAsyncioTestCase):
    @patch("app.Agentic_Tools.computer_use_tool.update_memory")
    @patch(
        "app.Agentic_Tools.computer_use_tool.create_agent_event",
        new_callable=AsyncMock,
    )
    @patch(
        "app.Agentic_Tools.computer_use_tool.send_ws_message",
        new_callable=AsyncMock,
    )
    @patch("app.Agentic_Tools.computer_use_tool.task_manager")
    async def test_capture_normalizes_success_and_separates_image_memory(
        self,
        task_manager_mock,
        send_ws_message_mock,
        create_agent_event_mock,
        update_memory_mock,
    ):
        task_manager_mock.get_state.return_value = SimpleNamespace(
            websocket=object(),
            dbpool=object(),
            get_next_seq=MagicMock(return_value=3),
        )
        task_manager_mock.wait_for_tool_response = AsyncMock(
            return_value={
                "type": "client_tool_response",
                "payload": {
                    "tool": "computer_use",
                    "tool_call_id": "call-capture",
                    "result": CAPTURE_CLIENT_RESULT,
                },
            }
        )
        send_ws_message_mock.return_value = "call-capture"
        executor = ComputerUseToolExecutor(
            llm=SimpleNamespace(),
            task_id="task-1",
            chat_id="chat-1",
            memory=Memory(),
        )

        result = await executor.capture(
            {"action": "capture", "mode": "som"},
            "call-capture",
        )

        self.assertTrue(result["success"])
        self.assertNotIn("ok", result)
        self.assertEqual(result["image"]["data"], "PNG_BYTES")
        self.assertEqual(
            send_ws_message_mock.await_args.kwargs["payload"]["input"],
            {"action": "capture", "mode": "som"},
        )
        create_agent_event_mock.assert_awaited_once()
        memory_content = update_memory_mock.call_args_list[-1].kwargs["content"]
        self.assertIsInstance(memory_content, list)
        self.assertNotIn("PNG_BYTES", memory_content[0]["text"])
        self.assertEqual(memory_content[-1]["type"], "image")
        self.assertEqual(memory_content[-1]["data"], "PNG_BYTES")

    def test_client_failure_uses_aura_success_field(self):
        executor = object.__new__(ComputerUseToolExecutor)
        result = executor._read_client_result(
            {
                "type": "client_tool_response",
                "payload": {
                    "tool": "computer_use",
                    "result": {
                        "ok": False,
                        "action": "capture",
                        "error": {
                            "code": "target_not_found",
                            "message": "Could not find target.",
                        },
                    },
                },
            },
            "capture",
        )

        self.assertFalse(result["success"])
        self.assertNotIn("ok", result)
        self.assertEqual(result["error"]["code"], "target_not_found")

    def test_executor_exposes_one_method_for_every_action(self):
        expected_methods = {
            "capture",
            "click",
            "double_click",
            "right_click",
            "middle_click",
            "drag",
            "scroll",
            "type_text",
            "key",
            "set_value",
            "wait",
            "list_apps",
            "list_windows",
            "focus_app",
        }
        for method_name in expected_methods:
            self.assertTrue(callable(getattr(ComputerUseToolExecutor, method_name)))

    def test_each_action_response_is_normalized_to_success(self):
        executor = object.__new__(ComputerUseToolExecutor)
        results_by_action = {
            "click": {
                "clicked": {"element": 4, "button": "left"},
                "effect": "confirmed",
            },
            "double_click": {
                "clicked": {"element": 5, "button": "left"},
            },
            "right_click": {
                "clicked": {"element": 9, "button": "right"},
            },
            "middle_click": {
                "clicked": {"coordinate": [512, 384], "button": "middle"},
            },
            "drag": {
                "dragged": {
                    "from_coordinate": [100, 200],
                    "to_coordinate": [500, 200],
                    "button": "left",
                },
            },
            "scroll": {"scrolled": {"direction": "down", "amount": 3}},
            "type": {"typed": {"element": 3, "character_count": 11}},
            "key": {"pressed": {"keys": "ctrl+s", "modifiers": ["ctrl"]}},
            "set_value": {"set": {"element": 8, "value": "Blue"}},
            "wait": {"seconds": 2, "summary": "Waited 2 seconds."},
            "list_apps": {"apps": [{"name": "Notepad", "pid": 2345}]},
            "list_windows": {
                "windows": [{"window_id": 9981, "pid": 1234}]
            },
            "focus_app": {"focused": {"app": "Chrome", "raised": False}},
        }

        for action, action_result in results_by_action.items():
            with self.subTest(action=action):
                result = executor._read_client_result(
                    {
                        "type": "client_tool_response",
                        "payload": {
                            "tool": "computer_use",
                            "result": {
                                "ok": True,
                                "action": action,
                                **action_result,
                            },
                        },
                    },
                    action,
                )
                self.assertTrue(result["success"])
                self.assertNotIn("ok", result)

    def test_capture_after_is_normalized_for_click(self):
        executor = object.__new__(ComputerUseToolExecutor)
        result = executor._read_client_result(
            {
                "type": "client_tool_response",
                "payload": {
                    "tool": "computer_use",
                    "result": {
                        "ok": True,
                        "action": "click",
                        "clicked": {"element": 4, "button": "left"},
                        "capture_after": dict(CAPTURE_CLIENT_RESULT),
                    },
                },
            },
            "click",
        )

        self.assertTrue(result["success"])
        self.assertTrue(result["capture_after"]["success"])
        self.assertNotIn("ok", result["capture_after"])


class ComputerUseMultimodalTests(unittest.TestCase):
    def setUp(self):
        self.server_result = {
            "success": True,
            **{
                key: value
                for key, value in CAPTURE_CLIENT_RESULT.items()
                if key != "ok"
            },
        }

    def test_langchain_formatter_separates_capture_image_from_json(self):
        messages = _create_tool_messages(
            SimpleNamespace(
                tool="computer_use",
                tool_call_id="call-capture",
                message_log=[],
            ),
            self.server_result,
            "openai",
        )

        self.assertEqual(len(messages), 2)
        self.assertIsInstance(messages[0], ToolMessage)
        self.assertIsInstance(messages[1], HumanMessage)
        self.assertNotIn("PNG_BYTES", messages[0].content)
        metadata = json.loads(messages[0].content)
        self.assertTrue(metadata["success"])
        self.assertEqual(metadata["image"]["width"], 1280)
        self.assertEqual(
            metadata["image"]["note"],
            "The captured image is attached in the following user-role message.",
        )
        self.assertNotIn("note", metadata)
        image_block = messages[1].content[-1]
        self.assertEqual(image_block["type"], "image")
        self.assertEqual(image_block["mime_type"], "image/png")
        self.assertEqual(image_block["data"], "PNG_BYTES")

    def test_capture_without_inline_image_remains_json_only(self):
        result = {
            "success": True,
            "action": "capture",
            "mode": "ax",
            "elements": [{"element": 1, "label": "Save"}],
        }
        messages = _create_tool_messages(
            SimpleNamespace(tool="computer_use", tool_call_id="call-ax"),
            result,
            "openai",
        )

        self.assertEqual(len(messages), 1)
        self.assertIsInstance(messages[0], ToolMessage)
        self.assertEqual(json.loads(messages[0].content), result)

    def test_non_capture_action_is_sent_to_llm_as_json_only(self):
        result = {
            "success": True,
            "action": "click",
            "clicked": {"element": 4, "button": "left"},
            "path": "ax",
            "verified": True,
            "effect": "confirmed",
            "summary": "Clicked element 4.",
        }
        messages = _create_tool_messages(
            SimpleNamespace(tool="computer_use", tool_call_id="call-click"),
            result,
            "openai",
        )

        self.assertEqual(len(messages), 1)
        self.assertIsInstance(messages[0], ToolMessage)
        self.assertEqual(json.loads(messages[0].content), result)

    def test_native_bridge_promotes_capture_data_to_image_block(self):
        result = canonical_tool_result(
            tool_call={
                "tool_call_id": "call-capture",
                "name": "computer_use",
            },
            result=self.server_result,
        )

        text = next(
            block["text"]
            for block in result["content"]
            if block["type"] == "text"
        )
        image = next(
            block
            for block in result["content"]
            if block["type"] == "image"
        )
        self.assertNotIn("PNG_BYTES", text)
        self.assertEqual(
            image["image_url"],
            "data:image/png;base64,PNG_BYTES",
        )

    def test_native_bridge_promotes_capture_after_data_to_image_block(self):
        click_result = {
            "success": True,
            "action": "click",
            "clicked": {"element": 4, "button": "left"},
            "capture_after": self.server_result,
        }
        result = canonical_tool_result(
            tool_call={"tool_call_id": "call-click", "name": "computer_use"},
            result=click_result,
        )

        text = next(
            block["text"]
            for block in result["content"]
            if block["type"] == "text"
        )
        image = next(
            block
            for block in result["content"]
            if block["type"] == "image"
        )
        self.assertNotIn("PNG_BYTES", text)
        self.assertEqual(
            image["image_url"],
            "data:image/png;base64,PNG_BYTES",
        )

    def test_capture_after_image_is_also_separated_from_json(self):
        click_result = {
            "success": True,
            "action": "click",
            "clicked": {"element": 4, "button": "left"},
            "capture_after": self.server_result,
        }
        messages = _create_tool_messages(
            SimpleNamespace(tool="computer_use", tool_call_id="call-click"),
            click_result,
            "openai",
        )

        self.assertEqual(len(messages), 2)
        self.assertNotIn("PNG_BYTES", messages[0].content)
        metadata = json.loads(messages[0].content)
        self.assertNotIn("data", metadata["capture_after"]["image"])
        self.assertEqual(
            metadata["capture_after"]["image"]["note"],
            "The captured image is attached in the following user-role message.",
        )
        self.assertNotIn("note", metadata)
        self.assertEqual(messages[1].content[-1]["data"], "PNG_BYTES")


if __name__ == "__main__":
    unittest.main()
