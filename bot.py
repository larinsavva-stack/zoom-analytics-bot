"""
Zoom Analytics Bot — простой интерфейс.
Запуск: python3 bot.py
"""

from datetime import datetime, timezone, timedelta
import json
import os
import threading
import time
from typing import Optional
import shutil
import recall_client
import storage
from config import WEBHOOK_BASE_URL, MATERIALS_DIR

storage.init_db()

# ── Таймзона ──────────────────────────────────────────────────
MSK = timezone(timedelta(hours=3))

def to_msk(ts_str: str) -> str:
    if not ts_str:
        return "—"
    try:
        ts_str = ts_str.replace("Z", "+00:00")
        dt = datetime.fromisoformat(ts_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(MSK).strftime("%d.%m.%Y  %H:%M:%S")
    except Exception:
        return ts_str[:19].replace("T", " ")

def to_msk_short(ts_str: str) -> str:
    if not ts_str:
        return "—"
    try:
        ts_str = ts_str.replace("Z", "+00:00")
        dt = datetime.fromisoformat(ts_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(MSK).strftime("%d.%m  %H:%M")
    except Exception:
        return ts_str[:16]

# ── Цвета (ANSI) ──────────────────────────────────────────────
R  = "\033[0m"
B  = "\033[1m"
D  = "\033[2m"
G  = "\033[32m"
Y  = "\033[33m"
C  = "\033[36m"
RE = "\033[31m"
W  = "\033[97m"
M  = "\033[35m"

def c(text, *codes): return "".join(codes) + str(text) + R

# ── UI-примитивы ──────────────────────────────────────────────
W_ = 52

def line(char="─"): print(c(char * W_, D))
def dline():        print(c("═" * W_, C))

def section(title):
    print()
    dline()
    pad = (W_ - len(title) - 2) // 2
    print(c("║", C) + " " * pad + c(title, B, W) + " " * (W_ - pad - len(title) - 1) + c("║", C))
    dline()

def ok(msg):   print(c("  ✓  ", G, B)  + c(msg, W))
def err(msg):  print(c("  ✗  ", RE, B) + c(msg, W))
def info(msg): print(c("  ·  ", D)     + c(msg, D))
def warn(msg): print(c("  ▲  ", Y)     + c(msg, Y))
def ask(prompt): return input(c("  ›  ", C, B) + c(prompt + ": ", W)).strip()

def fmt_ts(ev):
    ts = ev.get("timestamp", {})
    if isinstance(ts, dict):
        return to_msk(ts.get("absolute", ""))
    return to_msk(str(ts))


# ── Ядро: загрузка и сохранение данных встречи ────────────────

def _fetch_and_save(bot_id: str) -> Optional[dict]:
    """
    Загрузить данные из Recall.ai и сохранить в SQLite + JSON-бэкап.
    Возвращает {"participants": [...], "chat": [...]} или None при ошибке.
    """
    try:
        chat         = recall_client.get_chat_messages(bot_id)
        participants = recall_client.get_participant_events(bot_id)
    except Exception:
        return None

    # JSON-бэкап (сырые данные, страховка)
    backup_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "backups")
    os.makedirs(backup_dir, exist_ok=True)
    try:
        with open(os.path.join(backup_dir, f"{bot_id}.json"), "w", encoding="utf-8") as f:
            json.dump(
                {"bot_id": bot_id, "participants": participants, "chat": chat},
                f, ensure_ascii=False, indent=2,
            )
    except Exception:
        pass

    # Сохранить в SQLite
    chat_events = [e for e in participants if e.get("action") == "chat_message"]
    join_leave  = [e for e in participants if e.get("action") in ("join", "leave")]

    for ev in chat_events:
        name   = ev.get("participant", {}).get("name", "Unknown")
        text   = ev.get("data", {}).get("text", "")
        to     = ev.get("data", {}).get("to", "everyone")
        ts_raw = ev.get("timestamp", {})
        if isinstance(ts_raw, dict):
            ts_raw = ts_raw.get("absolute", "")
        if text:
            storage.save_chat_message(bot_id, name, text, str(ts_raw), to != "everyone")

    for ev in join_leave:
        name   = ev.get("participant", {}).get("name", "Unknown")
        pid    = str(ev.get("participant", {}).get("id", ""))
        action = ev.get("action", "join")
        ts_raw = ev.get("timestamp", {})
        if isinstance(ts_raw, dict):
            ts_raw = ts_raw.get("absolute", "")
        storage.save_participant_event(bot_id, name, pid, action, str(ts_raw))

    return {"participants": participants, "chat": chat}


def _watch_and_save(bot_id: str):
    """
    Фоновый поток: ждёт пока Recall.ai обработает встречу (статус done),
    затем автоматически сохраняет все данные.
    Макс. ожидание: 15 минут (30 попыток × 30 сек).
    """
    for _ in range(30):
        time.sleep(30)
        try:
            data    = recall_client.get_bot_status(bot_id)
            changes = data.get("status_changes", [])
            code    = changes[-1]["code"] if changes else ""
            if code == "done":
                result = _fetch_and_save(bot_id)
                if result:
                    participants = result["participants"]
                    chat_events  = [e for e in participants if e.get("action") == "chat_message"]
                    join_leave   = [e for e in participants if e.get("action") in ("join", "leave")]
                    n_chat = len(chat_events)
                    n_part = len({e.get("participant", {}).get("name") for e in join_leave})
                    print(
                        f"\n{c('  ✓  ', G, B)}"
                        f"{c(f'Данные встречи сохранены автоматически!', W)}\n"
                        f"{c(f'     · Участников: {n_part}  · Сообщений в чате: {n_chat}', D)}\n"
                    )
                break
            elif code == "fatal":
                break
        except Exception:
            pass


def _auto_sync_pending():
    """
    При запуске: найти встречи без сохранённых данных и засинхронизировать.
    Страховка на случай если бот был закрыт до автосохранения.
    """
    try:
        meetings = storage.list_meetings()
    except Exception:
        return

    synced = 0
    for m in meetings:
        if storage.get_participant_events(m["bot_id"]):
            continue  # уже сохранено
        try:
            data    = recall_client.get_bot_status(m["bot_id"])
            changes = data.get("status_changes", [])
            code    = changes[-1]["code"] if changes else ""
            if code == "done":
                result = _fetch_and_save(m["bot_id"])
                if result:
                    storage.end_meeting(m["bot_id"])
                    synced += 1
        except Exception:
            pass

    if synced:
        print()
        ok(f"Автосинхронизация: сохранены данные {synced} встреч(и)")


# ── Команды меню ───────────────────────────────────────────────

def build_zoom_url(meeting_input: str, password: str) -> str:
    meeting_input = meeting_input.strip()
    if meeting_input.startswith("http"):
        url = meeting_input
        if password and "pwd=" not in url:
            url += f"?pwd={password}" if "?" not in url else f"&pwd={password}"
        return url
    meeting_id = meeting_input.replace(" ", "").replace("-", "")
    url = f"https://zoom.us/j/{meeting_id}"
    if password:
        url += f"?pwd={password}"
    return url


def send_bot():
    section("ОТПРАВИТЬ БОТА НА ВСТРЕЧУ")
    print()

    meeting_input = ask("Ссылка или Meeting ID")
    if not meeting_input:
        err("Нужно ввести ссылку или ID.")
        return

    password = ask("Пароль встречи (Enter если нет)")
    url      = build_zoom_url(meeting_input, password)
    bot_name = ask("Имя бота (Enter = 'Nechto Zoom Analytics')") or "Nechto Zoom Analytics"

    broadcast_id = None
    broadcasts = storage.list_broadcasts()
    if broadcasts:
        print()
        line()
        print(c(f"  {'№':<4}{'Эфир':<30}Материалов", D))
        line()
        for i, b in enumerate(broadcasts, 1):
            mcount = len(storage.get_materials(b["id"]))
            num = c(f"{i}.", B, C)
            nm = c(f"{b['name'][:28]:<28}", W)
            print(f"  {num}  {nm}  {c(str(mcount), D)}")
        line()
        print()
        bc_choice = ask("Привязать к эфиру? Номер (Enter = нет)")
        if bc_choice.isdigit() and 1 <= int(bc_choice) <= len(broadcasts):
            broadcast_id = broadcasts[int(bc_choice) - 1]["id"]
            ok(f"Бот привязан к эфиру «{broadcasts[int(bc_choice) - 1]['name']}»")

    print()
    info("Подключаюсь к Recall.ai...")
    webhook_url = f"{WEBHOOK_BASE_URL}/webhook/chat" if WEBHOOK_BASE_URL else None
    try:
        result = recall_client.send_bot(url, bot_name, webhook_url=webhook_url)
    except Exception as e:
        err(f"Не удалось отправить бота: {e}")
        return

    bot_id = result["id"]
    storage.create_meeting(bot_id, url, bot_name, broadcast_id=broadcast_id)

    print()
    line()
    ok(f"Бот «{bot_name}» успешно отправлен!")
    print()
    info(f"Bot ID:   {bot_id}")
    info(f"Встреча:  {url[:60]}...")
    line()
    print()
    warn("Бот появится в списке участников через ~15–30 секунд.")
    warn("Если встреча с Waiting Room — хост должен его впустить.")


def check_status():
    section("СТАТУС БОТА")
    bot_id = _pick_bot()
    if not bot_id:
        return

    print()
    info("Запрашиваю статус...")
    try:
        data = recall_client.get_bot_status(bot_id)
    except Exception as e:
        err(f"{e}")
        return

    status_changes = data.get("status_changes", [])
    current        = status_changes[-1] if status_changes else {}
    code           = current.get("code", "unknown")

    status_map = {
        "joining_call":          (Y,  "⏳  Подключается к встрече..."),
        "in_call_not_recording": (G,  "🎙  В встрече  ·  ожидает запись"),
        "in_call_recording":     (G,  "🔴  В встрече  ·  идёт запись"),
        "call_ended":            (D,  "⏹   Встреча завершена, обрабатываю..."),
        "done":                  (G,  "✅  Данные обработаны и сохранены"),
        "fatal":                 (RE, "❌  Ошибка — не удалось подключиться"),
    }
    col, label = status_map.get(code, (D, code))

    print()
    line()
    print(f"  Статус   {c(label, col, B)}")
    print()
    info(f"Bot ID:    {bot_id}")
    meeting_id = data.get("meeting_url", {}).get("meeting_id", "—")
    info(f"Meeting:   {meeting_id}")
    if status_changes:
        ts = to_msk(status_changes[-1].get("created_at", ""))
        info(f"Обновлён:  {ts}  (МСК)")
    line()

    # История статусов
    if len(status_changes) > 1:
        print()
        print(c("  История:", D))
        for s in status_changes:
            ts = to_msk(s.get("created_at", ""))
            print(f"  {c(ts, D)}  {c(s['code'], D)}")


def sync_and_show():
    section("ЧАТ И УЧАСТНИКИ")
    bot_id = _pick_bot()
    if not bot_id:
        return

    print()
    info("Загружаю данные из Recall.ai...")
    result = _fetch_and_save(bot_id)
    if result is None:
        err("Не удалось загрузить данные. Проверь статус бота (пункт 2).")
        return

    participants = result["participants"]
    chat_events  = [e for e in participants if e.get("action") == "chat_message"]
    join_leave   = [e for e in participants if e.get("action") in ("join", "leave")]

    # ── Чат ──────────────────────────────────────────────────
    print()
    line()
    print(c(f"  💬  ЧАТ  ({len(chat_events)} сообщений)", C, B))
    line()
    if chat_events:
        for ev in chat_events:
            name    = ev.get("participant", {}).get("name", "?")
            text    = ev.get("data", {}).get("text", "")
            to      = ev.get("data", {}).get("to", "everyone")
            ts      = fmt_ts(ev)
            private = c("  [приватное]", Y) if to != "everyone" else ""
            print(f"  {c(ts, D)}  {c(name + ':', B, W)}")
            print(f"       {text}{private}")
            print()
    else:
        print()
        info("Сообщений в чате не было")
        print()

    # ── Участники ─────────────────────────────────────────────
    line()
    print(c(f"  УЧАСТНИКИ  ({len(join_leave)} событий)", C, B))
    line()
    if join_leave:
        count = 0
        max_count = 0
        timeline = []
        for ev in join_leave:
            name   = ev.get("participant", {}).get("name", "?")
            action = ev.get("action", "")
            ts     = fmt_ts(ev)
            if action == "join":
                count += 1
                max_count = max(max_count, count)
                tag = c("  +  ВОШЁЛ ", G, B)
            else:
                count = max(0, count - 1)
                tag = c("  −  ВЫШЕЛ ", RE, B)
            print(f"  {c(ts, D)}{tag}{c(name, W, B)}  {c(f'[сейчас: {count}]', D)}")
            raw_ts = ev.get("timestamp", {})
            if isinstance(raw_ts, dict):
                raw_ts = raw_ts.get("absolute", "")
            try:
                raw_ts = str(raw_ts).replace("Z", "+00:00")
                dt = datetime.fromisoformat(raw_ts)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                timeline.append((dt.astimezone(MSK), count))
            except Exception:
                pass

        # ── Аналитика срезов ──────────────────────────────────
        if timeline:
            print()
            line()
            print(c("  АНАЛИТИКА ПО ВРЕМЕНИ", C, B))
            line()

            def count_at(target_dt):
                result = 0
                for ev in join_leave:
                    raw = ev.get("timestamp", {})
                    if isinstance(raw, dict):
                        raw = raw.get("absolute", "")
                    try:
                        raw = str(raw).replace("Z", "+00:00")
                        dt  = datetime.fromisoformat(raw)
                        if dt.tzinfo is None:
                            dt = dt.replace(tzinfo=timezone.utc)
                        dt = dt.astimezone(MSK)
                    except Exception:
                        continue
                    if dt <= target_dt:
                        if ev.get("action") == "join":
                            result += 1
                        else:
                            result = max(0, result - 1)
                return result

            start_dt = min(dt for dt, _ in timeline)
            end_dt   = max(dt for dt, _ in timeline)
            duration = end_dt - start_dt

            print(c("  От старта встречи:", D))
            t    = start_dt
            step = timedelta(minutes=30)
            while t <= end_dt:
                cnt     = count_at(t)
                elapsed = int((t - start_dt).total_seconds() // 60)
                bar     = c("█" * cnt + "·" * max(0, max_count - cnt), G if cnt > 0 else D)
                print(f"  +{elapsed:>3} мин  {t.strftime('%H:%M')}  {bar}  {c(str(cnt), W, B)} чел.")
                t += step
            if (end_dt - start_dt).total_seconds() % 1800 != 0:
                cnt     = count_at(end_dt)
                elapsed = int(duration.total_seconds() // 60)
                bar     = c("█" * cnt + "·" * max(0, max_count - cnt), G if cnt > 0 else D)
                print(f"  +{elapsed:>3} мин  {end_dt.strftime('%H:%M')}  {bar}  {c(str(cnt), W, B)} чел.  {c('← конец', D)}")

            print()
            print(c("  От конца встречи:", D))
            for mins_before in [30, 15, 10, 5]:
                t = end_dt - timedelta(minutes=mins_before)
                if t >= start_dt:
                    cnt = count_at(t)
                    bar = c("█" * cnt + "·" * max(0, max_count - cnt), G if cnt > 0 else D)
                    print(f"  −{mins_before:>3} мин  {t.strftime('%H:%M')}  {bar}  {c(str(cnt), W, B)} чел.")

            print()
            line()
            ok(f"Пик:       {c(str(max_count), C, B)} участников одновременно")
            ok(f"Длительность: {c(str(int(duration.total_seconds()//60)), C, B)} минут")
            line()
    else:
        print()
        info("Нет данных об участниках")
        print()


def stop_bot():
    section("ОСТАНОВИТЬ БОТА")
    bot_id = _pick_bot()
    if not bot_id:
        return

    print()
    warn("Бот покинет встречу. Данные сохранятся автоматически через 1–3 мин.")
    print()
    confirm = ask("Продолжить? (да / нет)")
    if confirm.lower() not in ("да", "y", "yes", "д"):
        info("Отменено.")
        return

    try:
        recall_client.stop_bot(bot_id)
        storage.end_meeting(bot_id)
        print()
        ok("Бот успешно покинул встречу.")
        info("Данные сохраняются в фоне — можешь закрыть бота или продолжить работу.")
        threading.Thread(target=_watch_and_save, args=(bot_id,), daemon=True).start()
    except Exception as e:
        err(f"{e}")


def get_recording():
    section("ЗАПИСЬ ВСТРЕЧИ")
    bot_id = _pick_bot()
    if not bot_id:
        return

    print()
    info("Запрашиваю ссылку на запись...")
    try:
        url = recall_client.get_recording_url(bot_id)
    except Exception as e:
        err(f"{e}")
        return

    print()
    if url:
        line()
        ok("Запись готова!")
        print()
        info("Скопируй ссылку ниже и открой в браузере для скачивания:")
        print()
        print(url)
        print()
        line()
        info("Формат: MP4 (видео + аудио, открывается в любом плеере)")
    else:
        warn("Запись ещё не готова.")
        info("Подожди 2–3 минуты после завершения встречи и попробуй снова.")


def _pick_broadcast() -> Optional[int]:
    broadcasts = storage.list_broadcasts()
    if not broadcasts:
        print()
        warn("Нет созданных эфиров. Сначала создай эфир (пункт 1).")
        return None

    print()
    line()
    print(c(f"  {'№':<4}{'Дата':<14}{'Эфир':<26}Материалов", D))
    line()
    for i, b in enumerate(broadcasts, 1):
        mcount = len(storage.get_materials(b["id"]))
        num = c(f"{i}.", B, C)
        ts = to_msk_short(b["created_at"])
        nm = c(f"{b['name'][:24]:<24}", W)
        print(f"  {num}  {c(f'{ts:<12}', D)}  {nm}  {c(str(mcount), D)}")
    line()
    print()
    choice = ask("Номер эфира")
    if choice.isdigit() and 1 <= int(choice) <= len(broadcasts):
        return broadcasts[int(choice) - 1]["id"]
    return None


def manage_materials():
    section("МАТЕРИАЛЫ ДЛЯ ЭФИРА")

    while True:
        print()
        line()
        print(c("  1", B, C) + c("  →  ", D) + c("Создать новый эфир",                 W))
        print(c("  2", B, C) + c("  →  ", D) + c("Добавить ссылку к эфиру",             W))
        print(c("  3", B, C) + c("  →  ", D) + c("Добавить файл к эфиру (PDF, docx)",   W))
        print(c("  4", B, C) + c("  →  ", D) + c("Посмотреть материалы эфира",          W))
        print(c("  5", B, C) + c("  →  ", D) + c("Удалить материал",                    W))
        print(c("  6", B, C) + c("  →  ", D) + c("Удалить эфир",                        W))
        print(c("  0", B, D) + c("  →  ", D) + c("Назад",                               D))
        line()

        choice = ask("Выбор")

        if choice == "1":
            print()
            name = ask("Название эфира")
            if not name:
                err("Нужно ввести название.")
                continue
            bid = storage.create_broadcast(name)
            print()
            ok(f"Эфир «{name}» создан! (ID: {bid})")

        elif choice == "2":
            bid = _pick_broadcast()
            if not bid:
                continue
            broadcast = storage.get_broadcast(bid)
            print()
            url = ask("URL")
            if not url:
                err("Нужно ввести URL.")
                continue
            title = ask("Название (Enter = URL)") or url[:60]
            content = ask("Описание")
            keywords = ask("Ключевые слова (через запятую)")
            storage.save_material(bid, title, content or "", url=url, keywords=keywords)
            print()
            ok(f"Ссылка добавлена к эфиру «{broadcast['name']}»!")

        elif choice == "3":
            bid = _pick_broadcast()
            if not bid:
                continue
            broadcast = storage.get_broadcast(bid)
            print()
            fpath = ask("Путь к файлу")
            if not fpath or not os.path.isfile(fpath):
                err("Файл не найден.")
                continue

            os.makedirs(MATERIALS_DIR, exist_ok=True)
            filename = os.path.basename(fpath)
            name_part, ext = os.path.splitext(filename)
            safe_filename = f"{name_part}_{int(time.time())}{ext}"
            dest = os.path.join(MATERIALS_DIR, safe_filename)
            shutil.copy2(fpath, dest)
            ok(f"Файл скопирован: {safe_filename}")

            file_url = f"{WEBHOOK_BASE_URL}/files/{safe_filename}" if WEBHOOK_BASE_URL else f"/files/{safe_filename}"
            title = ask(f"Название (Enter = {filename})") or filename
            content = ask("Описание")
            keywords = ask("Ключевые слова (через запятую)")
            storage.save_material(
                bid, title, content or "", url=file_url, file_path=dest,
                keywords=keywords, material_type="file",
            )
            print()
            ok(f"Файл добавлен к эфиру «{broadcast['name']}»!")
            info(f"Ссылка для участников: {file_url}")

        elif choice == "4":
            bid = _pick_broadcast()
            if not bid:
                continue
            broadcast = storage.get_broadcast(bid)
            materials = storage.get_materials(bid)
            print()
            line()
            print(c(f"  Эфир: {broadcast['name']}", C, B))
            line()
            if materials:
                print(c(f"  {'№':<4}{'Тип':<6}{'Название':<28}Ключевые слова", D))
                line()
                for i, m in enumerate(materials, 1):
                    icon = "📄" if m["material_type"] == "file" else "🔗"
                    num = c(f"{i}.", B, C)
                    nm = c(f"{m['title'][:26]:<26}", W)
                    kw = c(m.get("keywords", "") or "", D)
                    print(f"  {num}  {icon}    {nm}  {kw}")
                line()
                print(c(f"  Всего: {len(materials)} материал(ов)", D))
            else:
                print()
                info("Материалов пока нет.")
            print()

        elif choice == "5":
            bid = _pick_broadcast()
            if not bid:
                continue
            materials = storage.get_materials(bid)
            if not materials:
                warn("Нет материалов для удаления.")
                continue
            print()
            line()
            for i, m in enumerate(materials, 1):
                icon = "📄" if m["material_type"] == "file" else "🔗"
                print(f"  {c(f'{i}.', B, C)}  {icon}  {c(m['title'][:40], W)}")
            line()
            print()
            del_choice = ask("Номер материала для удаления")
            if del_choice.isdigit() and 1 <= int(del_choice) <= len(materials):
                mat = materials[int(del_choice) - 1]
                storage.delete_material(mat["id"])
                ok(f"Материал «{mat['title']}» удалён.")
            else:
                warn("Неверный номер.")

        elif choice == "6":
            bid = _pick_broadcast()
            if not bid:
                continue
            broadcast = storage.get_broadcast(bid)
            materials = storage.get_materials(bid)
            print()
            warn(f"Эфир «{broadcast['name']}» и все его материалы ({len(materials)} шт.) будут удалены!")
            confirm = ask("Точно удалить? (да / нет)")
            if confirm.lower() in ("да", "y", "yes", "д"):
                storage.delete_broadcast(bid)
                ok(f"Эфир «{broadcast['name']}» удалён.")
            else:
                info("Отменено.")

        elif choice == "0":
            break
        else:
            warn("Введи цифру от 0 до 6.")


def _pick_bot() -> str:
    meetings = storage.list_meetings()
    if not meetings:
        print()
        warn("Нет сохранённых встреч.")
        return ask("Введи Bot ID вручную")

    print()
    line()
    print(c(f"  {'№':<4}{'Дата и время':<18}{'Имя бота':<22}Статус", D))
    line()
    for i, m in enumerate(meetings, 1):
        status  = c("● активна",   G, B) if m["status"] == "active" else c("○ завершена", D)
        started = to_msk_short(m["started_at"])
        name    = m["bot_name"][:20]
        num     = c(f"{i}.", B, C)
        ts      = c(f"{started:<16}", D)
        nm      = c(f"{name:<20}", W)
        print(f"  {num}  {ts}  {nm}  {status}")
    line()
    print()
    choice = ask("Номер встречи")
    if choice.isdigit() and 1 <= int(choice) <= len(meetings):
        return meetings[int(choice) - 1]["bot_id"]
    return choice


def main():
    print()
    dline()
    print(c("║", C) + c("                                                    ", C) + c("║", C))
    print(c("║", C) + c("           ZOOM  ANALYTICS  BOT  v1.0         ", B, W) + c("  ║", C))
    print(c("║", C) + c("                  Время: МСК (UTC+3)                ", D) + c("║", C))
    dline()

    if not WEBHOOK_BASE_URL:
        print()
        warn("WEBHOOK_BASE_URL не задан — чат-ответы бота отключены.")
        info("Для включения: запусти ngrok http 8000, добавь URL в .env")

    _auto_sync_pending()

    while True:
        print()
        line()
        print(c("  1", B, C) + c("  →  ", D) + c("Отправить бота на встречу",    W))
        print(c("  2", B, C) + c("  →  ", D) + c("Проверить статус бота",        W))
        print(c("  3", B, C) + c("  →  ", D) + c("Посмотреть чат и участников",  W))
        print(c("  4", B, C) + c("  →  ", D) + c("Скачать запись (видео+аудио)", W))
        print(c("  5", B, C) + c("  →  ", D) + c("Остановить бота",              W))
        print(c("  6", B, C) + c("  →  ", D) + c("Материалы для эфира",          W))
        print(c("  0", B, D) + c("  →  ", D) + c("Выход",                        D))
        line()

        choice = ask("Выбор")

        if   choice == "1": send_bot()
        elif choice == "2": check_status()
        elif choice == "3": sync_and_show()
        elif choice == "4": get_recording()
        elif choice == "5": stop_bot()
        elif choice == "6": manage_materials()
        elif choice == "0":
            print()
            ok("До встречи!")
            print()
            break
        else:
            warn("Введи цифру от 0 до 6.")


if __name__ == "__main__":
    main()
