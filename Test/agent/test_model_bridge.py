import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from langchain_core.tools import StructuredTool
from pydantic import BaseModel

from app.LLM.llm_factory import LLMFactory
from app.LLM.memory import Memory
from app.LLM.model_bridge.anthropic import anthropic_message_formater
from app.LLM.model_bridge.gemini import (
    DUMMY_THOUGHT_SIGNATURE,
    gemini_message_formater,
    gemini_tool_formater,
)
from app.LLM.model_bridge.openai import openai_message_formater


HISTORY = [
    {
        "role": "user",
        "content": [{"type": "text", "text": "Take a screenshot."}],
    },
    {
        "role": "assistant",
        "content": [
            {"type": "text", "text": "I will capture it."},
            {
                "type": "tool_call",
                "tool_call_id": "call_1",
                "name": "screenshot",
                "input": {},
            },
        ],
    },
    {
        "role": "tool",
        "tool_call_id": "call_1",
        "tool_name": "screenshot",
        "is_error": False,
        "content": [
            {
                "type": "image",
                "media_type": "image/png",
                "image_url": "data:image/png;base64,abc",
            },
            {"type": "text", "text": "{\"success\": true}"},
        ],
    },
]


class WeatherInput(BaseModel):
    location: str


class Dumpable:
    def __init__(self, value):
        self.value = value

    def model_dump(self, exclude_none=True):
        if not exclude_none:
            return dict(self.value)
        return {key: value for key, value in self.value.items() if value is not None}


class ProviderMessageFormatterTests(unittest.TestCase):
    def test_anthropic_converts_tool_call_and_rich_result(self):
        messages = anthropic_message_formater(HISTORY)

        self.assertEqual(messages[1]["content"][1]["type"], "tool_use")
        self.assertEqual(messages[1]["content"][1]["id"], "call_1")
        self.assertEqual(messages[2]["role"], "user")
        tool_result = messages[2]["content"][0]
        self.assertEqual(tool_result["type"], "tool_result")
        self.assertEqual(tool_result["content"][0]["type"], "image")
        self.assertEqual(tool_result["content"][0]["source"]["data"], "abc")

    def test_openai_uses_chat_completion_tool_messages(self):
        messages = openai_message_formater(HISTORY, system_prompt="Be helpful.")

        self.assertEqual(messages[0], {"role": "system", "content": "Be helpful."})
        assistant = messages[2]
        self.assertEqual(assistant["tool_calls"][0]["id"], "call_1")
        self.assertEqual(
            json.loads(assistant["tool_calls"][0]["function"]["arguments"]),
            {},
        )
        self.assertEqual(messages[3]["role"], "tool")
        self.assertEqual(messages[3]["tool_call_id"], "call_1")
        self.assertEqual(messages[4]["role"], "user")
        self.assertEqual(messages[4]["content"][1]["type"], "image_url")

    def test_openai_file_data_uses_complete_data_url(self):
        messages = openai_message_formater(
            [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "document",
                            "media_type": "application/pdf",
                            "document_url": "data:application/pdf;base64,PDF_DATA",
                        }
                    ],
                }
            ]
        )

        self.assertEqual(
            messages[0]["content"][0]["file"]["file_data"],
            "data:application/pdf;base64,PDF_DATA",
        )

    def test_gemini_uses_function_parts_and_import_signature(self):
        contents = gemini_message_formater(HISTORY)

        function_part = contents[1]["parts"][1]
        self.assertEqual(function_part["function_call"]["name"], "screenshot")
        self.assertEqual(
            function_part["thought_signature"],
            DUMMY_THOUGHT_SIGNATURE,
        )
        response = contents[2]["parts"][0]["function_response"]
        self.assertEqual(response["id"], "call_1")
        self.assertEqual(response["parts"][0]["inline_data"]["data"], "abc")

    def test_gemini_tool_schema_uses_json_schema(self):
        tool = StructuredTool.from_function(
            name="weather",
            description="Get weather for a location.",
            func=lambda location: location,
            args_schema=WeatherInput,
        )
        formatted = gemini_tool_formater([tool])
        declaration = formatted[0]["function_declarations"][0]

        self.assertEqual(declaration["name"], "weather")
        self.assertEqual(
            declaration["parameters_json_schema"]["properties"]["location"]["type"],
            "string",
        )


class AuraInvokerTests(unittest.IsolatedAsyncioTestCase):
    async def test_openai_native_tool_loop_returns_final_output(self):
        first_response = SimpleNamespace(
            model="gpt-4.1",
            usage=Dumpable(
                {
                    "prompt_tokens": 10,
                    "completion_tokens": 5,
                    "total_tokens": 15,
                }
            ),
            choices=[
                SimpleNamespace(
                    finish_reason="tool_calls",
                    message=Dumpable(
                        {
                            "role": "assistant",
                            "content": "I will check.",
                            "tool_calls": [
                                {
                                    "id": "call_weather",
                                    "type": "function",
                                    "function": {
                                        "name": "weather",
                                        "arguments": "{\"location\":\"Paris\"}",
                                    },
                                }
                            ],
                        }
                    ),
                )
            ],
        )
        final_response = SimpleNamespace(
            model="gpt-4.1",
            usage=Dumpable(
                {
                    "prompt_tokens": 20,
                    "completion_tokens": 7,
                    "total_tokens": 27,
                }
            ),
            choices=[
                SimpleNamespace(
                    finish_reason="stop",
                    message=Dumpable(
                        {
                            "role": "assistant",
                            "content": "Paris is 18C.",
                        }
                    ),
                )
            ],
        )
        create = AsyncMock(side_effect=[first_response, final_response])
        fake_llm = SimpleNamespace(
            model_name="gpt-4.1",
            max_tokens=1024,
            root_async_client=SimpleNamespace(
                chat=SimpleNamespace(
                    completions=SimpleNamespace(create=create),
                )
            ),
        )

        async def weather(location: str):
            return {"success": True, "temperature": 18, "location": location}

        tool = StructuredTool.from_function(
            name="weather",
            description="Get weather for a location.",
            coroutine=weather,
            func=lambda location: None,
            args_schema=WeatherInput,
        )
        factory = LLMFactory(memory=Memory())

        result = await factory.aura_invoker(
            system_prompt="Use tools when needed.",
            llm=fake_llm,
            query="Weather in Paris?",
            llm_provider="openai",
            agent_type="aura",
            tools=[tool],
            history=[],
        )

        self.assertEqual(result["output"], "Paris is 18C.")
        self.assertEqual(result["iterations"], 2)
        self.assertEqual(len(result["intermediate_steps"]), 1)
        self.assertEqual(result["usage"]["total_tokens"], 42)
        second_request = create.await_args_list[1].kwargs
        self.assertEqual(second_request["messages"][-1]["role"], "tool")
        self.assertEqual(
            second_request["messages"][-1]["tool_call_id"],
            "call_weather",
        )
        self.assertTrue(
            any(message.tool_calls for message in factory.memory.messages)
        )


if __name__ == "__main__":
    unittest.main()
