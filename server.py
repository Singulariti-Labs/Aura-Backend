#!/usr/bin/env python3
"""
WebSocket Server - Main entry point for the WebSocket server.
Handles connection lifecycle, authentication, and message routing.
"""
import asyncio
import signal
import json
from urllib.parse import parse_qs
import uuid
import sys
import platform
from typing import Dict, Set, Optional, Any

from app.Agents.supervisor import SupervisorAgent
from app.Agents.agent import Agent
from app.Agents.planner import PlannerAgent
from app.Types.agent_types import LLMConfig, SystemInfo


import websockets
from websockets.server import WebSocketServerProtocol
from websockets.exceptions import ConnectionClosedError, ConnectionClosedOK

from websocket.router import MessageRouter
from websocket.auth import authenticate_client, JWTAuthenticationError
from websocket.queues import QueueManager, QueueConnectionError
from websocket.rate_limit import RateLimiter
from websocket.logger import get_logger
from websocket.metrics import (
    ACTIVE_CONNECTIONS, 
    CONNECTION_ERRORS, 
    MESSAGE_COUNT, 
    LATENCY_HISTOGRAM,
    register_metrics_endpoint
)
from websocket.config import settings


logger = get_logger(__name__)

class WebSocketServer:
    """
    WebSocket server that handles client connections, authentication,
    message routing, and manages connections to the message queue.
    """
    def __init__(self) -> None:
        """Initialize the server components."""
        self.clients: Dict[str, WebSocketServerProtocol] = {}
        self.router = MessageRouter()
        self.queue_manager: Optional[QueueManager] = None
        self.should_exit = False
        self.tasks: Set[asyncio.Task] = set()
        self.stop_event = asyncio.Event()
    
    async def start(self) -> None:
        """Start the WebSocket server and all required services."""
        # Set up signal handlers for graceful shutdown in a platform-compatible way
        self._setup_signal_handlers()
        
        # Initialize queue connections
        try:
            self.queue_manager = QueueManager()
            await self.queue_manager.connect()
            # Start consumer for automation results
            consumer_task = asyncio.create_task(self.consume_automation_results())
            self.tasks.add(consumer_task)
            consumer_task.add_done_callback(self.tasks.discard)
            
            # Start WebSocket server
            logger.info("Starting WebSocket server", host=settings.HOST, port=settings.PORT)
            
            # Set up metrics HTTP endpoint
            metrics_task = asyncio.create_task(register_metrics_endpoint())
            self.tasks.add(metrics_task)
            metrics_task.add_done_callback(self.tasks.discard)
            
            # Start heartbeat monitor task
            heartbeat_task = asyncio.create_task(self.heartbeat_monitor())
            self.tasks.add(heartbeat_task)
            heartbeat_task.add_done_callback(self.tasks.discard)
            
            # Start WebSocket server
            async with websockets.serve(
                self.handle_client,
                host=settings.HOST,
                port=settings.PORT,
                ping_interval=settings.PING_INTERVAL,
                ping_timeout=settings.PING_TIMEOUT,
                max_size=settings.MAX_MESSAGE_SIZE,
                max_queue=settings.MAX_QUEUE_SIZE,
            ):
                await self.stop_event.wait()
            
        except QueueConnectionError as e:
            logger.error("Failed to connect to message queue", error=str(e))
            raise
        except Exception as e:
            logger.error("Failed to start server", error=str(e))
            raise
        finally:
            await self.cleanup()

    def _setup_signal_handlers(self) -> None:
        """
        Set up signal handlers in a cross-platform compatible way.
        """
        loop = asyncio.get_running_loop()
        
        # On Windows, we can't use add_signal_handler directly
        if platform.system() == "Windows":
            # For Windows, we need to use signal.signal directly
            for sig in (signal.SIGINT, signal.SIGTERM):
                signal.signal(sig, lambda s, f: asyncio.create_task(self.shutdown(s)))
        else:
            # On Unix-like systems, we can use the event loop's signal handler
            for sig in (signal.SIGINT, signal.SIGTERM):
                loop.add_signal_handler(
                    sig, lambda s=sig: asyncio.create_task(self.shutdown(s))
                )
    
    async def handle_client(self, websocket: WebSocketServerProtocol, path: str) -> None:
        """
        Handle a client connection - authentication, message processing, and cleanup.
        
        Args:
            websocket: The WebSocket connection
            path: The request path with query parameters
        """
        client_id = str(uuid.uuid4())
        client_info = {"ip": websocket.remote_address[0], "client_id": client_id}
        
        # Parse query parameters
        query_params = parse_qs(path.split("?", 1)[1]) if "?" in path else {}
        
        # Extract token from query params or headers
        token = None
        if "token" in query_params:
            token = query_params["token"][0]
        elif "Authorization" in websocket.request_headers:
            auth_header = websocket.request_headers["Authorization"]
            if auth_header.startswith("Bearer "):
                token = auth_header[7:]  # Remove 'Bearer ' prefix
        
        try:
            # Authenticate the client
            if not token:
                logger.warning("No authentication token provided", **client_info)
                await websocket.close(1008, "Authentication required")
                return
            
            user_data = await authenticate_client(token)
            client_info.update({"user_id": user_data["user_id"], "username": user_data.get("username", "unknown")})
            logger.info("Client connected", **client_info)
            
            # Register client
            self.clients[client_id] = websocket
            ACTIVE_CONNECTIONS.inc()
            
            # Create rate limiter for this connection
            rate_limiter = RateLimiter(
                client_id=client_id,
                max_messages=settings.RATE_LIMIT_MAX_MESSAGES,
                time_window=settings.RATE_LIMIT_WINDOW_SECONDS
            )
            
            # Process messages
            async for message in websocket:
                start_time = asyncio.get_running_loop().time()
                
                # Check rate limit
                if not rate_limiter.allow_message():
                    logger.warning("Rate limit exceeded", **client_info)
                    await websocket.send(json.dumps({
                        "error": "rate_limit_exceeded",
                        "message": "Too many messages, please slow down"
                    }))
                    continue
                
                MESSAGE_COUNT.inc()
                
                try:
                    # Parse and validate the message
                    data = json.loads(message)
                    response = await self.router.route_message(data, user_data, self.queue_manager)
                    
                    # Send response if any
                    if response:
                        await websocket.send(json.dumps(response))
                    
                except json.JSONDecodeError:
                    logger.warning("Invalid JSON received", **client_info)
                    await websocket.send(json.dumps({
                        "error": "invalid_format",
                        "message": "Invalid JSON format"
                    }))
                except Exception as e:
                    logger.error("Error processing message", error=str(e), **client_info)
                    await websocket.send(json.dumps({
                        "error": "processing_error",
                        "message": "Failed to process message"
                    }))
                finally:
                    # Record request latency
                    latency = asyncio.get_running_loop().time() - start_time
                    LATENCY_HISTOGRAM.observe(latency)
        
        except JWTAuthenticationError as e:
            logger.warning("Authentication failed", error=str(e), **client_info)
            await websocket.close(1008, "Authentication failed")
        except (ConnectionClosedError, ConnectionClosedOK):
            logger.info("Client disconnected", **client_info)
        except Exception as e:
            logger.error("Unexpected error", error=str(e), **client_info)
            CONNECTION_ERRORS.inc()
            try:
                await websocket.close(1011, "Internal server error")
            except:
                pass
        finally:
            # Clean up client resources
            if client_id in self.clients:
                del self.clients[client_id]
                ACTIVE_CONNECTIONS.dec()
                logger.info("Client cleanup complete", **client_info)
    
    async def consume_automation_results(self) -> None:
        """
        Consume messages from the automation_results queue and route them
        to the appropriate clients based on request_id.
        """
        logger.info("Starting automation results consumer")
        try:
            await self.queue_manager.consume_automation_results(self.handle_automation_result)
        except Exception as e:
            logger.error("Error in automation results consumer", error=str(e))
            if not self.should_exit:
                # Attempt to reconnect if this wasn't triggered by shutdown
                logger.info("Attempting to reconnect automation consumer")
                await asyncio.sleep(5)  # Backoff before reconnecting
                asyncio.create_task(self.consume_automation_results())
    
    async def handle_automation_result(self, result: Dict[str, Any]) -> None:
        """
        Process automation results and send them to the appropriate client.
        
        Args:
            result: The automation result with request_id and payload
        """
        request_id = result.get("request_id")
        client_id = result.get("client_id")
        
        if not client_id or not request_id:
            logger.error("Invalid automation result", result=result)
            return
        
        if client_id not in self.clients:
            logger.warning("Client not found for automation result", 
                          client_id=client_id, request_id=request_id)
            return
        
        try:
            websocket = self.clients[client_id]
            response = {
                "version": result.get("version", "v1"),
                "channel": "automation",
                "request_id": request_id,
                "payload": result.get("payload", {})
            }
            await websocket.send(json.dumps(response))
            logger.info("Sent automation result to client", 
                       client_id=client_id, request_id=request_id)
        except Exception as e:
            logger.error("Failed to send automation result", 
                        error=str(e), client_id=client_id, request_id=request_id)
    
    async def heartbeat_monitor(self) -> None:
        """
        Periodically check client connections and remove stale ones.
        """
        while not self.should_exit:
            try:
                for client_id, websocket in list(self.clients.items()):
                    try:
                        pong_waiter = await websocket.ping()
                        await asyncio.wait_for(pong_waiter, timeout=settings.PING_TIMEOUT)
                    except asyncio.TimeoutError:
                        logger.warning("Client heartbeat timeout", client_id=client_id)
                        try:
                            await websocket.close(1001, "Heartbeat timeout")
                        except:
                            pass
                        if client_id in self.clients:
                            del self.clients[client_id]
                            ACTIVE_CONNECTIONS.dec()
                    except Exception:
                        # Connection likely already closed
                        if client_id in self.clients:
                            del self.clients[client_id]
                            ACTIVE_CONNECTIONS.dec()
            except Exception as e:
                logger.error("Error in heartbeat monitor", error=str(e))
            
            await asyncio.sleep(settings.HEARTBEAT_INTERVAL)
    
    async def shutdown(self, signal=None) -> None:
        """
        Handle graceful shutdown on signal.
        
        Args:
            signal: The signal that triggered the shutdown
        """
        if signal:
            logger.info(f"Received exit signal {signal.name}")
        
        logger.info("Shutting down server...")
        self.should_exit = True
        
        # Stop accepting new connections
        self.stop_event.set()
        
        # Close all client connections
        close_tasks = []
        for client_id, websocket in self.clients.items():
            try:
                close_tasks.append(asyncio.create_task(
                    websocket.close(1001, "Server shutting down")
                ))
            except:
                pass
        
        if close_tasks:
            await asyncio.gather(*close_tasks, return_exceptions=True)
        
        # Cancel all background tasks
        for task in self.tasks:
            task.cancel()
        
        # Wait for tasks to complete with timeout
        if self.tasks:
            await asyncio.wait(self.tasks, timeout=5)
        
        logger.info("Server shutdown complete")
    
    async def cleanup(self) -> None:
        """Clean up resources during shutdown."""
        if self.queue_manager:
            await self.queue_manager.close()


async def main() -> None:
    """Main entry point for the WebSocket server."""
    server = WebSocketServer()
    await server.start()


if __name__ == "__main__":
    asyncio.run(main())