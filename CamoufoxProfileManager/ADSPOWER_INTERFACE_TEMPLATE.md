# Шаблон интерфейса AdsPower для Camoufox Profile Manager

## Общий дизайн и архитектура

### Цветовая схема
- **Темная тема** (основная): темно-серый/синий фон (#1a1a1a, #2d2d2d)
- **Акцентные цвета**: оранжевый (#ff6b35), синий (#007bff)
- **Текст**: белый/светло-серый (#ffffff, #e0e0e0)
- **Границы**: тонкие серые линии (#404040)

### Типографика
- **Основной шрифт**: современный sans-serif (Inter, Roboto, Open Sans)
- **Размеры**: 12px-14px для обычного текста, 16px-18px для заголовков
- **Контраст**: хороший контраст между текстом и фоном

## Структура интерфейса

### 1. Левая боковая панель (Sidebar)
**Ширина**: ~250px
**Позиция**: фиксированная слева
**Содержимое**:
- **Логотип** (вверху)
- **Навигационное меню**:
  - 📊 Dashboard
  - 👤 Profiles (основная страница)
  - 📁 Groups  
  - 🌐 Proxies
  - 🔧 Extensions
  - 🤖 Automation (RPA)
  - 👥 Team
  - ⚙️ Settings
  - 🗑️ Trash

**Особенности**:
- Иконки слева от названий
- Активная страница подсвечивается
- Счетчики элементов (например, количество профилей)
- Возможность свернуть/развернуть

### 2. Верхняя панель (Top Bar)
**Высота**: ~60px
**Содержимое**:
- **Строка поиска** (по центру)
- **Кнопки действий**:
  - + New Profile (основная кнопка)
  - Bulk Create
  - Import/Export
- **Пользовательское меню** (справа):
  - Уведомления
  - Аватар пользователя
  - Выпадающее меню (Profile, Settings, Logout)

### 3. Основная область контента

#### Страница Profiles (главная)

**Фильтры и управление**:
- **Панель фильтров** (под топ-баром):
  - Group selector (выпадающий список)
  - Status filter (Active, Inactive, Error)
  - Platform filter (Facebook, Google, etc.)
  - Date range
  - Custom filters
  
- **Панель действий**:
  - Select All checkbox
  - Bulk Actions (Start, Stop, Delete, Export)
  - View Options (Table/Grid)
  - Columns customization

**Таблица профилей**:
- **Столбцы** (настраиваемые):
  - ☑️ Checkbox (выбор)
  - 🔢 ID/Custom No.
  - 📝 Name
  - 🌐 Platform
  - 🔗 Proxy
  - 💻 OS
  - 📱 Browser
  - 📊 Status (цветные индикаторы)
  - 📅 Last Opened
  - 👥 Group
  - ⚙️ Actions (кнопки)

**Особенности таблицы**:
- **Сортировка** по всем столбцам
- **Изменяемая ширина** столбцов (drag & drop)
- **Компактный режим** для отображения большего количества строк
- **Цветные индикаторы статуса**:
  - 🟢 Зеленый: Active/Online
  - 🔴 Красный: Error/Blocked
  - ⚪ Серый: Inactive/Offline
  - 🟡 Желтый: Warning/Pending

**Кнопки действий для каждого профиля**:
- ▶️ Start/Open
- ⏸️ Stop/Close
- ✏️ Edit
- 📋 Clone
- 🗑️ Delete
- 📤 Export
- 📊 Details

#### Создание/Редактирование профиля

**Модальное окно или отдельная страница**:
- **Вкладки** (табы):
  1. **General** - основная информация
  2. **Proxy** - настройки прокси
  3. **Platform** - платформа и логин
  4. **Fingerprint** - отпечаток браузера
  5. **Advanced** - дополнительные настройки

**General Tab**:
- Name (текстовое поле)
- Browser (SunBrowser/FlowerBrowser)
- OS (Windows/Mac/Linux/Android/iOS)
- User Agent (выпадающий список)
- Group (выбор группы)
- Tags (чипы)
- Notes (textarea)

**Proxy Tab**:
- Proxy Type (HTTP/HTTPS/SOCKS5)
- Host:Port (IP:port:username:password)
- Saved Proxies (выпадающий список)
- Random assignment (toggle)
- Test Connection (кнопка)

**Fingerprint Tab**:
- **Overview** (справа) - сводка параметров
- **New Fingerprint** (кнопка генерации)
- **Detailed Settings**:
  - Screen Resolution
  - Timezone
  - Language
  - Geolocation
  - Hardware (CPU, RAM)
  - WebGL, Canvas
  - Fonts

## UI/UX паттерны AdsPower

### Кнопки
- **Primary**: оранжевый фон, белый текст
- **Secondary**: прозрачный фон, оранжевая граница
- **Success**: зеленый фон
- **Danger**: красный фон
- **Rounded corners**: 6-8px border-radius

### Формы
- **Input fields**: темный фон, светлая граница
- **Focus state**: оранжевая граница
- **Placeholders**: серый текст
- **Validation**: красные/зеленые индикаторы

### Модальные окна
- **Overlay**: полупрозрачный темный фон
- **Content**: белый/светло-серый фон
- **Close button**: X в правом верхнем углу
- **Actions**: кнопки снизу (Cancel, OK)

### Таблицы
- **Zebra striping**: чередующиеся строки
- **Hover effects**: подсветка при наведении
- **Sorting indicators**: стрелки в заголовках
- **Pagination**: внизу таблицы

### Индикаторы статуса
- **Dots**: цветные точки рядом с текстом
- **Badges**: маленькие цветные метки
- **Progress bars**: для процессов загрузки

## Responsive дизайн
- **Desktop**: полная функциональность
- **Tablet**: боковая панель сворачивается
- **Mobile**: адаптивная таблица, мобильное меню

## Анимации и переходы
- **Smooth transitions**: 0.2-0.3s ease
- **Loading states**: спиннеры, skeleton screens
- **Hover effects**: subtle elevation, color changes
- **Page transitions**: fade in/out

## Функциональные особенности
- **Bulk operations**: множественный выбор и действия
- **Drag & drop**: изменение размеров столбцов
- **Keyboard shortcuts**: быстрые действия
- **Context menus**: правый клик для быстрого доступа
- **Tooltips**: подсказки при наведении
- **Auto-save**: автоматическое сохранение настроек

## Специфичные для антидетект браузера элементы
- **Fingerprint visualizer**: графическое отображение отпечатка
- **Proxy status indicators**: цветные индикаторы соединения
- **Browser engine badges**: Chrome/Firefox иконки
- **Platform icons**: Facebook, Google, TikTok и т.д.
- **Automation status**: индикаторы выполнения скриптов 