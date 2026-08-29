import asyncio
import json
import unittest
from unittest.mock import AsyncMock, patch

from pydantic import ValidationError

from app.Agentic_Tools.file_editor import FileEditor
from app.LLM.model_bridge.common import canonical_tool_result
from app.Task.task_manager import task_manager
from app.Tools.patch import PATCH_TOOL_DESCRIPTION
from app.Types.agent_types import PatchToolInput


class PatchToolInputTests(unittest.TestCase):
    """Verify the public patch schema and its mode-specific requirements."""

    def test_replace_mode_is_default_and_allows_deletion(self):
        inputs = PatchToolInput(
            path="src/index.ts",
            old_string="console.log('debug');\n",
            new_string="",
        )

        self.assertEqual(inputs.mode, "replace")
        self.assertFalse(inputs.replace_all)
        self.assertEqual(inputs.new_string, "")

    def test_mode_specific_fields_are_required(self):
        with self.assertRaises(ValidationError):
            PatchToolInput(mode="replace", path="src/index.ts")

        with self.assertRaises(ValidationError):
            PatchToolInput(
                mode="replace",
                path="src/index.ts",
                old_string="same",
                new_string="same",
            )

        with self.assertRaises(ValidationError):
            PatchToolInput(mode="patch")

    def test_description_documents_multi_file_and_multiple_match_behavior(self):
        self.assertIn("multiple *** Update File: sections", PATCH_TOOL_DESCRIPTION)
        self.assertIn(
            "multiple matches without replace_all are invalid and rejected",
            PATCH_TOOL_DESCRIPTION,
        )


class PatchFileEditorTests(unittest.IsolatedAsyncioTestCase):
    """Exercise patch client requests and result forwarding end to end."""

    async def _run_client_round_trip(self, *, task_id, call_id, method_kwargs, result):
        task_manager.create_task(task_id, websocket=None, pool=None)
        editor = FileEditor(
            llm=None,
            task_id=task_id,
            chat_id="chat-test",
            memory=None,
        )

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
                patch("app.Agentic_Tools.file_editor.update_memory") as update_memory,
            ):
                async def register_request(**kwargs):
                    payload = kwargs["payload"]
                    task_manager.register_tool_call(
                        task_id,
                        payload["tool_call_id"],
                        tool_name=payload["tool"],
                    )
                    return payload["tool_call_id"]

                send_message.side_effect = register_request
                pending_result = asyncio.create_task(
                    editor.patch(tool_call_id=call_id, **method_kwargs)
                )
                await asyncio.sleep(0)
                task_manager.provide_input(
                    task_id,
                    {
                        "type": "client_tool_response",
                        "payload": {
                            "tool": "patch",
                            "tool_call_id": call_id,
                            "result": result,
                        },
                    },
                )
                returned_result = await pending_result

                request_payload = send_message.await_args.kwargs["payload"]
                event_payload = create_event.await_args.kwargs["payload"]
                memory_result = update_memory.call_args_list[-1].kwargs["content"]
                return returned_result, request_payload, event_payload, memory_result
        finally:
            task_manager.remove_task(task_id)

    async def test_replace_request_and_success_response_are_preserved(self):
        client_result = {
            "success": True,
            "diff": "--- a/src/index.ts\n+++ b/src/index.ts\n",
            "files_modified": ["C:/project/src/index.ts"],
            "resolved_path": "C:/project/src/index.ts",
            "lint": {
                "status": "skipped",
                "message": "No linter for .ts files",
            },
            "_warning": "File changed since last read",
        }

        returned, request, event, memory_result = await self._run_client_round_trip(
            task_id="patch-replace-test",
            call_id="toolu_patch_replace",
            method_kwargs={
                "path": "src/index.ts",
                "old_string": "const x = 1;",
                "new_string": "const x = 2;",
                "replace_all": True,
            },
            result=client_result,
        )

        self.assertEqual(returned, client_result)
        self.assertEqual(request["tool"], "patch")
        self.assertEqual(request["tool_call_id"], "toolu_patch_replace")
        self.assertEqual(
            request["input"],
            {
                "mode": "replace",
                "path": "src/index.ts",
                "old_string": "const x = 1;",
                "new_string": "const x = 2;",
                "replace_all": True,
            },
        )
        self.assertEqual(event["input"], request["input"])
        self.assertEqual(json.loads(memory_result), client_result)

        llm_result = canonical_tool_result(
            tool_call={
                "tool_call_id": "toolu_patch_replace",
                "name": "patch",
            },
            result=returned,
        )
        self.assertEqual(json.loads(llm_result["content"][0]["text"]), client_result)

    async def test_v4a_request_and_failure_response_are_preserved(self):
        v4a_patch = """*** Begin Patch
*** Update File: src/a.ts
@@
-export const a = 1;
+export const a = 2;
*** End Patch"""
        client_result = {
            "success": False,
            "error": "Could not find a match for old_string in the file",
            "diff": "--- a/src/earlier.ts\n+++ b/src/earlier.ts\n",
            "files_modified": ["C:/project/src/earlier.ts"],
            "_hint": "Use read_file to verify the current content.",
        }

        returned, request, _, _ = await self._run_client_round_trip(
            task_id="patch-v4a-test",
            call_id="toolu_patch_v4a",
            method_kwargs={"mode": "patch", "patch": v4a_patch},
            result=client_result,
        )

        self.assertEqual(returned, client_result)
        self.assertEqual(request["input"], {"mode": "patch", "patch": v4a_patch})

        llm_result = canonical_tool_result(
            tool_call={"tool_call_id": "toolu_patch_v4a", "name": "patch"},
            result=returned,
        )
        self.assertTrue(llm_result["is_error"])
        self.assertEqual(json.loads(llm_result["content"][0]["text"]), client_result)


if __name__ == "__main__":
    unittest.main()
