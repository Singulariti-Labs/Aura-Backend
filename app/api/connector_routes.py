from typing import Annotated

from composio_client import APIError
from fastapi import APIRouter, Depends, HTTPException, Path, Query, Response
from starlette.concurrency import run_in_threadpool

from app.Connectors.Composio import catalog, connections
from app.Connectors.Composio.client import ConnectorError, provider_error
from app.Connectors.Composio.models import (
    CONNECTION_PATTERN,
    TOOLKIT_PATTERN,
    Connection,
    ConnectionPage,
    ConnectLink,
    ConnectRequest,
    ToolkitPage,
)
from app.DB.pool import get_pool
from app.DB.Queries.user import get_user_by_auth0_id
from app.api.auth_utils import get_current_user

connector_router = APIRouter(prefix="/api/connectors", tags=["connectors"])


async def connector_user_id(current_user: dict = Depends(get_current_user)) -> str:
    subject = current_user.get("sub")
    if not subject:
        raise HTTPException(401, "Missing authenticated user identity.")
    user = await get_user_by_auth0_id(await get_pool(), subject)
    if not user:
        raise HTTPException(404, "User record not found. Please sync first.")
    return str(user["id"])


async def call_connector(operation, *args):
    try:
        return await run_in_threadpool(operation, *args)
    except ConnectorError as error:
        raise HTTPException(error.status_code, str(error)) from None
    except APIError as error:
        failure = provider_error(error, operation.__name__)
        raise HTTPException(failure.status_code, str(failure)) from None


UserId = Annotated[str, Depends(connector_user_id)]
PageLimit = Annotated[int, Query(ge=1, le=100)]
PageCursor = Annotated[str | None, Query(min_length=1, max_length=2048)]
ConnectionId = Annotated[str, Path(pattern=CONNECTION_PATTERN)]
ToolkitSlug = Annotated[str, Path(pattern=TOOLKIT_PATTERN)]


@connector_router.get("/catalog", dependencies=[Depends(connector_user_id)])
async def get_catalog(
    limit: PageLimit = 50,
    cursor: PageCursor = None,
    category: Annotated[str | None, Query(min_length=1, max_length=100)] = None,
) -> ToolkitPage:
    return await call_connector(catalog.list_toolkits, limit, cursor, category)


@connector_router.get("/connections")
async def get_connections(
    user_id: UserId, limit: PageLimit = 50, cursor: PageCursor = None
) -> ConnectionPage:
    return await call_connector(connections.list_connections, user_id, limit, cursor, True)


@connector_router.post("/catalog/{toolkit}/connect", status_code=201)
async def connect_app(
    toolkit: ToolkitSlug,
    user_id: UserId,
    response: Response,
    request: ConnectRequest | None = None,
) -> ConnectLink:
    response.headers["Cache-Control"] = "no-store"
    alias = request.alias if request else None
    return await call_connector(connections.start_connection, user_id, toolkit, alias)


@connector_router.get("/connections/{connection_id}")
async def get_connection(connection_id: ConnectionId, user_id: UserId) -> Connection:
    return await call_connector(connections.get_connection, user_id, connection_id, True)


@connector_router.delete("/connections/{connection_id}", status_code=204)
async def disconnect_app(connection_id: ConnectionId, user_id: UserId) -> Response:
    await call_connector(connections.disconnect, user_id, connection_id)
    return Response(status_code=204)
