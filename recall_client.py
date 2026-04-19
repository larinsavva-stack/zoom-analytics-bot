"""
Клиент для работы с Recall.ai API.
Документация: https://docs.recall.ai
"""

import time
import httpx
from typing import Optional
from config import RECALL_API_KEY, RECALL_BASE_URL, BOT_NAME

TIMEOUT = 30
MAX_RETRIES = 3
BACKOFF_SECONDS = [2, 4, 8]
RETRYABLE_STATUS_CODES = {502, 503, 504, 429}


class RecallAPIError(Exception):
    def __init__(self, message: str, status_code: Optional[int] = None):
        self.status_code = status_code
        super().__init__(message)


def _headers() -> dict:
    return {
        "Authorization": f"Token {RECALL_API_KEY}",
        "Content-Type": "application/json",
    }


def _api_request(method: str, url: str, **kwargs) -> httpx.Response:
    kwargs.setdefault("headers", _headers())
    timeout = kwargs.pop("timeout", TIMEOUT)

    last_exception = None
    for attempt in range(MAX_RETRIES):
        try:
            with httpx.Client(timeout=timeout) as client:
                response = getattr(client, method)(url, **kwargs)
            if response.status_code in RETRYABLE_STATUS_CODES and attempt < MAX_RETRIES - 1:
                time.sleep(BACKOFF_SECONDS[attempt])
                continue
            response.raise_for_status()
            return response
        except httpx.TimeoutException as e:
            last_exception = e
            if attempt < MAX_RETRIES - 1:
                time.sleep(BACKOFF_SECONDS[attempt])
                continue
        except httpx.HTTPStatusError as e:
            raise RecallAPIError(
                f"Recall API error {e.response.status_code}: {method.upper()} {url}",
                status_code=e.response.status_code,
            ) from e
        except httpx.HTTPError as e:
            last_exception = e
            if attempt < MAX_RETRIES - 1:
                time.sleep(BACKOFF_SECONDS[attempt])
                continue

    raise RecallAPIError(
        f"Recall API недоступен после {MAX_RETRIES} попыток: {method.upper()} {url}"
    ) from last_exception


def send_bot(meeting_url: str, bot_name: Optional[str] = None) -> dict:
    """
    Отправить бота на Zoom встречу.

    Args:
        meeting_url: URL встречи, например https://zoom.us/j/12345?pwd=abc
        bot_name: Имя бота в списке участников (по умолчанию из config)

    Returns:
        dict с bot_id и статусом
    """
    payload = {
        "meeting_url": meeting_url,
        "bot_name": bot_name or BOT_NAME,
        "recording_config": {
            "transcript": {
                "provider": {
                    "meeting_captions": {}
                }
            }
        },
        "chat": {}
    }

    response = _api_request("post", f"{RECALL_BASE_URL}/bot", json=payload)
    return response.json()


def get_bot_status(bot_id: str) -> dict:
    """Получить текущий статус бота."""
    response = _api_request("get", f"{RECALL_BASE_URL}/bot/{bot_id}")
    return response.json()


def stop_bot(bot_id: str) -> dict:
    """Остановить бота и покинуть встречу."""
    response = _api_request("post", f"{RECALL_BASE_URL}/bot/{bot_id}/leave_call")
    return response.json()


def get_transcript(bot_id: str) -> list[dict]:
    """
    Получить транскрипцию встречи.
    Доступна после завершения встречи.

    Returns:
        Список [{speaker, words: [{text, start_timestamp, end_timestamp}]}]
    """
    response = _api_request("get", f"{RECALL_BASE_URL}/bot/{bot_id}/transcript")
    return response.json()


def get_chat_messages(bot_id: str) -> list[dict]:
    """
    Получить сообщения чата встречи.
    Чат приходит внутри participant_events как action="chat_message".

    Returns:
        Список [{sender_name, message, sent_at, is_private}]
    """
    all_events = get_participant_events(bot_id)
    messages = []
    for ev in all_events:
        if ev.get("action") != "chat_message":
            continue
        participant = ev.get("participant", {})
        ts = ev.get("timestamp", {})
        sent_at = ts.get("absolute") if isinstance(ts, dict) else str(ts)
        data = ev.get("data", {}) or {}
        to = data.get("to", "everyone")
        messages.append({
            "sender_name": participant.get("name", "Unknown"),
            "message": data.get("text", ""),
            "sent_at": sent_at or "",
            "is_private": to not in (None, "everyone", ""),
        })
    return messages


def get_participant_events(bot_id: str) -> list[dict]:
    """
    Получить события входа/выхода участников через download_url.

    Returns:
        Список событий участников
    """
    bot = get_bot_status(bot_id)
    recordings = bot.get("recordings", [])
    if not recordings:
        return []

    url = (
        recordings[0]
        .get("media_shortcuts", {})
        .get("participant_events", {})
        .get("data", {})
        .get("participant_events_download_url")
    )
    if not url:
        return []

    response = _api_request("get", url)
    data = response.json()
    return data if isinstance(data, list) else data.get("results", [])


def get_recording_url(bot_id: str) -> Optional[str]:
    """
    Получить URL видеозаписи встречи (MP4, содержит аудио).
    Доступен после завершения обработки встречи.

    Returns:
        URL файла или None если запись ещё не готова
    """
    bot_data = get_bot_status(bot_id)
    recordings = bot_data.get("recordings", [])
    if not recordings:
        return None
    shortcuts = recordings[0].get("media_shortcuts", {})
    return (
        shortcuts.get("video_mixed", {}).get("data", {}).get("download_url")
    )


def list_bots() -> list[dict]:
    """Получить список всех запущенных ботов."""
    response = _api_request("get", f"{RECALL_BASE_URL}/bot")
    data = response.json()
    return data.get("results", [])
