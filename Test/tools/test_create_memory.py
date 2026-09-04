import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from pydantic import ValidationError

from app.Agentic_Tools.memory_tools import MemoryTools
from app.LLM.model_bridge.common import canonical_tool_result
from app.Tools.create_memory import (
    CREATE_MEMORY_TOOL_DESCRIPTION,
    CreateMemoryTool,
)
from app.Types.agent_types import CreateMemoryToolInput


CREATE_INPUT = {
    "name": "preference",
    "target": "user",
    "description": (
        "User preferences, communication style, and response expectations."
    ),
    "aliases": ["prefs", "preferences", "style"],
    "facts": [
        "User prefers concise answers.",
        "User prefers copy-paste-ready TypeScript code.",
        (
            "User prefers read-only code reviews unless they explicitly ask "
            "for changes."
        ),
    ],
}


class CreateMemoryInputTests(unittest.TestCase):
    """Verify the model-facing schema exactly represents the tool contract."""

    def test_all_fields_are_required_and_values_are_typed(self):
        inputs = CreateMemoryToolInput(**CREATE_INPUT)

        self.assertEqual(inputs.name, "preference")
        self.assertEqual(inputs.target, "user")
        self.assertEqual(inputs.aliases, ["prefs", "preferences", "style"])
        self.assertEqual(len(inputs.facts), 3)

        for missing_field in CREATE_INPUT:
            with self.subTest(missing_field=missing_field):
                invalid_input = dict(CREATE_INPUT)
                invalid_input.pop(missing_field)
                with self.assertRaises(ValidationError):
                    CreateMemoryToolInput(**invalid_input)

    def test_target_enum_and_additional_properties_are_rejected(self):
        with self.assertRaises(ValidationError):
            CreateMemoryToolInput(**{**CREATE_INPUT, "target": "project"})
        with self.assertRaises(ValidationError):
            CreateMemoryToolInput(**CREATE_INPUT, allies=["wrong-field-name"])

    def test_json_schema_and_description_match_specification(self):
        schema = CreateMemoryToolInput.model_json_schema()

        self.assertEqual(
            schema["required"],
            ["name", "target", "description", "aliases", "facts"],
        )
        self.assertFalse(schema["additionalProperties"])
        self.assertEqual(schema["properties"]["target"]["enum"], ["user", "memory"])
        self.assertEqual(schema["properties"]["aliases"]["items"], {"type": "string"})
        self.assertEqual(schema["properties"]["facts"]["items"], {"type": "string"})
        self.assertIn("Create a new named memory file", CREATE_MEMORY_TOOL_DESCRIPTION)
        self.assertIn("completely rewrite", CREATE_MEMORY_TOOL_DESCRIPTION)
        self.assertIn("memory update tool", CREATE_MEMORY_TOOL_DESCRIPTION)


class CreateMemoryBridgeTests(unittest.IsolatedAsyncioTestCase):
    """Verify request/event parity and complete client-result handling."""

    async def _run_bridge(self, result):
        state = SimpleNamespace(
            websocket=object(),
            dbpool=object(),
            get_next_seq=MagicMock(return_value=21),
        )
        response = {
            "type": "client_tool_response",
            "chat_id": "chat-123",
            "task_id": "task-123",
            "payload": {
                "tool": "create_memory",
                "tool_call_id": "call-123",
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
            returned = await bridge.create_memory(
                **CREATE_INPUT,
                tool_call_id="call-123",
            )

            return {
                "returned": returned,
                "request": send_message.await_args.kwargs,
                "event": create_event.await_args.kwargs,
                "memory_calls": update_memory.call_args_list,
                "wait": manager.wait_for_tool_response,
            }

    async def test_create_success_sends_exact_request_and_returns_result(self):
        client_result = {
            "success": True,
            "message": "Successfully created memory file preference.",
        }

        observed = await self._run_bridge(client_result)

        self.assertEqual(observed["returned"], client_result)
        request_payload = observed["request"]["payload"]
        self.assertEqual(
            request_payload,
            {
                "tool": "create_memory",
                "tool_call_id": "call-123",
                "input": CREATE_INPUT,
                "coming_from": "create_memory_tool_func/server",
            },
        )
        self.assertEqual(
            observed["event"]["payload"],
            {
                "tool_call_id": "call-123",
                "input": CREATE_INPUT,
            },
        )
        observed["wait"].assert_awaited_once_with("task-123", "call-123")
        tool_memory = observed["memory_calls"][-1].kwargs
        self.assertEqual(tool_memory["name"], "create_memory")
        self.assertEqual(json.loads(tool_memory["content"]), client_result)

    async def test_rewrite_success_message_is_preserved_unchanged(self):
        client_result = {
            "success": True,
            "message": "Successfully rewrote memory file preference.",
        }

        observed = await self._run_bridge(client_result)

        self.assertEqual(observed["returned"], client_result)

    async def test_failure_returns_the_entire_result_as_json_string(self):
        client_result = {
            "success": False,
            "error": "Facts use 4,350/4,200 chars and exceed the memory limit.",
            "usage": "4,350/4,200",
            "current_facts": ["Existing durable fact."],
        }

        observed = await self._run_bridge(client_result)

        self.assertIsInstance(observed["returned"], str)
        self.assertEqual(json.loads(observed["returned"]), client_result)
        canonical = canonical_tool_result(
            tool_call={"tool_call_id": "call-123", "name": "create_memory"},
            result=observed["returned"],
        )
        self.assertTrue(canonical["is_error"])
        self.assertEqual(json.loads(canonical["content"][0]["text"]), client_result)


class CreateMemoryWrapperTests(unittest.IsolatedAsyncioTestCase):
    async def test_wrapper_forwards_all_fields_and_runtime_tool_call_id(self):
        with (
            patch("app.Tools.create_memory.MemoryTools") as memory_tools_class,
            patch(
                "app.Tools.create_memory.send_last_assistant_message",
                new_callable=AsyncMock,
                return_value="call-create",
            ) as send_last_message,
        ):
            bridge = memory_tools_class.return_value
            bridge.create_memory = AsyncMock(return_value={"success": True})
            tool = CreateMemoryTool(
                llm=SimpleNamespace(),
                task_id="task-1",
                chat_id="chat-1",
            )

            result = await tool.run(CreateMemoryToolInput(**CREATE_INPUT))

        self.assertEqual(result, {"success": True})
        send_last_message.assert_awaited_once_with(
            memory=None,
            task_id="task-1",
            chat_id="chat-1",
            tool_name="create_memory",
        )
        bridge.create_memory.assert_awaited_once_with(
            **CREATE_INPUT,
            tool_call_id="call-create",
        )


if __name__ == "__main__":
    unittest.main()
