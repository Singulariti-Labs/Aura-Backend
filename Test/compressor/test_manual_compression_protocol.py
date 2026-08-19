import unittest
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.Context.models import CompressionSummary
from app.LLM.llm_factory import LLMFactory
from app.LLM.memory import Memory
from app.Task.task_manager import task_manager


def text_message(role: str, text: str, sequence: int) -> dict:
    return {
        "role": role,
        "sequence": sequence,
        "content": [{"type": "text", "text": text}],
    }


class ManualCompressionProtocolTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.task_id = f"task-{uuid.uuid4()}"
        self.compression_id = f"compression_{uuid.uuid4()}"
        self.websocket = SimpleNamespace(state=SimpleNamespace())
        task_manager.create_task(self.compression_id, self.websocket, None)
        task_manager.create_task(self.task_id, self.websocket, None)
        self.factory = LLMFactory(memory=Memory())
        self.llm = SimpleNamespace(model_name="gpt-4.1", max_tokens=1024)

    def tearDown(self):
        task_manager.remove_task(self.compression_id)
        task_manager.remove_task(self.task_id)

    async def invoke(
        self,
        *,
        history,
        compression_range=None,
        include_compression_id=True,
        compression_reason="preflight",
    ):
        compression_id = self.compression_id if include_compression_id else None
        return await self.factory.aura_invoker(
            system_prompt="Compress context.",
            llm=self.llm,
            query="Context compression",
            llm_provider="openai",
            agent_type="aura",
            tools=[],
            history=history,
            task_id=self.task_id,
            runtime_task_id=compression_id or self.task_id,
            chat_id="chat",
            compression_id=compression_id,
            force_preflight_compression=True,
            compression_range=compression_range,
            compression_reason=compression_reason,
        )

    async def test_success_sends_only_existing_completed_compression_event(self):
        history = [
            text_message("user" if index % 2 else "assistant", f"message {index}", index)
            for index in range(1, 7)
        ]
        compression_range = {
            "preserved_head_seqs": [],
            "summarized": {"start_seq": 1, "end_seq": 3},
            "preserved_tail": {"start_seq": 4, "end_seq": 6},
        }

        with (
            patch(
                "app.LLM.llm_factory.send_ws_message",
                new_callable=AsyncMock,
            ) as send,
            patch.object(
                self.factory,
                "_summarize_dedicated_context",
                new=AsyncMock(
                    return_value=CompressionSummary(summary="compact summary")
                ),
            ),
        ):
            result = await self.invoke(
                history=history,
                compression_range=compression_range,
            )

        self.assertEqual(result["compression"]["status"], "completed")
        self.assertEqual(result["compression"]["trigger"], "preflight")
        self.assertEqual(
            result["compression"]["compression_id"],
            self.compression_id,
        )
        self.assertEqual([call.kwargs["type"] for call in send.await_args_list], ["compression"])
        self.assertEqual(send.await_args.kwargs["payload"], result["compression"])
        self.assertEqual(send.await_args.kwargs["task_id"], self.task_id)
        self.assertEqual(
            send.await_args.kwargs["compression_id"],
            self.compression_id,
        )

    async def test_already_compact_sends_one_compression_event(self):
        with patch(
            "app.LLM.llm_factory.send_ws_message",
            new_callable=AsyncMock,
        ) as send:
            result = await self.invoke(
                history=[text_message("user", "hello", 1)],
            )

        self.assertEqual(result["compression"]["status"], "already_compact")
        self.assertEqual(result["compression"]["trigger"], "preflight")
        self.assertEqual(
            result["compression"]["compression_id"],
            self.compression_id,
        )
        self.assertEqual([call.kwargs["type"] for call in send.await_args_list], ["compression"])

    async def test_manual_trigger_preserves_compression_id(self):
        with patch(
            "app.LLM.llm_factory.send_ws_message",
            new_callable=AsyncMock,
        ) as send:
            result = await self.invoke(
                history=[text_message("user", "hello", 1)],
                compression_reason="manual",
            )

        self.assertEqual(result["compression"]["trigger"], "manual")
        self.assertEqual(
            result["compression"]["compression_id"],
            self.compression_id,
        )
        self.assertEqual(send.await_args.kwargs["compression_id"], self.compression_id)

    async def test_failure_sends_one_compression_event(self):
        invalid_range = {
            "summarized": {"start_seq": 10, "end_seq": 20},
            "preserved_tail": {"start_seq": 21, "end_seq": 30},
        }
        with patch(
            "app.LLM.llm_factory.send_ws_message",
            new_callable=AsyncMock,
        ) as send:
            result = await self.invoke(
                history=[text_message("user", "hello", 1)],
                compression_range=invalid_range,
            )

        self.assertEqual(result["compression"]["status"], "failed")
        self.assertEqual(result["compression"]["trigger"], "preflight")
        self.assertEqual(
            result["compression"]["compression_id"],
            self.compression_id,
        )
        self.assertEqual(
            result["compression"]["error"]["code"],
            "COMPRESSION_FAILED",
        )
        self.assertEqual([call.kwargs["type"] for call in send.await_args_list], ["compression"])

    async def test_missing_optional_compression_id_gets_server_generated_id(self):
        with patch(
            "app.LLM.llm_factory.send_ws_message",
            new_callable=AsyncMock,
        ) as send:
            result = await self.invoke(
                history=[text_message("user", "hello", 1)],
                include_compression_id=False,
            )

        generated_id = result["compression"]["compression_id"]
        self.assertTrue(generated_id.startswith("compression_"))
        self.assertEqual(send.await_args.kwargs["compression_id"], generated_id)
        self.assertEqual(send.await_args.kwargs["task_id"], self.task_id)


if __name__ == "__main__":
    unittest.main()
