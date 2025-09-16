from flask import Flask, request, jsonify, send_from_directory, Response
import os
import requests
from datetime import datetime, timedelta
import pytz

from yandex_calendar import YaCalClient  # календарь (CalDAV)
# ----- Zoom -----
from zoom_client import ZoomClient, handle_zoom_intents
# ----- Telemost -----
from telemost_client import TelemostClient, handle_telemost_intents

# вычищаем прокси-переменные, чтобы не мешали локальным запросам
for k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy"):
    os.environ.pop(k, None)

# ===== Настройки LLM (через OpenRouter) =====
API_KEY  = os.environ.get("OPENAI_API_KEY", "")
MODEL    = os.environ.get("MODEL", "deepseek/deepseek-chat")
API_URL  = os.environ.get("OPENROUTER_URL", "https://openrouter.ai/api/v1/chat/completions")
APP_URL  = os.environ.get("APP_URL", "http://localhost:8080")
APP_NAME = os.environ.get("APP_NAME", "help-gpt")

app = Flask(__name__, static_folder="static", static_url_path="")

# ===== Инициализация Zoom (мягко) =====
ZOOM_ACCOUNT_ID    = os.getenv("ZOOM_ACCOUNT_ID")
ZOOM_CLIENT_ID     = os.getenv("ZOOM_CLIENT_ID")
ZOOM_CLIENT_SECRET = os.getenv("ZOOM_CLIENT_SECRET")
ZOOM_HOST_EMAIL    = os.getenv("ZOOM_HOST_EMAIL")
ZOOM_TZ            = os.getenv("ZOOM_TZ", "Europe/Moscow")

zoom = None
if all([ZOOM_ACCOUNT_ID, ZOOM_CLIENT_ID, ZOOM_CLIENT_SECRET, ZOOM_HOST_EMAIL]):
    try:
        zoom = ZoomClient(
            account_id=ZOOM_ACCOUNT_ID,
            client_id=ZOOM_CLIENT_ID,
            client_secret=ZOOM_CLIENT_SECRET,
            host_email=ZOOM_HOST_EMAIL,
            tz=ZOOM_TZ,
        )
        print("[Zoom] Client initialized")
    except Exception as e:
        print(f"[Zoom] Failed to init ZoomClient: {e}")
else:
    print("[Zoom] Skipped init (missing creds)")

# ===== Инициализация Яндекс Календаря (мягко через CalDAV) =====
ycal = YaCalClient.from_env(tz=ZOOM_TZ)
if ycal:
    print("[Calendar] CalDAV client initialized")
else:
    print("[Calendar] Skipped init (no YXCAL_USER/PASSWORD)")

# ===== Инициализация Яндекс Телемоста (мягко) =====
telemost = None
YANDEX_OAUTH_TOKEN   = os.getenv("YANDEX_OAUTH_TOKEN")
YANDEX_CLIENT_ID     = os.getenv("YANDEX_CLIENT_ID")
YANDEX_CLIENT_SECRET = os.getenv("YANDEX_CLIENT_SECRET")

try:
    if YANDEX_OAUTH_TOKEN or (YANDEX_CLIENT_ID and YANDEX_CLIENT_SECRET):
        # ВАЖНО: передаём календарь в клиент Телемоста
        telemost = TelemostClient(tz=ZOOM_TZ, calendar=ycal)
        print("[Telemost] Client initialized")
    else:
        print("[Telemost] Skipped init: no YANDEX_OAUTH_TOKEN and no YANDEX_CLIENT_ID/SECRET")
except Exception as e:
    print(f"[Telemost] Failed to init TelemostClient: {e}")

# ===== Роуты =====
@app.get("/")
def index():
    return send_from_directory("static", "index.html")

@app.get("/health")
def health():
    return jsonify({"ok": True})

@app.get("/debug/zoom")
def debug_zoom():
    if not zoom:
        return {"ok": False, "error": "Zoom not configured"}, 400
    try:
        items = zoom.list_meetings("upcoming", 1)
        return {"ok": True, "count": len(items)}
    except Exception as e:
        return {"ok": False, "error": str(e)}, 500

@app.get("/debug/telemost")
def debug_telemost():
    """
    У Telemost нет публичной ручки "список встреч", поэтому для проверки
    аккуратно создаём комнату и сразу удаляем её.
    """
    if not telemost:
        return {"ok": False, "error": "Telemost not configured"}, 400
    try:
        room = telemost.create_meeting()
        rid = room.get("id")
        join_url = room.get("join_url", "—")
        # Пытаемся удалить; если не получится — всё равно покажем создание
        deleted = False
        try:
            if rid:
                telemost.delete_meeting(rid)
                deleted = True
        except Exception:
            pass
        return {"ok": True, "created_id": rid, "join_url": join_url, "deleted": deleted}
    except Exception as e:
        return {"ok": False, "error": str(e)}, 500

@app.post("/api/chat")
def chat():
    payload = request.get_json(force=True) or {}
    msg = (payload.get("message") or "").strip()
    if not msg:
        return jsonify({"error": "empty"}), 400

    # 1) Telemost: реагируем только, если текст явно про телемост
    if telemost:
        try:
            tm_reply = handle_telemost_intents(telemost, msg)
            if tm_reply:
                return jsonify({"reply": tm_reply})
        except Exception as e:
            return jsonify({"reply": f"❌ Telemost: {e}"}), 200

    # 2) Zoom: реагируем на zoom/зум…
    if zoom:
        try:
            zoom_reply = handle_zoom_intents(zoom, msg)
            if zoom_reply:
                return jsonify({"reply": zoom_reply})
        except Exception as e:
            return jsonify({"reply": f"❌ Zoom: {e}"}), 200

    # 3) Простая утилита: текущее время
    low = msg.lower()
    if "время" in low or "дата" in low:
        now = datetime.now(pytz.timezone(ZOOM_TZ)).strftime("%d.%m.%Y %H:%M:%S")
        return jsonify({"reply": f"Сейчас {now} ({ZOOM_TZ}) ⏰"})

    # 4) Ответ через LLM
    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json",
        "Referer": APP_URL,
        "X-Title": APP_NAME,
    }
    body = {
        "model": MODEL,
        "messages": [{"role": "user", "content": msg}],
    }

    try:
        r = requests.post(API_URL, headers=headers, json=body, timeout=60)
    except requests.RequestException as e:
        return jsonify({"error": "network", "details": str(e)}), 502

    if not r.ok:
        return jsonify({"error": f"upstream {r.status_code}", "details": r.text}), 502

    data = r.json()
    reply = ((data.get("choices") or [{}])[0].get("message", {}).get("content", "")) or "…"
    return jsonify({"reply": reply})

# ===== Единичный .ICS для конкретной комнаты (по локальному времени из store) =====
@app.get("/telemost/<conf_id>.ics")
def telemost_ics(conf_id):
    if not telemost:
        return "Telemost not configured", 404

    rec = telemost.get_local_record(conf_id) if hasattr(telemost, "get_local_record") else None
    if not rec or not rec.get("start_time"):
        return "Встреча не найдена или у неё не задано время", 404

    tz = pytz.timezone(telemost.tz)
    start = datetime.fromisoformat(rec["start_time"])   # уже локальная дата
    duration = int(rec.get("duration", 60))
    end = start + timedelta(minutes=duration)

    def fmt_utc(dt):
        import pytz as _p
        return dt.astimezone(_p.utc).strftime("%Y%m%dT%H%M%SZ")

    link = rec.get("join_url") or ""
    topic = rec.get("topic") or "Встреча"
    uid = f"{conf_id}@help-gpt"

    ics = f"""BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//ISE//help-gpt//RU
METHOD:PUBLISH
BEGIN:VEVENT
UID:{uid}
DTSTAMP:{fmt_utc(datetime.now(pytz.utc))}
DTSTART:{fmt_utc(start)}
DTEND:{fmt_utc(end)}
SUMMARY:{topic}
DESCRIPTION:Ссылка для подключения: {link}
URL:{link}
END:VEVENT
END:VCALENDAR
"""
    return Response(
        ics,
        mimetype="text/calendar",
        headers={"Content-Disposition": f'attachment; filename="telemost_{conf_id}.ics"'}
    )

if __name__ == "__main__":
    # host=0.0.0.0 чтобы было видно из Docker/Portainer
    app.run(host="0.0.0.0", port=8080)
