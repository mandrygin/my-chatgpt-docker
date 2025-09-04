from flask import Flask, request, jsonify, send_from_directory
import os, requests

# ==== Конфиг из переменных окружения ====
API_KEY  = os.environ["OPENAI_API_KEY"]                          # sk-or-...
MODEL    = os.environ.get("MODEL", "deepseek/deepseek-chat")     # нужная модель
API_URL  = os.environ.get("OPENROUTER_URL",
                           "https://openrouter.ai/api/v1/chat/completions")
APP_URL  = os.environ.get("APP_URL", "http://localhost:8080")
APP_NAME = os.environ.get("APP_NAME", "help-gpt")

# Короткая системная инструкция: разрешены Markdown и LaTeX
SYSTEM_PROMPT = os.environ.get(
    "SYSTEM_PROMPT",
    "Отвечай понятно и кратко. Разрешено использовать Markdown и LaTeX "
    "(формулы: $...$, $$...$$, \\(...\\), \\[...\\])."
)

app = Flask(__name__, static_folder="static", static_url_path="")

# ==== Маршруты ====
@app.get("/")
def index():
    return send_from_directory("static", "index.html")

@app.get("/health")
def health():
    return jsonify({"ok": True})

@app.post("/api/chat")
def chat():
    payload = request.get_json(force=True) or {}
    msg = (payload.get("message") or "").strip()
    if not msg:
        return jsonify({"error": "empty"}), 400

    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json",
        "Referer": APP_URL,
        "X-Title": APP_NAME,
    }
    body = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user",   "content": msg},
        ],
        "temperature": 0.3
    }

    try:
        r = requests.post(API_URL, headers=headers, json=body, timeout=60)
    except requests.RequestException as e:
        return jsonify({"error": "network", "details": str(e)}), 502

    if not r.ok:
        return jsonify({"error": f"upstream {r.status_code}", "details": r.text}), 502

    data = r.json()
    reply = (
        (data.get("choices") or [{}])[0]
        .get("message", {})
        .get("content", "")
    )
    return jsonify({"reply": reply})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
