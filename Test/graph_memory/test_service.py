import unittest
from decimal import Decimal
from unittest.mock import AsyncMock, patch

from app.GraphMemory.config import MemorySettings
from app.GraphMemory.llm import MemoryLLMResult
from app.GraphMemory.schemas import ConsolidationApiRequest, ConsolidationExtraction
from app.GraphMemory.service import MemoryConsolidationService
from app.Types.agent_types import LLMConfig
from Test.graph_memory.test_schemas import valid_request_payload
from Test.graph_memory.test_validator import valid_extraction_payload


class MemoryConsolidationServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_validates_and_wraps_extraction_without_rate_limit_check(self):
        extraction = ConsolidationExtraction.model_validate(valid_extraction_payload())
        memory_llm = AsyncMock()
        memory_llm.extract.return_value = MemoryLLMResult(
            extraction=extraction,
            usage={"input": 10, "output": 5},
        )
        settings = MemorySettings(
            llm_config=LLMConfig(
                provider="anthropic",
                model_name="claude-haiku-4-5-20251001",
            ),
            api_key=None,
        )
        service = MemoryConsolidationService(settings, memory_llm=memory_llm)
        request = ConsolidationApiRequest.model_validate(valid_request_payload())

        with patch(
            "app.GraphMemory.service.schedule_token_usage_update"
        ) as schedule_usage:
            response = await service.consolidate(
                request,
                pool=object(),
                user_id="user-1",
            )

        self.assertEqual(response.extraction.episode_id, "task-123")
        memory_llm.extract.assert_awaited_once_with(request.request)
        schedule_usage.assert_called_once()
        scheduled_usage = schedule_usage.call_args.kwargs["usage"]
        self.assertEqual(scheduled_usage["input"], 10)
        self.assertEqual(scheduled_usage["output"], 5)
        self.assertEqual(scheduled_usage["total_tokens"], 15)
        self.assertEqual(Decimal(str(scheduled_usage["cost"])), Decimal("0.000035"))
        self.assertEqual(
            schedule_usage.call_args.kwargs["details"],
            {
                "provider": "anthropic",
                "model_name": "claude-haiku-4-5-20251001",
                "credential_source": "platform",
            },
        )


if __name__ == "__main__":
    unittest.main()
