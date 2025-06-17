"""
Модуль для экспорта и импорта профилей через Excel
"""

import io
from typing import List, Dict, Any, Tuple, Optional
from datetime import datetime
from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font, PatternFill, Alignment
from openpyxl.worksheet.datavalidation import DataValidation
from openpyxl.comments import Comment
from loguru import logger

from .models import Profile, BrowserSettings, ProxyConfig, ProfileStatus, WebRTCMode, ProxyType
from .profile_manager import ProfileManager


class ExcelManager:
    """Менеджер для работы с Excel файлами профилей"""
    
    def __init__(self, profile_manager: ProfileManager):
        self.profile_manager = profile_manager
        
        # Определяем колонки для экспорта
        self.columns = [
            ("id", "ID профиля", "Автоматически генерируемый ID (только для чтения)"),
            ("name", "Название профиля", "Уникальное название профиля"),
            ("group", "Группа", "Название группы профилей"),
            ("status", "Статус", "active или inactive"),
            ("os", "Операционная система", "windows, macos или linux"),
            ("screen", "Разрешение экрана", "Например: 1920x1080"),
            ("window_width", "Ширина окна", "Ширина окна браузера (800-3840)"),
            ("window_height", "Высота окна", "Высота окна браузера (600-2160)"),
            ("languages", "Языки браузера", "Через запятую: en-US, en"),
            ("timezone", "Часовой пояс", "Например: Europe/Moscow"),
            ("locale", "Локализация", "Например: ru_RU"),
            ("webrtc_mode", "Режим WebRTC", "forward, replace, real или none"),
            ("canvas_noise", "Canvas шум", "true или false"),
            ("webgl_noise", "WebGL шум", "true или false"),
            ("audio_noise", "Audio шум", "true или false"),
            ("hardware_concurrency", "Ядра процессора", "Количество ядер (1-32)"),
            ("device_memory", "Память устройства", "Память в GB (1-128)"),
            ("max_touch_points", "Точки касания", "Количество точек касания (0-10)"),
            ("proxy_type", "Тип прокси", "http, https, socks4, socks5 или пусто"),
            ("proxy_server", "Сервер прокси", "host:port или пусто"),
            ("proxy_username", "Логин прокси", "Имя пользователя или пусто"),
            ("proxy_password", "Пароль прокси", "Пароль или пусто"),
            ("geo_mode", "Режим геолокации", "auto или manual"),
            ("geo_latitude", "Широта", "Координата широты (-90 до 90)"),
            ("geo_longitude", "Долгота", "Координата долготы (-180 до 180)"),
            ("geo_accuracy", "Точность геолокации", "Точность в метрах (1-10000)"),
            ("notes", "Заметки", "Дополнительная информация о профиле")
        ]
    
    async def export_profiles_to_excel(self, profiles: Optional[List[Profile]] = None) -> bytes:
        """Экспорт профилей в Excel файл"""
        logger.info("Начинаем экспорт профилей в Excel")
        
        if profiles is None:
            profiles = await self.profile_manager.list_profiles()
        
        # Создаем новую книгу Excel
        wb = Workbook()
        ws = wb.active
        ws.title = "Профили браузеров"
        
        # Настраиваем стили
        header_font = Font(bold=True, color="FFFFFF")
        header_fill = PatternFill(start_color="366092", end_color="366092", fill_type="solid")
        
        # Добавляем заголовки
        for col_idx, (field, header, comment_text) in enumerate(self.columns, 1):
            cell = ws.cell(row=1, column=col_idx, value=header)
            cell.font = header_font
            cell.fill = header_fill
            cell.alignment = Alignment(horizontal="center")
            
            # Добавляем комментарий с пояснением
            comment = Comment(comment_text, "CamoufoxProfileManager")
            cell.comment = comment
        
        # Добавляем данные профилей
        for row_idx, profile in enumerate(profiles, 2):
            self._write_profile_row(ws, row_idx, profile)
        
        # Добавляем валидацию данных
        self._add_data_validation(ws, len(profiles) + 10)  # +10 строк для новых профилей
        
        # Автоподбор ширины колонок
        for column in ws.columns:
            max_length = 0
            column_letter = column[0].column_letter
            for cell in column:
                try:
                    if len(str(cell.value)) > max_length:
                        max_length = len(str(cell.value))
                except:
                    pass
            adjusted_width = min(max_length + 2, 50)
            ws.column_dimensions[column_letter].width = adjusted_width
        
        # Сохраняем в байты
        excel_buffer = io.BytesIO()
        wb.save(excel_buffer)
        excel_buffer.seek(0)
        
        logger.success(f"Экспорт завершен: {len(profiles)} профилей")
        return excel_buffer.getvalue()
    
    def _write_profile_row(self, ws, row_idx: int, profile: Profile):
        """Записывает данные профиля в строку Excel"""
        browser_settings = profile.browser_settings
        proxy = profile.proxy
        
        # Подготавливаем данные
        row_data = {
            "id": profile.id,
            "name": profile.name,
            "group": profile.group or "",
            "status": profile.status.value if hasattr(profile.status, 'value') else str(profile.status),
            "os": browser_settings.os,
            "screen": browser_settings.screen,
            "window_width": browser_settings.window_width or "",
            "window_height": browser_settings.window_height or "",
            "languages": ", ".join(browser_settings.languages),
            "timezone": browser_settings.timezone or "",
            "locale": browser_settings.locale or "",
            "webrtc_mode": browser_settings.webrtc_mode.value if hasattr(browser_settings.webrtc_mode, 'value') else str(browser_settings.webrtc_mode),
            "canvas_noise": str(browser_settings.canvas_noise).lower(),
            "webgl_noise": str(browser_settings.webgl_noise).lower(),
            "audio_noise": str(browser_settings.audio_noise).lower(),
            "hardware_concurrency": browser_settings.hardware_concurrency or "",
            "device_memory": browser_settings.device_memory or "",
            "max_touch_points": browser_settings.max_touch_points,
            "proxy_type": proxy.type.value if proxy and hasattr(proxy.type, 'value') else (str(proxy.type) if proxy else ""),
            "proxy_server": proxy.server if proxy else "",
            "proxy_username": proxy.username if proxy else "",
            "proxy_password": proxy.password if proxy else "",
            "geo_mode": "manual" if browser_settings.geolocation else "auto",
            "geo_latitude": browser_settings.geolocation.get("lat", "") if browser_settings.geolocation else "",
            "geo_longitude": browser_settings.geolocation.get("lon", "") if browser_settings.geolocation else "",
            "geo_accuracy": browser_settings.geolocation.get("accuracy", "") if browser_settings.geolocation else "",
            "notes": profile.notes or ""
        }
        
        # Записываем данные в строку
        for col_idx, (field, _, _) in enumerate(self.columns, 1):
            ws.cell(row=row_idx, column=col_idx, value=row_data.get(field, ""))
    
    def _add_data_validation(self, ws, max_row: int):
        """Добавляет валидацию данных для колонок"""
        # Валидация для статуса
        status_validation = DataValidation(
            type="list",
            formula1='"active,inactive"',
            allow_blank=True
        )
        ws.add_data_validation(status_validation)
        status_validation.add(f"D2:D{max_row}")
        
        # Валидация для ОС
        os_validation = DataValidation(
            type="list", 
            formula1='"windows,macos,linux"',
            allow_blank=True
        )
        ws.add_data_validation(os_validation)
        os_validation.add(f"E2:E{max_row}")
        
        # Валидация для WebRTC режима
        webrtc_validation = DataValidation(
            type="list",
            formula1='"forward,replace,real,none"',
            allow_blank=True
        )
        ws.add_data_validation(webrtc_validation)
        webrtc_validation.add(f"L2:L{max_row}")
        
        # Валидация для boolean полей
        bool_validation = DataValidation(
            type="list",
            formula1='"true,false"',
            allow_blank=True
        )
        ws.add_data_validation(bool_validation)
        bool_validation.add(f"M2:O{max_row}")  # Canvas, WebGL, Audio noise
        
        # Валидация для типа прокси
        proxy_validation = DataValidation(
            type="list",
            formula1='"http,https,socks4,socks5"',
            allow_blank=True
        )
        ws.add_data_validation(proxy_validation)
        proxy_validation.add(f"S2:S{max_row}")
        
        # Валидация для режима геолокации
        geo_validation = DataValidation(
            type="list",
            formula1='"auto,manual"',
            allow_blank=True
        )
        ws.add_data_validation(geo_validation)
        geo_validation.add(f"W2:W{max_row}")
    
    async def import_profiles_from_excel(self, excel_data: bytes) -> Dict[str, Any]:
        """Импорт профилей из Excel файла"""
        logger.info("Начинаем импорт профилей из Excel")
        
        result = {
            "success": True,
            "created_count": 0,
            "updated_count": 0,  # Всегда 0, так как мы не обновляем профили
            "error_count": 0,
            "errors": [],
            "summary": ""
        }
        
        try:
            # Загружаем Excel файл
            excel_buffer = io.BytesIO(excel_data)
            wb = load_workbook(excel_buffer)
            ws = wb.active
            
            # Получаем заголовки
            headers = {}
            for col_idx, (field, _, _) in enumerate(self.columns, 1):
                headers[field] = col_idx
            
            # Обрабатываем строки
            for row_idx in range(2, ws.max_row + 1):
                try:
                    await self._process_excel_row(ws, row_idx, headers, result)
                except Exception as e:
                    error_msg = f"Строка {row_idx}: {str(e)}"
                    result["errors"].append(error_msg)
                    result["error_count"] += 1
                    logger.error(error_msg)
            
            # Формируем итоговый отчет
            result["summary"] = (
                f"Импорт завершен:\n"
                f"✅ Создано профилей: {result['created_count']}\n"
                f"❌ Ошибок: {result['error_count']}\n"
                f"💡 Все профили создаются с новыми автоматически генерируемыми ID"
            )
            
            if result["error_count"] == 0:
                logger.success(f"Импорт успешен: создано {result['created_count']} профилей")
            else:
                logger.warning(f"Импорт с ошибками: {result['error_count']} ошибок")
                result["success"] = False
                
        except Exception as e:
            logger.error(f"Критическая ошибка импорта: {e}")
            result["success"] = False
            result["errors"].append(f"Критическая ошибка: {str(e)}")
            result["summary"] = "Импорт не удался из-за критической ошибки"
        
        return result
    
    async def _process_excel_row(self, ws, row_idx: int, headers: Dict[str, int], result: Dict[str, Any]):
        """Обрабатывает одну строку Excel"""
        # Читаем данные из строки
        row_data = {}
        for field, col_idx in headers.items():
            cell_value = ws.cell(row=row_idx, column=col_idx).value
            row_data[field] = str(cell_value).strip() if cell_value is not None else ""
        
        # Пропускаем пустые строки
        if not any(row_data.values()):
            return
        
        # Валидируем обязательные поля
        if not row_data["name"]:
            raise ValueError("Название профиля обязательно")
        
        # ID игнорируется - всегда создаем новый профиль с автоматически генерируемым ID
        # Это обеспечивает уникальность и последовательность ID
        browser_settings = self._create_browser_settings(row_data)
        proxy_config = self._create_proxy_config(row_data)
        
        await self.profile_manager.create_profile(
            name=row_data["name"],
            group=row_data["group"] or None,
            browser_settings=browser_settings,
            proxy_config=proxy_config,
            generate_fingerprint=False  # Используем данные из Excel
        )
        result["created_count"] += 1
    
    def _prepare_profile_updates(self, row_data: Dict[str, str]) -> Dict[str, Any]:
        """Подготавливает данные для обновления профиля"""
        updates = {}
        
        # Основные поля профиля
        if row_data["name"]:
            updates["name"] = row_data["name"]
        if row_data["group"]:
            updates["group"] = row_data["group"]
        if row_data["status"]:
            updates["status"] = ProfileStatus(row_data["status"])
        if row_data["notes"]:
            updates["notes"] = row_data["notes"]
        
        # Настройки браузера
        browser_updates = {}
        if row_data["os"]:
            browser_updates["os"] = row_data["os"]
        if row_data["screen"]:
            browser_updates["screen"] = row_data["screen"]
        if row_data["window_width"]:
            browser_updates["window_width"] = int(row_data["window_width"])
        if row_data["window_height"]:
            browser_updates["window_height"] = int(row_data["window_height"])
        if row_data["languages"]:
            browser_updates["languages"] = [lang.strip() for lang in row_data["languages"].split(",")]
        if row_data["timezone"]:
            browser_updates["timezone"] = row_data["timezone"]
        if row_data["locale"]:
            browser_updates["locale"] = row_data["locale"]
        if row_data["webrtc_mode"]:
            browser_updates["webrtc_mode"] = WebRTCMode(row_data["webrtc_mode"])
        if row_data["canvas_noise"]:
            browser_updates["canvas_noise"] = row_data["canvas_noise"].lower() == "true"
        if row_data["webgl_noise"]:
            browser_updates["webgl_noise"] = row_data["webgl_noise"].lower() == "true"
        if row_data["audio_noise"]:
            browser_updates["audio_noise"] = row_data["audio_noise"].lower() == "true"
        if row_data["hardware_concurrency"]:
            browser_updates["hardware_concurrency"] = int(row_data["hardware_concurrency"])
        if row_data["device_memory"]:
            browser_updates["device_memory"] = int(row_data["device_memory"])
        if row_data["max_touch_points"]:
            browser_updates["max_touch_points"] = int(row_data["max_touch_points"])
        
        # Геолокация
        if row_data["geo_mode"] == "manual" and row_data["geo_latitude"] and row_data["geo_longitude"]:
            browser_updates["geolocation"] = {
                "lat": float(row_data["geo_latitude"]),
                "lon": float(row_data["geo_longitude"]),
                "accuracy": int(row_data["geo_accuracy"]) if row_data["geo_accuracy"] else 10
            }
        elif row_data["geo_mode"] == "auto":
            browser_updates["geolocation"] = None
        
        if browser_updates:
            updates["browser_settings"] = browser_updates
        
        # Прокси
        if row_data["proxy_type"] and row_data["proxy_server"]:
            updates["proxy_config"] = {
                "type": row_data["proxy_type"],
                "server": row_data["proxy_server"],
                "username": row_data["proxy_username"] or None,
                "password": row_data["proxy_password"] or None
            }
        elif not row_data["proxy_type"]:
            updates["proxy_config"] = None
        
        return updates
    
    def _create_browser_settings(self, row_data: Dict[str, str]) -> BrowserSettings:
        """Создает объект BrowserSettings из данных Excel"""
        # Геолокация
        geolocation = None
        if row_data["geo_mode"] == "manual" and row_data["geo_latitude"] and row_data["geo_longitude"]:
            geolocation = {
                "lat": float(row_data["geo_latitude"]),
                "lon": float(row_data["geo_longitude"]),
                "accuracy": int(row_data["geo_accuracy"]) if row_data["geo_accuracy"] else 10
            }
        
        return BrowserSettings(
            os=row_data["os"] or "windows",
            screen=row_data["screen"] or "1920x1080",
            window_width=int(row_data["window_width"]) if row_data["window_width"] else 1280,
            window_height=int(row_data["window_height"]) if row_data["window_height"] else 720,
            languages=[lang.strip() for lang in row_data["languages"].split(",")] if row_data["languages"] else ["en-US", "en"],
            timezone=row_data["timezone"] or "UTC",
            locale=row_data["locale"] or "en_US",
            webrtc_mode=WebRTCMode(row_data["webrtc_mode"]) if row_data["webrtc_mode"] else WebRTCMode.REPLACE,
            canvas_noise=row_data["canvas_noise"].lower() == "true" if row_data["canvas_noise"] else True,
            webgl_noise=row_data["webgl_noise"].lower() == "true" if row_data["webgl_noise"] else True,
            audio_noise=row_data["audio_noise"].lower() == "true" if row_data["audio_noise"] else True,
            hardware_concurrency=int(row_data["hardware_concurrency"]) if row_data["hardware_concurrency"] else 4,
            device_memory=int(row_data["device_memory"]) if row_data["device_memory"] else 8,
            max_touch_points=int(row_data["max_touch_points"]) if row_data["max_touch_points"] else 0,
            geolocation=geolocation
        )
    
    def _create_proxy_config(self, row_data: Dict[str, str]) -> Optional[Dict[str, Any]]:
        """Создает конфигурацию прокси из данных Excel"""
        if not row_data["proxy_type"] or not row_data["proxy_server"]:
            return None
        
        return {
            "type": row_data["proxy_type"],
            "server": row_data["proxy_server"],
            "username": row_data["proxy_username"] or None,
            "password": row_data["proxy_password"] or None
        }
