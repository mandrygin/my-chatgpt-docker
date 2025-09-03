from flask import Flask, request, jsonify, send_from_directory
import os, requests

API = os.environ["OPENAI_API_KEY"]
MODEL = os.environ.get("MODEL", "gpt-4.1-mini")
URL = "https://api.openai.com/v1/responses"

app = Flask(__name__, static_folder="static", static_url_path="")

@app.get("/")
def index():
    return send_from_directory("static", "index.html")

@app.post("/api/chat")
def chat():
    msg = (request.get_json(force=True) or {}).get("message","").strip()
    if not msg: return jsonify({"error":"empty"}), 400
    r = requests.post(URL,
        headers={"Authorization":f"Bearer {API}","Content-Type":"application/json"},
        json={"model":MODEL,"input":msg}, timeout=60)
    return jsonify({"reply": (r.json().get("output_text") or "")})
