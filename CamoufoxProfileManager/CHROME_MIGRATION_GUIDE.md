# Руководство по миграции профилей Chrome в Camoufox

> **🎯 Цель**: Автоматизированный перенос куков, закладок и настроек из профилей Google Chrome в профили Camoufox

## 📋 Содержание

- [Быстрый старт](#быстрый-старт)
- [Где Chrome хранит данные](#где-chrome-хранит-данные)
- [Конфигурация миграции](#конфигурация-миграции)
- [Способы миграции](#способы-миграции)
- [API для автоматизации](#api-для-автоматизации)
- [Часто задаваемые вопросы](#часто-задаваемые-вопросы)
- [Устранение проблем](#устранение-проблем)

## 🚀 Быстрый старт

### 1. Интерактивный мастер миграции

```bash
cd CamoufoxProfileManager
python chrome_migration_wizard.py
```

Выберите опцию "1" для запуска мастера миграции с пошаговыми инструкциями.

### 2. Обнаружение профилей Chrome

```python
from core.chrome_migration_manager import ChromeMigrationManager
from core.profile_manager import ProfileManager
from core.database import StorageManager

# Инициализация
storage = StorageManager()
await storage.initialize()
profile_manager = ProfileManager(storage)
await profile_manager.initialize()
migration_manager = ChromeMigrationManager(profile_manager)

# Поиск профилей Chrome
chrome_profiles = await migration_manager.discover_chrome_profiles()
print(f"Найдено {len(chrome_profiles)} профилей Chrome")
```

### 3. Простая миграция одного профиля

```python
# Выбираем первый найденный профиль
if chrome_profiles:
    first_profile = chrome_profiles[0]
    
    # Создаем маппинг для миграции
    mapping = {
        "create_new_profile": True,
        "new_profile_name": f"Chrome - {first_profile['display_name']}",
        "new_profile_group": "chrome_imports",
        "migration_settings": {
            "include_cookies": True,
            "include_bookmarks": True,
            "include_history": False
        }
    }
    
    # Выполняем миграцию
    result = await migration_manager.migrate_profile(first_profile, mapping)
    
    if result["success"]:
        print(f"✅ Миграция успешна! Создан профиль: {result['camoufox_profile_name']}")
    else:
        print(f"❌ Ошибки миграции: {result['errors']}")
```

## 🗂️ Где Chrome хранит данные

### Windows
```
%LOCALAPPDATA%\Google\Chrome\User Data\
├── Default\                    # Основной профиль
├── Profile 1\                  # Дополнительный профиль 1
├── Profile 2\                  # Дополнительный профиль 2
└── ...
```

### macOS
```
~/Library/Application Support/Google/Chrome/
├── Default/
├── Profile 1/
└── ...
```

### Linux
```
~/.config/google-chrome/
├── Default/
├── Profile 1/
└── ...
```

### Структура профиля Chrome
```
Profile Name/
├── Network/
│   └── Cookies              # База данных куков (SQLite)
├── Bookmarks               # Закладки (JSON)
├── History                 # История (SQLite)
├── Preferences            # Настройки профиля (JSON)
└── ...
```

## ⚙️ Конфигурация миграции

### Файл конфигурации: `config/chrome_migration_config.yaml`

```yaml
# Путь к данным Chrome (если отличается от стандартного)
chrome_data_path: null  # автоматическое определение

# Настройки по умолчанию
default_migration_settings:
  include_cookies: true
  include_bookmarks: true
  include_history: false

# Маппинг профилей
profile_mapping:
  # Создание нового профиля
  - chrome_profile: "Default"
    create_new_profile: true
    new_profile_name: "Chrome - Основной"
    new_profile_group: "chrome_imports"
    migration_settings:
      include_cookies: true
      include_bookmarks: true
      include_history: false
  
  # Перенос в существующий профиль
  - chrome_profile: "Profile 1"
    camoufox_profile_id: "xcj2cs4r"
    migration_settings:
      include_cookies: true
      include_bookmarks: false
      include_history: false
```

### Генерация шаблона конфигурации

```python
# Автоматически создает шаблон на основе найденных профилей
template_path = await migration_manager.generate_mapping_template()
print(f"Шаблон создан: {template_path}")
```

## 🔄 Способы миграции

### 1. Интерактивный мастер миграции (рекомендуется для начинающих)

```bash
python chrome_migration_wizard.py
```

### 2. Программная миграция одного профиля

```python
# Поиск профиля по имени
chrome_profile = None
for profile in chrome_profiles:
    if profile['display_name'] == 'Работа':
        chrome_profile = profile
        break

if chrome_profile:
    result = await migration_manager.migrate_profile(chrome_profile)
```

### 3. Массовая миграция всех профилей

```python
# Сухой прогон (показывает что будет сделано)
dry_run_result = await migration_manager.migrate_all_profiles(dry_run=True)
print(f"Будет мигрировано: {dry_run_result['chrome_profiles_found']} профилей")

# Реальная миграция
migration_result = await migration_manager.migrate_all_profiles()
print(f"Успешно: {migration_result['profiles_migrated']}, "
      f"Ошибки: {migration_result['profiles_failed']}")
```

### 4. Миграция с кастомным путем к Chrome

```python
# Если Chrome установлен в нестандартном месте
custom_path = "/custom/path/to/chrome/User Data"
chrome_profiles = await migration_manager.discover_chrome_profiles(custom_path)
```

## 🔌 API для автоматизации

### Обнаружение профилей Chrome
```http
POST /api/chrome-migration/discover-profiles
Content-Type: application/json

{
  "chrome_data_path": "/custom/path/to/chrome"  # optional
}
```

### Миграция одного профиля
```http
POST /api/chrome-migration/migrate-profile
Content-Type: application/json

{
  "chrome_profile_name": "Default",
  "new_profile_name": "Chrome - Основной",
  "new_profile_group": "chrome_imports",
  "include_cookies": true,
  "include_bookmarks": true,
  "include_history": false
}
```

### Массовая миграция
```http
POST /api/chrome-migration/bulk-migrate
Content-Type: application/json

{
  "chrome_data_path": null,
  "dry_run": false,
  "custom_mapping": [
    {
      "chrome_profile": "Default",
      "create_new_profile": true,
      "new_profile_name": "Chrome - Основной",
      "new_profile_group": "chrome_imports",
      "migration_settings": {
        "include_cookies": true,
        "include_bookmarks": true,
        "include_history": false
      }
    }
  ]
}
```

### Статус миграции
```http
GET /api/chrome-migration/status
```

### Получение путей Chrome
```http
GET /api/chrome-migration/chrome-data-paths
```

## 📊 Работа с Excel для массовой миграции

### 1. Создание Excel шаблона

```python
# Генерирует Excel файл с примером маппинга
template_path = await migration_manager.generate_mapping_template(
    output_path="my_chrome_migration.xlsx"
)
```

### 2. Структура Excel файла

| Chrome Profile | Chrome Display Name | Action | Camoufox Profile ID | New Profile Name | New Profile Group | Include Cookies | Include Bookmarks | Include History |
|---------------|-------------------|---------|-------------------|----------------|------------------|----------------|------------------|----------------|
| Default | Основной | create_new | | Chrome - Основной | chrome_imports | TRUE | TRUE | FALSE |
| Profile 1 | Работа | create_new | | Chrome - Работа | work_profiles | TRUE | TRUE | FALSE |
| Profile 2 | Личный | use_existing | xcj2cs4r | | | TRUE | FALSE | FALSE |

### 3. Загрузка Excel конфигурации через API

```http
POST /api/chrome-migration/upload-mapping-config
Content-Type: multipart/form-data

file: [Excel файл с маппингом]
```

## ❓ Часто задаваемые вопросы

### Q: Какие данные переносятся?
**A:** 
- ✅ **Куки** - полностью поддерживается
- ✅ **Закладки** - копируются в JSON формате
- ⚠️ **История** - экспортируется в JSON (не интегрируется в Firefox)
- ❌ **Пароли** - не поддерживается из соображений безопасности
- ❌ **Расширения** - не поддерживается (разные архитектуры)

### Q: Можно ли мигрировать в существующий профиль Camoufox?
**A:** Да, укажите `camoufox_profile_id` в маппинге. Данные будут добавлены к существующим.

### Q: Что если Chrome заблокирован/запущен?
**A:** Система создает временные копии файлов для чтения, поэтому Chrome может быть запущен.

### Q: Как часто можно обновлять куки?
**A:** Можно запускать миграцию сколько угодно раз. Куки будут обновляться.

### Q: Поддерживаются ли профили из других браузеров?
**A:** Сейчас только Chrome. Планируется добавить поддержку Edge, Brave, Opera.

## 🔧 Устранение проблем

### Chrome профили не найдены

**Проблема**: `Найдено 0 профилей Chrome`

**Решения**:
1. Проверьте, установлен ли Chrome
2. Убедитесь, что есть профили с данными
3. Укажите кастомный путь к данным Chrome
4. Проверьте права доступа к директории

```python
# Проверка пути к Chrome
from core.chrome_importer import ChromeProfileImporter
importer = ChromeProfileImporter()
print(f"Поиск в: {importer.chrome_data_paths}")
```

### Ошибка чтения куков

**Проблема**: `Ошибка чтения куков из SQLite`

**Решения**:
1. Закройте Chrome перед миграцией
2. Проверьте, не повреждена ли база куков
3. Запустите от имени администратора (Windows)

### Не удается создать профиль Camoufox

**Проблема**: `Не удалось получить или создать целевой профиль`

**Решения**:
1. Проверьте, что ProfileManager инициализирован
2. Убедитесь, что есть права на создание файлов
3. Проверьте доступное место на диске

### Профиль запускается, но куки не работают

**Проблема**: Куки не сохраняются в Camoufox

**Решения**:
1. Проверьте, что cookies.sqlite создан в профиле
2. Убедитесь, что структура базы корректная
3. Проверьте логи на ошибки преобразования куков

## 📈 Мониторинг и логи

### Включение подробного логирования

```python
from loguru import logger

# Включить отладочные логи
logger.add("chrome_migration.log", level="DEBUG")
```

### Проверка результатов миграции

```python
# Статус миграции
status = await migration_manager.get_migration_status()
print(f"Мигрированных профилей: {status['migrated_profiles']}")

# Детали мигрированных профилей
for profile in status['migrated_profile_details']:
    print(f"- {profile['name']} (ID: {profile['id']})")
```

## 🔮 Будущие возможности

- [ ] Поддержка других браузеров (Edge, Brave, Opera)
- [ ] Миграция расширений
- [ ] Автоматическая синхронизация куков
- [ ] Веб-интерфейс для управления миграцией
- [ ] Резервное копирование перед миграцией
- [ ] Фильтрация куков по доменам
- [ ] Экспорт результатов в различные форматы

## 📞 Поддержка

Если возникли вопросы или проблемы:

1. Проверьте логи в файле `chrome_migration.log`
2. Запустите интерактивный мастер: `python chrome_migration_wizard.py`
3. Создайте Issue в GitHub репозитории
4. Приложите логи и описание проблемы

---

**Автор**: Camoufox Profile Manager Team  
**Версия**: 1.0  
**Дата**: Январь 2025 