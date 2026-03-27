"""
Zoom Analytics Bot — FastAPI сервер.

Эндпоинты:
  POST /send-bot              — отправить бота на встречу
  GET  /bots                  — список всех сессий
  GET  /status/{bot_id}       — статус бота на recall.ai
  POST /stop/{bot_id}         — остановить бота
  POST /sync/{bot_id}         — синхронизировать данные из recall.ai
  GET  /chat/{bot_id}         — сообщения чата
  GET  /participants/{bot_id} — события участников
  GET  /timeline/{bot_id}     — хронология количества участников
  GET  /recording/{bot_id}    — ссылка на запись аудио
  GET  /export/{bot_id}       — экспорт всех данных в JSON

Запуск:
  uvicorn main:app --reload --port 8000
"""

import csv
import io
import json
import os
import shutil
import time
from fastapi import FastAPI, HTTPException, UploadFile, File, Form, Depends, Security
from fastapi.responses import StreamingResponse, JSONResponse, FileResponse
from fastapi.security import APIKeyHeader
from pydantic import BaseModel
from typing import Optional

import recall_client
import storage
from config import SERVER_PORT, MATERIALS_DIR

API_KEY = os.environ.get("API_KEY", "")
_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


def _check_api_key(key: str = Security(_api_key_header)):
    if not API_KEY:
        return
    if key != API_KEY:
        raise HTTPException(status_code=403, detail="Invalid API key")

app = FastAPI(
    title="Zoom Analytics Bot",
    description="Бот-аналитика для Zoom встреч через Recall.ai",
    version="1.0.0",
)

# Инициализировать БД и директории при старте
storage.init_db()
os.makedirs(MATERIALS_DIR, exist_ok=True)


# --- Схемы запросов ---

class SendBotRequest(BaseModel):
    meeting_url: str
    bot_name: Optional[str] = None
    broadcast_id: Optional[int] = None


class BroadcastRequest(BaseModel):
    name: str


class MaterialRequest(BaseModel):
    title: str
    content: str
    url: Optional[str] = None
    keywords: Optional[str] = None


# --- Эндпоинты ---

@app.post("/send-bot", summary="Отправить бота на встречу", dependencies=[Depends(_check_api_key)])
def send_bot(req: SendBotRequest):
    """
    Отправляет бота на Zoom встречу.

    Пример meeting_url: https://zoom.us/j/12345678901?pwd=abc123
    """
    try:
        result = recall_client.send_bot(req.meeting_url, req.bot_name)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

    bot_id = result["id"]
    bot_name = req.bot_name or result.get("bot_name", "Analytics Bot")
    storage.create_meeting(bot_id, req.meeting_url, bot_name, broadcast_id=req.broadcast_id)

    return {
        "bot_id": bot_id,
        "status": result.get("status_changes", [{}])[-1].get("code", "joining"),
        "message": f"Бот '{bot_name}' отправлен на встречу. Bot ID: {bot_id}",
    }


@app.get("/bots", summary="Список всех сессий", dependencies=[Depends(_check_api_key)])
def list_bots():
    """Возвращает список всех встреч из локальной БД."""
    return storage.list_meetings()


@app.get("/status/{bot_id}", summary="Статус бота", dependencies=[Depends(_check_api_key)])
def get_status(bot_id: str):
    """Текущий статус бота из Recall.ai (joining / in_call / done и т.д.)."""
    try:
        data = recall_client.get_bot_status(bot_id)
    except Exception as e:
        raise HTTPException(status_code=404, detail=str(e))

    status_changes = data.get("status_changes", [])
    current_status = status_changes[-1].get("code") if status_changes else "unknown"

    return {
        "bot_id": bot_id,
        "status": current_status,
        "bot_name": data.get("bot_name"),
        "meeting_url": data.get("meeting_url"),
        "status_history": status_changes,
    }


@app.post("/stop/{bot_id}", summary="Остановить бота", dependencies=[Depends(_check_api_key)])
def stop_bot(bot_id: str):
    """Заставить бота покинуть встречу."""
    try:
        recall_client.stop_bot(bot_id)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

    storage.end_meeting(bot_id)
    return {"message": f"Бот {bot_id} покинул встречу."}


@app.post("/sync/{bot_id}", summary="Синхронизировать данные из Recall.ai", dependencies=[Depends(_check_api_key)])
def sync_data(bot_id: str):
    """
    Подтягивает чат и события участников из Recall.ai в локальную БД.
    Вызывать после завершения встречи или для обновления данных.
    """
    try:
        chat = recall_client.get_chat_messages(bot_id)
        participants = recall_client.get_participant_events(bot_id)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

    storage.sync_from_recall(bot_id, chat, participants)

    return {
        "synced_chat_messages": len(chat),
        "synced_participant_events": len(participants),
    }


@app.get("/chat/{bot_id}", summary="Сообщения чата", dependencies=[Depends(_check_api_key)])
def get_chat(bot_id: str):
    """Возвращает все сообщения чата встречи из локальной БД."""
    messages = storage.get_chat_messages(bot_id)
    return {
        "bot_id": bot_id,
        "total_messages": len(messages),
        "messages": messages,
    }


@app.get("/participants/{bot_id}", summary="События участников", dependencies=[Depends(_check_api_key)])
def get_participants(bot_id: str):
    """Все события входа/выхода участников с временными метками."""
    events = storage.get_participant_events(bot_id)
    current_count = storage.get_current_participant_count(bot_id)
    return {
        "bot_id": bot_id,
        "current_count": current_count,
        "total_events": len(events),
        "events": events,
    }


@app.get("/timeline/{bot_id}", summary="Хронология участников", dependencies=[Depends(_check_api_key)])
def get_timeline(bot_id: str):
    """
    Хронология изменения количества участников.
    Удобно для построения графика.
    """
    timeline = storage.get_participant_timeline(bot_id)
    return {
        "bot_id": bot_id,
        "timeline": timeline,
    }


@app.get("/recording/{bot_id}", summary="Ссылка на аудио запись", dependencies=[Depends(_check_api_key)])
def get_recording(bot_id: str):
    """Получить URL аудио записи встречи (доступен после обработки)."""
    try:
        url = recall_client.get_recording_url(bot_id)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

    if not url:
        return {"message": "Запись ещё не готова. Попробуйте позже.", "url": None}
    return {"url": url}


@app.get("/export/{bot_id}", summary="Экспорт всех данных", dependencies=[Depends(_check_api_key)])
def export_data(bot_id: str, format: str = "json"):
    """
    Экспорт всех данных встречи.

    ?format=json  — JSON (по умолчанию)
    ?format=csv   — CSV для чата
    """
    meeting = storage.get_meeting(bot_id)
    if not meeting:
        raise HTTPException(status_code=404, detail="Встреча не найдена")

    chat = storage.get_chat_messages(bot_id)
    participants = storage.get_participant_events(bot_id)
    timeline = storage.get_participant_timeline(bot_id)

    if format == "csv":
        output = io.StringIO()
        writer = csv.DictWriter(output, fieldnames=["sent_at", "sender_name", "message", "is_private"])
        writer.writeheader()
        writer.writerows(chat)
        output.seek(0)
        return StreamingResponse(
            iter([output.getvalue()]),
            media_type="text/csv",
            headers={"Content-Disposition": f"attachment; filename=chat_{bot_id}.csv"},
        )

    return {
        "meeting": meeting,
        "chat_messages": chat,
        "participant_events": participants,
        "participant_timeline": timeline,
    }


# --- Эфиры (broadcasts) ---

@app.post("/broadcasts", summary="Создать эфир", dependencies=[Depends(_check_api_key)])
def create_broadcast(req: BroadcastRequest):
    broadcast_id = storage.create_broadcast(req.name)
    return {"id": broadcast_id, "name": req.name, "message": f"Эфир '{req.name}' создан."}


@app.get("/broadcasts", summary="Список эфиров", dependencies=[Depends(_check_api_key)])
def list_broadcasts():
    broadcasts = storage.list_broadcasts()
    for b in broadcasts:
        b["materials_count"] = len(storage.get_materials(b["id"]))
    return {"total": len(broadcasts), "broadcasts": broadcasts}


@app.delete("/broadcasts/{broadcast_id}", summary="Удалить эфир", dependencies=[Depends(_check_api_key)])
def delete_broadcast(broadcast_id: int):
    if not storage.get_broadcast(broadcast_id):
        raise HTTPException(status_code=404, detail="Эфир не найден")
    storage.delete_broadcast(broadcast_id)
    return {"message": f"Эфир {broadcast_id} и все его материалы удалены."}


# --- Материалы ---

@app.post("/materials/{broadcast_id}", summary="Добавить ссылку к эфиру", dependencies=[Depends(_check_api_key)])
def add_material(broadcast_id: int, req: MaterialRequest):
    if not storage.get_broadcast(broadcast_id):
        raise HTTPException(status_code=404, detail="Эфир не найден")
    material_id = storage.save_material(
        broadcast_id, req.title, req.content, url=req.url, keywords=req.keywords,
    )
    return {"id": material_id, "message": f"Материал '{req.title}' добавлен."}


@app.post("/materials/{broadcast_id}/upload", summary="Загрузить файл к эфиру", dependencies=[Depends(_check_api_key)])
async def upload_material(
    broadcast_id: int,
    file: UploadFile = File(...),
    title: str = Form(""),
    content: str = Form(""),
    keywords: str = Form(""),
):
    if not storage.get_broadcast(broadcast_id):
        raise HTTPException(status_code=404, detail="Эфир не найден")

    os.makedirs(MATERIALS_DIR, exist_ok=True)

    filename = file.filename or "file"
    name, ext = os.path.splitext(filename)
    safe_filename = f"{name}_{int(time.time())}{ext}"
    file_path = os.path.join(MATERIALS_DIR, safe_filename)

    with open(file_path, "wb") as f:
        shutil.copyfileobj(file.file, f)

    file_url = f"{WEBHOOK_BASE_URL}/files/{safe_filename}" if WEBHOOK_BASE_URL else f"/files/{safe_filename}"
    material_id = storage.save_material(
        broadcast_id,
        title or filename,
        content,
        url=file_url,
        file_path=file_path,
        keywords=keywords,
        material_type="file",
    )
    return {"id": material_id, "filename": safe_filename, "url": file_url}


@app.get("/materials/{broadcast_id}", summary="Список материалов эфира", dependencies=[Depends(_check_api_key)])
def list_materials(broadcast_id: int):
    if not storage.get_broadcast(broadcast_id):
        raise HTTPException(status_code=404, detail="Эфир не найден")
    materials = storage.get_materials(broadcast_id)
    return {"broadcast_id": broadcast_id, "total": len(materials), "materials": materials}


@app.delete("/materials/{broadcast_id}/{material_id}", summary="Удалить материал", dependencies=[Depends(_check_api_key)])
def delete_material(broadcast_id: int, material_id: int):
    if not storage.get_broadcast(broadcast_id):
        raise HTTPException(status_code=404, detail="Эфир не найден")
    storage.delete_material(material_id)
    return {"message": f"Материал {material_id} удалён."}


@app.get("/files/{filename}", summary="Скачать файл материала")
def serve_file(filename: str):
    file_path = os.path.join(MATERIALS_DIR, filename)
    if not os.path.isfile(file_path):
        raise HTTPException(status_code=404, detail="Файл не найден")
    return FileResponse(file_path, filename=filename)


@app.get("/", summary="Информация о сервере")
def root():
    return {
        "name": "Zoom Analytics Bot",
        "version": "1.0.0",
        "endpoints": [
            "POST /send-bot",
            "GET  /bots",
            "GET  /status/{bot_id}",
            "POST /stop/{bot_id}",
            "POST /sync/{bot_id}",
            "GET  /chat/{bot_id}",
            "GET  /participants/{bot_id}",
            "GET  /timeline/{bot_id}",
            "GET  /recording/{bot_id}",
            "GET  /export/{bot_id}?format=json|csv",
            "POST /broadcasts",
            "GET  /broadcasts",
            "DELETE /broadcasts/{broadcast_id}",
            "POST /materials/{broadcast_id}",
            "POST /materials/{broadcast_id}/upload",
            "GET  /materials/{broadcast_id}",
            "DELETE /materials/{broadcast_id}/{material_id}",
            "GET  /files/{filename}",
        ],
        "docs": "/docs",
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=SERVER_PORT, reload=True)
