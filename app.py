from flask import Flask, request, jsonify, send_from_directory
import os
import requests
from datetime import datetime

# ----- Zoom -----
from zoom_client import ZoomClient, handle_zoom_intents

# ----- Telemost (мягкий импорт, чтобы не падать если файла нет) -----
try:
    from telemost_client import TelemostClient, handle_telemost_intents
except Exception:
    TelemostClient = None
    def handle_telemost_intents(*args, **kwargs):
        return None

# ===== Настройки LLM =====
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
    except Exception as e:
        print(f"[Zoom] Failed to init ZoomClient: {e}")

# ===== Инициализация Telemost (мягко) =====
TELEMOST_TZ = os.getenv("TELEMOST_TZ", "Europe/Moscow")
telemost = None
if TelemostClient is not None:
    try:
        # Внутри TelemostClient используется YANDEX_OAUTH_TOKEN из env
        telemost = TelemostClient(tz=TELEMOST_TZ)
    except Exception as e:
        print(f"[Telemost] Failed to init TelemostClient: {e}")

# ===== Роуты =====
@app.get("/")
def index():
    return send_from_directory("static", "index.html")

@app.get("/health")
def health():
    return jsonify({"ok": True})

# Быстрая проверка Zoom
@app.get("/debug/zoom")
def debug_zoom():
    if not zoom:
        return {"ok": False, "error": "Zoom not configured"}, 400
    try:
        items = zoom.list_meetings("upcoming", 1)
        return {"ok": True, "count": len(items)}
    except Exception as e:
        return {"ok": False, "error": str(e)}, 500

# Быстрая проверка Telemost
@app.get("/debug/telemost")
def debug_telemost():
    if not telemost:
        return {"ok": False, "error": "Telemost not configured"}, 400
    try:
        items = telemost.list_meetings()
        return {"ok": True, "count": len(items)}
    except Exception as e:
        return {"ok": False, "error": str(e)}, 500

@app.post("/api/chat")
def chat():
    payload = request.get_json(force=True) or {}
    msg = (payload.get("message") or "").strip()
    if not msg:
        return jsonify({"error": "empty"}), 400

    # 👉 1) пробуем команды Telemost
    if telemost:
        try:
            t_reply = handle_telemost_intents(telemost, msg)
            if t_reply:
                return jsonify({"reply": t_reply})
        except Exception as e:
            # не ломаем чат
            return jsonify({"reply": f"❌ Telemost: {e}"}), 200

    # 👉 2) пробуем команды Zoom
    if zoom:
        try:
            z_reply = handle_zoom_intents(zoom, msg)
            if z_reply:
                return jsonify({"reply": z_reply})
        except Exception as e:
            return jsonify({"reply": f"❌ Zoom: {e}"}), 200

    # 👉 3) простая справка по времени
    if "время" in msg.lower() or "дата" in msg.lower():
        now = datetime.now().strftime("%d.%m.%Y %H:%M:%S")
        return jsonify({"reply": f"Сейчас {now} по системному времени сервера ⏰"})

    # 👉 4) LLM через OpenRouter
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


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
