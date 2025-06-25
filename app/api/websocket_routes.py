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
from app.Task.task_manager import task_manager
import asyncio
import uuid

# Initialize FastAPI router for WebSocket routes
ws_router = APIRouter()

# Global connection manager instance to handle active WebSocket connections
manager = ConnectionManager()

# Default configuration for the LLM agent
llm_config = LLMConfig(provider="openai", model_name="gpt-4o")
# task_manager = TaskManager()

@ws_router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """
    Handle WebSocket connections at /ws.

    Accepts JSON messages from the client containing a query and optional system info.
    Each message triggers an asynchronous agent invocation, with status updates sent
    back to the client throughout the lifecycle of the request.
    """
    await manager.connect(websocket)

    async def handle_query(message: dict, task_id: str):
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

            # # Create a task_id to identify task
            # task_id = str(uuid.uuid4())
            
            # # Creating the task using TaskManager class
            # task_manager.create_task(task_id, websocket)          

            # Prepare agent with system and LLM configuration
            system_info = SystemInfo(os=os_info, version=version_info)
            agent = Agent(llm=llm_config, query=query, system_info=system_info, task_id=task_id)

            # Notify client that processing has started
            await send_ws_message(
                websocket,
                type_="status",
                status="processing",
                query=query,
                message="Agent is processing the request",
                id_=id_,
                task_id=task_id # New Parameter task_id
            )

            # Invoke the agent
            response = await agent.invoke()
            # response = {
            #             "type": "screenshot",
            #             "return_format": "base64",
            #             "resize": [640, 480],
            #             "quality": 50
            #             }
            # response = {
            #             "type": "desktop_interaction",
            #                 "actions":[{
            #                     "action": {
            #                         "type": "click",
            #                         "position": [200, 200],
            #                         "button": "left"
            #                     },
            #                     "interacting_on": "default",
            #                     "confidence": 1.0
            # }]}

            final_result = {
                "input": response["input"],
                "output": response["output"]
            }

            # Send back the final response
            await send_ws_message(
                websocket,
                type_="response",
                status="completed",
                query=query,
                data=final_result,
                message="Completed",
                id_=id_,
                task_id=task_id
            )

        except asyncio.CancelledError:
            await send_ws_message(
                websocket,
                type_="status",
                status="cancelled",
                message="Task was cancelled by the user.",
                id_=id_,
                task_id=task_id
            )

        except KeyError:
            # Handle missing 'query' field
            await send_ws_message(
                websocket,
                type_="error",
                status="error",
                query=message.get("query", ""),
                message="Missing required field 'query'",
                id_=message.get("id"),
                task_id=task_id
            )
        except Exception as e:
            # Catch-all for unexpected runtime errors
            await send_ws_message(
                websocket,
                type_="error",
                status="error",
                query=message.get("query", ""),
                message=str(e),
                id_=message.get("id"),
                task_id=task_id
            )
        finally:
            task_manager.remove_task(task_id)

    try:
        while True:
            try:
                # Wait for the next incoming JSON message from the client
                message = await websocket.receive_json()
                print(f"WEBSOCKET MESSAGE RECIVED, {message}")
                msg_type = message.get("type_")
                task_id = str(uuid.uuid4())

                if msg_type == "query":
                    # Create task state with WebSocket
                    task_manager.create_task(task_id, websocket)
                    # Handle each message in its own asynchronous task
                    task = asyncio.create_task(handle_query(message, task_id))
                    task_manager.set_task(task_id, task)
                
                elif msg_type == "cancel":
                    task_id = message.get("task_id")
                    task_manager.cancel_task(task_id)

                elif msg_type == "pause":
                    task_id = message.get("task_id")
                    task_manager.pause_task(task_id)

                elif msg_type == "resume":
                    task_id = message.get("task_id")
                    task_manager.resume_task(task_id)

                elif msg_type == "user_input":
                    task_id = message.get("task_id")
                    input_data = message.get("data")
                    task_manager.provide_input(task_id, input_data)
                
                else:
                    await send_ws_message(
                        websocket,
                        type_="error",
                        status="error",
                        message=f"Unknown message type: {msg_type}",
                        id_ = message.get("id_"),
                        task_id=message.get("task_id")
                )

            except ValueError:
                # Notify client of JSON decode errors
                await websocket.send_json({
                    "status": "error",
                    "message": "Invalid JSON format"
                })

    except WebSocketDisconnect:
        # Cleanly remove disconnected WebSocket from the manager
        manager.disconnect(websocket)
