# Camoufox Profile Manager

[![CI](https://github.com/polyackiy/camoufox-profile-manager/actions/workflows/ci.yml/badge.svg)](https://github.com/polyackiy/camoufox-profile-manager/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![Camoufox 0.5.4](https://img.shields.io/badge/camoufox-0.5.4-orange.svg)](https://github.com/daijro/camoufox)

Self-hosted менеджер профилей антидетект-браузера
[Camoufox](https://github.com/daijro/camoufox) с открытым исходным кодом. Создавайте
и организуйте профили браузера с реалистичными, консистентными отпечатками,
запускайте их по требованию и управляйте всем через REST API или веб-интерфейс —
бесплатная альтернатива коммерческим решениям вроде AdsPower, Multilogin или GoLogin.

> **Статус:** ранний релиз (`v0.1.0`). Ядро работает и покрыто тестами; до `1.0`
> возможны шероховатости и изменения API.

## Возможности

- **Профили и группы** — полный CRUD, клонирование, массовые операции, поиск, пагинация.
- **Реалистичные отпечатки** — Camoufox генерирует консистентные отпечатки; менеджер
  задаёт высокоуровневые ограничения (ОС, экран, регион → локаль/таймзона/гео, железо),
  а детали остаются за браузером.
- **Управление браузером** — запуск, мониторинг и закрытие сессий Camoufox по профилю.
- **Прокси** — HTTP/HTTPS/SOCKS; пароли шифруются при хранении.
- **Импорт/экспорт Excel** — массовое управление профилями через `.xlsx`.
- **REST API + веб-UI** — бэкенд на FastAPI с OpenAPI-документацией и фронтенд на Next.js.
- **Опциональная миграция из Chrome** — импорт cookies/истории из ваших собственных
  профилей Chrome (см. [`extras/chrome_migration`](extras/chrome_migration/README.md)).

## Требования

- Python 3.10+ и [uv](https://docs.astral.sh/uv/)
- Node.js 20+ (для веб-интерфейса)

## Быстрый старт

### Бэкенд (API)

```bash
uv sync
uv run camoufox fetch                       # скачать бинарь браузера (один раз)
uv run python examples/seed_demo.py         # (опц.) демо-профили
uv run python -m camoufox_pm.main           # http://127.0.0.1:8000, docs на /docs
```

### Фронтенд (веб-UI)

```bash
cd web
npm install
npm run dev   # http://localhost:3000
```

Веб-UI проксирует запросы к API, поэтому в разработке не нужно настраивать CORS.

## Конфигурация

Настройки берутся из переменных окружения (префикс `CPM_`). Скопируйте `.env.example`
в `.env` и отредактируйте. Полная таблица переменных — в [README.md](README.md).

## Разработка

См. [CONTRIBUTING.md](CONTRIBUTING.md). Кратко:

```bash
uv sync --extra dev
uv run ruff check src tests
uv run mypy src/camoufox_pm
uv run pytest -m "not browser"
```

## Дисклеймер

Инструмент предназначен только для законного использования — например, тестирования,
приватности и управления несколькими аккаунтами в соответствии с правилами каждого
сервиса. Вы несёте ответственность за то, как его используете.

## Лицензия

[MIT](LICENSE) © Camoufox Profile Manager Contributors.

Основано на [Camoufox](https://github.com/daijro/camoufox) от daijro.

---

English version: [README.md](README.md).
