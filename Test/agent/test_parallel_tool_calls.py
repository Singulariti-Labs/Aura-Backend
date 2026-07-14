import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from pydantic import BaseModel

from app.LLM.llm_factory import LLMFactory
from app.LLM.memory import Memory
from app.LLM.model_token_limits import MAX_OUTPUT_TOKEN_LIMIT_MESSAGE
from app.Types.agent_types import LLMConfig
from app.Tools.base_tool import (
    BaseTool,
    get_current_tool_call_id,
    get_current_tool_input,
)
from app.handler import AgentCallbackHandler, MaxOutputTokenLimitError
from app.Task.task_manager import TaskControlState, task_manager
from app.Agentic_Tools.file_editor import FileEditor
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

    async def test_parallel_same_tool_calls_keep_their_own_runtime_ids(self):
        """Each invocation of the same tool receives its original model ID."""
        tool = EchoTool().to_tool()

        first_result, second_result = await asyncio.gather(
            tool.ainvoke(
                {
                    "type": "tool_call",
                    "name": "echo",
                    "id": "call_first",
                    "args": {"value": "first"},
                }
            ),
            tool.ainvoke(
                {
                    "type": "tool_call",
                    "name": "echo",
                    "id": "call_second",
                    "args": {"value": "second"},
                }
            ),
        )

        self.assertEqual(first_result.tool_call_id, "call_first")
        self.assertEqual(second_result.tool_call_id, "call_second")
        self.assertIn("call_first", first_result.content)
        self.assertIn("call_second", second_result.content)
        self.assertIn('"value": "first"', first_result.content)
        self.assertIn('"value": "second"', second_result.content)


class ToolResponseRoutingTests(unittest.IsolatedAsyncioTestCase):
    """Verify parallel client responses are correlated by tool_call_id."""

    async def test_same_tool_responses_can_arrive_in_reverse_order(self):
        """Reverse completion order must not swap same-tool results."""
        state = TaskControlState(websocket=None, dbpool=None)

        first_waiter = asyncio.create_task(
            state.wait_for_tool_response("toolu_first")
        )
        second_waiter = asyncio.create_task(
            state.wait_for_tool_response("toolu_second")
        )
        await asyncio.sleep(0)

        second_response = {
            "type": "client_tool_response",
            "payload": {
                "tool": "read_file",
                "tool_call_id": "toolu_second",
                "result": {"success": True, "content": "second file"},
            },
        }
        first_response = {
            "type": "client_tool_response",
            "payload": {
                "tool": "read_file",
                "tool_call_id": "toolu_first",
                "result": {"success": True, "content": "first file"},
            },
        }

        state.route_input(second_response)
        state.route_input(first_response)
        first_result, second_result = await asyncio.gather(
            first_waiter,
            second_waiter,
        )

        self.assertEqual(
            first_result["payload"]["tool_call_id"],
            "toolu_first",
        )
        self.assertEqual(
            first_result["payload"]["result"]["content"],
            "first file",
        )
        self.assertEqual(
            second_result["payload"]["tool_call_id"],
            "toolu_second",
        )
        self.assertEqual(
            second_result["payload"]["result"]["content"],
            "second file",
        )
        self.assertEqual(state.tool_response_queues, {})

    async def test_response_is_buffered_when_it_arrives_before_waiter(self):
        """A fast client response remains available for its exact call."""
        state = TaskControlState(websocket=None, dbpool=None)
        response = {
            "type": "client_tool_response",
            "payload": {
                "tool": "read_file",
                "tool_call_id": "toolu_fast",
                "result": {"success": True, "content": "ready"},
            },
        }

        state.route_input(response)
        received = await state.wait_for_tool_response("toolu_fast")

        self.assertIs(received, response)
        self.assertEqual(state.tool_response_queues, {})

    async def test_parallel_different_tools_keep_their_existing_ids(self):
        """ID routing remains correct when parallel tool names are different."""
        state = TaskControlState(websocket=None, dbpool=None)
        read_waiter = asyncio.create_task(
            state.wait_for_tool_response("toolu_read")
        )
        list_waiter = asyncio.create_task(
            state.wait_for_tool_response("toolu_list")
        )

        state.route_input(
            {
                "type": "client_tool_response",
                "payload": {
                    "tool": "ls",
                    "tool_call_id": "toolu_list",
                    "result": {"success": True},
                },
            }
        )
        state.route_input(
            {
                "type": "client_tool_response",
                "payload": {
                    "tool": "read_file",
                    "tool_call_id": "toolu_read",
                    "result": {"success": True},
                },
            }
        )
        read_result, list_result = await asyncio.gather(
            read_waiter,
            list_waiter,
        )

        self.assertEqual(read_result["payload"]["tool_call_id"], "toolu_read")
        self.assertEqual(list_result["payload"]["tool_call_id"], "toolu_list")

    async def test_waiting_without_tool_call_id_is_rejected(self):
        """An empty ID is rejected before an invalid tool result is stored."""
        state = TaskControlState(websocket=None, dbpool=None)

        with self.assertRaisesRegex(ValueError, "tool_call_id is required"):
            await state.wait_for_tool_response("")

    async def test_general_user_input_keeps_legacy_queue_behavior(self):
        """Non-tool input must remain available to existing user-input flows."""
        state = TaskControlState(websocket=None, dbpool=None)
        user_input = {
            "type": "user_input",
            "data": {"answer": "continue"},
        }

        state.route_input(user_input)
        received = await state.input_queue.get()

        self.assertIs(received, user_input)


class FileEditorParallelResponseTests(unittest.IsolatedAsyncioTestCase):
    """Exercise the complete WebSocket round trip for duplicate tool names."""

    async def test_parallel_read_file_calls_keep_ids_and_reverse_order_results(self):
        """Each read_file result and event stays attached to its original ID."""
        task_id = "parallel-read-file-test"
        task_manager.create_task(task_id, websocket=None, pool=None)
        editor = FileEditor(
            llm=None,
            task_id=task_id,
            chat_id="chat-test",
            memory=None,
        )

        first_response = {
            "type": "client_tool_response",
            "payload": {
                "tool": "read_file",
                "tool_call_id": "toolu_first",
                "result": {"success": True, "content": "first file"},
            },
        }
        second_response = {
            "type": "client_tool_response",
            "payload": {
                "tool": "read_file",
                "tool_call_id": "toolu_second",
                "result": {"success": True, "content": "second file"},
            },
        }

        try:
            with (
                patch(
                    "app.Agentic_Tools.file_editor.send_ws_message",
                    new_callable=AsyncMock,
                ) as send_message,
                patch(
                    "app.Agentic_Tools.file_editor.create_agent_event",
                    new_callable=AsyncMock,
                ) as create_event,
                patch("app.Agentic_Tools.file_editor.update_memory"),
            ):
                first_call = asyncio.create_task(
                    editor.read_file(
                        filePath="first.txt",
                        tool_call_id="toolu_first",
                    )
                )
                second_call = asyncio.create_task(
                    editor.read_file(
                        filePath="second.txt",
                        tool_call_id="toolu_second",
                    )
                )
                await asyncio.sleep(0)

                # Deliberately complete the second invocation first.
                task_manager.provide_input(task_id, second_response)
                task_manager.provide_input(task_id, first_response)
                first_result, second_result = await asyncio.gather(
                    first_call,
                    second_call,
                )

                self.assertEqual(first_result["output"], "first file")
                self.assertEqual(second_result["output"], "second file")

                request_ids = {
                    call.kwargs["payload"]["tool_call_id"]
                    for call in send_message.await_args_list
                }
                event_ids = {
                    call.kwargs["payload"]["tool_call_id"]
                    for call in create_event.await_args_list
                }
                self.assertEqual(request_ids, {"toolu_first", "toolu_second"})
                self.assertEqual(event_ids, {"toolu_first", "toolu_second"})
        finally:
            task_manager.remove_task(task_id)


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


class ModelTokenLimitConfigTests(unittest.TestCase):
    def test_model_specific_limits_are_applied_to_chat_wrappers(self):
        anthropic_llm = LLMFactory.create_llm(
            LLMConfig(
                provider="anthropic",
                model_name="claude-opus-4-8",
                api_key="test-key",
            )
        )
        self.assertEqual(anthropic_llm.model_dump()["max_tokens"], 28000)

        openai_llm = LLMFactory.create_llm(
            LLMConfig(
                provider="openai",
                model_name="gpt-4.1",
                api_key="test-key",
            )
        )
        self.assertEqual(openai_llm.model_dump()["max_tokens"], 16384)

        google_llm = LLMFactory.create_llm(
            LLMConfig(
                provider="google",
                model_name="gemini-3-flash-preview",
                api_key="test-key",
            )
        )
        self.assertEqual(google_llm.model_dump()["max_output_tokens"], 28000)

        open_router_llm = LLMFactory.create_llm(
            LLMConfig(
                provider="open_router",
                model_name="z-ai",
                api_key="test-key",
            )
        )
        dump = open_router_llm.model_dump()
        self.assertEqual(dump["model_name"], "z-ai/glm-4.5-air:free")
        self.assertEqual(dump["max_tokens"], 8192)


class MaxTokensStopGuardTests(unittest.TestCase):
    def test_max_tokens_stop_blocks_tool_action_before_memory_save(self):
        memory = Memory()
        handler = AgentCallbackHandler(memory=memory)
        handler.latest_llm_details = {
            "provider": "anthropic",
            "model_name": "claude-opus-4-8",
            "finish_reason": "max_tokens",
        }
        handler.latest_llm_usage = {
            "input": 100,
            "output": 16000,
            "total_tokens": 16100,
            "cost": 0,
        }
        handler._print_action = lambda action: self.fail("tool action should not print")
        action = SimpleNamespace(
            tool="create_file",
            tool_input={"path": "index.html"},
            tool_call_id="toolu_partial",
            log="Invoking create_file with partial args",
            message_log=[],
        )

        with self.assertRaises(MaxOutputTokenLimitError) as ctx:
            handler.on_agent_action(action)

        self.assertEqual(memory.messages, [])
        self.assertEqual(ctx.exception.error_body["stop_reason"], "max_tokens")
        self.assertEqual(
            ctx.exception.error_body["message"],
            MAX_OUTPUT_TOKEN_LIMIT_MESSAGE,
        )

    def test_max_tokens_stop_from_message_log_blocks_without_usage(self):
        memory = Memory()
        handler = AgentCallbackHandler(memory=memory)
        handler._print_action = lambda action: self.fail("tool action should not print")
        ai_message = SimpleNamespace(
            content="",
            additional_kwargs={},
            response_metadata={
                "stop_reason": "max_tokens",
                "model": "claude-opus-4-8",
            },
            usage_metadata=None,
            tool_calls=[],
        )
        action = SimpleNamespace(
            tool="create_file",
            tool_input={"path": "index.html"},
            tool_call_id="toolu_partial",
            log="Invoking create_file with partial args",
            message_log=[ai_message],
        )

        with self.assertRaises(MaxOutputTokenLimitError):
            handler.on_agent_action(action)

        self.assertEqual(memory.messages, [])

    def test_openai_length_finish_reason_is_treated_as_output_limit(self):
        memory = Memory()
        handler = AgentCallbackHandler(memory=memory)
        handler.latest_llm_details = {
            "provider": "openai",
            "model_name": "gpt-4.1",
            "finish_reason": "length",
        }

        with self.assertRaises(MaxOutputTokenLimitError) as ctx:
            handler.on_agent_finish(SimpleNamespace(return_values={"output": "partial"}))

        self.assertEqual(memory.messages, [])
        self.assertEqual(ctx.exception.error_body["stop_reason"], "max_tokens")


if __name__ == "__main__":
    unittest.main()
