"""Client-side browser tool implementations.

Additional browser actions can be added to :class:`BrowserTools` as the client
exposes them. Each action in this module delegates execution to the connected
client through the existing ``client_tool_request`` WebSocket protocol.
"""

import json
from typing import Any, Dict, Optional

from langchain_core.language_models.chat_models import BaseChatModel

from app.DB.Queries.agent_event import create_agent_event
from app.LLM.memory import Memory
from app.Task.task_manager import task_manager
from app.api.websocket_utils import send_ws_message
from app.helper import update_memory


class BrowserTools:
    """Expose browser operations that are executed by the connected client."""

    def __init__(
        self,
        llm: BaseChatModel,
        task_id: str,
        chat_id: str,
        memory: Optional[Memory] = None,
    ) -> None:
        """Initialize the browser bridge for a task and its WebSocket client.

        Args:
            llm: Chat model associated with the agent using the browser tools.
            task_id: Identifier used to route requests and responses for the task.
            chat_id: Identifier of the chat that owns the browser session.
            memory: Optional conversation memory updated after tool execution.
        """
        self.llm = llm
        self.task_id = task_id
        self.chat_id = chat_id
        self.memory = memory
        self.task_state = task_manager.get_state(self.task_id)
        self.websocket = self.task_state.websocket
        self.dbpool = self.task_state.dbpool

    async def browser_navigate(
        self,
        url: str,
        tool_call_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Navigate the client browser to ``url`` and return its page snapshot.

        The client initializes the browser session, loads the requested page, and
        responds with a compact snapshot containing interactive element reference
        IDs. The response body is preserved so future browser actions can use all
        client-provided navigation metadata.

        Args:
            url: URL that the client browser should load.
            tool_call_id: Optional model tool-call ID used to correlate the result.

        Returns:
            A dictionary containing the client's success flag and navigation output.
        """
        input_params = {"url": url}

        try:
            # Ask the connected client to perform the browser operation.
            await send_ws_message(
                websocket=self.websocket,
                type="client_tool_request",
                chat_id=self.chat_id,
                task_id=self.task_id,
                payload={
                    "tool": "browser_navigate",
                    "tool_call_id": tool_call_id,
                    "input": input_params,
                    "coming_from": "browser_navigate_tool_func/server",
                },
            )

            # Persist the outgoing request alongside the other agent tool events.
            await create_agent_event(
                pool=self.dbpool,
                task_id=self.task_id,
                role="tool",
                message_type="client_tool_request",
                tool="browser_navigate",
                payload={
                    "tool_call_id": tool_call_id,
                    "input": input_params,
                },
                seq=self.task_state.get_next_seq(),
            )

            # Wait for the browser implementation on the client to finish loading.
            tool_response = await task_manager.wait_for_input(self.task_id)
            response_type = tool_response.get("type")
            payload = tool_response.get("payload", {})

            if (
                response_type != "client_tool_response"
                or payload.get("tool") != "browser_navigate"
            ):
                return {
                    "success": False,
                    "output": (
                        "Unexpected client tool response: "
                        f"type={response_type}, tool={payload.get('tool')}"
                    ),
                }

            result = payload.get("result", {})
            if not isinstance(result, dict):
                return {
                    "success": False,
                    "output": "Invalid browser_navigate result received from client.",
                }

            if result.get("success") is True:
                # Map the documented BrowserNavigateSuccess response into the
                # standard tool result shape consumed by the LLM.
                browser_output = {
                    "url": result.get("url", ""),
                    "title": result.get("title", ""),
                    "stealth_features": result.get("stealth_features", []),
                    "snapshot": result.get("snapshot", ""),
                    "element_count": result.get("element_count", 0),
                }

                # Warning fields are optional and should only be shown to the
                # LLM when the browser client actually returned them.
                if result.get("bot_detection_warning") is not None:
                    browser_output["bot_detection_warning"] = result[
                        "bot_detection_warning"
                    ]
                if result.get("stealth_warning") is not None:
                    browser_output["stealth_warning"] = result[
                        "stealth_warning"
                    ]

                final_result = {
                    "success": True,
                    "output": browser_output,
                }
            else:
                # BrowserNavigateFailure exposes its client-provided error
                # directly so the LLM can understand and act on the failure.
                final_result = {
                    "success": False,
                    "output": result.get(
                        "error",
                        "Browser navigation failed without an error message.",
                    ),
                }

            # Record the invocation and result in the shared conversation memory.
            update_memory(
                role="assistant",
                content=f"Navigating the browser to {url}",
                memory=self.memory,
            )
            update_memory(
                role="tool",
                name="browser_navigate",
                tool_call_id=tool_call_id,
                content=json.dumps(final_result),
                memory=self.memory,
            )

            return final_result

        except Exception as error:
            return {
                "success": False,
                "output": f"Error executing browser_navigate: {error}",
            }

    # --------------  Browser Snapshot Tool -----------------------
    async def browser_snapshot(
        self,
        full: bool = False,
        tool_call_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Capture the current page's text accessibility tree.

        Args:
            full: Whether the client should return the complete accessibility
                tree instead of the default compact interactive-element view.
            tool_call_id: Optional model tool-call ID used to correlate the result.

        Returns:
            A dictionary containing either the snapshot metadata or the client's
            human-readable failure message.
        """
        input_params = {"full": full}

        try:
            # Ask the connected client to capture the current browser page.
            await send_ws_message(
                websocket=self.websocket,
                type="client_tool_request",
                chat_id=self.chat_id,
                task_id=self.task_id,
                payload={
                    "tool": "browser_snapshot",
                    "tool_call_id": tool_call_id,
                    "input": input_params,
                    "coming_from": "browser_snapshot_tool_func/server",
                },
            )

            # Persist the request using the same event shape as browser_navigate.
            await create_agent_event(
                pool=self.dbpool,
                task_id=self.task_id,
                role="tool",
                message_type="client_tool_request",
                tool="browser_snapshot",
                payload={
                    "tool_call_id": tool_call_id,
                    "input": input_params,
                },
                seq=self.task_state.get_next_seq(),
            )

            # Wait for the client-side browser implementation to return the tree.
            tool_response = await task_manager.wait_for_input(self.task_id)
            response_type = tool_response.get("type")
            payload = tool_response.get("payload", {})

            if (
                response_type != "client_tool_response"
                or payload.get("tool") != "browser_snapshot"
            ):
                return {
                    "success": False,
                    "output": (
                        "Unexpected client tool response: "
                        f"type={response_type}, tool={payload.get('tool')}"
                    ),
                }

            result = payload.get("result", {})
            if not isinstance(result, dict):
                return {
                    "success": False,
                    "output": "Invalid browser_snapshot result received from client.",
                }

            if result.get("success") is True:
                # Required BrowserSnapshotOutput fields are always exposed to the
                # LLM under the standard tool output envelope.
                snapshot_output = {
                    "snapshot": result.get("snapshot", ""),
                    "element_count": result.get("element_count", 0),
                }

                # URL and title are optional client fields; omit them when absent.
                if result.get("url") is not None:
                    snapshot_output["url"] = result["url"]
                if result.get("title") is not None:
                    snapshot_output["title"] = result["title"]

                final_result = {
                    "success": True,
                    "output": snapshot_output,
                }
            else:
                # BrowserSnapshotOutput failures contain one actionable error.
                final_result = {
                    "success": False,
                    "output": result.get(
                        "error",
                        "Browser snapshot failed without an error message.",
                    ),
                }

            # Save the exact LLM-facing result in shared conversation memory.
            update_memory(
                role="assistant",
                content=(
                    "Capturing a full browser page snapshot"
                    if full
                    else "Refreshing the compact browser page snapshot"
                ),
                memory=self.memory,
            )
            update_memory(
                role="tool",
                name="browser_snapshot",
                tool_call_id=tool_call_id,
                content=json.dumps(final_result),
                memory=self.memory,
            )

            return final_result

        except Exception as error:
            return {
                "success": False,
                "output": f"Error executing browser_snapshot: {error}",
            }
