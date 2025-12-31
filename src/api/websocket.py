"""
WebSocket connection manager for real-time job updates.
"""

import asyncio
import json
import logging
from collections import defaultdict
from dataclasses import dataclass, field
from uuid import UUID

from fastapi import WebSocket, WebSocketDisconnect

from src.types.events import JobEvent, WebSocketMessage

logger = logging.getLogger(__name__)


@dataclass
class ConnectionInfo:
    """Information about a WebSocket connection."""

    websocket: WebSocket
    tenant_id: str
    subscribed_jobs: set[UUID] = field(default_factory=set)


class WebSocketManager:
    """
    Manager for WebSocket connections.

    Handles connection lifecycle and message broadcasting
    for real-time job status updates.
    """

    def __init__(self):
        """Initialize the WebSocket manager."""
        # Connections by tenant
        self._connections: dict[str, list[ConnectionInfo]] = defaultdict(list)
        # Lock for thread-safe operations
        self._lock = asyncio.Lock()

    async def connect(
        self,
        websocket: WebSocket,
        tenant_id: str,
    ) -> ConnectionInfo:
        """
        Accept a new WebSocket connection.

        Args:
            websocket: The WebSocket connection.
            tenant_id: The tenant identifier.

        Returns:
            ConnectionInfo for the new connection.
        """
        await websocket.accept()

        connection = ConnectionInfo(
            websocket=websocket,
            tenant_id=tenant_id,
        )

        async with self._lock:
            self._connections[tenant_id].append(connection)

        logger.info(
            "WebSocket connected",
            extra={"tenant_id": tenant_id}
        )

        return connection

    async def disconnect(self, connection: ConnectionInfo) -> None:
        """
        Handle WebSocket disconnection.

        Args:
            connection: The connection to remove.
        """
        async with self._lock:
            tenant_connections = self._connections[connection.tenant_id]
            if connection in tenant_connections:
                tenant_connections.remove(connection)

        logger.info(
            "WebSocket disconnected",
            extra={"tenant_id": connection.tenant_id}
        )

    async def subscribe_to_job(
        self,
        connection: ConnectionInfo,
        job_id: UUID,
    ) -> None:
        """
        Subscribe a connection to job updates.

        Args:
            connection: The WebSocket connection.
            job_id: The job to subscribe to.
        """
        connection.subscribed_jobs.add(job_id)

    async def unsubscribe_from_job(
        self,
        connection: ConnectionInfo,
        job_id: UUID,
    ) -> None:
        """
        Unsubscribe a connection from job updates.

        Args:
            connection: The WebSocket connection.
            job_id: The job to unsubscribe from.
        """
        connection.subscribed_jobs.discard(job_id)

    async def broadcast_to_tenant(
        self,
        tenant_id: str,
        message: WebSocketMessage,
    ) -> None:
        """
        Broadcast a message to all connections for a tenant.

        Args:
            tenant_id: The tenant identifier.
            message: The message to broadcast.
        """
        async with self._lock:
            connections = self._connections.get(tenant_id, []).copy()

        if not connections:
            return

        message_json = message.model_dump_json()

        # Send to all connections, handling failures
        disconnected = []
        for connection in connections:
            try:
                await connection.websocket.send_text(message_json)
            except Exception as e:
                logger.warning(
                    f"Failed to send WebSocket message: {e}",
                    extra={"tenant_id": tenant_id}
                )
                disconnected.append(connection)

        # Clean up disconnected connections
        for connection in disconnected:
            await self.disconnect(connection)

    async def broadcast_job_event(self, event: JobEvent) -> None:
        """
        Broadcast a job event to relevant connections.

        Args:
            event: The job event to broadcast.
        """
        message = WebSocketMessage.from_event(event)
        await self.broadcast_to_tenant(event.tenant_id, message)

    async def send_to_connection(
        self,
        connection: ConnectionInfo,
        message: WebSocketMessage,
    ) -> bool:
        """
        Send a message to a specific connection.

        Args:
            connection: The target connection.
            message: The message to send.

        Returns:
            True if sent successfully, False otherwise.
        """
        try:
            await connection.websocket.send_text(message.model_dump_json())
            return True
        except Exception as e:
            logger.warning(
                f"Failed to send WebSocket message: {e}"
            )
            return False

    def get_connection_count(self, tenant_id: str | None = None) -> int:
        """
        Get the number of active connections.

        Args:
            tenant_id: Optional tenant filter.

        Returns:
            Number of active connections.
        """
        if tenant_id is not None:
            return len(self._connections.get(tenant_id, []))
        return sum(len(conns) for conns in self._connections.values())


# Global WebSocket manager instance
_ws_manager: WebSocketManager | None = None


def get_ws_manager() -> WebSocketManager:
    """Get or create the WebSocket manager instance."""
    global _ws_manager
    if _ws_manager is None:
        _ws_manager = WebSocketManager()
    return _ws_manager


async def websocket_handler(
    websocket: WebSocket,
    tenant_id: str,
) -> None:
    """
    Handle a WebSocket connection for job updates.

    Args:
        websocket: The WebSocket connection.
        tenant_id: The tenant identifier.
    """
    manager = get_ws_manager()
    connection = await manager.connect(websocket, tenant_id)

    try:
        while True:
            # Receive and process messages from client
            data = await websocket.receive_text()

            try:
                message = json.loads(data)
                action = message.get("action")

                if action == "subscribe":
                    job_id = UUID(message.get("job_id"))
                    await manager.subscribe_to_job(connection, job_id)
                    await websocket.send_json({
                        "type": "subscribed",
                        "job_id": str(job_id),
                    })

                elif action == "unsubscribe":
                    job_id = UUID(message.get("job_id"))
                    await manager.unsubscribe_from_job(connection, job_id)
                    await websocket.send_json({
                        "type": "unsubscribed",
                        "job_id": str(job_id),
                    })

                elif action == "ping":
                    await websocket.send_json({"type": "pong"})

            except (json.JSONDecodeError, ValueError, KeyError) as e:
                await websocket.send_json({
                    "type": "error",
                    "message": f"Invalid message: {e}",
                })

    except WebSocketDisconnect:
        await manager.disconnect(connection)
