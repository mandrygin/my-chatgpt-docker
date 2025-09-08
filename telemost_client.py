# telemost_client.py
import requests
from datetime import datetime, timedelta
import pytz
import os

class TelemostClient:
    API_BASE = "https://api.telemost.yandex.net/v1"

    def __init__(self, tz: str = "Europe/Moscow"):
        self.client_id = os.getenv("YANDEX_CLIENT_ID")
        self.client_secret = os.getenv("YANDEX_CLIENT_SECRET")
        self.oauth_token = os.getenv("YANDEX_OAUTH_TOKEN")
        self.tz = tz
        if not self.oauth_token:
            raise ValueError("❌ Нет OAuth токена. Добавь YANDEX_OAUTH_TOKEN в переменные окружения.")

    def _headers(self):
        return {
            "Authorization": f"OAuth {self.oauth_token}",
            "Content-Type": "application/json"
        }

    def create_meeting(self, topic: str, when_dt: datetime, duration_min: int = 60):
        tz = pytz.timezone(self.tz)
        local_dt = tz.localize(when_dt) if when_dt.tzinfo is None else when_dt
        end_dt = local_dt + timedelta(minutes=duration_min)

        payload = {
            "title": topic or "Встреча",
            "start_time": local_dt.isoformat(),
            "end_time": end_dt.isoformat(),
            "auto_record": False
        }

        r = requests.post(
            f"{self.API_BASE}/conferences",
            headers=self._headers(),
            json=payload,
            timeout=20
        )
        r.raise_for_status()
        return r.json()

    def list_meetings(self):
        r = requests.get(
            f"{self.API_BASE}/conferences",
            headers=self._headers(),
            timeout=20
        )
        r.raise_for_status()
        return r.json().get("conferences", [])

    def delete_meeting(self, conf_id: str):
        r = requests.delete(
            f"{self.API_BASE}/conferences/{conf_id}",
            headers=self._headers(),
            timeout=20
        )
        if r.status_code not in (200, 204):
            r.raise_for_status()
        return True
