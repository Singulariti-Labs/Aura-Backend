import logging
import os
from functools import lru_cache
from typing import TYPE_CHECKING

from composio_client import APIError, APIStatusError

if TYPE_CHECKING:
    from composio import Composio

logger = logging.getLogger(__name__)


class ConnectorError(Exception):
    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


def is_configured() -> bool:
    return bool(os.getenv("COMPOSIO_API_KEY", "").strip())


@lru_cache(maxsize=1)
def get_composio() -> "Composio":
    api_key = os.getenv("COMPOSIO_API_KEY", "").strip()
    if not api_key:
        raise ConnectorError("Connectors are unavailable on this server.", 503)

    try:
        from composio import Composio

        # A timed-out action may already have reached the provider.
        return Composio(api_key=api_key, timeout=30.0, max_retries=0)
    except RuntimeError:
        logger.error("Composio initialization failed. Check the SDK cache directory.")
        raise ConnectorError("Connectors are unavailable on this server.", 503) from None


def composio_user_id(user_id: str) -> str:
    return f"aura:{user_id}"


def provider_error(error: APIError, operation: str) -> ConnectorError:
    status = error.status_code if isinstance(error, APIStatusError) else None
    if status in (401, 403):
        logger.warning(
            "Composio %s rejected the server credential (upstream status: %s). "
            "Check COMPOSIO_API_KEY and its project permissions, then restart Aura.",
            operation,
            status,
        )
        return ConnectorError("The server's Composio project credential was rejected.", 503)
    logger.warning("Composio %s failed (upstream status: %s)", operation, status)
    if status == 429:
        return ConnectorError("Connector service is rate limited. Try again shortly.", 429)
    return ConnectorError("The connector service could not complete the request.", 502)
