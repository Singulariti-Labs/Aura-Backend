"""
WebSocket routes for handling real-time client interactions using FastAPI.

This module sets up a WebSocket endpoint (`/ws`) to receive messages from clients,
invoke the agent asynchronously, and send back standardized responses and status updates.
"""

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, status
from .connection_manager import ConnectionManager
from app.Agents.agent import Agent
from app.Types.agent_types import LLMConfig, SystemInfo, DEFAULT_MODELS, PROVIDER_MAPPING
from app.api.websocket_utils import send_ws_message
from app.Task.task_manager import task_manager
from app.DB.pool import get_pool
from app.api.auth_utils import token_verifier
from app.DB.Queries.task import create_task, update_task_status
from app.DB.Queries.agent_event import create_agent_event
from app.DB.Queries.user import get_user_by_auth0_id
from app.DB.Queries.user_settings import get_user_settings

import asyncio
import uuid
import logging

# Configure logger for WebSocket routes
logger = logging.getLogger(__name__)

# Initialize FastAPI router for WebSocket routes
ws_router = APIRouter()

# Global connection manager instance to handle active WebSocket connections
manager = ConnectionManager()

# Default configuration for the LLM agent
llm_config = LLMConfig(provider="google", model_name="gemini-2.5-flash")

# task_manager = TaskManager()

@ws_router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """
    Handle WebSocket connections at /ws.`

    Accepts JSON messages from the client containing a query and optional system info.
    Each message triggers an asynchronous agent invocation, with status updates sent
    back to the client throughout the lifecycle of the request.
    """
    client_host = websocket.client.host if websocket.client else "unknown"
    client_port = websocket.client.port if websocket.client else "unknown"
    logger.info(f"🔌 WebSocket connection attempt from {client_host}:{client_port}")
    
    await manager.connect(websocket)
    logger.info(f"✅ WebSocket connection established for {client_host}:{client_port}")

    # Auth0 Token Validation
    token = websocket.query_params.get("token")
    if not token:
        logger.warning(f"❌ Auth failed: No token provided from {client_host}:{client_port}")
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return
    
    logger.info(f"🔐 Verifying Auth0 token for {client_host}:{client_port}")
    try:
        user_payload = token_verifier.verify(token)
        logger.info(f"✅ Token verified successfully for user: {user_payload.get('sub', 'unknown')}")
    except Exception as e:
        logger.error(f"❌ Token verification failed from {client_host}:{client_port}: {str(e)}")
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    # Initialize database pool
    pool = await get_pool()

    auth0_id = user_payload.get("sub")
    logger.info(f"🔍 Looking up user in database: {auth0_id}")
    
    user = await get_user_by_auth0_id(pool, auth0_id)
    if not user:
        logger.error(f"❌ User not found in database: {auth0_id} from {client_host}:{client_port}")
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return
    
    user_id = user["id"]
    logger.info(f"✅ User authenticated successfully: {user.get('email', 'unknown')} (ID: {user_id})")
    
    user_settings = await get_user_settings(pool, user_id)
    if user_settings:
        logger.info(f"📋 Loaded user settings for user_id: {user_id}")
    else:
        logger.info(f"📋 No custom settings found for user_id: {user_id}, using defaults")

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
                
                # Check for LLM config override from user settings
                current_llm_config = llm_config
                using_custom = False
                if user_settings and "api_creds" in user_settings:
                    api_creds = user_settings.get("api_creds", {})
                    raw_provider = api_creds.get("provider")
                    custom_api_key = api_creds.get("key")
                    
                    if raw_provider and custom_api_key:
                        target_provider = PROVIDER_MAPPING.get(raw_provider)
                        if target_provider:
                            default_model = DEFAULT_MODELS.get(target_provider)
                            try:
                                current_llm_config = LLMConfig(
                                    provider=target_provider,
                                    model_name=default_model,
                                    api_key=custom_api_key
                                )
                                using_custom = True
                                logger.info(f"Using custom LLM config for user using, {target_provider}")
                            except Exception as e:
                                logger.error(f"Failed to create custom LLM config: {e}. Falling back to default.")
                
                if not using_custom:
                    logger.info(f"Using default LLM config for user using, {current_llm_config.provider}")

                if not using_custom and payload.get("option") == "smart":
                    await send_ws_message(
                        websocket,
                        type="aura_message",
                        task_id=task_id,
                        chat_id=chat_id,
                        payload={
                            "role": "assistant",
                            "tool": "aura",
                            "message": "Please use your own API keys to access smart mode. You can use gemini or open-ai model"
                        }
                    )
                    await update_task_status(pool=pool, task_id=task_id, status="completed")
                    return
                
                agent = Agent(llm=current_llm_config, query=query, payload=payload, system_info=system_info, task_id=task_id, chat_id=chat_id, pool=pool)

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
        logger.info(f"🔌 WebSocket disconnected: {client_host}:{client_port}")
        manager.disconnect(websocket)
