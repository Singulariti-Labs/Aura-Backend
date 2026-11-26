from composio_client import APIError

from .client import ConnectorError, composio_user_id, get_composio, provider_error
from .connections import get_connection, list_connections


class ConnectorActions:
    """Tool discovery state scoped to one authenticated Aura task."""

    def __init__(self, user_id: str):
        self.user_id = user_id
        self.discovered: dict[str, str] = {}

    def _session(self, toolkits: list[str], accounts: dict[str, str] | None = None):
        return get_composio().create(
            user_id=composio_user_id(self.user_id),
            toolkits=toolkits,
            connected_accounts=accounts,
            tags={"enable": ["readOnlyHint"], "disable": ["destructiveHint"]},
            manage_connections=False,
            workbench={"enable": False, "enable_proxy_execution": False},
        )

    def discover(self, query: str, toolkit: str | None) -> dict:
        accounts = []
        cursor = None
        try:
            while True:
                page = list_connections(
                    self.user_id, cursor=cursor, toolkit=toolkit, active_only=True
                )
                accounts.extend(account for account in page.items if account.is_active)
                cursor = page.next_cursor
                if not cursor:
                    break
            if not accounts:
                return {
                    "connections": [],
                    "actions": [],
                    "message": "Connect an account in Aura's Apps settings and try again.",
                }

            toolkits = sorted({account.toolkit for account in accounts})
            result = self._session(toolkits).search(query=query)
        except APIError as error:
            raise provider_error(error, "search") from None

        if not result.success:
            raise ConnectorError("Connector action search failed. Try again shortly.", 502)

        actions = []
        for definition in result.tool_schemas.values():
            slug = definition.tool_slug
            toolkit_slug = definition.toolkit.lower()
            if (
                toolkit_slug not in toolkits
                or slug.upper().startswith("COMPOSIO_")
                or definition.input_schema is None
            ):
                continue
            self.discovered[slug] = toolkit_slug
            actions.append({
                "slug": slug,
                "toolkit": toolkit_slug,
                "description": definition.description,
                "input_schema": definition.input_schema,
            })
        return {
            "connections": [
                {"id": account.id, "toolkit": account.toolkit, "alias": account.alias}
                for account in accounts
            ],
            "actions": actions,
        }

    def execute(self, slug: str, connection_id: str, arguments: dict) -> dict:
        toolkit = self.discovered.get(slug)
        if toolkit is None:
            raise ConnectorError("Discover this connector action before executing it.")

        try:
            account = get_connection(self.user_id, connection_id)
            if account.toolkit != toolkit or not account.is_active:
                raise ConnectorError("Select an active connection for this app.", 409)
            session = self._session([toolkit], {toolkit: account.id})
            result = session.execute(slug, arguments=arguments)
        except APIError as error:
            raise provider_error(error, "execute") from None

        if result.error or getattr(result, "result_type", "completed") != "completed":
            raise ConnectorError(
                "The app could not complete this action. Check the inputs and account permissions.",
                502,
            )
        return {"data": result.data}
