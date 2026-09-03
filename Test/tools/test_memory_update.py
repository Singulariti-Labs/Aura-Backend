import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from pydantic import ValidationError

from app.Agentic_Tools.memory_tools import MemoryTools
from app.LLM.model_bridge.common import canonical_tool_result
from app.Tools.memory_update import (
    MEMORY_UPDATE_TOOL_DESCRIPTION,
    MemoryUpdateTool,
)
from app.Types.agent_types import MemoryUpdateToolInput


class MemoryUpdateInputTests(unittest.TestCase):
    """Verify the model-facing schema matches the documented client contract."""

    def test_required_fields_and_enum_values(self):
        inputs = MemoryUpdateToolInput(
            action="add",
            name="current-project",
            target="memory",
            content="Aura uses TypeScript.",
        )

        self.assertEqual(inputs.action, "add")
        self.assertEqual(inputs.name, "current-project")
        self.assertEqual(inputs.target, "memory")

        with self.assertRaises(ValidationError):
            MemoryUpdateToolInput(name="preference")
        with self.assertRaises(ValidationError):
            MemoryUpdateToolInput(name="preference", target="unknown")

    def test_batch_operation_requires_action(self):
        inputs = MemoryUpdateToolInput(
            name="project",
            target="memory",
            operations=[
                {"action": "remove", "old_text": "obsolete fact"},
                {"action": "add", "new_text": "Replacement fact"},
            ],
        )

        self.assertIsNone(inputs.action)
        self.assertEqual(inputs.operations[0].action, "remove")
        with self.assertRaises(ValidationError):
            MemoryUpdateToolInput(
                name="project",
                target="memory",
                operations=[{"old_text": "missing action"}],
            )

    def test_json_schema_and_tool_description_match_specification(self):
        schema = MemoryUpdateToolInput.model_json_schema()

        self.assertEqual(schema["required"], ["name", "target"])
        self.assertEqual(
            schema["properties"]["target"]["enum"],
            ["memory", "user"],
        )
        operation_schema = schema["$defs"]["MemoryUpdateOperation"]
        self.assertEqual(operation_schema["required"], ["action"])
        self.assertIn("Update durable facts", MEMORY_UPDATE_TOOL_DESCRIPTION)
        self.assertIn("add", MEMORY_UPDATE_TOOL_DESCRIPTION)
        self.assertIn("replace", MEMORY_UPDATE_TOOL_DESCRIPTION)
        self.assertIn("remove", MEMORY_UPDATE_TOOL_DESCRIPTION)


class MemoryUpdateBridgeTests(unittest.IsolatedAsyncioTestCase):
    """Verify WebSocket request construction and complete response preservation."""

    async def _run_bridge(self, *, result, **tool_input):
        state = SimpleNamespace(
            websocket=object(),
            dbpool=object(),
            get_next_seq=MagicMock(return_value=12),
        )
        response = {
            "type": "client_tool_response",
            "chat_id": "chat-123",
            "task_id": "task-123",
            "payload": {
                "tool": "memory_update",
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
            returned = await bridge.memory_update(
                tool_call_id="call-123",
                **tool_input,
            )

            return {
                "returned": returned,
                "request": send_message.await_args.kwargs,
                "event": create_event.await_args.kwargs,
                "memory_calls": update_memory.call_args_list,
                "wait": manager.wait_for_tool_response,
            }

    async def test_single_success_sends_exact_request_and_returns_result(self):
        client_result = {
            "success": True,
            "done": True,
            "target": "memory",
            "usage": "23% — 510/2,200 chars",
            "message": (
                "Successfully added an entry to memory file current-project."
            ),
        }

        observed = await self._run_bridge(
            result=client_result,
            name="current-project",
            target="memory",
            action="add",
            content="Aura uses TypeScript.",
        )

        self.assertEqual(observed["returned"], client_result)
        request_payload = observed["request"]["payload"]
        self.assertEqual(
            request_payload,
            {
                "tool": "memory_update",
                "tool_call_id": "call-123",
                "input": {
                    "name": "current-project",
                    "target": "memory",
                    "action": "add",
                    "content": "Aura uses TypeScript.",
                },
                "coming_from": "memory_update_tool_func/server",
            },
        )
        self.assertEqual(
            observed["event"]["payload"],
            {
                "tool_call_id": "call-123",
                "input": request_payload["input"],
            },
        )
        observed["wait"].assert_awaited_once_with("task-123", "call-123")
        tool_memory = observed["memory_calls"][-1].kwargs
        self.assertEqual(tool_memory["name"], "memory_update")
        self.assertEqual(json.loads(tool_memory["content"]), client_result)

    async def test_batch_omits_absent_fields_and_preserves_operations(self):
        operations = [
            {"action": "remove", "old_text": "old project"},
            {"action": "add", "new_text": "new project"},
        ]
        observed = await self._run_bridge(
            result={"success": True, "done": True, "target": "memory"},
            name="project",
            target="memory",
            description="Active project facts",
            operations=operations,
        )

        self.assertEqual(
            observed["request"]["payload"]["input"],
            {
                "name": "project",
                "target": "memory",
                "description": "Active project facts",
                "operations": operations,
            },
        )

    async def test_rich_client_failure_is_returned_as_complete_json_string(self):
        client_result = {
            "success": False,
            "done": True,
            "target": "memory",
            "error": (
                'No memory entry matched "old project". Use a fact id or a '
                "unique substring."
            ),
            "current_entries": [
                "- [fact-001] Aura uses TypeScript.",
                "- [fact-002] Aura is an Electron application.",
            ],
            "usage": "510/2,200",
        }

        observed = await self._run_bridge(
            result=client_result,
            name="project",
            target="memory",
            action="remove",
            old_text="old project",
        )

        self.assertIsInstance(observed["returned"], str)
        self.assertEqual(json.loads(observed["returned"]), client_result)
        canonical = canonical_tool_result(
            tool_call={"tool_call_id": "call-123", "name": "memory_update"},
            result=observed["returned"],
        )
        self.assertTrue(canonical["is_error"])
        self.assertEqual(json.loads(canonical["content"][0]["text"]), client_result)

    async def test_drift_failure_keeps_backup_and_remediation_fields(self):
        client_result = {
            "success": False,
            "done": True,
            "target": "memory",
            "error": "Unsafe or unsupported manual drift detected.",
            "drift_backup": (
                "AuraMemory\\memory\\project.md.drift-"
                "2026-09-02T12-30-00-000Z-a1b2c3d4.bak"
            ),
            "remediation": (
                "Review the drift backup, restore valid YAML frontmatter and "
                "- [fact-NNN] entries, then retry memory_update."
            ),
        }

        observed = await self._run_bridge(
            result=client_result,
            name="project",
            target="memory",
            action="add",
            content="New fact",
        )

        self.assertEqual(json.loads(observed["returned"]), client_result)

    async def test_invalid_client_result_returns_structured_json_error(self):
        observed = await self._run_bridge(
            result=None,
            name="project",
            target="memory",
            action="add",
            content="New fact",
        )

        error = json.loads(observed["returned"])
        self.assertFalse(error["success"])
        self.assertTrue(error["done"])
        self.assertEqual(error["target"], "memory")
        self.assertIn("Invalid memory_update result", error["error"])


class MemoryUpdateWrapperTests(unittest.IsolatedAsyncioTestCase):
    async def test_wrapper_forwards_model_dump_and_runtime_tool_call_id(self):
        state = SimpleNamespace(websocket=object(), dbpool=object())
        with (
            patch(
                "app.Tools.memory_update.MemoryTools",
            ) as memory_tools_class,
            patch(
                "app.Tools.memory_update.send_last_assistant_message",
                new_callable=AsyncMock,
                return_value="call-batch",
            ),
            patch(
                "app.Agentic_Tools.memory_tools.task_manager.get_state",
                return_value=state,
            ),
        ):
            bridge = memory_tools_class.return_value
            bridge.memory_update = AsyncMock(return_value={"success": True})
            tool = MemoryUpdateTool(
                llm=SimpleNamespace(),
                task_id="task-1",
                chat_id="chat-1",
            )
            inputs = MemoryUpdateToolInput(
                name="preference",
                target="user",
                operations=[
                    {
                        "action": "replace",
                        "old_text": "likes blue",
                        "content": "Prefers green",
                    }
                ],
            )

            result = await tool.run(inputs)

        self.assertEqual(result, {"success": True})
        bridge.memory_update.assert_awaited_once_with(
            name="preference",
            target="user",
            action=None,
            description=None,
            content=None,
            new_text=None,
            old_text=None,
            operations=[
                {
                    "action": "replace",
                    "content": "Prefers green",
                    "old_text": "likes blue",
                }
            ],
            tool_call_id="call-batch",
        )


if __name__ == "__main__":
    unittest.main()
