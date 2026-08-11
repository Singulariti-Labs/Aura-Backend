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
            tool_response = await task_manager.wait_for_tool_response(self.task_id, tool_call_id)
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
            tool_response = await task_manager.wait_for_tool_response(self.task_id, tool_call_id)
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

    # --------------  Browser Click Tool -----------------------
    async def browser_click(
        self,
        ref: str,
        tool_call_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Click a snapshot element through the connected client browser.

        Args:
            ref: Element reference returned by browser_navigate or
                browser_snapshot, such as ``@e5`` or ``e5``.
            tool_call_id: Optional model tool-call ID used to correlate the result.

        Returns:
            A dictionary containing the normalized clicked reference and optional
            browser metadata, or the client's structured failure information.
        """
        input_params = {"ref": ref}

        try:
            # Ask the client browser to click the element identified by the ref.
            await send_ws_message(
                websocket=self.websocket,
                type="client_tool_request",
                chat_id=self.chat_id,
                task_id=self.task_id,
                payload={
                    "tool": "browser_click",
                    "tool_call_id": tool_call_id,
                    "input": input_params,
                    "coming_from": "browser_click_tool_func/server",
                },
            )

            # Persist the outgoing request using the shared client-tool event shape.
            await create_agent_event(
                pool=self.dbpool,
                task_id=self.task_id,
                role="tool",
                message_type="client_tool_request",
                tool="browser_click",
                payload={
                    "tool_call_id": tool_call_id,
                    "input": input_params,
                },
                seq=self.task_state.get_next_seq(),
            )

            # Wait for the client to report the click result.
            tool_response = await task_manager.wait_for_tool_response(self.task_id, tool_call_id)
            response_type = tool_response.get("type")
            payload = tool_response.get("payload", {})

            if (
                response_type != "client_tool_response"
                or payload.get("tool") != "browser_click"
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
                    "output": "Invalid browser_click result received from client.",
                }

            if result.get("success") is True:
                # Preserve the required normalized ref and optional success fields.
                click_output = {"clicked": result.get("clicked", "")}
                if result.get("url") is not None:
                    click_output["url"] = result["url"]
                if result.get("fallback_warning") is not None:
                    click_output["fallback_warning"] = result["fallback_warning"]

                final_result = {
                    "success": True,
                    "output": click_output,
                }
            else:
                # Keep the error structured so an optional fallback warning is not
                # lost when the click fails.
                failure_output = {
                    "error": result.get(
                        "error",
                        "Browser click failed without an error message.",
                    )
                }
                if result.get("fallback_warning") is not None:
                    failure_output["fallback_warning"] = result["fallback_warning"]

                final_result = {
                    "success": False,
                    "output": failure_output,
                }

            # Save the exact LLM-facing result in shared conversation memory.
            update_memory(
                role="assistant",
                content=f"Clicking browser element {ref}",
                memory=self.memory,
            )
            update_memory(
                role="tool",
                name="browser_click",
                tool_call_id=tool_call_id,
                content=json.dumps(final_result),
                memory=self.memory,
            )

            return final_result

        except Exception as error:
            return {
                "success": False,
                "output": f"Error executing browser_click: {error}",
            }

    # --------------  Browser Type Tool -----------------------
    async def browser_type(
        self,
        ref: str,
        text: str,
        tool_call_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Clear and type text into a snapshot-identified input element.

        Args:
            ref: Element reference returned by browser_navigate or
                browser_snapshot, such as ``@e3`` or ``e3``.
            text: Replacement text that the client should type into the field.
            tool_call_id: Optional model tool-call ID used to correlate the result.

        Returns:
            A dictionary containing the typed text, normalized element ref, and
            optional fallback warning, or structured failure information.
        """
        input_params = {"ref": ref, "text": text}

        try:
            # Ask the client browser to clear and fill the referenced input.
            await send_ws_message(
                websocket=self.websocket,
                type="client_tool_request",
                chat_id=self.chat_id,
                task_id=self.task_id,
                payload={
                    "tool": "browser_type",
                    "tool_call_id": tool_call_id,
                    "input": input_params,
                    "coming_from": "browser_type_tool_func/server",
                },
            )

            # Persist the outgoing request using the shared client-tool event shape.
            await create_agent_event(
                pool=self.dbpool,
                task_id=self.task_id,
                role="tool",
                message_type="client_tool_request",
                tool="browser_type",
                payload={
                    "tool_call_id": tool_call_id,
                    "input": input_params,
                },
                seq=self.task_state.get_next_seq(),
            )

            # Wait for the client to report the typing result.
            tool_response = await task_manager.wait_for_tool_response(self.task_id, tool_call_id)
            response_type = tool_response.get("type")
            payload = tool_response.get("payload", {})

            if (
                response_type != "client_tool_response"
                or payload.get("tool") != "browser_type"
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
                    "output": "Invalid browser_type result received from client.",
                }

            if result.get("success") is True:
                type_output = {
                    "typed": result.get("typed", ""),
                    "element": result.get("element", ""),
                }
                if result.get("fallback_warning") is not None:
                    type_output["fallback_warning"] = result["fallback_warning"]

                final_result = {
                    "success": True,
                    "output": type_output,
                }
            else:
                failure_output = {
                    "error": result.get(
                        "error",
                        "Browser type failed without an error message.",
                    )
                }
                if result.get("fallback_warning") is not None:
                    failure_output["fallback_warning"] = result["fallback_warning"]

                final_result = {
                    "success": False,
                    "output": failure_output,
                }

            # Avoid copying potentially sensitive typed text into assistant memory.
            update_memory(
                role="assistant",
                content=f"Typing text into browser element {ref}",
                memory=self.memory,
            )
            update_memory(
                role="tool",
                name="browser_type",
                tool_call_id=tool_call_id,
                content=json.dumps(final_result),
                memory=self.memory,
            )

            return final_result

        except Exception as error:
            return {
                "success": False,
                "output": f"Error executing browser_type: {error}",
            }

    # --------------  Browser Scroll Tool -----------------------
    async def browser_scroll(
        self,
        direction: str,
        tool_call_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Scroll the current browser page up or down.

        Args:
            direction: Validated scroll direction, either ``up`` or ``down``.
            tool_call_id: Optional model tool-call ID used to correlate the result.

        Returns:
            A dictionary containing the completed scroll direction and optional
            fallback warning, or structured failure information.
        """
        input_params = {"direction": direction}

        try:
            # Ask the client browser to scroll in the requested direction.
            await send_ws_message(
                websocket=self.websocket,
                type="client_tool_request",
                chat_id=self.chat_id,
                task_id=self.task_id,
                payload={
                    "tool": "browser_scroll",
                    "tool_call_id": tool_call_id,
                    "input": input_params,
                    "coming_from": "browser_scroll_tool_func/server",
                },
            )

            # Persist the outgoing request using the shared client-tool event shape.
            await create_agent_event(
                pool=self.dbpool,
                task_id=self.task_id,
                role="tool",
                message_type="client_tool_request",
                tool="browser_scroll",
                payload={
                    "tool_call_id": tool_call_id,
                    "input": input_params,
                },
                seq=self.task_state.get_next_seq(),
            )

            # Wait for the client to report the scroll result.
            tool_response = await task_manager.wait_for_tool_response(self.task_id, tool_call_id)
            response_type = tool_response.get("type")
            payload = tool_response.get("payload", {})

            if (
                response_type != "client_tool_response"
                or payload.get("tool") != "browser_scroll"
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
                    "output": "Invalid browser_scroll result received from client.",
                }

            if result.get("success") is True:
                scroll_output = {"scrolled": result.get("scrolled", direction)}
                if result.get("fallback_warning") is not None:
                    scroll_output["fallback_warning"] = result["fallback_warning"]

                final_result = {
                    "success": True,
                    "output": scroll_output,
                }
            else:
                failure_output = {
                    "error": result.get(
                        "error",
                        "Browser scroll failed without an error message.",
                    )
                }
                if result.get("fallback_warning") is not None:
                    failure_output["fallback_warning"] = result["fallback_warning"]

                final_result = {
                    "success": False,
                    "output": failure_output,
                }

            update_memory(
                role="assistant",
                content=f"Scrolling the browser page {direction}",
                memory=self.memory,
            )
            update_memory(
                role="tool",
                name="browser_scroll",
                tool_call_id=tool_call_id,
                content=json.dumps(final_result),
                memory=self.memory,
            )

            return final_result

        except Exception as error:
            return {
                "success": False,
                "output": f"Error executing browser_scroll: {error}",
            }

    # --------------  Browser Back Tool -----------------------
    async def browser_back(
        self,
        tool_call_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Navigate to the previous page in the client browser's history.

        Args:
            tool_call_id: Optional model tool-call ID used to correlate the result.

        Returns:
            A dictionary containing the current URL and optional fallback warning,
            or structured failure information.
        """
        input_params: Dict[str, Any] = {}

        try:
            # Ask the client browser to navigate one history entry backward.
            await send_ws_message(
                websocket=self.websocket,
                type="client_tool_request",
                chat_id=self.chat_id,
                task_id=self.task_id,
                payload={
                    "tool": "browser_back",
                    "tool_call_id": tool_call_id,
                    "input": input_params,
                    "coming_from": "browser_back_tool_func/server",
                },
            )

            # Persist the outgoing request using the shared client-tool event shape.
            await create_agent_event(
                pool=self.dbpool,
                task_id=self.task_id,
                role="tool",
                message_type="client_tool_request",
                tool="browser_back",
                payload={
                    "tool_call_id": tool_call_id,
                    "input": input_params,
                },
                seq=self.task_state.get_next_seq(),
            )

            # Wait for the client to report the history navigation result.
            tool_response = await task_manager.wait_for_tool_response(self.task_id, tool_call_id)
            response_type = tool_response.get("type")
            payload = tool_response.get("payload", {})

            if (
                response_type != "client_tool_response"
                or payload.get("tool") != "browser_back"
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
                    "output": "Invalid browser_back result received from client.",
                }

            if result.get("success") is True:
                back_output = {"url": result.get("url", "")}
                if result.get("fallback_warning") is not None:
                    back_output["fallback_warning"] = result["fallback_warning"]

                final_result = {
                    "success": True,
                    "output": back_output,
                }
            else:
                failure_output = {
                    "error": result.get(
                        "error",
                        "Browser back failed without an error message.",
                    )
                }
                if result.get("fallback_warning") is not None:
                    failure_output["fallback_warning"] = result["fallback_warning"]

                final_result = {
                    "success": False,
                    "output": failure_output,
                }

            update_memory(
                role="assistant",
                content="Navigating back in browser history",
                memory=self.memory,
            )
            update_memory(
                role="tool",
                name="browser_back",
                tool_call_id=tool_call_id,
                content=json.dumps(final_result),
                memory=self.memory,
            )

            return final_result

        except Exception as error:
            return {
                "success": False,
                "output": f"Error executing browser_back: {error}",
            }

    # --------------  Browser Press Tool -----------------------
    async def browser_press(
        self,
        key: str,
        tool_call_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Press a keyboard key in the current client browser page.

        Args:
            key: Keyboard key name, such as ``Enter``, ``Tab``, or ``Escape``.
            tool_call_id: Optional model tool-call ID used to correlate the result.

        Returns:
            A dictionary containing the pressed key and optional fallback warning,
            or structured failure information.
        """
        input_params = {"key": key}

        try:
            # Ask the client browser to send the requested key press.
            await send_ws_message(
                websocket=self.websocket,
                type="client_tool_request",
                chat_id=self.chat_id,
                task_id=self.task_id,
                payload={
                    "tool": "browser_press",
                    "tool_call_id": tool_call_id,
                    "input": input_params,
                    "coming_from": "browser_press_tool_func/server",
                },
            )

            # Persist the outgoing request using the shared client-tool event shape.
            await create_agent_event(
                pool=self.dbpool,
                task_id=self.task_id,
                role="tool",
                message_type="client_tool_request",
                tool="browser_press",
                payload={
                    "tool_call_id": tool_call_id,
                    "input": input_params,
                },
                seq=self.task_state.get_next_seq(),
            )

            # Wait for the client to report the key press result.
            tool_response = await task_manager.wait_for_tool_response(self.task_id, tool_call_id)
            response_type = tool_response.get("type")
            payload = tool_response.get("payload", {})

            if (
                response_type != "client_tool_response"
                or payload.get("tool") != "browser_press"
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
                    "output": "Invalid browser_press result received from client.",
                }

            if result.get("success") is True:
                press_output = {"pressed": result.get("pressed", "")}
                if result.get("fallback_warning") is not None:
                    press_output["fallback_warning"] = result["fallback_warning"]

                final_result = {
                    "success": True,
                    "output": press_output,
                }
            else:
                failure_output = {
                    "error": result.get(
                        "error",
                        "Browser press failed without an error message.",
                    )
                }
                if result.get("fallback_warning") is not None:
                    failure_output["fallback_warning"] = result["fallback_warning"]

                final_result = {
                    "success": False,
                    "output": failure_output,
                }

            update_memory(
                role="assistant",
                content=f"Pressing the {key} key in the browser",
                memory=self.memory,
            )
            update_memory(
                role="tool",
                name="browser_press",
                tool_call_id=tool_call_id,
                content=json.dumps(final_result),
                memory=self.memory,
            )

            return final_result

        except Exception as error:
            return {
                "success": False,
                "output": f"Error executing browser_press: {error}",
            }

    # --------------  Browser Get Images Tool -----------------------
    async def browser_get_images(
        self,
        tool_call_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Return image URLs and metadata from the current client browser page.

        Args:
            tool_call_id: Optional model tool-call ID used to correlate the result.

        Returns:
            A dictionary containing normalized image records, their count, and
            optional warnings, or structured failure information.
        """
        input_params: Dict[str, Any] = {}

        try:
            # Ask the client browser to collect image metadata from the page.
            await send_ws_message(
                websocket=self.websocket,
                type="client_tool_request",
                chat_id=self.chat_id,
                task_id=self.task_id,
                payload={
                    "tool": "browser_get_images",
                    "tool_call_id": tool_call_id,
                    "input": input_params,
                    "coming_from": "browser_get_images_tool_func/server",
                },
            )

            # Persist the outgoing request using the shared client-tool event shape.
            await create_agent_event(
                pool=self.dbpool,
                task_id=self.task_id,
                role="tool",
                message_type="client_tool_request",
                tool="browser_get_images",
                payload={
                    "tool_call_id": tool_call_id,
                    "input": input_params,
                },
                seq=self.task_state.get_next_seq(),
            )

            # Wait for the client to return the extracted image records.
            tool_response = await task_manager.wait_for_tool_response(self.task_id, tool_call_id)
            response_type = tool_response.get("type")
            payload = tool_response.get("payload", {})

            if (
                response_type != "client_tool_response"
                or payload.get("tool") != "browser_get_images"
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
                    "output": "Invalid browser_get_images result received from client.",
                }

            if result.get("success") is True:
                # Normalize each image while preserving optional natural dimensions.
                images = []
                raw_images = result.get("images", [])
                if isinstance(raw_images, list):
                    for raw_image in raw_images:
                        if not isinstance(raw_image, dict):
                            continue
                        image_info: Dict[str, Any] = {
                            "src": raw_image.get("src", ""),
                            "alt": raw_image.get("alt", ""),
                        }
                        if raw_image.get("width") is not None:
                            image_info["width"] = raw_image["width"]
                        if raw_image.get("height") is not None:
                            image_info["height"] = raw_image["height"]
                        images.append(image_info)

                images_output = {
                    "images": images,
                    "count": result.get("count", len(images)),
                }
                if result.get("warning") is not None:
                    images_output["warning"] = result["warning"]
                if result.get("fallback_warning") is not None:
                    images_output["fallback_warning"] = result["fallback_warning"]

                final_result = {
                    "success": True,
                    "output": images_output,
                }
            else:
                failure_output = {
                    "error": result.get(
                        "error",
                        "Browser image extraction failed without an error message.",
                    )
                }
                if result.get("fallback_warning") is not None:
                    failure_output["fallback_warning"] = result["fallback_warning"]

                final_result = {
                    "success": False,
                    "output": failure_output,
                }

            update_memory(
                role="assistant",
                content="Getting images from the current browser page",
                memory=self.memory,
            )
            update_memory(
                role="tool",
                name="browser_get_images",
                tool_call_id=tool_call_id,
                content=json.dumps(final_result),
                memory=self.memory,
            )

            return final_result

        except Exception as error:
            return {
                "success": False,
                "output": f"Error executing browser_get_images: {error}",
            }

    # --------------  Browser Vision Tool -----------------------
    async def browser_vision(
        self,
        question: str,
        annotate: bool = False,
        full: bool = False,
        tool_call_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Capture the current browser page for native visual inspection.

        The client returns a PNG data URL alongside screenshot metadata. The
        data URL remains a distinct media value so downstream model adapters can
        attach it as an image instead of serializing it into text tokens.

        Args:
            question: Visual question the model should answer from the screenshot.
            annotate: Whether the client should label interactive page elements.
            full: Capture full page if true, visible viewport only if false.
            tool_call_id: Optional model tool-call ID used to correlate the result.

        Returns:
            The documented BrowserVisionOutput success or failure dictionary.
        """
        input_params = {
            "question": question,
            "annotate": annotate,
            "full": full,
        }

        try:
            # Ask the client browser to capture and optionally annotate the page.
            await send_ws_message(
                websocket=self.websocket,
                type="client_tool_request",
                chat_id=self.chat_id,
                task_id=self.task_id,
                payload={
                    "tool": "browser_vision",
                    "tool_call_id": tool_call_id,
                    "input": input_params,
                    "coming_from": "browser_vision_tool_func/server",
                },
            )

            # Persist the outgoing request using the shared browser event shape.
            await create_agent_event(
                pool=self.dbpool,
                task_id=self.task_id,
                role="tool",
                message_type="client_tool_request",
                tool="browser_vision",
                payload={
                    "tool_call_id": tool_call_id,
                    "input": input_params,
                },
                seq=self.task_state.get_next_seq(),
            )

            # Wait for the screenshot response matching this exact tool call.
            tool_response = await task_manager.wait_for_tool_response(
                self.task_id,
                tool_call_id,
            )
            response_type = tool_response.get("type")
            payload = tool_response.get("payload", {})

            if (
                response_type != "client_tool_response"
                or payload.get("tool") != "browser_vision"
            ):
                return {
                    "success": False,
                    "error": (
                        "Unexpected client tool response: "
                        f"type={response_type}, tool={payload.get('tool')}"
                    ),
                }

            result = payload.get("result", {})
            if not isinstance(result, dict):
                return {
                    "success": False,
                    "error": "Invalid browser_vision result received from client.",
                }

            if result.get("success") is True:
                screenshot_path = result.get("screenshot_path")
                image_data_url = result.get("image_data_url")
                mime_type = result.get("mime_type")
                image_size_bytes = result.get("image_size_bytes")

                missing_fields = []
                if not isinstance(screenshot_path, str) or not screenshot_path:
                    missing_fields.append("screenshot_path")
                if (
                    not isinstance(image_data_url, str)
                    or not image_data_url.startswith("data:image/png;base64,")
                ):
                    missing_fields.append("image_data_url")
                if mime_type != "image/png":
                    missing_fields.append("mime_type")
                if (
                    not isinstance(image_size_bytes, int)
                    or isinstance(image_size_bytes, bool)
                    or image_size_bytes < 0
                ):
                    missing_fields.append("image_size_bytes")

                if missing_fields:
                    final_result: Dict[str, Any] = {
                        "success": False,
                        "error": (
                            "Invalid browser_vision success result; missing or "
                            "invalid fields: " + ", ".join(missing_fields)
                        ),
                    }
                    if isinstance(screenshot_path, str) and screenshot_path:
                        final_result["screenshot_path"] = screenshot_path
                    if result.get("fallback_warning") is not None:
                        final_result["fallback_warning"] = result[
                            "fallback_warning"
                        ]
                else:
                    # Preserve the documented output fields at the tool boundary.
                    final_result = {
                        "success": True,
                        "question": question,
                        "screenshot_path": screenshot_path,
                        "image_data_url": image_data_url,
                        "mime_type": "image/png",
                        "image_size_bytes": image_size_bytes,
                        "native_vision": True,
                    }
                    if result.get("annotations") is not None:
                        final_result["annotations"] = result["annotations"]
                    if result.get("fallback_warning") is not None:
                        final_result["fallback_warning"] = result[
                            "fallback_warning"
                        ]
            else:
                final_result = {
                    "success": False,
                    "error": result.get(
                        "error",
                        "Browser vision capture failed without an error message.",
                    ),
                }
                if result.get("screenshot_path") is not None:
                    final_result["screenshot_path"] = result["screenshot_path"]
                if result.get("fallback_warning") is not None:
                    final_result["fallback_warning"] = result[
                        "fallback_warning"
                    ]

            update_memory(
                role="assistant",
                content=f"Capturing the browser page to answer: {question}",
                memory=self.memory,
            )

            # Store screenshot metadata as text and image bytes as a media block.
            # This prevents the base64 data URL from being tokenized as JSON.
            memory_result = {
                key: value
                for key, value in final_result.items()
                if key != "image_data_url"
            }
            memory_content: Any = json.dumps(memory_result)
            if final_result.get("success") is True:
                encoded_image = final_result["image_data_url"].split(",", 1)[1]
                memory_result["note"] = "Screenshot attached as image content."
                memory_content = [
                    {"type": "text", "text": json.dumps(memory_result)},
                    {
                        "type": "text",
                        "text": (
                            "Analyze this browser screenshot and answer: "
                            f"{final_result['question']}"
                        ),
                    },
                    {
                        "type": "image",
                        "source_type": "base64",
                        "mime_type": "image/png",
                        "data": encoded_image,
                    },
                ]

            update_memory(
                role="tool",
                name="browser_vision",
                tool_call_id=tool_call_id,
                content=memory_content,
                memory=self.memory,
            )

            return final_result

        except Exception as error:
            return {
                "success": False,
                "error": f"Error executing browser_vision: {error}",
            }

    # --------------  Browser Console Tool -----------------------
    async def browser_console(
        self,
        clear: bool = False,
        expression: Optional[str] = None,
        tool_call_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Read browser console messages or evaluate JavaScript in the page.

        Args:
            clear: Whether console and error buffers should be cleared after a
                console-mode read. Defaults to ``False``.
            expression: Optional JavaScript expression. When supplied, the client
                switches from console-reading mode to expression-evaluation mode.
            tool_call_id: Optional model tool-call ID used to correlate the result.

        Returns:
            A dictionary containing normalized console data, expression results,
            or structured failure information.
        """
        input_params: Dict[str, Any] = {"clear": clear}
        if expression is not None:
            input_params["expression"] = expression

        try:
            # Ask the client to read console buffers or evaluate an expression.
            await send_ws_message(
                websocket=self.websocket,
                type="client_tool_request",
                chat_id=self.chat_id,
                task_id=self.task_id,
                payload={
                    "tool": "browser_console",
                    "tool_call_id": tool_call_id,
                    "input": input_params,
                    "coming_from": "browser_console_tool_func/server",
                },
            )

            # Persist the outgoing request using the shared client-tool event shape.
            await create_agent_event(
                pool=self.dbpool,
                task_id=self.task_id,
                role="tool",
                message_type="client_tool_request",
                tool="browser_console",
                payload={
                    "tool_call_id": tool_call_id,
                    "input": input_params,
                },
                seq=self.task_state.get_next_seq(),
            )

            # Wait for the client to return console or evaluation data.
            tool_response = await task_manager.wait_for_tool_response(self.task_id, tool_call_id)
            response_type = tool_response.get("type")
            payload = tool_response.get("payload", {})

            if (
                response_type != "client_tool_response"
                or payload.get("tool") != "browser_console"
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
                    "output": "Invalid browser_console result received from client.",
                }

            if result.get("success") is True:
                if "console_messages" in result or "js_errors" in result:
                    # Console mode has separate normalized log and exception lists.
                    console_messages = []
                    raw_messages = result.get("console_messages", [])
                    if isinstance(raw_messages, list):
                        for raw_message in raw_messages:
                            if not isinstance(raw_message, dict):
                                continue
                            console_messages.append(
                                {
                                    "type": raw_message.get("type", ""),
                                    "text": raw_message.get("text", ""),
                                    "source": raw_message.get("source", "console"),
                                }
                            )

                    js_errors = []
                    raw_errors = result.get("js_errors", [])
                    if isinstance(raw_errors, list):
                        for raw_error in raw_errors:
                            if not isinstance(raw_error, dict):
                                continue
                            js_errors.append(
                                {
                                    "message": raw_error.get("message", ""),
                                    "source": raw_error.get(
                                        "source",
                                        "exception",
                                    ),
                                }
                            )

                    console_output = {
                        "console_messages": console_messages,
                        "js_errors": js_errors,
                        "total_messages": result.get(
                            "total_messages",
                            len(console_messages),
                        ),
                        "total_errors": result.get(
                            "total_errors",
                            len(js_errors),
                        ),
                    }
                    if result.get("note") is not None:
                        console_output["note"] = result["note"]
                    if result.get("fallback_warning") is not None:
                        console_output["fallback_warning"] = result[
                            "fallback_warning"
                        ]

                    final_result = {
                        "success": True,
                        "output": console_output,
                    }
                else:
                    # Expression mode preserves an arbitrary JSON-serialized result.
                    expression_output = {
                        "result": result.get("result"),
                        "result_type": result.get("result_type", ""),
                    }
                    if result.get("method") is not None:
                        expression_output["method"] = result["method"]
                    if result.get("fallback_warning") is not None:
                        expression_output["fallback_warning"] = result[
                            "fallback_warning"
                        ]

                    final_result = {
                        "success": True,
                        "output": expression_output,
                    }
            else:
                failure_output = {
                    "error": result.get(
                        "error",
                        "Browser console failed without an error message.",
                    )
                }
                if result.get("fallback_warning") is not None:
                    failure_output["fallback_warning"] = result["fallback_warning"]

                final_result = {
                    "success": False,
                    "output": failure_output,
                }

            update_memory(
                role="assistant",
                content=(
                    "Evaluating JavaScript in the current browser page"
                    if expression is not None
                    else "Reading browser console output and JavaScript errors"
                ),
                memory=self.memory,
            )
            update_memory(
                role="tool",
                name="browser_console",
                tool_call_id=tool_call_id,
                content=json.dumps(final_result),
                memory=self.memory,
            )

            return final_result

        except Exception as error:
            return {
                "success": False,
                "output": f"Error executing browser_console: {error}",
            }
