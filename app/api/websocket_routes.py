"""
WebSocket routes for handling real-time client interactions using FastAPI.

This module sets up a WebSocket endpoint (`/ws`) to receive messages from clients,
invoke the agent asynchronously, and send back standardized responses and status updates.
"""

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from .connection_manager import ConnectionManager
from app.Agents.agent import Agent
from app.Types.agent_types import LLMConfig, SystemInfo
from app.api.websocket_utils import send_ws_message
import asyncio

# Initialize FastAPI router for WebSocket routes
ws_router = APIRouter()

# Global connection manager instance to handle active WebSocket connections
manager = ConnectionManager()

# Default configuration for the LLM agent
llm_config = LLMConfig(provider="openai", model_name="gpt-4o")

@ws_router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """
    Handle WebSocket connections at /ws.

    Accepts JSON messages from the client containing a query and optional system info.
    Each message triggers an asynchronous agent invocation, with status updates sent
    back to the client throughout the lifecycle of the request.
    """
    await manager.connect(websocket)

    async def handle_query(message: dict):
        """
        Handle an individual query message from the client.

        Extracts parameters, invokes the agent asynchronously, and sends structured
        responses and status updates back to the client.

        Args:
            message (dict): The incoming JSON message from the WebSocket client.
        """
        try:
            query = message["query"]
            os_info = message.get("os", "windows")
            version_info = message.get("version", "11")
            id_ = message.get("id")  # Optional identifier for tracking response

            # Prepare agent with system and LLM configuration
            system_info = SystemInfo(os=os_info, version=version_info)
            agent = Agent(llm=llm_config, query=query, system_info=system_info)

            # Notify client that processing has started
            await send_ws_message(
                websocket,
                type_="status",
                status="processing",
                query=query,
                message="Agent is processing the request",
                id_=id_
            )

            # Invoke the agent
            response = await agent.invoke()

            # Send back the final response
            await send_ws_message(
                websocket,
                type_="response",
                status="completed",
                query=query,
                data={"response": response},
                message="Completed",
                id_=id_
            )

        except KeyError:
            # Handle missing 'query' field
            await send_ws_message(
                websocket,
                type_="error",
                status="error",
                query=message.get("query", ""),
                message="Missing required field 'query'",
                id_=message.get("id")
            )
        except Exception as e:
            # Catch-all for unexpected runtime errors
            await send_ws_message(
                websocket,
                type_="error",
                status="error",
                query=message.get("query", ""),
                message=str(e),
                id_=message.get("id")
            )

    try:
        while True:
            try:
                # Wait for the next incoming JSON message from the client
                message = await websocket.receive_json()
                
                # Handle each message in its own asynchronous task
                asyncio.create_task(handle_query(message))

            except ValueError:
                # Notify client of JSON decode errors
                await websocket.send_json({
                    "status": "error",
                    "message": "Invalid JSON format"
                })

    except WebSocketDisconnect:
        # Cleanly remove disconnected WebSocket from the manager
        manager.disconnect(websocket)
