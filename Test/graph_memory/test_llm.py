import unittest

from app.GraphMemory.config import MemorySettings
from app.GraphMemory.llm import StructuredMemoryLLM
from app.GraphMemory.provider_invoker import NativeMemoryResponse
from app.GraphMemory.schemas import ConsolidationApiRequest, ConsolidationExtraction
from app.Types.agent_types import LLMConfig
from Test.graph_memory.test_schemas import valid_request_payload
from Test.graph_memory.test_validator import valid_extraction_payload


class FakeProviderInvoker:
    def __init__(self, result):
        self.result = result
        self.calls = []

    async def invoke(self, **kwargs):
        self.calls.append(kwargs)
        return self.result


class StructuredMemoryLLMTests(unittest.IsolatedAsyncioTestCase):
    async def test_makes_exactly_one_structured_request_with_dedicated_prompt(self):
        fake = FakeProviderInvoker(
            NativeMemoryResponse(
                payload=valid_extraction_payload(),
                usage={"input": 120, "output": 35},
            )
        )
        settings = MemorySettings(
            llm_config=LLMConfig(
                provider="anthropic",
                model_name="claude-haiku-4-5-20251001",
            ),
            api_key=None,
        )
        client = StructuredMemoryLLM(settings, provider_invoker=fake)
        source = ConsolidationApiRequest.model_validate(valid_request_payload()).request

        result = await client.extract(source)

        self.assertEqual(len(fake.calls), 1)
        self.assertIn("graph-memory consolidation extractor", fake.calls[0]["system_prompt"])
        self.assertIn("Collection limits and JSON shape", fake.calls[0]["system_prompt"])
        self.assertIn("Never serialize or encode an array", fake.calls[0]["system_prompt"])
        self.assertIn('"schemaVersion":1', fake.calls[0]["user_prompt"])
        self.assertNotIn("Collection limits and JSON shape", fake.calls[0]["user_prompt"])
        self.assertEqual(fake.calls[0]["output_schema"]["type"], "object")
        self.assertEqual(result.extraction.episode_id, "task-123")
        self.assertEqual(result.usage, {"input": 120, "output": 35})

    async def test_redacts_credentials_before_sending_observations(self):
        fake = FakeProviderInvoker(
            NativeMemoryResponse(payload=valid_extraction_payload(), usage=None)
        )
        settings = MemorySettings(
            llm_config=LLMConfig(
                provider="anthropic",
                model_name="claude-haiku-4-5-20251001",
            ),
            api_key=None,
        )
        client = StructuredMemoryLLM(settings, provider_invoker=fake)
        payload = valid_request_payload()
        payload["request"]["observations"][0]["content"] = (
            "Accidentally printed sk-abcdefghijklmnopqrstuvwxyz123456"
        )
        source = ConsolidationApiRequest.model_validate(payload).request

        await client.extract(source)

        provider_message = fake.calls[0]["user_prompt"]
        self.assertNotIn("sk-abcdefghijklmnopqrstuvwxyz123456", provider_message)
        self.assertIn("[REDACTED_SECRET]", provider_message)


if __name__ == "__main__":
    unittest.main()
