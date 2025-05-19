#!/usr/bin/env python3
"""
Main entry point for the WebSocket server with integrated agent capabilities.
"""
import asyncio
import signal
import platform
import json
from typing import Dict, Any, Optional

from app.Agents.supervisor import SupervisorAgent
from app.Agents.agent import Agent
from app.Agents.planner import PlannerAgent
from app.Types.agent_types import LLMConfig, SystemInfo

from server import WebSocketServer
from websocket.router import MessageRouter
from websocket.logger import get_logger
from websocket.config import settings
from websocket.queues import QueueManager

logger = get_logger(__name__)


class AgentEnabledRouter(MessageRouter):
    """
    Enhanced MessageRouter that integrates with AI agents for handling chat messages.
    """
    def __init__(self, llm_config: LLMConfig, system_info: SystemInfo) -> None:
        """Initialize the router with AI agent capabilities."""
        super().__init__()
        self.llm_config = llm_config
        self.system_info = system_info
        # No need to override the chat handler since the base now supports agents
        # The base router will use the agent for chat processing


async def main() -> None:
    """
    Main entry point for the WebSocket server with integrated agent capabilities.
    """
    try:
        # Configure the LLM and system info
        llm_config = LLMConfig(provider="openai", model_name="gpt-4o")
        system_info = SystemInfo(os=platform.system().lower(), version=platform.version())
        
        # Initialize router with agent capabilities
        router = AgentEnabledRouter(llm_config, system_info)
        
        # Initialize and connect QueueManager
        queue_manager = QueueManager()
        await queue_manager.connect()
        
        # Create and start the WebSocket server with the agent-enabled router
        logger.info("Starting agent-enabled WebSocket server")
        server = WebSocketServer()
        # Override the default router with our agent-enabled router
        server.router = router
        
        await server.start()
        
    except Exception as e:
        logger.error("Failed to start server", error=str(e))
        raise


if __name__ == "__main__":
    asyncio.run(main())