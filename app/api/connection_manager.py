"""
Connection Manager for handling multiple WebSocket connections.

This module defines a ConnectionManager class that manages active WebSocket connections,
supports adding/removing clients, and broadcasting messages to all connected clients.
"""

from fastapi import WebSocket
from typing import List


class ConnectionManager:
    """
    Manages WebSocket connections.

    Provides methods to accept new connections, remove disconnected ones,
    and broadcast messages to all currently connected WebSocket clients.
    """

    def __init__(self):
        """
        Initialize the ConnectionManager with an empty list of active connections.
        """
        self.active_connections: List[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        """
        Accept a new WebSocket connection and add it to the list of active connections.

        Args:
            websocket (WebSocket): The WebSocket instance representing the client.
        """
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        """
        Remove a WebSocket connection from the list of active connections.

        Args:
            websocket (WebSocket): The WebSocket instance to remove.
        """
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)

    async def broadcast(self, message: str):
        """
        Send a text message to all currently connected WebSocket clients.

        Args:
            message (str): The message to broadcast.
        """
        for connection in self.active_connections:
            await connection.send_text(message)
