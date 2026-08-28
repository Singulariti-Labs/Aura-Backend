import json
import unittest

from app.Context.Store.memory import InMemoryContextStore
from app.Context.manager import ContextManager
from app.Context.models import CompressionConfig, CompressionSummary
from app.LLM.model_token_limits import ModelContextProfile


def text_message(role: str, text: str) -> dict:
    return {"role": role, "content": [{"type": "text", "text": text}]}


class ContextManagerTests(unittest.IsolatedAsyncioTestCase):
    async def test_assigns_sequences_and_saves_resumable_context(self):
        store = InMemoryContextStore()
        manager = ContextManager(
            task_id="task",
            chat_id="chat",
            agent_id="main",
            provider="test",
            model="small",
            profile=ModelContextProfile(
                provider="test",
                model="small",
                context_window=1000,
                max_output_tokens=100,
            ),
            messages=[text_message("user", "hello")],
            store=store,
        )
        await manager.initialize()
        assistant = await manager.record_assistant(text_message("assistant", "working"))

        saved = await store.get("task:main")
        self.assertEqual(assistant["sequence"], 2)
        self.assertEqual(saved.next_sequence, 3)
        self.assertEqual(len(saved.canonical_messages), 2)

    async def test_patch_result_records_all_changed_files_after_partial_failure(self):
        store = InMemoryContextStore()
        manager = ContextManager(
            task_id="task",
            chat_id="chat",
            agent_id="main",
            provider="test",
            model="small",
            profile=ModelContextProfile(
                provider="test",
                model="small",
                context_window=1000,
                max_output_tokens=100,
            ),
            messages=[],
            store=store,
        )
        await manager.initialize()
        await manager.record_assistant(
            {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_call",
                        "tool_call_id": "toolu_patch",
                        "name": "patch",
                        "input": {"mode": "patch", "patch": "*** Begin Patch"},
                    }
                ],
            }
        )

        await manager.record_tool_batch(
            [
                {
                    "role": "tool",
                    "tool_call_id": "toolu_patch",
                    "tool_name": "patch",
                    "is_error": True,
                    "content": [
                        {
                            "type": "text",
                            "text": json.dumps(
                                {
                                    "success": False,
                                    "error": "A later patch section failed",
                                    "files_modified": ["C:/project/src/a.ts"],
                                    "files_created": ["C:/project/src/b.ts"],
                                    "files_deleted": ["C:/project/src/old.ts"],
                                }
                            ),
                        }
                    ],
                }
            ]
        )

        self.assertEqual(
            manager.snapshot.checkpoint.files_changed,
            [
                "C:/project/src/a.ts",
                "C:/project/src/b.ts",
                "C:/project/src/old.ts",
            ],
        )

    async def test_compression_replaces_older_messages_and_emits_client_event(self):
        store = InMemoryContextStore()
        events = []

        async def receive_event(event):
            events.append(event)

        messages = [
            text_message("user" if index % 2 == 0 else "assistant", "word " * 130)
            for index in range(8)
        ]
        manager = ContextManager(
            task_id="task",
            chat_id="chat",
            agent_id="main",
            provider="test",
            model="small",
            profile=ModelContextProfile(
                provider="test",
                model="small",
                context_window=1200,
                max_output_tokens=100,
            ),
            messages=messages,
            store=store,
            config=CompressionConfig(
                threshold=0.70,
                hard_threshold=0.90,
                target_ratio=0.40,
                tail_ratio=0.20,
                min_tail_blocks=3,
                safety_margin_ratio=0,
            ),
            client_event_callback=receive_event,
        )
        await manager.initialize()

        async def summarize(_value):
            return CompressionSummary(
                summary="Earlier investigation completed; continue with recent work.",
                input_tokens=500,
                output_tokens=12,
            )

        event = await manager.compress_if_needed(
            system_prompt="system",
            native_tools=[],
            summarizer=summarize,
            force=True,
            reason="manual",
        )

        self.assertIsNotNone(event)
        self.assertEqual(event["type"], "compression")
        self.assertEqual(event["trigger"], "manual")
        self.assertEqual(event["trigger_reason"], "manual")
        self.assertTrue(event["compression_id"].startswith("compression_"))
        self.assertEqual(
            {item["compression_id"] for item in events},
            {event["compression_id"]},
        )
        self.assertEqual(manager.snapshot.compressor_state.generation, 1)
        self.assertLess(len(manager.snapshot.canonical_messages), len(messages))
        self.assertTrue(any(item.get("type") == "compression" for item in events))
        self.assertEqual(events[-1]["status"], "resuming_task_after_compressing")
        self.assertEqual(
            events[-1]["message"],
            "Context optimized; resuming the task with optimized context",
        )

    async def test_client_range_is_used_without_reselecting_the_tail(self):
        store = InMemoryContextStore()
        messages = [
            text_message("user" if index % 2 == 0 else "assistant", f"message {index}")
            for index in range(6)
        ]
        manager = ContextManager(
            task_id="task",
            chat_id="chat",
            agent_id="main",
            provider="test",
            model="small",
            profile=ModelContextProfile(
                provider="test",
                model="small",
                context_window=2000,
                max_output_tokens=100,
            ),
            messages=messages,
            store=store,
        )
        await manager.initialize()
        compressor_inputs = []

        async def summarize(value):
            compressor_inputs.append(json.loads(value))
            return CompressionSummary(summary="client-selected summary")

        requested_range = {
            "preserved_head_seqs": [],
            "summarized": {"start_seq": 1, "end_seq": 3},
            "preserved_tail": {"start_seq": 4, "end_seq": 6},
        }
        event = await manager.compress_if_needed(
            system_prompt="system",
            native_tools=[],
            summarizer=summarize,
            force=True,
            reason="preflight",
            requested_range=requested_range,
        )

        self.assertEqual(
            [message["sequence"] for message in compressor_inputs[0]["older_messages"]],
            [1, 2, 3],
        )
        self.assertEqual(len(compressor_inputs), 1)
        self.assertEqual(
            [message["sequence"] for message in manager.snapshot.canonical_messages],
            [4, 5, 6],
        )
        self.assertEqual(event["range"], requested_range)
        self.assertEqual(event["trigger"], "preflight")

    async def test_runtime_compression_uses_runtime_client_trigger(self):
        store = InMemoryContextStore()
        messages = [text_message("user", "word " * 130) for _ in range(8)]
        manager = ContextManager(
            task_id="task",
            chat_id="chat",
            agent_id="main",
            provider="test",
            model="small",
            profile=ModelContextProfile(
                provider="test",
                model="small",
                context_window=1200,
                max_output_tokens=100,
            ),
            messages=messages,
            store=store,
            config=CompressionConfig(
                hard_threshold=0.90,
                safety_margin_ratio=0,
            ),
        )
        await manager.initialize()

        async def summarize(_value):
            return CompressionSummary(summary="runtime summary")

        event = await manager.compress_if_needed(
            system_prompt="system",
            native_tools=[],
            summarizer=summarize,
            force=True,
            reason="runtime_threshold",
        )

        self.assertEqual(event["trigger"], "runtime")
        self.assertEqual(event["trigger_reason"], "runtime_threshold")
        self.assertTrue(event["compression_id"].startswith("compression_"))

    async def test_failed_compression_keeps_original_context(self):
        store = InMemoryContextStore()
        events = []

        async def receive_event(event):
            events.append(event)

        messages = [text_message("user", "word " * 200) for _ in range(6)]
        manager = ContextManager(
            task_id="task",
            chat_id="chat",
            agent_id="main",
            provider="test",
            model="small",
            profile=ModelContextProfile(
                provider="test", model="small", context_window=2500, max_output_tokens=100
            ),
            messages=messages,
            store=store,
            config=CompressionConfig(hard_threshold=0.99, safety_margin_ratio=0),
            client_event_callback=receive_event,
        )
        await manager.initialize()

        async def fail(_value):
            raise RuntimeError("compressor unavailable")

        await manager.compress_if_needed(
            system_prompt="system",
            native_tools=[],
            summarizer=fail,
            force=True,
        )

        self.assertEqual(len(manager.snapshot.canonical_messages), len(messages))
        self.assertEqual(manager.snapshot.compressor_state.status, "failed")
        self.assertEqual(events[-1]["status"], "runtime_compression_failed")
        self.assertTrue(events[-1]["compression_id"].startswith("compression_"))
        self.assertEqual(
            events[-1]["message"],
            "Context optimization failed; original context retained",
        )


if __name__ == "__main__":
    unittest.main()
