"""Secure HTTP handling delegated by ``app.api.rest_routes``."""

from __future__ import annotations

import asyncio
from functools import lru_cache
import logging
import re
import uuid
from typing import Optional

from fastapi import Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from app.DB.Queries.user import get_user_by_auth0_id
from app.DB.pool import get_pool
from app.GraphMemory.config import MemoryConfigurationError, MemorySettings
from app.GraphMemory.errors import (
    MemoryOutputValidationError,
    MemoryProviderError,
    build_error_response,
    provider_http_status,
)
from app.GraphMemory.schemas import ConsolidationApiRequest
from app.GraphMemory.service import MemoryConsolidationService
from app.api.auth_utils import token_verifier


logger = logging.getLogger(__name__)
_SAFE_REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{1,128}$")


@lru_cache(maxsize=1)
def get_memory_settings() -> MemorySettings:
    """Load immutable memory settings once for consistent concurrent behavior."""

    return MemorySettings.from_env()


@lru_cache(maxsize=1)
def get_memory_service() -> MemoryConsolidationService:
    """Reuse provider HTTP clients while keeping each request state isolated."""

    settings = get_memory_settings()
    return MemoryConsolidationService(settings)


async def handle_memory_consolidation_request(request: Request) -> JSONResponse:
    """Authenticate, validate, consolidate, and return a safe HTTP response.

    This endpoint deliberately performs no application rate-limit check because
    it runs only after an already-admitted task completes. The provider call is
    still bounded to 30 seconds and provider-side 429 errors remain retryable.
    """

    request_id = _get_request_id(request)
    episode_id = "unknown"

    try:
        auth_payload = await _authenticate_request(request)
        pool = await get_pool()
        user = await get_user_by_auth0_id(pool, auth_payload["sub"])
        if not user:
            return build_error_response(
                status_code=403,
                code="AUTHENTICATION_FAILED",
                message="The authenticated user is not authorized for this service.",
                retryable=False,
                episode_id=episode_id,
                request_id=request_id,
            )

        settings = get_memory_settings()
        raw_body = await _read_bounded_json_body(request, settings.max_body_bytes)
        try:
            api_request = ConsolidationApiRequest.model_validate_json(raw_body)
        except ValidationError:
            return build_error_response(
                status_code=400,
                code="INVALID_MEMORY_REQUEST",
                message="The memory consolidation request body is invalid.",
                retryable=False,
                episode_id=episode_id,
                request_id=request_id,
            )

        episode_id = api_request.request.episode.id
        service = get_memory_service()
        try:
            async with asyncio.timeout(settings.timeout_seconds):
                response = await service.consolidate(
                    api_request,
                    pool=pool,
                    user_id=str(user["id"]),
                    event_loop=asyncio.get_running_loop(),
                )
        except TimeoutError:
            logger.warning(
                "Memory consolidation timed out request_id=%s episode_id=%s",
                request_id,
                episode_id,
            )
            return build_error_response(
                status_code=504,
                code="MEMORY_EXTRACTION_FAILED",
                message="Unable to extract memory from this episode.",
                retryable=True,
                episode_id=episode_id,
                request_id=request_id,
            )
        except MemoryOutputValidationError:
            logger.warning(
                "Memory output validation failed request_id=%s episode_id=%s",
                request_id,
                episode_id,
                exc_info=True,
            )
            return build_error_response(
                status_code=502,
                code="MEMORY_EXTRACTION_FAILED",
                message="Unable to extract memory from this episode.",
                retryable=True,
                episode_id=episode_id,
                request_id=request_id,
            )
        except MemoryProviderError as exc:
            status_code = 429 if provider_http_status(exc) == 429 else 502
            logger.warning(
                "Memory provider failed request_id=%s episode_id=%s status=%s",
                request_id,
                episode_id,
                status_code,
                exc_info=True,
            )
            return build_error_response(
                status_code=status_code,
                code=(
                    "MEMORY_PROVIDER_RATE_LIMITED"
                    if status_code == 429
                    else "MEMORY_EXTRACTION_FAILED"
                ),
                message="Unable to extract memory from this episode.",
                retryable=True,
                episode_id=episode_id,
                request_id=request_id,
            )

        logger.info(
            "Memory consolidation completed request_id=%s episode_id=%s",
            request_id,
            episode_id,
        )
        return JSONResponse(
            status_code=200,
            content=response.model_dump(mode="json", by_alias=True),
            headers={
                "X-Request-ID": request_id,
                "Cache-Control": "no-store",
            },
        )

    except _MemoryHttpError as exc:
        return build_error_response(
            status_code=exc.status_code,
            code=exc.code,
            message=exc.message,
            retryable=False,
            episode_id=episode_id,
            request_id=request_id,
        )
    except MemoryConfigurationError:
        logger.error("Memory consolidation is not configured", exc_info=True)
        return build_error_response(
            status_code=503,
            code="MEMORY_EXTRACTION_FAILED",
            message="Unable to extract memory from this episode.",
            retryable=True,
            episode_id=episode_id,
            request_id=request_id,
        )
    except Exception:
        logger.exception(
            "Unexpected memory consolidation failure request_id=%s episode_id=%s",
            request_id,
            episode_id,
        )
        return build_error_response(
            status_code=500,
            code="MEMORY_EXTRACTION_FAILED",
            message="Unable to extract memory from this episode.",
            retryable=True,
            episode_id=episode_id,
            request_id=request_id,
        )


class _MemoryHttpError(ValueError):
    """An expected client error that is safe to expose."""

    def __init__(self, status_code: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message


async def _authenticate_request(request: Request) -> dict:
    """Validate the Aura bearer token without logging or forwarding it."""

    authorization = request.headers.get("authorization", "")
    scheme, separator, token = authorization.partition(" ")
    if not separator or scheme.casefold() != "bearer" or not token.strip():
        raise _MemoryHttpError(
            401,
            "AUTHENTICATION_FAILED",
            "A valid bearer token is required.",
        )

    try:
        payload = await asyncio.to_thread(token_verifier.verify, token.strip())
    except Exception as exc:
        raise _MemoryHttpError(
            401,
            "AUTHENTICATION_FAILED",
            "The bearer token is invalid or expired.",
        ) from exc

    subject = payload.get("sub") if isinstance(payload, dict) else None
    if not isinstance(subject, str) or not subject:
        raise _MemoryHttpError(
            401,
            "AUTHENTICATION_FAILED",
            "The bearer token does not contain a valid user identity.",
        )
    return payload


async def _read_bounded_json_body(request: Request, max_body_bytes: int) -> bytes:
    """Stream the JSON body and stop as soon as its configured limit is crossed."""

    content_type = request.headers.get("content-type", "").split(";", 1)[0].strip()
    if content_type.casefold() != "application/json":
        raise _MemoryHttpError(
            400,
            "INVALID_MEMORY_REQUEST",
            "Content-Type must be application/json.",
        )
    content_encoding = request.headers.get("content-encoding", "identity")
    if content_encoding.casefold() not in {"", "identity"}:
        raise _MemoryHttpError(
            400,
            "INVALID_MEMORY_REQUEST",
            "Compressed request bodies are not supported.",
        )

    content_length = request.headers.get("content-length")
    if content_length:
        try:
            declared_size = int(content_length)
        except ValueError as exc:
            raise _MemoryHttpError(
                400,
                "INVALID_MEMORY_REQUEST",
                "Content-Length must be a valid integer.",
            ) from exc
        if declared_size < 0:
            raise _MemoryHttpError(
                400,
                "INVALID_MEMORY_REQUEST",
                "Content-Length cannot be negative.",
            )
        if declared_size > max_body_bytes:
            raise _MemoryHttpError(
                413,
                "MEMORY_PAYLOAD_TOO_LARGE",
                "The memory consolidation request is too large.",
            )

    chunks: list[bytes] = []
    received_size = 0
    async for chunk in request.stream():
        received_size += len(chunk)
        if received_size > max_body_bytes:
            raise _MemoryHttpError(
                413,
                "MEMORY_PAYLOAD_TOO_LARGE",
                "The memory consolidation request is too large.",
            )
        chunks.append(chunk)

    body = b"".join(chunks)
    if not body:
        raise _MemoryHttpError(
            400,
            "INVALID_MEMORY_REQUEST",
            "The memory consolidation request body is required.",
        )
    return body


def _get_request_id(request: Request) -> str:
    supplied = request.headers.get("x-request-id", "").strip()
    if supplied and _SAFE_REQUEST_ID.fullmatch(supplied):
        return supplied
    return f"req-{uuid.uuid4()}"
