🎭 Fingerprint Injection & Rotation
Navigator Properties (частично используется)
✅ Уже есть: os, user_agent, languages
❌ Отсутствует:
platform - детальная платформа (Win32, MacIntel, Linux x86_64)
hardwareConcurrency - количество логических процессоров
deviceMemory - объем оперативной памяти
vendor, vendorSub - производитель браузера
product, productSub - информация о продукте
buildID - ID сборки браузера
oscpu - детали ОС и процессора
Screen Properties (частично используется)
✅ Уже есть: screen (разрешение)
❌ Отсутствует:
colorDepth - глубина цвета (24, 32 бита)
pixelDepth - глубина пикселя
availWidth/availHeight - доступная область экрана
orientation - ориентация экрана
Window Properties
❌ Полностью отсутствует:
innerWidth/innerHeight - размер окна браузера
outerWidth/outerHeight - размер окна с панелями
screenX/screenY - позиция окна на экране
devicePixelRatio - соотношение пикселей устройства
🌍 Geolocation & Internationalization
Geolocation (частично используется)
✅ Уже есть: lat, lon
❌ Отсутствует:
accuracy - точность геолокации
altitude - высота над уровнем моря
altitudeAccuracy - точность высоты
heading - направление движения
speed - скорость движения
Timezone & Locale (частично используется)
✅ Уже есть: timezone, languages
❌ Отсутствует:
locale - основная локаль
dateTimeFormat - формат даты/времени
numberFormat - формат чисел
currency - валюта
calendar - тип календаря
🎨 Anti-Graphical Fingerprinting
Canvas Fingerprinting
❌ Полностью отсутствует:
canvasNoise - добавление шума в canvas
canvasImageDataNoise - шум в ImageData
canvasTextNoise - шум в текстовом рендеринге
WebGL Properties
❌ Полностью отсутствует:
webglVendor - производитель WebGL
webglRenderer - рендерер WebGL
webglVersion - версия WebGL
shadingLanguageVersion - версия языка шейдеров
maxTextureSize - максимальный размер текстуры
maxVertexAttribs - максимальное количество атрибутов вершин
supportedExtensions - поддерживаемые расширения
Font Spoofing
❌ Полностью отсутствует:
availableFonts - список доступных шрифтов
fontFingerprint - отпечаток шрифтов
fontMetrics - метрики шрифтов
🔊 Audio & Media Fingerprinting
AudioContext
❌ Полностью отсутствует:
sampleRate - частота дискретизации
maxChannelCount - максимальное количество каналов
numberOfInputs/numberOfOutputs - количество входов/выходов
channelCount - количество каналов
channelCountMode - режим подсчета каналов
Speech Synthesis (Voices)
❌ Полностью отсутствует:
availableVoices - доступные голоса
defaultVoice - голос по умолчанию
voiceURI - URI голоса
lang - язык голоса
localService - локальный сервис
🌐 Network & WebRTC
WebRTC IP Spoofing
❌ Полностью отсутствует:
localIP - локальный IP адрес
publicIP - публичный IP адрес
stunServers - STUN серверы
iceServers - ICE серверы
rtcConfiguration - конфигурация RTC
HTTP Headers
❌ Полностью отсутствует:
acceptLanguage - принимаемые языки
acceptEncoding - принимаемые кодировки
acceptCharset - принимаемые наборы символов
dnt - Do Not Track
upgradeInsecureRequests - обновление небезопасных запросов
🖱️ Human Behavior Simulation
Cursor Movement
❌ Полностью отсутствует:
humanCursor - человекоподобное движение курсора
mouseSpeed - скорость движения мыши
mouseAcceleration - ускорение мыши
clickDelay - задержка между кликами
movementPattern - паттерн движения
Typing Behavior
❌ Полностью отсутствует:
typingSpeed - скорость печати
keyDelay - задержка между нажатиями
typingPattern - паттерн печати
typingErrors - имитация ошибок печати
🔧 Browser Configuration
Document Properties
❌ Полностью отсутствует:
documentMode - режим документа
compatMode - режим совместимости
charset - кодировка документа
referrer - реферер
Addons & Extensions
❌ Полностью отсутствует:
installedPlugins - установленные плагины
enabledExtensions - включенные расширения
adBlocker - блокировщик рекламы
flashVersion - версия Flash
Performance & Timing
❌ Полностью отсутствует:
performanceTiming - тайминги производительности
connectionType - тип соединения
effectiveType - эффективный тип соединения
downlink - скорость загрузки
rtt - время отклика
🎯 Quality of Life Features
Anti-Detection
❌ Полностью отсутствует:
headless - режим без GUI
automation - флаги автоматизации
webdriver - флаги WebDriver
chromeObject - объект Chrome
permissions - разрешения браузера
Behavioral Patterns
❌ Полностью отсутствует:
scrollBehavior - поведение прокрутки
focusPattern - паттерн фокуса
idleTime - время простоя
sessionDuration - длительность сессии
📊 Статистика и Мониторинг
Usage Analytics
❌ Полностью отсутствует:
fingerPrintRotationHistory - история ротации отпечатков
detectionEvents - события обнаружения
successRate - процент успешности
averageSessionTime - среднее время сессии