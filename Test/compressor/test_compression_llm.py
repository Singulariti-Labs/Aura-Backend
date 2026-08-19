import unittest
from types import SimpleNamespace

from app.Context.compression_llm import AnthropicCompressionService


class FakeMessages:
    def __init__(self, response):
        self.response = response
        self.request = None

    async def create(self, **request):
        self.request = request
        return self.response


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


if __name__ == "__main__":
    unittest.main()
