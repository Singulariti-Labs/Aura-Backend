"""
Router module - Handles message routing based on version and channel.
"""
import json
from typing import Dict, Any, Optional, Callable, Awaitable

from websocket.queues import QueueManager
from websocket.logger import get_logger
from websocket.metrics import MESSAGE_ROUTING_ERROR


logger = get_logger(__name__)


class MessageRouter:
    """
    Routes messages based on version and channel.
    Handles versioning and validation logic for different message types.
    """
    def __init__(self) -> None:
        """Initialize the message router with version and channel handlers."""
        # Register supported versions and their handlers
        self.version_handlers = {
            "v1": self._handle_v1_message,
            # Add more version handlers as they are developed
            # "v2": self._handle_v2_message,
        }
        
        # Register supported channels for v1
        self.v1_channel_handlers = {
            "chat": self._handle_chat_message,
            "automation": self._handle_automation_message,
        }
    
    async def route_message(
        self, message: Dict[str, Any], 
        user_data: Dict[str, Any],
        queue_manager: Optional[QueueManager] = None
    ) -> Dict[str, Any]:
        """
        Route a message to the appropriate handler based on version and channel.
        
        Args:
            message: The client message to route
            user_data: Authenticated user data
            queue_manager: Optional queue manager for publishing messages
            
        Returns:
            Optional response message or error
        """
        try:
            # Extract version
            version = message.get("version")
            if not version:
                error = "Missing version in message"
                logger.warning(error, message=message)
                MESSAGE_ROUTING_ERROR.labels(error="missing_version").inc()
                return {"error": "invalid_request", "message": error}
            
            # Check if version is supported
            if version not in self.version_handlers:
                error = f"Unsupported version: {version}"
                logger.warning(error, version=version)
                MESSAGE_ROUTING_ERROR.labels(error="unsupported_version").inc()
                return {"error": "unsupported_version", "message": error}
            
            # Route to version handler
            return await self.version_handlers[version](message, user_data, queue_manager)
            
        except Exception as e:
            logger.error("Error routing message", error=str(e), message=message)
            MESSAGE_ROUTING_ERROR.labels(error="routing_error").inc()
            return {"error": "routing_error", "message": "Error processing message"}
    
    async def _handle_v1_message(
        self, message: Dict[str, Any], 
        user_data: Dict[str, Any],
        queue_manager: Optional[QueueManager]
    ) -> Dict[str, Any]:
        """
        Handle v1 version messages, routing to the appropriate channel handler.
        
        Args:
            message: The v1 message to route
            user_data: Authenticated user data
            queue_manager: Optional queue manager for publishing messages
            
        Returns:
            Optional response message or error
        """
        # Extract and validate channel
        channel = message.get("channel")
        if not channel:
            error = "Missing channel in message"
            logger.warning(error, message=message)
            MESSAGE_ROUTING_ERROR.labels(error="missing_channel").inc()
            return {"error": "invalid_request", "message": error}
        
        # Check if channel is supported
        if channel not in self.v1_channel_handlers:
            error = f"Unsupported channel: {channel}"
            logger.warning(error, channel=channel)
            MESSAGE_ROUTING_ERROR.labels(error="unsupported_channel").inc()
            return {"error": "unsupported_channel", "message": error}
        
        # Check for required fields
        if "request_id" not in message:
            error = "Missing request_id in message"
            logger.warning(error, message=message)
            MESSAGE_ROUTING_ERROR.labels(error="missing_request_id").inc()
            return {"error": "invalid_request", "message": error}
        
        if "payload" not in message:
            error = "Missing payload in message"
            logger.warning(error, message=message)
            MESSAGE_ROUTING_ERROR.labels(error="missing_payload").inc()
            return {"error": "invalid_request", "message": error}
        
        # Route to channel handler
        return await self.v1_channel_handlers[channel](message, user_data, queue_manager)
    
    async def _handle_chat_message(
        self, message: Dict[str, Any], 
        user_data: Dict[str, Any],
        queue_manager: Optional[QueueManager]
    ) -> Dict[str, Any]:
        """
        Handle chat channel messages.
        
        Args:
            message: The chat message to process
            user_data: Authenticated user data
            queue_manager: Optional queue manager for publishing messages
            
        Returns:
            Response message or error
        """
        # Simple echo for now, would integrate with chat service in production
        request_id = message.get("request_id")
        payload = message.get("payload", {})
        
        logger.info("Processing chat message", 
                   user_id=user_data.get("user_id"), 
                   request_id=request_id)
        
        # Process chat message logic here
        
        # For now just echo the message back
        return {
            "version": "v1",
            "channel": "chat",
            "request_id": request_id,
            "payload": {
                "status": "received",
                "message": f"Received chat message: {json.dumps(payload)}"
            }
        }
    
    async def _handle_automation_message(
        self, message: Dict[str, Any], 
        user_data: Dict[str, Any],
        queue_manager: Optional[QueueManager]
    ) -> Dict[str, Any]:
        """
        Handle automation channel messages by routing to RabbitMQ.
        
        Args:
            message: The automation message to process
            user_data: Authenticated user data
            queue_manager: Optional queue manager for publishing messages
            
        Returns:
            Acknowledgement response or error
        """
        if not queue_manager:
            error = "Queue manager not available"
            logger.error(error)
            return {"error": "service_unavailable", "message": error}
        
        request_id = message.get("request_id")
        payload = message.get("payload", {})
        
        logger.info("Processing automation message", 
                   user_id=user_data.get("user_id"), 
                   request_id=request_id)
        
        # Enhanced message with user data and client info
        enriched_message = {
            "version": message.get("version"),
            "channel": "automation",
            "request_id": request_id,
            "client_id": user_data.get("client_id"),
            "user_id": user_data.get("user_id"),
            "username": user_data.get("username"),
            "payload": payload,
            "timestamp": message.get("timestamp")
        }
        
        # Publish message to RabbitMQ
        try:
            await queue_manager.publish_automation_job(enriched_message)
            
            return {
                "version": "v1",
                "channel": "automation",
                "request_id": request_id,
                "payload": {
                    "status": "accepted",
                    "message": "Automation job queued for processing"
                }
            }
        except Exception as e:
            logger.error("Failed to publish automation job", 
                        error=str(e), request_id=request_id)
            return {
                "error": "processing_error",
                "message": "Failed to queue automation job"
            }
