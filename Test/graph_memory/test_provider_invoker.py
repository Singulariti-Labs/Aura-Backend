import json
import unittest
from types import SimpleNamespace

from app.GraphMemory.config import MemorySettings
from app.GraphMemory.provider_invoker import (
    MemoryProviderInvoker,
    _build_provider_json_schema,
)
from app.GraphMemory.schemas import ConsolidationExtraction
from app.Types.agent_types import LLMConfig
from Test.graph_memory.test_validator import valid_extraction_payload


class FakeAnthropicMessages:
    def __init__(self, response):
        self.response = response
        self.calls = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        return self.response


class FakeGeminiModels:
    def __init__(self, response):
        self.response = response
        self.calls = []

    async def generate_content(self, **kwargs):
        self.calls.append(kwargs)
        return self.response


def memory_settings(provider: str, model_name: str) -> MemorySettings:
    return MemorySettings(
        llm_config=LLMConfig(provider=provider, model_name=model_name),
        api_key="test-key",
    )


class NativeMemoryProviderTests(unittest.IsolatedAsyncioTestCase):
    async def test_anthropic_uses_native_json_schema_and_usage(self):
        response = SimpleNamespace(
            content=[
                SimpleNamespace(
                    type="text",
                    text=json.dumps(valid_extraction_payload()),
                )
            ],
            usage=SimpleNamespace(input_tokens=101, output_tokens=29),
        )
        messages = FakeAnthropicMessages(response)
        invoker = MemoryProviderInvoker(
            memory_settings("anthropic", "claude-haiku-4-5-20251001"),
            anthropic_client=SimpleNamespace(messages=messages),
        )

        result = await invoker.invoke(
            system_prompt="system",
            user_prompt="episode",
            output_schema=ConsolidationExtraction.model_json_schema(by_alias=True),
        )

        self.assertEqual(len(messages.calls), 1)
        call = messages.calls[0]
        self.assertEqual(call["output_config"]["format"]["type"], "json_schema")
        schema = call["output_config"]["format"]["schema"]
        self.assertEqual(schema["properties"]["facts"]["type"], "array")
        self.assertNotIn("maxItems", schema["properties"]["facts"])
        self.assertNotIn(
            "maxItems",
            schema["$defs"]["ExtractedFact"]["properties"]["sourceObservationIds"],
        )
        self.assertEqual(result.payload["episodeId"], "task-123")
        self.assertEqual(result.usage, {"input": 101, "output": 29})

    async def test_gemini_uses_native_json_schema_and_usage(self):
        response = SimpleNamespace(
            text=json.dumps(valid_extraction_payload()),
            usage_metadata=SimpleNamespace(
                prompt_token_count=88,
                candidates_token_count=21,
            ),
        )
        models = FakeGeminiModels(response)
        invoker = MemoryProviderInvoker(
            memory_settings("google", "gemini-3-flash-preview"),
            gemini_client=SimpleNamespace(models=models),
        )

        result = await invoker.invoke(
            system_prompt="system",
            user_prompt="episode",
            output_schema=ConsolidationExtraction.model_json_schema(by_alias=True),
        )

        self.assertEqual(len(models.calls), 1)
        config = models.calls[0]["config"]
        self.assertEqual(config["response_mime_type"], "application/json")
        self.assertEqual(config["response_json_schema"]["properties"]["facts"]["type"], "array")
        self.assertEqual(
            config["response_json_schema"]["properties"]["facts"]["maxItems"],
            200,
        )
        self.assertEqual(result.payload["episodeId"], "task-123")
        self.assertEqual(result.usage, {"input": 88, "output": 21})


class ProviderSchemaTests(unittest.TestCase):
    def test_schema_keeps_named_properties_and_requires_all_fields(self):
        schema = _build_provider_json_schema(
            ConsolidationExtraction.model_json_schema(by_alias=True),
            provider="anthropic",
        )

        self.assertIn("episodeId", schema["properties"])
        self.assertIn("ExtractedFact", schema["$defs"])
        self.assertEqual(schema["properties"]["facts"]["type"], "array")
        self.assertEqual(
            set(schema["required"]),
            set(schema["properties"]),
        )
        self.assertNotIn("default", schema["$defs"]["ExtractedFact"]["properties"]["relation"])


if __name__ == "__main__":
    unittest.main()
