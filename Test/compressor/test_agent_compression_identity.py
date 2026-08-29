import unittest
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from app.Agents.agent import Agent
from app.Task.task_manager import task_manager
from app.Types.agent_types import AuraConfig, LLMConfig, SystemInfo


class AgentCompressionIdentityTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.task_id = f"task-{uuid.uuid4()}"
        self.compression_id = f"compression_{uuid.uuid4()}"
        self.websocket = SimpleNamespace(state=SimpleNamespace())
        task_manager.create_task(self.compression_id, self.websocket, None)

    def tearDown(self):
        task_manager.remove_task(self.compression_id)

    async def test_standalone_compression_uses_runtime_id_for_runtime_objects(self):
        tools = MagicMock()
        tools.get_supervisor_tools.return_value = []

        with (
            patch("app.Agents.agent.LLMFactory.create_llm", return_value=object()),
            patch("app.Agents.agent.Tools", return_value=tools) as tools_factory,
            patch("app.Agents.agent.ContextAgent") as context_agent,
            patch("app.Agents.agent.buildAuraSystemPrompt", return_value="prompt"),
        ):
            agent = Agent(
                query="Context compression",
                payload={
                    "_force_preflight_compression": True,
                    "trigger": "preflight",
                },
                task_id=self.task_id,
                runtime_task_id=self.compression_id,
                compression_id=self.compression_id,
                chat_id="chat",
                system_info=SystemInfo(
                    os="windows",
                    version="11",
                    workspace="workspace",
                    cwd="workspace",
                ),
                llm=LLMConfig(provider="openai", model_name="gpt-4.1"),
                aura_config=AuraConfig(),
            )
            agent.llm_factory.aura_invoker = AsyncMock(
                return_value={
                    "output": "summary",
                    "messages": [],
                }
            )

            await agent.invoke()

        self.assertEqual(
            tools_factory.call_args.kwargs["task_id"],
            self.compression_id,
        )
        tools.get_agent_tools.assert_not_called()
        context_agent.assert_not_called()
        invocation = agent.llm_factory.aura_invoker.await_args.kwargs
        self.assertEqual(invocation["task_id"], self.task_id)
        self.assertEqual(invocation["runtime_task_id"], self.compression_id)
        self.assertEqual(invocation["compression_id"], self.compression_id)


if __name__ == "__main__":
    unittest.main()
