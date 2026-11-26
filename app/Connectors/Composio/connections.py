import os

from composio_client import APIStatusError, NotFoundError

from .account_identity import resolve_account_label
from .client import ConnectorError, composio_user_id, get_composio
from .models import Connection, ConnectionPage, ConnectLink


def list_connections(
    user_id: str,
    limit: int = 50,
    cursor: str | None = None,
    include_identity: bool = False,
    *,
    toolkit: str | None = None,
    connection_id: str | None = None,
    active_only: bool = False,
) -> ConnectionPage:
    # Composio applies the user filter even when an account ID is supplied.
    owner = composio_user_id(user_id)
    filters = {"user_ids": [owner], "account_type": "PRIVATE"}
    if cursor is not None:
        filters["cursor"] = cursor
    if toolkit is not None:
        filters["toolkit_slugs"] = [toolkit]
    if connection_id is not None:
        filters["connected_account_ids"] = [connection_id]
    if active_only:
        filters["statuses"] = ["ACTIVE"]

    page = get_composio().connected_accounts.list(limit=limit, **filters)
    items = []
    for item in page.items:
        if getattr(item, "user_id", None) not in (None, "", owner):
            continue
        if connection_id is not None and item.id != connection_id:
            continue
        account = Connection(
            id=item.id,
            toolkit=item.toolkit.slug.lower(),
            alias=item.alias,
            status=item.status,
            is_disabled=item.is_disabled,
            created_at=item.created_at,
        )
        if include_identity:
            account.account_label = resolve_account_label(owner, account, getattr(item, "state", None))
        items.append(account)
    return ConnectionPage(items=items, next_cursor=page.next_cursor)


def get_connection(user_id: str, connection_id: str, include_identity: bool = False) -> Connection:
    page = list_connections(user_id, include_identity=include_identity, connection_id=connection_id)
    if not page.items:
        raise ConnectorError("Connection not found.", 404)
    return page.items[0]


def start_connection(user_id: str, toolkit: str, alias: str | None = None) -> ConnectLink:
    client = get_composio()
    try:
        details = client.toolkits.get(toolkit)
    except NotFoundError:
        raise ConnectorError("Connector app not found.", 404) from None
    if getattr(details, "no_auth", False):
        raise ConnectorError("This app does not require an account connection.", 409)

    session = client.create(
        user_id=composio_user_id(user_id),
        toolkits=[toolkit],
        workbench={"enable": False},
    )
    try:
        link = session.authorize(
            toolkit,
            alias=alias,
            callback_url=os.getenv("COMPOSIO_CALLBACK_URL", "").strip() or None,
        )
    except APIStatusError as error:
        if error.status_code in (400, 404, 409, 422):
            raise ConnectorError(
                "Authorization could not start. Check the account alias or the app's Auth Config.",
                error.status_code,
            ) from None
        raise
    if not link.redirect_url or not link.redirect_url.startswith("https://"):
        raise ConnectorError("The connector service did not return a secure Connect Link.", 502)
    return ConnectLink(connection_id=link.id, redirect_url=link.redirect_url)


def disconnect(user_id: str, connection_id: str) -> None:
    get_connection(user_id, connection_id)
    try:
        get_composio().connected_accounts.delete(connection_id)
    except NotFoundError:
        # Another request already removed the verified account.
        pass
