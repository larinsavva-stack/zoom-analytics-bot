# Zoom Analytics Bot

Бот-аналитика для Zoom встреч. Присоединяется к встрече как участник и собирает:
- Сообщения чата в реальном времени
- Количество участников (хронология)
- Запись аудио (опционально)

Работает через [Recall.ai](https://recall.ai) — официального партнёра Zoom.

---

## Быстрый старт

### 1. Получить API ключ Recall.ai

Зарегистрироваться на [recall.ai](https://recall.ai) и получить API ключ.

### 2. Установить зависимости

```bash
pip install -r requirements.txt
```

### 3. Настроить ключ

```bash
export RECALL_API_KEY="ваш_api_ключ"
```

Или создать файл `.env` и задать значение там.

### 4. Запустить сервер

```bash
python main.py
```

Сервер запустится на http://localhost:8000

Документация API: http://localhost:8000/docs

---

## Использование

### Отправить бота на встречу

```bash
curl -X POST http://localhost:8000/send-bot \
  -H "Content-Type: application/json" \
  -d '{
    "meeting_url": "https://zoom.us/j/12345678901?pwd=abc123",
    "bot_name": "Analytics Bot"
  }'
```

Ответ:
```json
{
  "bot_id": "abc-123-def",
  "status": "joining",
  "message": "Бот 'Analytics Bot' отправлен на встречу."
}
```

### Проверить статус

```bash
curl http://localhost:8000/status/abc-123-def
```

### Синхронизировать данные после встречи

```bash
curl -X POST http://localhost:8000/sync/abc-123-def
```

### Посмотреть чат

```bash
curl http://localhost:8000/chat/abc-123-def
```

### Хронология участников

```bash
curl http://localhost:8000/timeline/abc-123-def
```

### Получить ссылку на запись

```bash
curl http://localhost:8000/recording/abc-123-def
```

### Экспорт данных в JSON или CSV

```bash
# JSON
curl http://localhost:8000/export/abc-123-def > meeting_data.json

# CSV чат
curl "http://localhost:8000/export/abc-123-def?format=csv" > chat.csv
```

### Остановить бота

```bash
curl -X POST http://localhost:8000/stop/abc-123-def
```

---

## Важные ограничения

| Условие | Статус |
|---------|--------|
| Бот работает как гость | ✅ не нужны права хоста |
| Встречи с E2EE-шифрованием | ❌ бот войти не сможет |
| Waiting Room | ⚠️ хост должен пустить бота вручную |
| Приватные чаты | ⚠️ только если адресованы боту |

---

## Архитектура

```
main.py          — FastAPI сервер, HTTP эндпоинты
recall_client.py — Recall.ai API клиент (отправка бота, получение данных)
storage.py       — SQLite БД (чат, участники, сессии)
config.py        — настройки и переменные окружения
```

---

## Дальнейшее развитие (Фаза 2)

Если нужен полный контроль без зависимости от Recall.ai — можно перейти на
**Zoom Meeting SDK для Linux** (официальный, бесплатный, сложнее в настройке):
- Колбэки `onChatMsgNotification`, `onUserJoin`, `onUserLeave`
- Raw Audio Data для локальной записи
- Срок разработки: ~2-4 недели
