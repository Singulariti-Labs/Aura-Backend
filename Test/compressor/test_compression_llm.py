import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app.Context.compression_llm import (
    AnthropicCompressionService,
    OpenAICompressionService,
    get_compression_service,
)
from app.Context.models import CompressionConfig


class FakeMessages:
    def __init__(self, response):
        self.response = response
        self.request = None

    async def create(self, **request):
        self.request = request
        return self.response


class FakeResponses:
    def __init__(self, response):
        self.response = response
        self.request = None

    async def create(self, **request):
        self.request = request
        return self.response


class CompressionConfigurationTests(unittest.TestCase):
    def test_defaults_to_openai_luna(self):
        with patch.dict(
            "os.environ",
            {"COMPRESSION_PROVIDER": "openai", "COMPRESSION_MODEL": ""},
        ):
            config = CompressionConfig()

        self.assertEqual(config.compressor_provider, "openai")
        self.assertEqual(config.compressor_model, "gpt-5.6-luna")
        self.assertEqual(get_compression_service("openai").provider, "openai")

    def test_can_switch_back_to_anthropic(self):
        with patch.dict(
            "os.environ",
            {"COMPRESSION_PROVIDER": "anthropic", "COMPRESSION_MODEL": ""},
        ):
            config = CompressionConfig()

        self.assertEqual(config.compressor_provider, "anthropic")
        self.assertEqual(
            config.compressor_model,
            "claude-haiku-4-5-20251001",
        )
        self.assertEqual(get_compression_service("anthropic").provider, "anthropic")


class AnthropicCompressionServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_uses_dedicated_haiku_request_without_tools(self):
        response = SimpleNamespace(
            content=[SimpleNamespace(type="text", text="Compact summary")],
            usage=SimpleNamespace(input_tokens=120, output_tokens=18),
            stop_reason="end_turn",
        )
        messages = FakeMessages(response)
        service = AnthropicCompressionService(
            client=SimpleNamespace(messages=messages)
        )

        result = await service.summarize(
            "older messages",
            model="claude-3-5-haiku-20241022",
            max_output_tokens=4096,
        )

        self.assertEqual(result.summary, "Compact summary")
        self.assertEqual(result.input_tokens, 120)
        self.assertEqual(result.output_tokens, 18)
        self.assertEqual(
            messages.request["model"],
            "claude-3-5-haiku-20241022",
        )
        self.assertEqual(messages.request["max_tokens"], 4096)
        self.assertNotIn("tools", messages.request)

    async def test_rejects_truncated_compression(self):
        response = SimpleNamespace(
            content=[SimpleNamespace(type="text", text="Incomplete")],
            usage=SimpleNamespace(input_tokens=120, output_tokens=4096),
            stop_reason="max_tokens",
        )
        service = AnthropicCompressionService(
            client=SimpleNamespace(messages=FakeMessages(response))
        )

        with self.assertRaisesRegex(ValueError, "maximum output"):
            await service.summarize(
                "older messages",
                model="claude-3-5-haiku-20241022",
                max_output_tokens=4096,
            )


class OpenAICompressionServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_uses_responses_api_without_tools(self):
        response = SimpleNamespace(
            status="completed",
            incomplete_details=None,
            output=[
                SimpleNamespace(
                    type="message",
                    content=[
                        SimpleNamespace(type="output_text", text="Compact summary")
                    ],
                )
            ],
            usage=SimpleNamespace(input_tokens=120, output_tokens=18),
        )
        responses = FakeResponses(response)
        service = OpenAICompressionService(
            client=SimpleNamespace(responses=responses)
        )

        result = await service.summarize(
            "older messages",
            model="gpt-5.6-luna",
            max_output_tokens=4096,
        )

        self.assertEqual(result.summary, "Compact summary")
        self.assertEqual(result.input_tokens, 120)
        self.assertEqual(result.output_tokens, 18)
        self.assertEqual(responses.request["model"], "gpt-5.6-luna")
        self.assertEqual(responses.request["max_output_tokens"], 4096)
        self.assertFalse(responses.request["store"])
        self.assertNotIn("tools", responses.request)

    async def test_rejects_truncated_compression(self):
        response = SimpleNamespace(
            status="incomplete",
            incomplete_details=SimpleNamespace(reason="max_output_tokens"),
            output=[],
            usage=SimpleNamespace(input_tokens=120, output_tokens=4096),
        )
        service = OpenAICompressionService(
            client=SimpleNamespace(responses=FakeResponses(response))
        )

        with self.assertRaisesRegex(ValueError, "maximum output"):
            await service.summarize(
                "older messages",
                model="gpt-5.6-luna",
                max_output_tokens=4096,
            )


if __name__ == "__main__":
    unittest.main()
