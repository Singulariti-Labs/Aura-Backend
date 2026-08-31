"""Internal errors and safe public error responses for memory consolidation."""

from __future__ import annotations

from fastapi.responses import JSONResponse

from app.GraphMemory.schemas import ConsolidationError, ConsolidationErrorResponse


class MemoryProviderError(RuntimeError):
    """The configured provider failed to complete the structured request."""


class MemoryOutputValidationError(ValueError):
    """The provider returned structurally or semantically unsafe output."""


def build_error_response(
    *,
    status_code: int,
    code: str,
    message: str,
    retryable: bool,
    episode_id: str,
    request_id: str,
) -> JSONResponse:
    """Create the stable error envelope used by every memory API failure."""

    body = ConsolidationErrorResponse(
        error=ConsolidationError(
            code=code,
            message=message,
            retryable=retryable,
            episodeId=episode_id or "unknown",
            requestId=request_id,
        )
    )
    return JSONResponse(
        status_code=status_code,
        content=body.model_dump(mode="json", by_alias=True),
        headers={
            "X-Request-ID": request_id,
            "Cache-Control": "no-store",
        },
    )


def provider_http_status(exc: BaseException) -> int | None:
    """Read an HTTP status from a provider exception without importing its SDK."""

    current: BaseException | None = exc
    visited: set[int] = set()
    while current is not None and id(current) not in visited:
        visited.add(id(current))
        for attribute in ("status_code", "status"):
            value = getattr(current, attribute, None)
            if isinstance(value, int):
                return value
        current = current.__cause__ or current.__context__
    return None
