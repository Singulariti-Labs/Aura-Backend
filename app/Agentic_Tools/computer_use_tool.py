"""Client-side native computer-use execution bridge.

The LangChain-facing wrapper lives in :mod:`app.Tools.computer_use`. This module
owns the transport flow shared by client-executed tools: send a
``client_tool_request``, persist the request event, wait for the matching
``client_tool_response``, normalize the action result, and update Aura memory.

Each native action has its own method so action-specific response handling can
be added without turning the shared WebSocket transport into one large branch.
"""

from __future__ import annotations

import json
from typing import Any, Dict, Optional

from langchain_core.language_models.chat_models import BaseChatModel

from app.DB.Queries.agent_event import create_agent_event
from app.LLM.memory import Memory
from app.Task.task_manager import task_manager
from app.api.websocket_utils import send_ws_message
from app.helper import update_memory


CAPTURE_MODES = {"som", "vision", "ax"}
CAPTURE_MIME_TYPES = {"image/png", "image/jpeg"}


def _server_error(
    action: str,
    message: str,
    *,
    code: str = "invalid_client_response",
) -> Dict[str, Any]:
    """Build Aura's standard failure shape for a malformed client response."""

    return {
        "success": False,
        "action": action,
        "error": {"code": code, "message": message},
    }


def _capture_image(result: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Return an inline image from capture or capture_after when available."""

    capture_result = result
    if result.get("action") != "capture":
        capture_after = result.get("capture_after")
        if not isinstance(capture_after, dict):
            return None
        capture_result = capture_after

    image = capture_result.get("image")
    if not isinstance(image, dict):
        return None
    if image.get("mime_type") not in CAPTURE_MIME_TYPES:
        return None
    if not isinstance(image.get("data"), str) or not image["data"]:
        return None
    return image


def computer_capture_text_result(result: Dict[str, Any]) -> Dict[str, Any]:
    """Copy a result without placing capture image base64 in text content."""

    text_result = dict(result)
    capture_result = text_result
    nested_capture = result.get("action") != "capture"
    if nested_capture:
        capture_after = text_result.get("capture_after")
        if not isinstance(capture_after, dict):
            return text_result
        capture_result = dict(capture_after)

    image = capture_result.get("image")
    if isinstance(image, dict) and image.get("data"):
        capture_result["image"] = {
            key: value for key, value in image.items() if key != "data"
        }
        capture_result["image"]["note"] = (
            "The captured image is attached in the following user-role message."
        )
        if nested_capture:
            text_result["capture_after"] = capture_result
    return text_result


def computer_use_memory_content(result: Dict[str, Any]) -> Any:
    """Create JSON text and, when present, a separate capture image block."""

    image = _capture_image(result)
    if image is None:
        return json.dumps(result, ensure_ascii=False)

    return [
        {
            "type": "text",
            "text": json.dumps(
                computer_capture_text_result(result),
                ensure_ascii=False,
            ),
        },
        {
            "type": "text",
            "text": (
                "Inspect this native computer screenshot together with the "
                "capture metadata."
            ),
        },
        {
            "type": "image",
            "source_type": "base64",
            "mime_type": image["mime_type"],
            "data": image["data"],
        },
    ]


class ComputerUseToolExecutor:
    """Execute native computer actions through the connected desktop client."""

    def __init__(
        self,
        llm: BaseChatModel,
        task_id: str,
        chat_id: str,
        memory: Optional[Memory] = None,
    ) -> None:
        """Initialize the client bridge for one Aura task and chat."""

        self.llm = llm
        self.task_id = task_id
        self.chat_id = chat_id
        self.memory = memory
        self.task_state = task_manager.get_state(task_id)
        self.websocket = self.task_state.websocket
        self.dbpool = self.task_state.dbpool

    # ---------------------------------------------------------------------
    # Action router
    # ---------------------------------------------------------------------

    async def execute(
        self,
        input_params: Dict[str, Any],
        tool_call_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Route the request to the clearly named method for its action.

        Keeping this router explicit makes it easy to find an action and add its
        response handling later without following an indirect handler map.
        """

        action = input_params.get("action")
        if action == "capture":
            return await self.capture(input_params, tool_call_id)
        if action == "click":
            return await self.click(input_params, tool_call_id)
        if action == "double_click":
            return await self.double_click(input_params, tool_call_id)
        if action == "right_click":
            return await self.right_click(input_params, tool_call_id)
        if action == "middle_click":
            return await self.middle_click(input_params, tool_call_id)
        if action == "drag":
            return await self.drag(input_params, tool_call_id)
        if action == "scroll":
            return await self.scroll(input_params, tool_call_id)
        if action == "type":
            return await self.type_text(input_params, tool_call_id)
        if action == "key":
            return await self.key(input_params, tool_call_id)
        if action == "set_value":
            return await self.set_value(input_params, tool_call_id)
        if action == "wait":
            return await self.wait(input_params, tool_call_id)
        if action == "list_apps":
            return await self.list_apps(input_params, tool_call_id)
        if action == "list_windows":
            return await self.list_windows(input_params, tool_call_id)
        if action == "focus_app":
            return await self.focus_app(input_params, tool_call_id)

        return _server_error(
            str(action or "unknown"),
            f"Unsupported computer_use action: {action}",
            code="unsupported_action",
        )

    # ---------------------------------------------------------------------
    # Capture action
    # ---------------------------------------------------------------------

    async def capture(
        self,
        input_params: Dict[str, Any],
        tool_call_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Capture a screenshot, SOM elements, or accessibility-only output."""

        return await self._send_action_request(
            "capture",
            input_params,
            tool_call_id,
        )

    # ---------------------------------------------------------------------
    # Mouse actions
    # ---------------------------------------------------------------------

    async def click(
        self,
        input_params: Dict[str, Any],
        tool_call_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Click one element or coordinate with the requested mouse button."""

        return await self._send_action_request("click", input_params, tool_call_id)

    async def double_click(
        self,
        input_params: Dict[str, Any],
        tool_call_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Double-click one native element or coordinate."""

        return await self._send_action_request(
            "double_click",
            input_params,
            tool_call_id,
        )

    async def right_click(
        self,
        input_params: Dict[str, Any],
        tool_call_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Open the context menu for one native element or coordinate."""

        return await self._send_action_request(
            "right_click",
            input_params,
            tool_call_id,
        )

    async def middle_click(
        self,
        input_params: Dict[str, Any],
        tool_call_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Middle-click one native element or coordinate."""

        return await self._send_action_request(
            "middle_click",
            input_params,
            tool_call_id,
        )

    async def drag(
        self,
        input_params: Dict[str, Any],
        tool_call_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Drag from the supplied source to the supplied destination."""

        return await self._send_action_request("drag", input_params, tool_call_id)

    async def scroll(
        self,
        input_params: Dict[str, Any],
        tool_call_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Scroll the selected native window, element, or coordinate."""

        return await self._send_action_request("scroll", input_params, tool_call_id)

    # ---------------------------------------------------------------------
    # Keyboard and value actions
    # ---------------------------------------------------------------------

    async def type_text(
        self,
        input_params: Dict[str, Any],
        tool_call_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Type text into the focused or selected native control."""

        return await self._send_action_request("type", input_params, tool_call_id)

    async def key(
        self,
        input_params: Dict[str, Any],
        tool_call_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Send one key or key combination to the native target."""

        return await self._send_action_request("key", input_params, tool_call_id)

    async def set_value(
        self,
        input_params: Dict[str, Any],
        tool_call_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Set a control value directly through native accessibility support."""

        return await self._send_action_request(
            "set_value",
            input_params,
            tool_call_id,
        )

    # ---------------------------------------------------------------------
    # Timing, discovery, and focus actions
    # ---------------------------------------------------------------------

    async def wait(
        self,
        input_params: Dict[str, Any],
        tool_call_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Pause client-side execution for the requested number of seconds."""

        return await self._send_action_request("wait", input_params, tool_call_id)

    async def list_apps(
        self,
        input_params: Dict[str, Any],
        tool_call_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """List native applications that the computer driver can target."""

        return await self._send_action_request(
            "list_apps",
            input_params,
            tool_call_id,
        )

    async def list_windows(
        self,
        input_params: Dict[str, Any],
        tool_call_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """List native windows, optionally filtered to an app or process."""

        return await self._send_action_request(
            "list_windows",
            input_params,
            tool_call_id,
        )

    async def focus_app(
        self,
        input_params: Dict[str, Any],
        tool_call_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Focus the selected app/window and optionally raise it to the front."""

        return await self._send_action_request(
            "focus_app",
            input_params,
            tool_call_id,
        )

    # ---------------------------------------------------------------------
    # Shared WebSocket flow
    # ---------------------------------------------------------------------

    async def _send_action_request(
        self,
        action: str,
        input_params: Dict[str, Any],
        tool_call_id: Optional[str],
    ) -> Dict[str, Any]:
        """Send one action and wait for its matching client response.

        Only the identical transport steps are shared here. Action entry points
        stay separate above so their response formats can evolve independently.
        """

        request_input = dict(input_params)
        request_input["action"] = action

        try:
            registered_tool_call_id = await send_ws_message(
                websocket=self.websocket,
                type="client_tool_request",
                chat_id=self.chat_id,
                task_id=self.task_id,
                payload={
                    "tool": "computer_use",
                    "tool_call_id": tool_call_id,
                    "input": request_input,
                    "coming_from": "computer_use_tool_func/server",
                },
            )
            effective_tool_call_id = registered_tool_call_id or tool_call_id

            await create_agent_event(
                pool=self.dbpool,
                task_id=self.task_id,
                role="tool",
                message_type="client_tool_request",
                tool="computer_use",
                payload={
                    "tool_call_id": effective_tool_call_id,
                    "input": request_input,
                },
                seq=self.task_state.get_next_seq(),
            )

            client_response = await task_manager.wait_for_tool_response(
                self.task_id,
                effective_tool_call_id,
            )
            result = self._read_client_result(client_response, action)

            if self.memory is not None:
                update_memory(
                    role="assistant",
                    content=f"Executing native computer action: {action}",
                    memory=self.memory,
                )
                memory_content = computer_use_memory_content(result)
                update_memory(
                    role="tool",
                    name="computer_use",
                    tool_call_id=effective_tool_call_id,
                    content=memory_content,
                    memory=self.memory,
                )

            return result

        except Exception as error:
            return _server_error(
                action,
                f"Error executing computer_use: {error}",
                code="server_execution_error",
            )

    # ---------------------------------------------------------------------
    # Shared response validation
    # ---------------------------------------------------------------------

    def _read_client_result(
        self,
        client_response: Any,
        expected_action: str,
    ) -> Dict[str, Any]:
        """Validate the WebSocket envelope and normalize ``ok`` to ``success``."""

        if not isinstance(client_response, dict):
            return _server_error(expected_action, "Client response must be an object.")

        response_type = client_response.get("type")
        payload = client_response.get("payload", {})
        if not isinstance(payload, dict):
            return _server_error(expected_action, "Client response payload must be an object.")
        if response_type != "client_tool_response" or payload.get("tool") != "computer_use":
            return _server_error(
                expected_action,
                "Unexpected client tool response: "
                f"type={response_type}, tool={payload.get('tool')}",
            )

        raw_result = payload.get("result")
        if not isinstance(raw_result, dict):
            return _server_error(expected_action, "Client result must be an object.")
        if not isinstance(raw_result.get("ok"), bool):
            return _server_error(expected_action, "Client result must include boolean ok.")
        if raw_result.get("action") != expected_action:
            return _server_error(
                expected_action,
                f"Client result action must be '{expected_action}'.",
            )

        normalized = {
            "success": raw_result["ok"],
            **{key: value for key, value in raw_result.items() if key != "ok"},
        }

        if normalized["success"] is False:
            error = normalized.get("error")
            if not isinstance(error, dict) or not isinstance(error.get("message"), str):
                return _server_error(
                    expected_action,
                    "Failed client result must include error.message.",
                )
            return normalized

        if expected_action == "capture":
            return self._format_capture_response(normalized)
        if expected_action == "click":
            return self._format_click_response(normalized)
        if expected_action == "double_click":
            return self._format_double_click_response(normalized)
        if expected_action == "right_click":
            return self._format_right_click_response(normalized)
        if expected_action == "middle_click":
            return self._format_middle_click_response(normalized)
        if expected_action == "drag":
            return self._format_drag_response(normalized)
        if expected_action == "scroll":
            return self._format_scroll_response(normalized)
        if expected_action == "type":
            return self._format_type_response(normalized)
        if expected_action == "key":
            return self._format_key_response(normalized)
        if expected_action == "set_value":
            return self._format_set_value_response(normalized)
        if expected_action == "wait":
            return self._format_wait_response(normalized)
        if expected_action == "list_apps":
            return self._format_list_apps_response(normalized)
        if expected_action == "list_windows":
            return self._format_list_windows_response(normalized)
        if expected_action == "focus_app":
            return self._format_focus_app_response(normalized)

        return _server_error(
            expected_action,
            f"No response formatter exists for action '{expected_action}'.",
        )

    # ---------------------------------------------------------------------
    # Capture response
    # ---------------------------------------------------------------------

    def _format_capture_response(
        self,
        result: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Validate and return a successful capture response for Aura."""

        if result.get("mode") not in CAPTURE_MODES:
            return _server_error(
                "capture",
                "Successful capture result must include mode: som, vision, or ax.",
            )

        image = result.get("image")
        if image is not None:
            if not isinstance(image, dict):
                return _server_error("capture", "Capture image must be an object.")
            if image.get("mime_type") not in CAPTURE_MIME_TYPES:
                return _server_error(
                    "capture",
                    "Capture image mime_type must be image/png or image/jpeg.",
                )
            if image.get("data") is not None and not isinstance(image["data"], str):
                return _server_error("capture", "Capture image data must be base64 text.")
            for dimension in ("width", "height"):
                value = image.get(dimension)
                if value is not None and (
                    not isinstance(value, int)
                    or isinstance(value, bool)
                    or value < 0
                ):
                    return _server_error(
                        "capture",
                        f"Capture image {dimension} must be a non-negative integer.",
                    )

        return result

    # ---------------------------------------------------------------------
    # Click response family
    # ---------------------------------------------------------------------

    def _format_click_response(
        self,
        result: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Format the element/coordinate and metadata returned by ``click``."""

        return self._format_clicked_response(
            result,
            action="click",
            allowed_buttons={"left", "right", "middle"},
        )

    def _format_double_click_response(
        self,
        result: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Format the click-style response returned by ``double_click``."""

        return self._format_clicked_response(
            result,
            action="double_click",
            allowed_buttons={"left", "right", "middle"},
        )

    def _format_right_click_response(
        self,
        result: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Format a right-click response and require a right button label."""

        return self._format_clicked_response(
            result,
            action="right_click",
            allowed_buttons={"right"},
        )

    def _format_middle_click_response(
        self,
        result: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Format a middle-click response and require a middle button label."""

        return self._format_clicked_response(
            result,
            action="middle_click",
            allowed_buttons={"middle"},
        )

    def _format_clicked_response(
        self,
        result: Dict[str, Any],
        *,
        action: str,
        allowed_buttons: set[str],
    ) -> Dict[str, Any]:
        """Validate fields shared by click-family success responses."""

        clicked = result.get("clicked")
        if clicked is not None:
            if not isinstance(clicked, dict):
                return _server_error(action, "clicked must be an object.")
            if clicked.get("button") not in allowed_buttons:
                return _server_error(
                    action,
                    f"clicked.button must be one of {sorted(allowed_buttons)}.",
                )
            target_error = self._validate_element_or_coordinate(clicked)
            if target_error:
                return _server_error(action, target_error)

        return self._format_action_metadata(result, action)

    # ---------------------------------------------------------------------
    # Drag, scroll, type, key, and set-value responses
    # ---------------------------------------------------------------------

    def _format_drag_response(
        self,
        result: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Format the source/destination metadata returned by ``drag``."""

        dragged = result.get("dragged")
        if dragged is not None:
            if not isinstance(dragged, dict):
                return _server_error("drag", "dragged must be an object.")
            button = dragged.get("button")
            if button is not None and button not in {"left", "right", "middle"}:
                return _server_error("drag", "dragged.button is invalid.")
            for field_name in ("from_coordinate", "to_coordinate"):
                coordinate = dragged.get(field_name)
                if coordinate is not None and not self._is_coordinate(coordinate):
                    return _server_error(
                        "drag",
                        f"dragged.{field_name} must be [x, y].",
                    )

        return self._format_action_metadata(result, "drag")

    def _format_scroll_response(
        self,
        result: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Format the direction and amount returned by ``scroll``."""

        scrolled = result.get("scrolled")
        if scrolled is not None:
            if not isinstance(scrolled, dict):
                return _server_error("scroll", "scrolled must be an object.")
            if scrolled.get("direction") not in {"up", "down", "left", "right"}:
                return _server_error("scroll", "scrolled.direction is invalid.")
            if not self._is_number(scrolled.get("amount")):
                return _server_error("scroll", "scrolled.amount must be a number.")
            target_error = self._validate_element_or_coordinate(
                scrolled,
                target_optional=True,
            )
            if target_error:
                return _server_error("scroll", target_error)

        return self._format_action_metadata(result, "scroll")

    def _format_type_response(
        self,
        result: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Format target and character count returned by the ``type`` action."""

        typed = result.get("typed")
        if typed is not None:
            if not isinstance(typed, dict):
                return _server_error("type", "typed must be an object.")
            character_count = typed.get("character_count")
            if not isinstance(character_count, int) or isinstance(character_count, bool):
                return _server_error(
                    "type",
                    "typed.character_count must be an integer.",
                )
            target_error = self._validate_element_or_coordinate(typed)
            if target_error:
                return _server_error("type", target_error)

        return self._format_action_metadata(result, "type")

    def _format_key_response(
        self,
        result: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Format the key combination returned by the ``key`` action."""

        pressed = result.get("pressed")
        if pressed is not None:
            if not isinstance(pressed, dict):
                return _server_error("key", "pressed must be an object.")
            if not isinstance(pressed.get("keys"), str) or not pressed["keys"]:
                return _server_error("key", "pressed.keys must be non-empty text.")
            modifiers = pressed.get("modifiers")
            if modifiers is not None and not (
                isinstance(modifiers, list)
                and all(isinstance(value, str) for value in modifiers)
            ):
                return _server_error("key", "pressed.modifiers must be text values.")

        return self._format_action_metadata(result, "key")

    def _format_set_value_response(
        self,
        result: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Format the element and value returned by ``set_value``."""

        set_result = result.get("set")
        if set_result is not None:
            if not isinstance(set_result, dict):
                return _server_error("set_value", "set must be an object.")
            if not isinstance(set_result.get("value"), str):
                return _server_error("set_value", "set.value must be text.")
            element = set_result.get("element")
            if element is not None and (
                not isinstance(element, int) or isinstance(element, bool)
            ):
                return _server_error("set_value", "set.element must be an integer.")

        return self._format_action_metadata(result, "set_value")

    # ---------------------------------------------------------------------
    # Wait, discovery, and focus responses
    # ---------------------------------------------------------------------

    def _format_wait_response(
        self,
        result: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Format the required duration and summary returned by ``wait``."""

        if not self._is_number(result.get("seconds")):
            return _server_error("wait", "wait response requires numeric seconds.")
        if not isinstance(result.get("summary"), str):
            return _server_error("wait", "wait response requires summary text.")
        return result

    def _format_list_apps_response(
        self,
        result: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Format the application records returned by ``list_apps``."""

        apps = result.get("apps")
        if not isinstance(apps, list) or not all(
            isinstance(app, dict) for app in apps
        ):
            return _server_error("list_apps", "list_apps response requires apps.")
        return result

    def _format_list_windows_response(
        self,
        result: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Format and validate native window records from ``list_windows``."""

        windows = result.get("windows")
        if not isinstance(windows, list):
            return _server_error(
                "list_windows",
                "list_windows response requires windows.",
            )
        for window in windows:
            if not isinstance(window, dict):
                return _server_error("list_windows", "Each window must be an object.")
            for field_name in ("window_id", "pid"):
                value = window.get(field_name)
                if not isinstance(value, int) or isinstance(value, bool):
                    return _server_error(
                        "list_windows",
                        f"Each window requires integer {field_name}.",
                    )
        return result

    def _format_focus_app_response(
        self,
        result: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Format the selected app/window state returned by ``focus_app``."""

        focused = result.get("focused")
        if focused is not None:
            if not isinstance(focused, dict):
                return _server_error("focus_app", "focused must be an object.")
            if not isinstance(focused.get("raised"), bool):
                return _server_error(
                    "focus_app",
                    "focused.raised must be true or false.",
                )
        return result

    # ---------------------------------------------------------------------
    # Reusable metadata helpers
    # ---------------------------------------------------------------------

    def _format_action_metadata(
        self,
        result: Dict[str, Any],
        action: str,
    ) -> Dict[str, Any]:
        """Validate common effect/escalation fields and normalize capture_after."""

        effect = result.get("effect")
        if effect is not None and effect not in {
            "confirmed",
            "unverifiable",
            "suspected_noop",
        }:
            return _server_error(action, "effect has an unsupported value.")

        escalation = result.get("escalation")
        if escalation is not None:
            if not isinstance(escalation, dict):
                return _server_error(action, "escalation must be an object.")
            if escalation.get("recommended") not in {"px", "foreground", "page"}:
                return _server_error(action, "escalation.recommended is invalid.")
            if not isinstance(escalation.get("reason"), str):
                return _server_error(action, "escalation.reason must be text.")

        capture_after = result.get("capture_after")
        if capture_after is not None:
            if not isinstance(capture_after, dict):
                return _server_error(action, "capture_after must be an object.")
            if capture_after.get("ok") is not True:
                return _server_error(action, "capture_after.ok must be true.")
            if capture_after.get("action") != "capture":
                return _server_error(
                    action,
                    "capture_after.action must be 'capture'.",
                )
            normalized_capture = {
                "success": True,
                **{
                    key: value
                    for key, value in capture_after.items()
                    if key != "ok"
                },
            }
            formatted_capture = self._format_capture_response(normalized_capture)
            if formatted_capture.get("success") is False:
                return _server_error(action, "capture_after is invalid.")
            result = {**result, "capture_after": formatted_capture}

        return result

    def _validate_element_or_coordinate(
        self,
        target: Dict[str, Any],
        *,
        target_optional: bool = False,
    ) -> Optional[str]:
        """Validate an optional element ID or two-number coordinate target."""

        element = target.get("element")
        coordinate = target.get("coordinate")
        if element is not None and (
            not isinstance(element, int) or isinstance(element, bool)
        ):
            return "element must be an integer."
        if coordinate is not None and not self._is_coordinate(coordinate):
            return "coordinate must be [x, y]."
        if not target_optional and element is None and coordinate is None:
            return "response requires element or coordinate."
        return None

    @staticmethod
    def _is_coordinate(value: Any) -> bool:
        """Return whether a value is a two-number coordinate."""

        return (
            isinstance(value, (list, tuple))
            and len(value) == 2
            and all(ComputerUseToolExecutor._is_number(item) for item in value)
        )

    @staticmethod
    def _is_number(value: Any) -> bool:
        """Return whether a value is numeric but not a boolean."""

        return isinstance(value, (int, float)) and not isinstance(value, bool)
