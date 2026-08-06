"""WebSocket routes for real-time monitoring."""

import json
from datetime import datetime

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from loguru import logger

from camoufox_pm.api.models.system import WebSocketMessage

router = APIRouter()


def _encode(message: WebSocketMessage) -> str:
    """Serialize a WebSocket message to JSON."""
    return json.dumps(
        {
            "type": message.type,
            "timestamp": message.timestamp.isoformat(),
            "data": message.data,
        }
    )


class ConnectionManager:
    """Track WebSocket connections and their event subscriptions."""

    def __init__(self) -> None:
        self.active_connections: set[WebSocket] = set()
        self.subscriptions: dict[WebSocket, set[str]] = {}

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        self.active_connections.add(websocket)
        self.subscriptions[websocket] = set()
        logger.info("WebSocket connected (%d active)" % len(self.active_connections))

    def disconnect(self, websocket: WebSocket) -> None:
        self.active_connections.discard(websocket)
        self.subscriptions.pop(websocket, None)
        logger.info("WebSocket disconnected (%d active)" % len(self.active_connections))

    async def send_personal_message(self, message: WebSocketMessage, websocket: WebSocket) -> None:
        try:
            await websocket.send_text(_encode(message))
        except Exception as exc:  # noqa: BLE001
            logger.error("Failed to send personal message: %s" % exc)
            self.disconnect(websocket)

    async def broadcast(self, message: WebSocketMessage, event_type: str | None = None) -> None:
        disconnected: set[WebSocket] = set()
        for websocket in self.active_connections:
            subscribed = self.subscriptions.get(websocket)
            if event_type and subscribed and event_type not in subscribed:
                continue
            try:
                await websocket.send_text(_encode(message))
            except Exception as exc:  # noqa: BLE001
                logger.error("Failed to broadcast message: %s" % exc)
                disconnected.add(websocket)
        for websocket in disconnected:
            self.disconnect(websocket)


manager = ConnectionManager()


@router.websocket("/monitor")
async def websocket_monitor(websocket: WebSocket):
    """Real-time system monitoring channel."""
    await manager.connect(websocket)
    try:
        await manager.send_personal_message(
            WebSocketMessage(
                type="connected",
                timestamp=datetime.now(),
                data={
                    "message": "Monitoring connection established",
                    "available_events": [
                        "profile_created",
                        "profile_updated",
                        "profile_deleted",
                        "profile_launched",
                        "browser_closed",
                        "system_status",
                    ],
                },
            ),
            websocket,
        )

        while True:
            try:
                message_data = json.loads(await websocket.receive_text())
            except WebSocketDisconnect:
                break
            except json.JSONDecodeError:
                await manager.send_personal_message(
                    WebSocketMessage(
                        type="error",
                        timestamp=datetime.now(),
                        data={"error": "Invalid JSON format"},
                    ),
                    websocket,
                )
                continue

            kind = message_data.get("type")
            if kind == "subscribe":
                manager.subscriptions[websocket].update(message_data.get("events", []))
                await manager.send_personal_message(
                    WebSocketMessage(
                        type="subscription_updated",
                        timestamp=datetime.now(),
                        data={"subscribed_to": list(manager.subscriptions[websocket])},
                    ),
                    websocket,
                )
            elif kind == "ping":
                await manager.send_personal_message(
                    WebSocketMessage(type="pong", timestamp=datetime.now(), data={"message": "pong"}),
                    websocket,
                )
    except WebSocketDisconnect:
        pass
    finally:
        manager.disconnect(websocket)


async def broadcast_event(event_type: str, data: dict) -> None:
    """Broadcast an event to all subscribed clients."""
    if not manager.active_connections:
        return
    await manager.broadcast(
        WebSocketMessage(type=event_type, timestamp=datetime.now(), data=data), event_type
    )


async def notify_profile_created(profile_id: str, profile_name: str) -> None:
    await broadcast_event(
        "profile_created",
        {"profile_id": profile_id, "name": profile_name, "message": f"Profile created: {profile_name}"},
    )


async def notify_profile_updated(profile_id: str, profile_name: str, changes: dict) -> None:
    await broadcast_event(
        "profile_updated",
        {
            "profile_id": profile_id,
            "name": profile_name,
            "changes": changes,
            "message": f"Profile updated: {profile_name}",
        },
    )


async def notify_profile_deleted(profile_id: str) -> None:
    await broadcast_event(
        "profile_deleted", {"profile_id": profile_id, "message": f"Profile deleted: {profile_id}"}
    )


async def notify_browser_launched(profile_id: str, profile_name: str) -> None:
    await broadcast_event(
        "profile_launched",
        {"profile_id": profile_id, "name": profile_name, "message": f"Browser launched for: {profile_name}"},
    )


async def notify_system_status(status_data: dict) -> None:
    await broadcast_event("system_status", status_data)
