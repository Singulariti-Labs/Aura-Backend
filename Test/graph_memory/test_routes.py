import asyncio
import json
import unittest
from unittest.mock import AsyncMock, patch

from starlette.requests import Request

from app.GraphMemory.config import MemorySettings
from app.GraphMemory.routes import handle_memory_consolidation_request
from app.GraphMemory.schemas import ConsolidationApiResponse, ConsolidationExtraction
from app.Types.agent_types import LLMConfig
from Test.graph_memory.test_schemas import valid_request_payload
from Test.graph_memory.test_validator import valid_extraction_payload


def make_request(payload, *, headers=None):
    body = json.dumps(payload).encode("utf-8")
    raw_headers = {
        "authorization": "Bearer test-token",
        "content-type": "application/json",
        "content-length": str(len(body)),
        **(headers or {}),
    }
    scope = {
        "type": "http",
        "http_version": "1.1",
        "method": "POST",
        "scheme": "https",
        "path": "/v1/memory/consolidate",
        "raw_path": b"/v1/memory/consolidate",
        "query_string": b"",
        "headers": [
            (key.lower().encode("ascii"), value.encode("ascii"))
            for key, value in raw_headers.items()
        ],
        "client": ("127.0.0.1", 12345),
        "server": ("testserver", 443),
    }
    sent = False

    async def receive():
        nonlocal sent
        if sent:
            return {"type": "http.disconnect"}
        sent = True
        return {"type": "http.request", "body": body, "more_body": False}

    return Request(scope, receive)


def settings(*, timeout_seconds=30.0, max_body_bytes=1_048_576):
    return MemorySettings(
        llm_config=LLMConfig(
            provider="anthropic",
            model_name="claude-haiku-4-5-20251001",
        ),
        api_key=None,
        timeout_seconds=timeout_seconds,
        max_body_bytes=max_body_bytes,
    )


class FakeService:
    async def consolidate(self, api_request, **kwargs):
        extraction = ConsolidationExtraction.model_validate(
            valid_extraction_payload()
        )
        return ConsolidationApiResponse(extraction=extraction)


class SlowService:
    async def consolidate(self, api_request, **kwargs):
        await asyncio.sleep(1)


class MemoryRoutesTests(unittest.IsolatedAsyncioTestCase):
    async def test_returns_valid_extraction_and_request_id(self):
        request = make_request(
            valid_request_payload(),
            headers={"x-request-id": "req-client-123"},
        )
        with (
            patch(
                "app.GraphMemory.routes._authenticate_request",
                new=AsyncMock(return_value={"sub": "auth0|123"}),
            ),
            patch("app.GraphMemory.routes.get_pool", new=AsyncMock(return_value=object())),
            patch(
                "app.GraphMemory.routes.get_user_by_auth0_id",
                new=AsyncMock(return_value={"id": "user-1"}),
            ),
            patch("app.GraphMemory.routes.get_memory_settings", return_value=settings()),
            patch("app.GraphMemory.routes.get_memory_service", return_value=FakeService()),
        ):
            response = await handle_memory_consolidation_request(request)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["x-request-id"], "req-client-123")
        self.assertEqual(json.loads(response.body)["extraction"]["episodeId"], "task-123")

    async def test_rejects_oversized_payload_before_model_validation(self):
        request = make_request(
            valid_request_payload(),
            headers={"content-length": "1000"},
        )
        with (
            patch(
                "app.GraphMemory.routes._authenticate_request",
                new=AsyncMock(return_value={"sub": "auth0|123"}),
            ),
            patch("app.GraphMemory.routes.get_pool", new=AsyncMock(return_value=object())),
            patch(
                "app.GraphMemory.routes.get_user_by_auth0_id",
                new=AsyncMock(return_value={"id": "user-1"}),
            ),
            patch(
                "app.GraphMemory.routes.get_memory_settings",
                return_value=settings(max_body_bytes=100),
            ),
        ):
            response = await handle_memory_consolidation_request(request)

        self.assertEqual(response.status_code, 413)
        self.assertFalse(json.loads(response.body)["error"]["retryable"])

    async def test_enforces_the_configured_request_timeout(self):
        request = make_request(valid_request_payload())
        with (
            patch(
                "app.GraphMemory.routes._authenticate_request",
                new=AsyncMock(return_value={"sub": "auth0|123"}),
            ),
            patch("app.GraphMemory.routes.get_pool", new=AsyncMock(return_value=object())),
            patch(
                "app.GraphMemory.routes.get_user_by_auth0_id",
                new=AsyncMock(return_value={"id": "user-1"}),
            ),
            patch(
                "app.GraphMemory.routes.get_memory_settings",
                return_value=settings(timeout_seconds=0.01),
            ),
            patch("app.GraphMemory.routes.get_memory_service", return_value=SlowService()),
        ):
            response = await handle_memory_consolidation_request(request)

        error = json.loads(response.body)["error"]
        self.assertEqual(response.status_code, 504)
        self.assertEqual(error["episodeId"], "task-123")
        self.assertTrue(error["retryable"])


if __name__ == "__main__":
    unittest.main()
