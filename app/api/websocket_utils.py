"""
Helper function to send standardized WebSocket messages.

This utility formats the response payload consistently for client-side processing,
including status, type, query context, message body, and optional metadata.
"""

from fastapi import WebSocket

from app.Types.agent_types import WS_MESSAGE_TYPE


async def send_ws_message(
    websocket: WebSocket,
    *,
    type: WS_MESSAGE_TYPE,
    task_id: str,
    chat_id: str,
    payload: dict = {},
    # message: str = "",
    # query: str = "",
    # data: dict = None,
    # id_: str = None,
):
    """
    Sends a structured JSON message to a WebSocket client.

    Args:
        websocket (WebSocket): The WebSocket connection to send the message through.
        type (str): The type of message (eg, "client_tool_request", "client_tool_response", "server_tool_response", "error_message", "screenshot_response", "user_input").
        task_id (str): Unique Identifier for a task.
        chat_id (str): Unique Identifier for a chat.
        payload (dict, optional): Any additional data to send (e.g., agent response, tool response). Defaults to None.
    """
    # Build the standard message structure
    message = {
        "type": type,
        "task_id": task_id,
        "chat_id": chat_id,
        "payload": payload
    }

    # Include optional fields if provided
    # if data is not None:
    #     payload["data"] = data
    # if id_ is not None:
    #     payload["id"] = id_


    # Send the message over WebSocket
    await websocket.send_json(message)
