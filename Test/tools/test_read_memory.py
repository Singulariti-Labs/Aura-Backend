import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from pydantic import ValidationError

from app.Agentic_Tools.memory_tools import MemoryTools
from app.LLM.model_bridge.common import canonical_tool_result
from app.Tools.read_memory import READ_MEMORY_TOOL_DESCRIPTION, ReadMemoryTool
from app.Types.agent_types import ReadMemoryToolInput


MEMORY_FILE_CONTENT = (
    "---\n"
    "name: current-project\n"
    'description: "Project memory"\n'
    "version: 1\n"
    "---\n\n"
    "- [fact-001] Uses React and TypeScript.\n"
)


class ReadMemoryInputTests(unittest.TestCase):
    """Verify the model-facing read schema matches the supplied contract."""

    def test_name_and_target_are_required(self):
        inputs = ReadMemoryToolInput(name="current-project", target="memory")

        self.assertEqual(inputs.name, "current-project")
        self.assertEqual(inputs.target, "memory")
        with self.assertRaises(ValidationError):
            ReadMemoryToolInput(name="current-project")
        with self.assertRaises(ValidationError):
            ReadMemoryToolInput(target="user")

    def test_target_enum_and_additional_properties_are_rejected(self):
        with self.assertRaises(ValidationError):
            ReadMemoryToolInput(name="preference", target="project")
        with self.assertRaises(ValidationError):
            ReadMemoryToolInput(
                name="preference",
                target="user",
                file_extension=".md",
            )

    def test_json_schema_and_tool_description_match_specification(self):
        schema = ReadMemoryToolInput.model_json_schema()

        self.assertEqual(schema["required"], ["name", "target"])
        self.assertFalse(schema["additionalProperties"])
        self.assertEqual(schema["properties"]["target"]["enum"], ["user", "memory"])
        self.assertIn("Read a specific named memory file", READ_MEMORY_TOOL_DESCRIPTION)
        self.assertIn("description", READ_MEMORY_TOOL_DESCRIPTION)
        self.assertIn("Do not call this tool for every", READ_MEMORY_TOOL_DESCRIPTION)


class ReadMemoryBridgeTests(unittest.IsolatedAsyncioTestCase):
    """Verify exact request forwarding and JSON-string result preservation."""

    async def _run_bridge(self, result):
        state = SimpleNamespace(
            websocket=object(),
            dbpool=object(),
            get_next_seq=MagicMock(return_value=31),
        )
        response = {
            "type": "client_tool_response",
            "chat_id": "chat-123",
            "task_id": "task-123",
            "payload": {
                "tool": "read_memory",
                "tool_call_id": "call-read",
                "result": result,
            },
        }

        with (
            patch("app.Agentic_Tools.memory_tools.task_manager") as manager,
            patch(
                "app.Agentic_Tools.memory_tools.send_ws_message",
                new_callable=AsyncMock,
            ) as send_message,
            patch(
                "app.Agentic_Tools.memory_tools.create_agent_event",
                new_callable=AsyncMock,
            ) as create_event,
            patch("app.Agentic_Tools.memory_tools.update_memory") as update_memory,
        ):
            manager.get_state.return_value = state
            manager.wait_for_tool_response = AsyncMock(return_value=response)
            bridge = MemoryTools(
                llm=SimpleNamespace(),
                task_id="task-123",
                chat_id="chat-123",
                memory=SimpleNamespace(),
            )
            returned = await bridge.read_memory(
                name="current-project",
                target="memory",
                tool_call_id="call-read",
            )

            return {
                "returned": returned,
                "request": send_message.await_args.kwargs,
                "event": create_event.await_args.kwargs,
                "memory_calls": update_memory.call_args_list,
                "wait": manager.wait_for_tool_response,
            }

    async def test_success_returns_complete_result_as_json_string(self):
        client_result = {
            "success": True,
            "content": MEMORY_FILE_CONTENT,
        }

        observed = await self._run_bridge(client_result)

        self.assertIsInstance(observed["returned"], str)
        self.assertEqual(json.loads(observed["returned"]), client_result)
        self.assertEqual(
            json.loads(observed["returned"])["content"],
            MEMORY_FILE_CONTENT,
        )
        request_payload = observed["request"]["payload"]
        self.assertEqual(
            request_payload,
            {
                "tool": "read_memory",
                "tool_call_id": "call-read",
                "input": {
                    "name": "current-project",
                    "target": "memory",
                },
                "coming_from": "read_memory_tool_func/server",
            },
        )
        self.assertEqual(
            observed["event"]["payload"],
            {
                "tool_call_id": "call-read",
                "input": request_payload["input"],
            },
        )
        observed["wait"].assert_awaited_once_with("task-123", "call-read")
        tool_memory = observed["memory_calls"][-1].kwargs
        self.assertEqual(tool_memory["name"], "read_memory")
        self.assertEqual(tool_memory["content"], observed["returned"])

        canonical = canonical_tool_result(
            tool_call={"tool_call_id": "call-read", "name": "read_memory"},
            result=observed["returned"],
        )
        self.assertFalse(canonical["is_error"])
        self.assertEqual(json.loads(canonical["content"][0]["text"]), client_result)

    async def test_failure_returns_complete_result_as_json_string(self):
        client_result = {
            "success": False,
            "error": "Memory file current-project does not exist.",
        }

        observed = await self._run_bridge(client_result)

        self.assertIsInstance(observed["returned"], str)
        self.assertEqual(json.loads(observed["returned"]), client_result)
        canonical = canonical_tool_result(
            tool_call={"tool_call_id": "call-read", "name": "read_memory"},
            result=observed["returned"],
        )
        self.assertTrue(canonical["is_error"])

    async def test_invalid_client_result_returns_json_error(self):
        observed = await self._run_bridge(None)

        result = json.loads(observed["returned"])
        self.assertFalse(result["success"])
        self.assertIn("Invalid read_memory result", result["error"])


class ReadMemoryWrapperTests(unittest.IsolatedAsyncioTestCase):
    async def test_wrapper_forwards_input_and_runtime_tool_call_id(self):
        with (
            patch("app.Tools.read_memory.MemoryTools") as memory_tools_class,
            patch(
                "app.Tools.read_memory.send_last_assistant_message",
                new_callable=AsyncMock,
                return_value="call-read",
            ) as send_last_message,
        ):
            bridge = memory_tools_class.return_value
            bridge.read_memory = AsyncMock(return_value='{"success": true}')
            tool = ReadMemoryTool(
                llm=SimpleNamespace(),
                task_id="task-1",
                chat_id="chat-1",
            )

            result = await tool.run(
                ReadMemoryToolInput(name="preference", target="user")
            )

        self.assertEqual(result, '{"success": true}')
        send_last_message.assert_awaited_once_with(
            memory=None,
            task_id="task-1",
            chat_id="chat-1",
            tool_name="read_memory",
        )
        bridge.read_memory.assert_awaited_once_with(
            name="preference",
            target="user",
            tool_call_id="call-read",
        )


if __name__ == "__main__":
    unittest.main()
