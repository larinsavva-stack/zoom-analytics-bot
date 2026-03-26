# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Что это

Бот-аналитика для Zoom встреч через Recall.ai API. Собирает чат, события участников (join/leave), записи аудио/видео. Два интерфейса: HTTP API (FastAPI) и интерактивный терминал (CLI).

## Команды

```bash
# Установка зависимостей
pip install -r requirements.txt

# Запуск HTTP API сервера (порт 8000, hot reload)
python main.py
# или: uvicorn main:app --reload --port 8000

# Запуск интерактивного CLI
python bot.py
```

Swagger UI доступен на http://localhost:8000/docs

## Архитектура

Два независимых entry point работают с общим storage и recall_client:

```
main.py ──────┐
              ├──→ recall_client.py ──→ Recall.ai API (httpx, sync)
bot.py  ──────┤
              └──→ storage.py ──→ SQLite (analytics.db)

config.py ── переменные окружения (.env)
```

**main.py** — FastAPI сервер. REST API для управления ботами: отправка на встречу, синхронизация данных, экспорт в JSON/CSV. Инициализирует БД при старте.

**bot.py** — CLI с интерактивным меню (ANSI-цвета, пронумерованные пункты). Имеет собственную логику парсинга данных Recall.ai (`_fetch_and_save`), отличную от `sync_from_recall` в storage.py. Фоновый поток `_watch_and_save` автоматически сохраняет данные после завершения встречи. При запуске `_auto_sync_pending` досинхронизирует пропущенные встречи.

**recall_client.py** — синхронный HTTP-клиент к Recall.ai API (httpx). Все методы создают новый `httpx.Client` на каждый запрос. Чат и участники приходят через `download_url` из объекта recordings, а не через прямые эндпоинты.

**storage.py** — SQLite через context manager `get_db()`. Три таблицы: `meetings`, `participant_events`, `chat_messages`. Дедупликация через SELECT перед INSERT (не через UNIQUE constraint). Два пути записи данных: `save_chat_message`/`save_participant_event` (из bot.py) и `sync_from_recall` (из main.py) — парсят разные форматы Recall.ai ответов.

## Особенности

- Формат данных Recall.ai неоднороден: timestamp может быть строкой или dict с полем `absolute`, participant может быть вложенным объектом или плоским. Оба парсера (bot.py и storage.py) обрабатывают это по-разному.
- `backups/` — JSON-бэкапы сырых данных Recall.ai, создаются только из bot.py.
- Все timestamps в БД хранятся в UTC (ISO 8601). bot.py конвертирует в МСК (UTC+3) только для отображения.
- Тесты и линтер не настроены.
