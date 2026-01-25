"""
WebSocket routes for handling real-time client interactions using FastAPI.

This module sets up a WebSocket endpoint (`/ws`) to receive messages from clients,
invoke the agent asynchronously, and send back standardized responses and status updates.
"""

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, status
from .connection_manager import ConnectionManager
from app.Agents.agent import Agent
from app.Types.agent_types import LLMConfig, SystemInfo
from app.api.websocket_utils import send_ws_message
from app.Task.task_manager import task_manager
from app.DB.pool import get_pool
from app.api.auth_utils import token_verifier
from app.DB.Queries.task import create_task, update_task_status
from app.DB.Queries.agent_event import create_agent_event
from app.DB.Queries.user import get_user_by_auth0_id

import asyncio
import uuid

# Initialize FastAPI router for WebSocket routes
ws_router = APIRouter()

# Global connection manager instance to handle active WebSocket connections
manager = ConnectionManager()

# Default configuration for the LLM agent
llm_config = LLMConfig(provider="open_router", model_name="xiaomi")
# task_manager = TaskManager()

@ws_router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """
    Handle WebSocket connections at /ws.`

    Accepts JSON messages from the client containing a query and optional system info.
    Each message triggers an asynchronous agent invocation, with status updates sent
    back to the client throughout the lifecycle of the request.
    """
    await manager.connect(websocket)

    # Auth0 Token Validation
    token = websocket.query_params.get("token")
    if not token:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return
    
    try:
        user_payload = token_verifier.verify(token)
    except Exception:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    # Initialize database pool
    pool = await get_pool()

    auth0_id = user_payload.get("sub")
    user = await get_user_by_auth0_id(pool, auth0_id)
    if not user:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return
    user_id = user["id"]

    async def handle_query(message: dict, task_id: str, chat_id: str):
        """
        Handle an individual query message from the client.

        Extracts parameters, invokes the agent asynchronously, and sends structured
        responses and status updates back to the client.

        Args:
            message (dict): The incoming JSON message from the WebSocket client.
        """
        try:
            payload = message.get("payload")
            print(f"payload: {payload}")
            if payload:
                query = payload.get("query")

                if query is None:
                    raise ValueError("Missing required field: 'query'")
                
                # Create task in the database
                await create_task(pool, task_id, chat_id, query, user_id)

                os_info = payload.get("os", "windows")
                version_info = message.get("os_version", "11")
                # workspace_path = message.get("workspace")  # WIP** -> Workspace Path need to add in the AURA Prompt.
                # id_ = message.get("id")  # Optional identifier for tracking response         

                # Prepare agent with system and LLM configuration
                system_info = SystemInfo(os=os_info, version=version_info)
                agent = Agent(llm=llm_config, query=query, payload=payload, system_info=system_info, task_id=task_id, chat_id=chat_id, pool=pool)

                # Notify client that processing has started
                await send_ws_message(
                    websocket,
                    type="aura_status",
                    task_id=task_id, # New Parameter task_id
                    chat_id=chat_id,
                    payload= {
                        "query": query,
                        "message": "Agent is processing the request",
                        "status": "processing",
                    }
                )

                # Invoke the agent
                response = await agent.invoke()

                # Robust response handling
                if response is None:
                    final_result = {
                        "input": query,
                        "output": ""
                    }
                elif isinstance(response, str):
                    final_result = {
                        "input": query,
                        "output": response
                    }
                elif isinstance(response, dict):
                    final_result = {
                        "input": response.get("input", query),
                        "output": response.get("output", "")
                    }
                else:
                    final_result = {
                        "input": query,
                        "output": str(response)
                    }
                
                # Send back the final response [NO NEED TO SEND THE FINAL RESPONSE AS THE RESPONSE IS ALREADY STREAMED]
                # await send_ws_message(
                #     websocket,
                #     type="server_tool_response",
                #     task_id=task_id,
                #     chat_id=chat_id,
                #     payload={
                #         "tool": "aura",
                #         "content": {
                #             "role": "assistant",
                #             "message": final_result["output"],
                #             "status": "success"
                #         }
                #     }
                # )
                
                # Retrieve task state for event logging
                # task_state = task_manager.get_state(task_id)

                # Insert AURA complex agent event in the DB - FINAL ANSWER
                # await create_agent_event(
                #     pool=pool,
                #     task_id=task_id,
                #     role="tool",
                #     message_type="server_tool_response",
                #     tool="aura",
                #     payload= {
                #         "content": {
                #             "message": final_result["output"],
                #             "status": "success"
                #         }
                #     },
                #     seq = task_state.get_next_seq() if task_state else 1
                # )

                # UPDATE TASK STATUS TO COMPLETED
                await update_task_status(pool=pool, task_id=task_id, status="completed")

            else:
                await send_ws_message(
                    websocket,
                    type="error_message",
                    task_id=task_id,
                    chat_id=chat_id,
                    payload={
                        "error_code": "PAYLOAD_NOT_FOUND",
                        "message": "Error due to payload not found in the task request"
                    }
                )

        except asyncio.CancelledError:
            await send_ws_message(
                websocket,
                type="aura_status",
                task_id=task_id,
                chat_id=chat_id,
                payload= {
                    "query": query,
                    "message": "Task is cancled by the user",
                    "status": "cancelled",
                }
            )

        except KeyError:
            # Handle missing 'query' field
            await send_ws_message(
                websocket,
                type="error_message",
                task_id=task_id,
                chat_id=chat_id,
                payload={
                    "error_code": "MISSING_REQUIRED_FIELD",
                    "message": "Missing required field 'query' in the payload"
                }
            )
        except Exception as e:
            # Catch-all for unexpected runtime errors
            await send_ws_message(
                websocket,
                type="error_message",
                task_id=task_id,
                chat_id=chat_id,
                payload={
                    "error_code": "SYSTEM_ERROR",
                    "message": f"ERROR: {str(e)}"
                }            
            )
        finally:
            task_manager.remove_task(task_id)

    try:
        while True:
            try:
                # Wait for the next incoming JSON message from the client
                message = await websocket.receive_json()
                msg_type = message.get("type")
                task_id = message.get("task_id") or str(uuid.uuid4())
                chat_id = message.get("chat_id")

                if msg_type == "task_request":

                    # Create task state with WebSocket and DB Pool
                    task_manager.create_task(task_id, websocket, pool)
                    # Handle each message in its own asynchronous task
                    task = asyncio.create_task(handle_query(message, task_id, chat_id))
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

                elif msg_type == "client_tool_response":
                    task_id = message.get("task_id")
                    input_data = message
                    task_manager.provide_input(task_id, input_data)
                
                else:
                    await send_ws_message(
                        websocket,
                        type="error_message",
                        task_id=message.get("task_id"),
                        chat_id=chat_id,
                        payload={
                            "error_code": "INTERNAL_ERROR",
                            "message": f"Unknown message type: {msg_type}"
                        }    
                )

            except ValueError:
                # Notify client of JSON decode errors
                await send_ws_message(
                    websocket,
                    type="error_message",
                    task_id=message.get("task_id"),
                    chat_id=chat_id,
                    payload={
                        "error_code": "INTERNAL_ERROR",
                        "message": f"Invalid JSON format"
                    }
                )

    except WebSocketDisconnect:
        # Cleanly remove disconnected WebSocket from the manager
        manager.disconnect(websocket)
