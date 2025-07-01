"""
Helper function to send standardized WebSocket messages.

This utility formats the response payload consistently for client-side processing,
including status, type, query context, message body, and optional metadata.
"""

from fastapi import WebSocket


async def send_ws_message(
    websocket: WebSocket,
    *,
    type: str,
    status: str,
    task_id: str,
    message: str = "",
    query: str = "",
    data: dict = None,
    id_: str = None,
):
    """
    Sends a structured JSON message to a WebSocket client.

    Args:
        websocket (WebSocket): The WebSocket connection to send the message through.
        type (str): The type of message (e.g., "status", "response", "error").
        status (str): The status of the message (e.g., "processing", "completed", "error").
        query (str): The original query from the client, echoed back for context.
        message (str, optional): A human-readable message for the client. Defaults to "".
        data (dict, optional): Any additional data to send (e.g., agent response). Defaults to None.
        id_ (str, optional): Optional ID to correlate the response with a specific client message. Defaults to None.
        task_id (str): Unique Identifier for a task.
    """
    # Build the standard payload structure
    payload = {
        "type": type,
        "status": status,
        "query": query,
        "message": message,
        "task_id": task_id
    }

    # Include optional fields if provided
    if data is not None:
        payload["data"] = data
    if id_ is not None:
        payload["id"] = id_

    # Send the message over WebSocket
    await websocket.send_json(payload)
