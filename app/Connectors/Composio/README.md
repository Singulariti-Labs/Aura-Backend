# Aura connectors server contract

Set `COMPOSIO_API_KEY` in the Aura server environment and install the pinned dependencies. Use a different Composio project for each deployment environment. On hosts with a read-only home directory, set `COMPOSIO_CACHE_DIR` to a writable directory. Apps without managed authentication need an appropriate Auth Config in the Composio project.

## Client integration

Send Aura’s existing `Authorization: Bearer <token>` header on all `/api/connectors` requests. The server resolves the internal Aura user ID from Auth0. Composio is the source of truth for connection status.

| Method and path | Client behavior |
| --- | --- |
| `GET /api/connectors/catalog` | Display the app catalog. Accepts `limit`, `cursor`, and optional `category`. Each app has a slug, display metadata, `requires_connection`, and `managed_auth_available`. Follow `next_cursor` until it is null. |
| `GET /api/connectors/connections` | Show the signed-in user's accounts and statuses. Accepts `limit` and `cursor`. Group accounts by `toolkit`. |
| `POST /api/connectors/catalog/{toolkit}/connect` | Start authorization. Optionally send `{"alias":"Work"}` to distinguish accounts. Open the returned `redirect_url` in the system browser and retain `connection_id` for status checks. |
| `GET /api/connectors/connections/{connection_id}` | Poll while authorization is pending, or refresh on return from the browser. Treat an account as connected only when `status` is `ACTIVE` and `is_disabled` is false. Stop polling on success, terminal failure, or a client timeout. |
| `DELETE /api/connectors/connections/{connection_id}` | Disconnect the account. A `204` means it has been removed. Refresh the account list. |

For reconnect, start a fresh authorization from the app's **Connect** button. If the old connection conflicts with the alias, use a new alias or remove the stale account explicitly. An optional `COMPOSIO_CALLBACK_URL` can return users to a trusted client entry point. Treat callback parameters as advisory and refetch status over Aura's authenticated API. Provider credentials and Composio session credentials remain server-side.

## Agent behavior

Aura’s task agent gets `search_connectors` and `run_connector_action` when the server key is configured. It searches the signed-in user's active apps, uses the selected account, and checks ownership and active status again before execution. Composio sessions are restricted to read-only app actions. The client has no tool-execution endpoint. Apps without a connected account appear in the catalog when listed by Composio, but do not participate in this account-based agent flow.
