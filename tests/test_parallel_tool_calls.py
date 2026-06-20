import unittest
from types import SimpleNamespace

from pydantic import BaseModel

from app.LLM.memory import Memory
from app.Tools.base_tool import (
    BaseTool,
    get_current_tool_call_id,
    get_current_tool_input,
)
from app.handler import AgentCallbackHandler
from app.helper import (
    _find_tool_call,
    _get_tool_call_id,
    _sent_aura_thinking_batches,
    _should_send_aura_thinking,
)
from app.utils.tool_message_formatter import format_multimodal_tool_messages


class ParallelToolCallHandlerTests(unittest.TestCase):
    def test_agent_action_saves_full_tool_call_batch_from_message_log(self):
        memory = Memory()
        handler = AgentCallbackHandler(memory=memory)
        handler._print_action = lambda action: None
        ai_message = SimpleNamespace(
            content="",
            additional_kwargs={},
            response_metadata={},
            usage_metadata=None,
            tool_calls=[
                {
                    "id": "toolu_first",
                    "name": "web_search",
                    "args": {"query": "first query", "num_results": 5},
                },
                {
                    "id": "toolu_second",
                    "name": "web_search",
                    "args": {"query": "second query", "num_results": 5},
                },
            ],
        )
        first_action = SimpleNamespace(
            tool="web_search",
            tool_input={"query": "first query", "num_results": 5},
            tool_call_id="toolu_first",
            log="Invoking first web_search",
            message_log=[ai_message],
        )
        second_action = SimpleNamespace(
            tool="web_search",
            tool_input={"query": "second query", "num_results": 5},
            tool_call_id="toolu_second",
            log="Invoking second web_search",
            message_log=[ai_message],
        )

        handler.on_agent_action(first_action)
        handler.on_agent_action(second_action)

        self.assertEqual(len(memory.messages), 1)
        assistant_message = memory.messages[0].to_dict()
        tool_calls = [
            block
            for block in assistant_message["content"]
            if block.get("type") == "tool_call"
        ]

        self.assertEqual(
            [tool_call["tool_call_id"] for tool_call in tool_calls],
            ["toolu_first", "toolu_second"],
        )
        self.assertEqual(
            [tool_call["input"]["query"] for tool_call in tool_calls],
            ["first query", "second query"],
        )


class EchoInput(BaseModel):
    value: str


class EchoTool(BaseTool):
    def __init__(self):
        super().__init__(
            name="echo",
            description="Echoes the active tool call id.",
            args_schema=EchoInput,
        )

    async def run(self, inputs: EchoInput):
        return {
            "tool_call_id": get_current_tool_call_id() or "missing",
            "tool_input": get_current_tool_input(),
        }


class BaseToolRuntimeIdTests(unittest.IsolatedAsyncioTestCase):
    async def test_runtime_tool_call_id_is_forwarded_without_schema_injection(self):
        tool = EchoTool().to_tool()

        direct_result = await tool.ainvoke({"value": "hello"})
        self.assertEqual(direct_result["tool_call_id"], "missing")
        self.assertEqual(direct_result["tool_input"], {"value": "hello"})

        tool_call_result = await tool.ainvoke(
            {
                "type": "tool_call",
                "name": "echo",
                "id": "call_exact",
                "args": {"value": "hello"},
            }
        )

        self.assertEqual(tool_call_result.tool_call_id, "call_exact")
        self.assertIn("call_exact", tool_call_result.content)
        self.assertIn('"value": "hello"', tool_call_result.content)


class MultimodalToolMessageFormatterTests(unittest.TestCase):
    def test_screenshot_observation_becomes_anthropic_safe_image_content(self):
        action = SimpleNamespace(
            tool="screenshot",
            tool_call_id="toolu_screenshot",
            message_log=[],
            log="",
        )
        observation = {
            "success": True,
            "output": "Screenshot captured successfully.",
            "image_base64": "abc123",
            "mime_type": "image/png",
        }

        messages = format_multimodal_tool_messages(
            [(action, observation)],
            provider="anthropic",
        )

        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0].tool_call_id, "toolu_screenshot")
        self.assertIsInstance(messages[0].content, list)
        self.assertEqual(messages[0].content[1]["type"], "image")
        self.assertEqual(messages[0].content[1]["source_type"], "base64")
        self.assertEqual(messages[0].content[1]["data"], "abc123")

    def test_screenshot_observation_uses_user_image_for_openai(self):
        action = SimpleNamespace(
            tool="screenshot",
            tool_call_id="call_screenshot",
            message_log=[],
            log="",
        )
        observation = {
            "success": True,
            "output": "Screenshot captured successfully.",
            "image_base64": "abc123",
            "mime_type": "image/png",
        }

        messages = format_multimodal_tool_messages(
            [(action, observation)],
            provider="openai",
        )

        self.assertEqual(len(messages), 2)
        self.assertEqual(messages[0].content, "Screenshot captured successfully.")
        self.assertNotIn("abc123", messages[0].content)
        self.assertEqual(messages[1].content[1]["type"], "image")
        self.assertEqual(messages[1].content[1]["data"], "abc123")

    def test_regular_tool_observation_stays_text_json(self):
        action = SimpleNamespace(
            tool="web_search",
            tool_call_id="call_search",
            message_log=[],
            log="",
        )

        messages = format_multimodal_tool_messages(
            [(action, {"success": True, "output": "done"})],
            provider="anthropic",
        )

        self.assertEqual(len(messages), 1)
        self.assertIsInstance(messages[0].content, str)
        self.assertIn('"output": "done"', messages[0].content)


class HelperToolCallMatchingTests(unittest.TestCase):
    def test_exact_tool_call_id_wins_over_duplicate_tool_name(self):
        tool_calls = [
            {
                "type": "tool_call",
                "id": "toolu_first",
                "name": "web_search",
                "input": {"query": "first query"},
            },
            {
                "type": "tool_call",
                "id": "toolu_second",
                "name": "web_search",
                "input": {"query": "second query"},
            },
        ]

        selected = _find_tool_call(
            tool_calls,
            tool_call_id="toolu_second",
            tool_name="web_search",
        )

        self.assertEqual(_get_tool_call_id(selected), "toolu_second")

    def test_tool_input_selects_correct_duplicate_tool_name_without_id(self):
        tool_calls = [
            {
                "type": "tool_call",
                "id": "toolu_first",
                "name": "web_search",
                "input": {"query": "first query", "num_results": 5},
            },
            {
                "type": "tool_call",
                "id": "toolu_second",
                "name": "web_search",
                "input": {"query": "second query", "num_results": 5},
            },
        ]

        selected = _find_tool_call(
            tool_calls,
            tool_name="web_search",
            tool_input={"query": "second query", "num_results": 5},
        )

        self.assertEqual(_get_tool_call_id(selected), "toolu_second")

    def test_parallel_batch_aura_thinking_is_sent_only_once(self):
        _sent_aura_thinking_batches.clear()
        tool_calls = [
            {"id": "toolu_first", "name": "web_search"},
            {"id": "toolu_second", "name": "web_search"},
            {"id": "toolu_third", "name": "web_search"},
        ]

        first = _should_send_aura_thinking(
            task_id="task_1",
            chat_id="chat_1",
            message_type="aura_thinking",
            tool_calls=tool_calls,
        )
        second = _should_send_aura_thinking(
            task_id="task_1",
            chat_id="chat_1",
            message_type="aura_thinking",
            tool_calls=tool_calls,
        )

        self.assertTrue(first)
        self.assertFalse(second)


if __name__ == "__main__":
    unittest.main()
