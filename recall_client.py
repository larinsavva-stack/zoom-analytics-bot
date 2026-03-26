"""
Клиент для работы с Recall.ai API.
Документация: https://docs.recall.ai
"""

import httpx
from typing import Optional
from config import RECALL_API_KEY, RECALL_BASE_URL, BOT_NAME


def _headers() -> dict:
    return {
        "Authorization": f"Token {RECALL_API_KEY}",
        "Content-Type": "application/json",
    }


def send_bot(meeting_url: str, bot_name: Optional[str] = None, webhook_url: Optional[str] = None) -> dict:
    """
    Отправить бота на Zoom встречу.

    Args:
        meeting_url: URL встречи, например https://zoom.us/j/12345?pwd=abc
        bot_name: Имя бота в списке участников (по умолчанию из config)
        webhook_url: URL для real-time webhook (если задан — включает чат-ответы)

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

    if webhook_url:
        payload["recording_config"]["realtime_endpoints"] = [
            {
                "type": "webhook",
                "url": webhook_url,
                "events": ["participant_events.chat_message"],
            }
        ]

    with httpx.Client(timeout=30) as client:
        response = client.post(
            f"{RECALL_BASE_URL}/bot",
            json=payload,
            headers=_headers(),
        )
        response.raise_for_status()
        return response.json()


def get_bot_status(bot_id: str) -> dict:
    """Получить текущий статус бота."""
    with httpx.Client(timeout=15) as client:
        response = client.get(
            f"{RECALL_BASE_URL}/bot/{bot_id}",
            headers=_headers(),
        )
        response.raise_for_status()
        return response.json()


def stop_bot(bot_id: str) -> dict:
    """Остановить бота и покинуть встречу."""
    with httpx.Client(timeout=15) as client:
        response = client.post(
            f"{RECALL_BASE_URL}/bot/{bot_id}/leave_call",
            headers=_headers(),
        )
        response.raise_for_status()
        return response.json()


def get_transcript(bot_id: str) -> list[dict]:
    """
    Получить транскрипцию встречи.
    Доступна после завершения встречи.

    Returns:
        Список [{speaker, words: [{text, start_timestamp, end_timestamp}]}]
    """
    with httpx.Client(timeout=30) as client:
        response = client.get(
            f"{RECALL_BASE_URL}/bot/{bot_id}/transcript",
            headers=_headers(),
        )
        response.raise_for_status()
        return response.json()


def get_chat_messages(bot_id: str) -> list[dict]:
    """
    Получить сообщения чата встречи через download_url после завершения.

    Returns:
        Список [{created_at, participant_name, text}]
    """
    bot = get_bot_status(bot_id)
    recordings = bot.get("recordings", [])
    if not recordings:
        return []

    # Recall.ai не имеет отдельного /chat эндпоинта —
    # чат приходит через transcript download_url
    transcript_url = (
        recordings[0]
        .get("media_shortcuts", {})
        .get("transcript", {})
        .get("data", {})
        .get("download_url")
    )
    if not transcript_url:
        return []

    with httpx.Client(timeout=30) as client:
        response = client.get(transcript_url)
        response.raise_for_status()
        return response.json() if isinstance(response.json(), list) else []


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

    with httpx.Client(timeout=30) as client:
        response = client.get(url)
        response.raise_for_status()
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


def send_chat_message(bot_id: str, message: str, to: Optional[str] = None) -> dict:
    """Отправить сообщение в чат Zoom встречи через бота."""
    payload = {"message": message}
    if to:
        payload["to"] = to
    with httpx.Client(timeout=15) as client:
        response = client.post(
            f"{RECALL_BASE_URL}/bot/{bot_id}/send_chat_message",
            json=payload,
            headers=_headers(),
        )
        response.raise_for_status()
        return response.json()


def list_bots() -> list[dict]:
    """Получить список всех запущенных ботов."""
    with httpx.Client(timeout=15) as client:
        response = client.get(
            f"{RECALL_BASE_URL}/bot",
            headers=_headers(),
        )
        response.raise_for_status()
        data = response.json()
        return data.get("results", [])
