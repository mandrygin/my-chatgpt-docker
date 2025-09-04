from flask import Flask, request, jsonify, send_from_directory
import os, requests

API = os.environ["OPENAI_API_KEY"]
MODEL = os.environ.get("MODEL", "openai/gpt-4.1-mini")  # имя модели в каталоге OpenRouter
URL = os.environ.get("OPENROUTER_URL", "https://openrouter.ai/api/v1/chat/completions")

app = Flask(__name__, static_folder="static", static_url_path="")

@app.get("/")
def index():
    return send_from_directory("static", "index.html")

@app.post("/api/chat")
def chat():
    msg = (request.get_json(force=True) or {}).get("message", "").strip()
    if not msg:
        return jsonify({"error": "empty"}), 400

    try:
        r = requests.post(
            URL,
            headers={
                "Authorization": f"Bearer {API}",
                "Content-Type": "application/json",
                # необязательно, но рекомендуется OpenRouter:
                "HTTP-Referer": os.environ.get("APP_URL", "http://localhost"),
                "X-Title": os.environ.get("APP_NAME", "help-gpt"),
            },
            json={
                "model": MODEL,
                "messages": [{"role": "user", "content": msg}],
            },
            timeout=60,
        )
        r.raise_for_status()
        data = r.json()
        reply = (
            data.get("choices", [{}])[0]
                .get("message", {})
                .get("content", "")
        )
        return jsonify({"reply": reply})
    except requests.RequestException as e:
        return jsonify({"error": f"upstream: {e}"}), 502

if __name__ == "__main__":
    # важно: слушаем 0.0.0.0 и порт 8080
    app.run(host="0.0.0.0", port=8080)
