"""Connection registry and per-WebSocket send synchronization."""

import asyncio
from typing import List

from fastapi import WebSocket


class ConnectionManager:
    """Accept, track, and safely broadcast to active WebSocket clients."""

    def __init__(self):
        self.active_connections: List[WebSocket] = []

    async def connect(self, websocket: WebSocket) -> None:
        """Accept a socket and install the lock shared by all task writers."""

        await websocket.accept()
        websocket.state.send_lock = asyncio.Lock()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket) -> None:
        """Remove a socket from the active connection registry."""

        if websocket in self.active_connections:
            self.active_connections.remove(websocket)

    async def broadcast(self, message: str) -> None:
        """Send text to all clients while serializing writes per connection."""

        for connection in tuple(self.active_connections):
            send_lock = getattr(connection.state, "send_lock", None)
            if send_lock is None:
                send_lock = asyncio.Lock()
                connection.state.send_lock = send_lock
            async with send_lock:
                await connection.send_text(message)
