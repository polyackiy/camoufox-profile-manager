"""
WebSocket роуты для мониторинга в реальном времени
"""

import json
import asyncio
from datetime import datetime
from typing import Dict, Set
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from loguru import logger

from camoufox_pm.api.models.system import WebSocketMessage
from camoufox_pm.api.dependencies import get_profile_manager


router = APIRouter()

# Активные WebSocket соединения
active_connections: Set[WebSocket] = set()

# Словарь для хранения подписок на события
subscriptions: Dict[WebSocket, Set[str]] = {}


class ConnectionManager:
    """Менеджер WebSocket соединений"""
    
    def __init__(self):
        self.active_connections: Set[WebSocket] = set()
        self.subscriptions: Dict[WebSocket, Set[str]] = {}
    
    async def connect(self, websocket: WebSocket):
        """Подключить новый WebSocket"""
        await websocket.accept()
        self.active_connections.add(websocket)
        self.subscriptions[websocket] = set()
        logger.info(f"🔌 WebSocket подключен. Активных соединений: {len(self.active_connections)}")
    
    def disconnect(self, websocket: WebSocket):
        """Отключить WebSocket"""
        self.active_connections.discard(websocket)
        self.subscriptions.pop(websocket, None)
        logger.info(f"🔌 WebSocket отключен. Активных соединений: {len(self.active_connections)}")
    
    async def send_personal_message(self, message: str, websocket: WebSocket):
        """Отправить сообщение конкретному соединению"""
        try:
            await websocket.send_text(message)
        except Exception as e:
            logger.error(f"Ошибка отправки личного сообщения: {e}")
            self.disconnect(websocket)
    
    async def broadcast(self, message: WebSocketMessage, event_type: str = None):
        """Отправить сообщение всем подключенным клиентам"""
        if not self.active_connections:
            return
            
        message_json = json.dumps({
            "type": message.type,
            "timestamp": message.timestamp.isoformat(),
            "data": message.data
        })
        
        # Отправляем только тем, кто подписан на этот тип событий
        disconnected = set()
        for websocket in self.active_connections:
            try:
                # Если клиент подписан на конкретные события, проверяем подписку
                if event_type and self.subscriptions.get(websocket):
                    if event_type not in self.subscriptions[websocket]:
                        continue
                
                await websocket.send_text(message_json)
            except Exception as e:
                logger.error(f"Ошибка отправки сообщения: {e}")
                disconnected.add(websocket)
        
        # Удаляем отключенные соединения
        for websocket in disconnected:
            self.disconnect(websocket)


# Глобальный менеджер соединений
manager = ConnectionManager()


@router.websocket("/monitor")
async def websocket_monitor(websocket: WebSocket):
    """WebSocket для мониторинга системы в реальном времени"""
    await manager.connect(websocket)
    
    try:
        # Отправляем приветственное сообщение
        welcome_message = WebSocketMessage(
            type="connected",
            timestamp=datetime.now(),
            data={
                "message": "Подключение к мониторингу установлено",
                "available_events": [
                    "profile_created",
                    "profile_updated", 
                    "profile_deleted",
                    "profile_launched",
                    "browser_closed",
                    "system_status"
                ]
            }
        )
        
        await manager.send_personal_message(
            json.dumps({
                "type": welcome_message.type,
                "timestamp": welcome_message.timestamp.isoformat(),
                "data": welcome_message.data
            }),
            websocket
        )
        
        # Слушаем сообщения от клиента
        while True:
            try:
                data = await websocket.receive_text()
                message_data = json.loads(data)
                
                # Обрабатываем команды от клиента
                if message_data.get("type") == "subscribe":
                    # Подписка на события
                    events = message_data.get("events", [])
                    manager.subscriptions[websocket].update(events)
                    
                    response = WebSocketMessage(
                        type="subscription_updated",
                        timestamp=datetime.now(),
                        data={
                            "subscribed_to": list(manager.subscriptions[websocket])
                        }
                    )
                    
                    await manager.send_personal_message(
                        json.dumps({
                            "type": response.type,
                            "timestamp": response.timestamp.isoformat(),
                            "data": response.data
                        }),
                        websocket
                    )
                
                elif message_data.get("type") == "ping":
                    # Ping-pong для проверки соединения
                    response = WebSocketMessage(
                        type="pong",
                        timestamp=datetime.now(),
                        data={"message": "pong"}
                    )
                    
                    await manager.send_personal_message(
                        json.dumps({
                            "type": response.type,
                            "timestamp": response.timestamp.isoformat(),
                            "data": response.data
                        }),
                        websocket
                    )
                    
            except WebSocketDisconnect:
                break
            except json.JSONDecodeError:
                # Неверный JSON
                error_message = WebSocketMessage(
                    type="error",
                    timestamp=datetime.now(),
                    data={"error": "Invalid JSON format"}
                )
                
                await manager.send_personal_message(
                    json.dumps({
                        "type": error_message.type,
                        "timestamp": error_message.timestamp.isoformat(),
                        "data": error_message.data
                    }),
                    websocket
                )
            except Exception as e:
                logger.error(f"Ошибка в WebSocket: {e}")
                break
                
    except WebSocketDisconnect:
        pass
    finally:
        manager.disconnect(websocket)


async def broadcast_event(event_type: str, data: dict):
    """Отправить событие всем подключенным клиентам"""
    if not manager.active_connections:
        return
    
    message = WebSocketMessage(
        type=event_type,
        timestamp=datetime.now(),
        data=data
    )
    
    await manager.broadcast(message, event_type)


# Функции для отправки конкретных событий

async def notify_profile_created(profile_id: str, profile_name: str):
    """Уведомить о создании профиля"""
    await broadcast_event("profile_created", {
        "profile_id": profile_id,
        "name": profile_name,
        "message": f"Создан новый профиль: {profile_name}"
    })


async def notify_profile_updated(profile_id: str, profile_name: str, changes: dict):
    """Уведомить об обновлении профиля"""
    await broadcast_event("profile_updated", {
        "profile_id": profile_id,
        "name": profile_name,
        "changes": changes,
        "message": f"Обновлен профиль: {profile_name}"
    })


async def notify_profile_deleted(profile_id: str):
    """Уведомить об удалении профиля"""
    await broadcast_event("profile_deleted", {
        "profile_id": profile_id,
        "message": f"Удален профиль: {profile_id}"
    })


async def notify_browser_launched(profile_id: str, profile_name: str):
    """Уведомить о запуске браузера"""
    await broadcast_event("profile_launched", {
        "profile_id": profile_id,
        "name": profile_name,
        "message": f"Запущен браузер для профиля: {profile_name}"
    })


async def notify_system_status(status_data: dict):
    """Уведомить о статусе системы"""
    await broadcast_event("system_status", status_data) 