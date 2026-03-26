"""
SQLite хранилище для данных встреч: чат, участники, сессии.
"""

import sqlite3
import json
from datetime import datetime
from contextlib import contextmanager
from typing import Optional
from config import DB_PATH


@contextmanager
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    """Создать таблицы если не существуют."""
    with get_db() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS meetings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                bot_id TEXT UNIQUE NOT NULL,
                meeting_url TEXT NOT NULL,
                bot_name TEXT NOT NULL,
                started_at TEXT NOT NULL,
                ended_at TEXT,
                status TEXT DEFAULT 'active'
            );

            CREATE TABLE IF NOT EXISTS participant_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                bot_id TEXT NOT NULL,
                participant_name TEXT,
                participant_id TEXT,
                event TEXT NOT NULL,        -- 'joined' или 'left'
                timestamp TEXT NOT NULL,
                participant_count INTEGER    -- текущее количество после события
            );

            CREATE TABLE IF NOT EXISTS chat_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                bot_id TEXT NOT NULL,
                sender_name TEXT,
                message TEXT NOT NULL,
                sent_at TEXT NOT NULL,
                is_private INTEGER DEFAULT 0
            );

            CREATE INDEX IF NOT EXISTS idx_participant_events_bot
                ON participant_events(bot_id);
            CREATE INDEX IF NOT EXISTS idx_chat_messages_bot
                ON chat_messages(bot_id);

            CREATE TABLE IF NOT EXISTS broadcasts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS materials (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                broadcast_id INTEGER NOT NULL,
                title TEXT NOT NULL,
                content TEXT NOT NULL,
                url TEXT,
                file_path TEXT,
                keywords TEXT,
                material_type TEXT DEFAULT 'link',
                created_at TEXT NOT NULL,
                FOREIGN KEY (broadcast_id) REFERENCES broadcasts(id)
            );
            CREATE INDEX IF NOT EXISTS idx_materials_broadcast
                ON materials(broadcast_id);
        """)

        # Миграция: добавить broadcast_id в meetings если нет
        cols = [row[1] for row in conn.execute("PRAGMA table_info(meetings)").fetchall()]
        if "broadcast_id" not in cols:
            conn.execute("ALTER TABLE meetings ADD COLUMN broadcast_id INTEGER")


# --- Meetings ---

def create_meeting(bot_id: str, meeting_url: str, bot_name: str, broadcast_id: Optional[int] = None) -> int:
    with get_db() as conn:
        cur = conn.execute(
            "INSERT INTO meetings (bot_id, meeting_url, bot_name, started_at, broadcast_id) VALUES (?, ?, ?, ?, ?)",
            (bot_id, meeting_url, bot_name, datetime.utcnow().isoformat(), broadcast_id),
        )
        return cur.lastrowid


def end_meeting(bot_id: str):
    with get_db() as conn:
        conn.execute(
            "UPDATE meetings SET ended_at = ?, status = 'ended' WHERE bot_id = ?",
            (datetime.utcnow().isoformat(), bot_id),
        )


def get_meeting(bot_id: str) -> Optional[dict]:
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM meetings WHERE bot_id = ?", (bot_id,)
        ).fetchone()
        return dict(row) if row else None


def list_meetings() -> list[dict]:
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM meetings ORDER BY started_at DESC"
        ).fetchall()
        return [dict(r) for r in rows]


# --- Participant events ---

def save_participant_event(
    bot_id: str,
    participant_name: str,
    participant_id: str,
    event: str,
    timestamp: str,
):
    """Сохранить событие join/leave. Пропускает дубликаты по (bot_id, participant_name, event, timestamp)."""
    with get_db() as conn:
        exists = conn.execute(
            "SELECT 1 FROM participant_events WHERE bot_id = ? AND participant_name = ? AND event = ? AND timestamp = ?",
            (bot_id, participant_name, event, timestamp),
        ).fetchone()
        if exists:
            return

        joined = conn.execute(
            "SELECT COUNT(*) FROM participant_events WHERE bot_id = ? AND event = 'joined'",
            (bot_id,)
        ).fetchone()[0]
        left = conn.execute(
            "SELECT COUNT(*) FROM participant_events WHERE bot_id = ? AND event = 'left'",
            (bot_id,)
        ).fetchone()[0]
        current_count = joined - left + (1 if event == "joined" else -1)
        current_count = max(0, current_count)

        conn.execute(
            """INSERT INTO participant_events
               (bot_id, participant_name, participant_id, event, timestamp, participant_count)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (bot_id, participant_name, participant_id, event, timestamp, current_count),
        )


def get_participant_events(bot_id: str) -> list[dict]:
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM participant_events WHERE bot_id = ? ORDER BY timestamp",
            (bot_id,)
        ).fetchall()
        return [dict(r) for r in rows]


def get_current_participant_count(bot_id: str) -> int:
    """Получить текущее количество участников."""
    with get_db() as conn:
        row = conn.execute(
            "SELECT participant_count FROM participant_events WHERE bot_id = ? ORDER BY id DESC LIMIT 1",
            (bot_id,)
        ).fetchone()
        return row[0] if row else 0


def get_participant_timeline(bot_id: str) -> list[dict]:
    """Хронология изменения количества участников."""
    with get_db() as conn:
        rows = conn.execute(
            "SELECT timestamp, participant_count, event, participant_name FROM participant_events WHERE bot_id = ? ORDER BY timestamp",
            (bot_id,)
        ).fetchall()
        return [dict(r) for r in rows]


# --- Chat messages ---

def save_chat_message(
    bot_id: str,
    sender_name: str,
    message: str,
    sent_at: str,
    is_private: bool = False,
):
    """Сохранить сообщение чата. Пропускает дубликаты по (bot_id, sender_name, sent_at)."""
    with get_db() as conn:
        exists = conn.execute(
            "SELECT 1 FROM chat_messages WHERE bot_id = ? AND sender_name = ? AND sent_at = ?",
            (bot_id, sender_name, sent_at),
        ).fetchone()
        if exists:
            return
        conn.execute(
            "INSERT INTO chat_messages (bot_id, sender_name, message, sent_at, is_private) VALUES (?, ?, ?, ?, ?)",
            (bot_id, sender_name, message, sent_at, int(is_private)),
        )


def get_chat_messages(bot_id: str) -> list[dict]:
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM chat_messages WHERE bot_id = ? ORDER BY sent_at",
            (bot_id,)
        ).fetchall()
        return [dict(r) for r in rows]


# --- Broadcasts (эфиры) ---

def create_broadcast(name: str) -> int:
    with get_db() as conn:
        cur = conn.execute(
            "INSERT INTO broadcasts (name, created_at) VALUES (?, ?)",
            (name, datetime.utcnow().isoformat()),
        )
        return cur.lastrowid


def list_broadcasts() -> list[dict]:
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM broadcasts ORDER BY created_at DESC"
        ).fetchall()
        return [dict(r) for r in rows]


def get_broadcast(broadcast_id: int) -> Optional[dict]:
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM broadcasts WHERE id = ?", (broadcast_id,)
        ).fetchone()
        return dict(row) if row else None


def delete_broadcast(broadcast_id: int):
    with get_db() as conn:
        conn.execute("DELETE FROM materials WHERE broadcast_id = ?", (broadcast_id,))
        conn.execute("DELETE FROM broadcasts WHERE id = ?", (broadcast_id,))


# --- Materials ---

def save_material(
    broadcast_id: int,
    title: str,
    content: str,
    url: Optional[str] = None,
    file_path: Optional[str] = None,
    keywords: Optional[str] = None,
    material_type: str = "link",
) -> int:
    with get_db() as conn:
        cur = conn.execute(
            """INSERT INTO materials
               (broadcast_id, title, content, url, file_path, keywords, material_type, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (broadcast_id, title, content, url, file_path, keywords, material_type,
             datetime.utcnow().isoformat()),
        )
        return cur.lastrowid


def get_materials(broadcast_id: int) -> list[dict]:
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM materials WHERE broadcast_id = ? ORDER BY created_at",
            (broadcast_id,)
        ).fetchall()
        return [dict(r) for r in rows]


def delete_material(material_id: int):
    with get_db() as conn:
        conn.execute("DELETE FROM materials WHERE id = ?", (material_id,))


def search_materials(broadcast_id: int, query_text: str) -> list[dict]:
    import re
    query_words = set(re.sub(r'[^\w\s]', '', query_text.lower()).split())
    if not query_words:
        return []

    materials = get_materials(broadcast_id)
    scored = []
    for m in materials:
        searchable = f"{m.get('title', '')} {m.get('keywords', '')}".lower()
        score = sum(1 for w in query_words if w in searchable)
        if score > 0:
            scored.append((score, m))

    scored.sort(key=lambda x: x[0], reverse=True)
    return [m for _, m in scored]


def get_broadcast_id_by_bot(bot_id: str) -> Optional[int]:
    with get_db() as conn:
        row = conn.execute(
            "SELECT broadcast_id FROM meetings WHERE bot_id = ?", (bot_id,)
        ).fetchone()
        return row[0] if row and row[0] else None


def sync_from_recall(bot_id: str, chat_data: list, participant_data: list):
    """
    Синхронизировать данные из Recall.ai API в локальную БД.
    Вызывать после завершения встречи или для обновления.
    """
    # Чат
    for msg in chat_data:
        sender = msg.get("participant_name") or msg.get("sender", "Unknown")
        text = msg.get("text", "")
        sent_at = msg.get("created_at") or msg.get("timestamp", "")
        is_private = msg.get("to") not in (None, "everyone", "")
        # Пропустить дубликаты
        with get_db() as conn:
            exists = conn.execute(
                "SELECT 1 FROM chat_messages WHERE bot_id = ? AND sender_name = ? AND sent_at = ?",
                (bot_id, sender, sent_at)
            ).fetchone()
            if not exists and text:
                conn.execute(
                    "INSERT INTO chat_messages (bot_id, sender_name, message, sent_at, is_private) VALUES (?, ?, ?, ?, ?)",
                    (bot_id, sender, text, sent_at, int(is_private)),
                )

    # Участники
    with get_db() as conn:
        existing = conn.execute(
            "SELECT COUNT(*) FROM participant_events WHERE bot_id = ?", (bot_id,)
        ).fetchone()[0]

    if not existing:
        for ev in participant_data:
            name = str(ev.get("participant", {}).get("name") or ev.get("name") or "Unknown")
            pid = str(ev.get("participant", {}).get("id") or ev.get("id") or "")
            event_type = str(ev.get("event") or ev.get("type") or "joined")
            ts = ev.get("timestamp") or ev.get("ts") or ev.get("created_at") or datetime.utcnow().isoformat()
            ts = str(ts)
            save_participant_event(bot_id, name, pid, event_type, ts)
