# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Что это

Бот-аналитика для Zoom встреч через Recall.ai API. Собирает чат, события участников (join/leave), записи аудио/видео. Управление эфирами (broadcasts) и материалами к ним. Два интерфейса: HTTP API (FastAPI) и интерактивный терминал (CLI). Деплоится на Railway.

## Команды

```bash
pip install -r requirements.txt

# HTTP API сервер (Railway или локально)
python main.py

# Интерактивный CLI (только локально)
python bot.py
```

Swagger UI: `/docs` на сервере.

## Архитектура

```
main.py ──────┐
              ├──→ recall_client.py ──→ Recall.ai API (httpx, sync)
bot.py  ──────┤
              └──→ storage.py ──→ SQLite (analytics.db)

config.py ── переменные окружения (.env)
```

**main.py** — FastAPI сервер. REST API: управление ботами, синхронизация данных, экспорт JSON/CSV, CRUD эфиров и материалов, раздача файлов. Защищён API-ключом (env `API_KEY`, header `X-API-Key`). Без `API_KEY` — API открыт.

**bot.py** — CLI с интерактивным меню (ANSI-цвета). Своя логика парсинга данных Recall.ai (`_fetch_and_save`), отличная от `sync_from_recall` в storage.py. Фоновый поток `_watch_and_save` автоматически сохраняет данные. `_auto_sync_pending` при запуске досинхронизирует пропущенные встречи.

**recall_client.py** — синхронный HTTP-клиент (httpx). Каждый метод создаёт новый `httpx.Client`. Чат и участники приходят через `download_url` из recordings, не через прямые эндпоинты.

**storage.py** — SQLite через context manager `get_db()`. Таблицы: `meetings`, `participant_events`, `chat_messages`, `broadcasts`, `materials`. Дедупликация через SELECT перед INSERT. Два пути записи: bot.py и main.py парсят разные форматы Recall.ai.

## Деплой (Railway)

- Сервер: `web-production-a00f0.up.railway.app`
- Volume `/data` для персистентных данных
- Env: `RECALL_API_KEY`, `API_KEY`, `DB_PATH=/data/analytics.db`, `MATERIALS_DIR=/data/materials_files`
- Конфиги: `Procfile`, `railway.toml`, `runtime.txt` (Python 3.11.9)
- Push в main → автодеплой

## Особенности

- Формат данных Recall.ai неоднороден: timestamp может быть строкой или dict с `absolute`, participant может быть вложенным или плоским. Оба парсера обрабатывают это по-разному.
- `backups/` — JSON-бэкапы сырых данных, только из bot.py.
- Timestamps в БД: UTC (ISO 8601). bot.py конвертирует в МСК (UTC+3) для отображения.
- Тесты и линтер не настроены.

## TODO (в beads)

Функция чат-ответов бота участникам Zoom (webhook + send_chat_message) — временно убрана из кода, не доработана. Задачи на доработку в beads (`bd list --status=open`).
