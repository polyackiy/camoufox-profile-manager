"""
Middleware для логирования HTTP запросов
"""

import time
from typing import Callable
from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from loguru import logger


class LoggingMiddleware(BaseHTTPMiddleware):
    """Middleware для логирования запросов к API"""
    
    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        # Начало обработки запроса
        start_time = time.time()
        
        # Логируем входящий запрос
        logger.info(
            f"🌐 {request.method} {request.url.path} "
            f"from {request.client.host if request.client else 'unknown'}"
        )
        
        # Обрабатываем запрос
        try:
            response = await call_next(request)
            
            # Вычисляем время обработки
            process_time = time.time() - start_time
            
            # Логируем ответ
            status_emoji = "✅" if response.status_code < 400 else "❌"
            logger.info(
                f"{status_emoji} {request.method} {request.url.path} "
                f"→ {response.status_code} ({process_time:.3f}s)"
            )
            
            # Добавляем заголовок с временем обработки
            response.headers["X-Process-Time"] = str(process_time)
            
            return response
            
        except Exception as e:
            # Логируем ошибку
            process_time = time.time() - start_time
            logger.error(
                f"💥 {request.method} {request.url.path} "
                f"ERROR: {str(e)} ({process_time:.3f}s)"
            )
            raise 