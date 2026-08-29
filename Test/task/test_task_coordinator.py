import asyncio
import unittest
from types import SimpleNamespace

from app.Task.TaskCoordinator.config import TaskCoordinatorSettings
from app.Task.TaskCoordinator.memory import InMemoryTaskCoordinator
from app.Task.TaskCoordinator.models import RejectionReason, TaskStatus
from app.api.websocket_utils import send_ws_message


def coordinator(
    *,
    per_user: int = 2,
    per_instance: int = 3,
    queued: int = 10,
) -> InMemoryTaskCoordinator:
    """Create an isolated coordinator with small deterministic limits."""

    return InMemoryTaskCoordinator(
        TaskCoordinatorSettings(
            backend="memory",
            max_running_per_user=per_user,
            max_running_per_instance=per_instance,
            max_queued_per_instance=queued,
        )
    )


class InMemoryTaskCoordinatorTests(unittest.IsolatedAsyncioTestCase):
    async def test_same_user_can_run_tasks_in_different_chats(self):
        task_coordinator = coordinator(per_user=2, per_instance=3)

        first = await task_coordinator.submit_task(
            task_id="task-1",
            user_id="user-1",
            chat_id="chat-1",
        )
        second = await task_coordinator.submit_task(
            task_id="task-2",
            user_id="user-1",
            chat_id="chat-2",
        )

        self.assertEqual(first.status, TaskStatus.RUNNING)
        self.assertEqual(second.status, TaskStatus.RUNNING)
        self.assertEqual(await task_coordinator.running_count(), 2)

    async def test_second_task_in_same_chat_is_rejected_atomically(self):
        task_coordinator = coordinator()

        results = await asyncio.gather(
            task_coordinator.submit_task(
                task_id="task-1",
                user_id="user-1",
                chat_id="chat-1",
            ),
            task_coordinator.submit_task(
                task_id="task-2",
                user_id="user-1",
                chat_id="chat-1",
            ),
        )

        accepted = [result for result in results if result.accepted]
        rejected = [result for result in results if not result.accepted]
        self.assertEqual(len(accepted), 1)
        self.assertEqual(len(rejected), 1)
        self.assertEqual(
            rejected[0].reason,
            RejectionReason.CHAT_ALREADY_RUNNING,
        )

    async def test_user_limit_queues_then_promotes_task(self):
        task_coordinator = coordinator(per_user=1, per_instance=3)
        await task_coordinator.submit_task(
            task_id="task-1",
            user_id="user-1",
            chat_id="chat-1",
        )
        queued = await task_coordinator.submit_task(
            task_id="task-2",
            user_id="user-1",
            chat_id="chat-2",
        )

        self.assertEqual(queued.status, TaskStatus.QUEUED)
        finish = await task_coordinator.finish_task(
            task_id="task-1",
            terminal_status=TaskStatus.COMPLETED,
        )

        self.assertEqual(finish.promoted_task_ids, ("task-2",))
        promoted = await task_coordinator.get_task("task-2")
        self.assertEqual(promoted.status, TaskStatus.RUNNING)

    async def test_promotion_skips_user_at_limit(self):
        task_coordinator = coordinator(per_user=1, per_instance=2)
        await task_coordinator.submit_task(
            task_id="a-running",
            user_id="user-a",
            chat_id="chat-a1",
        )
        await task_coordinator.submit_task(
            task_id="a-queued",
            user_id="user-a",
            chat_id="chat-a2",
        )
        await task_coordinator.submit_task(
            task_id="b-running",
            user_id="user-b",
            chat_id="chat-b1",
        )
        await task_coordinator.submit_task(
            task_id="b-queued",
            user_id="user-b",
            chat_id="chat-b2",
        )

        finish = await task_coordinator.finish_task(
            task_id="b-running",
            terminal_status=TaskStatus.COMPLETED,
        )

        self.assertEqual(finish.promoted_task_ids, ("b-queued",))
        self.assertEqual(
            (await task_coordinator.get_task("a-queued")).status,
            TaskStatus.QUEUED,
        )

    async def test_terminal_cleanup_releases_chat_and_is_idempotent(self):
        task_coordinator = coordinator()
        await task_coordinator.submit_task(
            task_id="task-1",
            user_id="user-1",
            chat_id="chat-1",
        )

        first_finish = await task_coordinator.finish_task(
            task_id="task-1",
            terminal_status=TaskStatus.CANCELLED,
        )
        second_finish = await task_coordinator.finish_task(
            task_id="task-1",
            terminal_status=TaskStatus.CANCELLED,
        )
        replacement = await task_coordinator.submit_task(
            task_id="task-2",
            user_id="user-1",
            chat_id="chat-1",
        )

        self.assertTrue(first_finish.finished)
        self.assertFalse(second_finish.finished)
        self.assertTrue(replacement.accepted)

    async def test_bounded_queue_rejects_excess_requests(self):
        task_coordinator = coordinator(
            per_user=1,
            per_instance=1,
            queued=1,
        )
        await task_coordinator.submit_task(
            task_id="running",
            user_id="user-1",
            chat_id="chat-1",
        )
        first_queued = await task_coordinator.submit_task(
            task_id="queued",
            user_id="user-2",
            chat_id="chat-2",
        )
        rejected = await task_coordinator.submit_task(
            task_id="rejected",
            user_id="user-3",
            chat_id="chat-3",
        )

        self.assertEqual(first_queued.status, TaskStatus.QUEUED)
        self.assertFalse(rejected.accepted)
        self.assertEqual(rejected.reason, RejectionReason.QUEUE_FULL)


class _ConcurrentSendWebSocket:
    """Fake socket that fails the test if two send operations overlap."""

    def __init__(self):
        self.state = SimpleNamespace()
        self.active_sends = 0
        self.max_active_sends = 0
        self.messages = []

    async def send_json(self, message):
        self.active_sends += 1
        self.max_active_sends = max(self.max_active_sends, self.active_sends)
        await asyncio.sleep(0)
        self.messages.append(message)
        self.active_sends -= 1


class WebSocketSendSerializationTests(unittest.IsolatedAsyncioTestCase):
    async def test_optional_compression_id_is_added_only_when_supplied(self):
        websocket = _ConcurrentSendWebSocket()

        await send_ws_message(
            websocket,
            type="compression",
            task_id="source-task",
            chat_id="chat",
            compression_id="compression_123",
            payload={"status": "completed"},
        )
        await send_ws_message(
            websocket,
            type="aura_status",
            task_id="task",
            chat_id="chat",
            payload={"status": "processing"},
        )

        self.assertEqual(
            websocket.messages[0]["compression_id"],
            "compression_123",
        )
        self.assertNotIn("compression_id", websocket.messages[1])

    async def test_parallel_task_messages_are_serialized_per_socket(self):
        websocket = _ConcurrentSendWebSocket()

        await asyncio.gather(
            *[
                send_ws_message(
                    websocket,
                    type="aura_status",
                    task_id=f"task-{index}",
                    chat_id=f"chat-{index}",
                    payload={"status": "processing"},
                )
                for index in range(10)
            ]
        )

        self.assertEqual(websocket.max_active_sends, 1)
        self.assertEqual(len(websocket.messages), 10)
        self.assertEqual(
            {message["task_id"] for message in websocket.messages},
            {f"task-{index}" for index in range(10)},
        )
