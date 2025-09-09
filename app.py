from flask import Flask, request, jsonify, send_from_directory
import os
import requests
from datetime import datetime

# ----- Zoom -----
from zoom_client import ZoomClient, handle_zoom_intents
# ----- Telemost -----
from telemost_client import TelemostClient, handle_telemost_intents

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
    except Exception as e:
        print(f"[Zoom] Failed to init ZoomClient: {e}")

# ===== Инициализация Яндекс Телемоста (мягко) =====
telemost = None
YANDEX_OAUTH_TOKEN = os.getenv("YANDEX_OAUTH_TOKEN")
YANDEX_ORG_ID      = os.getenv("YANDEX_ORG_ID")  # обязателен для Telemost API

try:
    if YANDEX_OAUTH_TOKEN and YANDEX_ORG_ID:
        telemost = TelemostClient(tz=ZOOM_TZ)
    else:
        print(f"[Telemost] Skipped init. YANDEX_OAUTH_TOKEN set: {bool(YANDEX_OAUTH_TOKEN)}, "
              f"YANDEX_ORG_ID set: {bool(YANDEX_ORG_ID)}")
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

    # 1) Телемост — реагирует только при слове "телемост"
    if telemost:
        try:
            tm_reply = handle_telemost_intents(telemost, msg)
            if tm_reply:
                return jsonify({"reply": tm_reply})
        except Exception as e:
            return jsonify({"reply": f"❌ Telemost: {e}"}), 200

    # 2) Zoom — реагирует только при словах zoom/зум/зуме/зума
    if zoom:
        try:
            zoom_reply = handle_zoom_intents(zoom, msg)
            if zoom_reply:
                return jsonify({"reply": zoom_reply})
        except Exception as e:
            return jsonify({"reply": f"❌ Zoom: {e}"}), 200

    # 3) Простая утилита: текущее время
    if "время" in msg.lower() or "дата" in msg.lower():
        now = datetime.now().strftime("%d.%m.%Y %H:%M:%S")
        return jsonify({"reply": f"Сейчас {now} по системному времени сервера ⏰"})

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


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
