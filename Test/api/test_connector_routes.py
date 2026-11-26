import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import httpx
from composio_client import APIStatusError
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.connector_routes import connector_router, connector_user_id
from app.Connectors.Composio.actions import ConnectorActions
from app.Connectors.Composio.client import ConnectorError


def account(owner, *, disabled=False):
    return SimpleNamespace(
        id="ca_test",
        user_id=owner,
        toolkit=SimpleNamespace(slug="gmail"),
        alias="Work",
        status="ACTIVE",
        is_disabled=disabled,
        created_at="2026-11-24T00:00:00Z",
        data={"access_token": "secret"},
    )


def page(*accounts):
    return SimpleNamespace(items=list(accounts), next_cursor=None)


class ConnectorRoutesTests(unittest.TestCase):
    def setUp(self):
        app = FastAPI()
        app.include_router(connector_router)
        app.dependency_overrides[connector_user_id] = lambda: "me"
        self.http = TestClient(app)
        self.sdk = MagicMock()
        client_patch = patch("app.Connectors.Composio.connections.get_composio", return_value=self.sdk)
        client_patch.start()
        self.addCleanup(client_patch.stop)
        identity_patch = patch("app.Connectors.Composio.account_identity.get_composio", return_value=self.sdk)
        identity_patch.start()
        self.addCleanup(identity_patch.stop)
        self.sdk.tools.proxy.return_value = SimpleNamespace(status=403, data={})

    def test_catalog_requires_authentication(self):
        self.http.app.dependency_overrides.clear()
        response = self.http.get("/api/connectors/catalog")
        self.assertEqual(response.status_code, 401)

    def test_upstream_auth_failure_is_reported_as_server_configuration_error(self):
        upstream_response = httpx.Response(
            401, request=httpx.Request("GET", "https://backend.composio.dev/api/v3/toolkits")
        )
        error = APIStatusError("private upstream detail", response=upstream_response, body=None)
        with patch("app.api.connector_routes.catalog.list_toolkits", side_effect=error) as operation:
            operation.__name__ = "list_toolkits"
            response = self.http.get("/api/connectors/catalog")

        self.assertEqual(response.status_code, 503)
        self.assertIn("project credential was rejected", response.json()["detail"])
        self.assertNotIn("private upstream detail", response.text)

    def test_execution_requires_a_discovered_app_action(self):
        with self.assertRaisesRegex(ConnectorError, "Discover"):
            ConnectorActions("me").execute("COMPOSIO_MULTI_EXECUTE_TOOL", "ca_test", {})
        self.sdk.create.assert_not_called()

    def test_connect_opens_a_session_for_the_authenticated_user(self):
        self.sdk.toolkits.get.return_value = SimpleNamespace(slug="gmail")
        self.sdk.create.return_value.authorize.return_value = SimpleNamespace(
            id="ca_test", redirect_url="https://connect.example.com/test"
        )
        response = self.http.post("/api/connectors/catalog/gmail/connect", json={"alias": "Work"})
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertEqual(response.json()["connection_id"], "ca_test")
        self.assertEqual(self.sdk.create.call_args.kwargs["user_id"], "aura:me")

    def test_connection_status_projects_only_public_fields(self):
        self.sdk.connected_accounts.list.return_value = page(account("aura:me"))
        response = self.http.get("/api/connectors/connections/ca_test")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "ACTIVE")
        self.assertNotIn("secret", response.text)
        self.assertEqual(self.sdk.connected_accounts.list.call_args.kwargs["user_ids"], ["aura:me"])

    def test_underscore_toolkit_can_start_authorization(self):
        self.sdk.toolkits.get.return_value = SimpleNamespace(slug="_1password")
        self.sdk.create.return_value.authorize.return_value = SimpleNamespace(
            id="ca_test", redirect_url="https://connect.example.com/test"
        )
        response = self.http.post("/api/connectors/catalog/_1password/connect")
        self.assertEqual(response.status_code, 201)
        self.sdk.toolkits.get.assert_called_once_with("_1password")

    def test_gmail_account_identity_is_returned_on_list_and_status(self):
        self.sdk.connected_accounts.list.return_value = page(account("aura:me"))
        self.sdk.tools.proxy.return_value = SimpleNamespace(
            status=200, data={"emailAddress": "mailbox@example.com", "historyId": "private"}
        )
        result = self.http.get("/api/connectors/connections").json()
        self.assertEqual(result["items"][0]["account_label"], "mailbox@example.com")
        response = self.http.get("/api/connectors/connections/ca_test")
        self.assertEqual(response.json()["account_label"], "mailbox@example.com")
        self.assertNotIn("private", response.text)
        self.assertEqual(self.sdk.tools.proxy.call_args.kwargs["connected_account_id"], "ca_test")
        self.assertEqual(self.sdk.tools.proxy.call_args.kwargs["user_id"], "aura:me")

    def test_foreign_account_profile_is_not_requested(self):
        self.sdk.connected_accounts.list.return_value = page(account("aura:other"))
        response = self.http.get("/api/connectors/connections/ca_test")
        self.assertEqual(response.status_code, 404)
        self.sdk.tools.proxy.assert_not_called()

    def test_foreign_account_cannot_be_disconnected(self):
        self.sdk.connected_accounts.list.return_value = page(account("aura:other"))
        response = self.http.delete("/api/connectors/connections/ca_test")
        self.assertEqual(response.status_code, 404)
        self.sdk.connected_accounts.delete.assert_not_called()

    def test_execution_rechecks_account_and_pins_it_in_read_only_session(self):
        slug = "GMAIL_FETCH_EMAILS"
        session = self.sdk.create.return_value
        session.search.return_value = SimpleNamespace(
            success=True,
            tool_schemas={slug: SimpleNamespace(
                tool_slug=slug, toolkit="gmail", description="Fetch email", input_schema={}
            )},
        )
        session.execute.return_value = SimpleNamespace(error=None, data={"emails": []})
        self.sdk.connected_accounts.list.return_value = page(account("aura:me"))
        actions = ConnectorActions("me")
        with patch("app.Connectors.Composio.actions.get_composio", return_value=self.sdk):
            self.assertEqual(actions.discover("list email", None)["actions"][0]["slug"], slug)
            self.assertEqual(actions.execute(slug, "ca_test", {}), {"data": {"emails": []}})
            self.sdk.connected_accounts.list.return_value = page(account("aura:other"))
            with self.assertRaisesRegex(ConnectorError, "Connection not found"):
                actions.execute(slug, "ca_test", {})

        scope = self.sdk.create.call_args.kwargs
        self.assertEqual(scope["connected_accounts"], {"gmail": "ca_test"})
        self.assertEqual(scope["tags"]["enable"], ["readOnlyHint"])
        session.execute.assert_called_once_with(slug, arguments={})
