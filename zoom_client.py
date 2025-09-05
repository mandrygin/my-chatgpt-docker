# zoom_client.py
import time
import re
from datetime import datetime
import requests
import pytz
import dateparser


class ZoomClient:
    TOKEN_URL = "https://zoom.us/oauth/token"
    API_BASE = "https://api.zoom.us/v2"

    def __init__(self, account_id: str, client_id: str, client_secret: str,
                 host_email: str, tz: str = "Europe/Moscow"):
        if not all([account_id, client_id, client_secret, host_email]):
            raise ValueError("ZoomClient: не заданы ACCOUNT_ID/CLIENT_ID/CLIENT_SECRET/HOST_EMAIL")
        self.account_id = account_id
        self.client_id = client_id
        self.client_secret = client_secret
        self.host_email = host_email
        self.tz = tz
        self._access_token = None
        self._exp_ts = 0  # unix-время истечения токена

    # ---------- внутреннее ----------
    def _get_access_token(self) -> str:
        if self._access_token and time.time() < self._exp_ts - 60:
            return self._access_token

        r = requests.post(
            self.TOKEN_URL,
            params={"grant_type": "account_credentials", "account_id": self.account_id},
            auth=(self.client_id, self.client_secret),
            timeout=15,
        )
        r.raise_for_status()
        data = r.json()
        self._access_token = data["access_token"]
        self._exp_ts = time.time() + int(data.get("expires_in", 3600))
        return self._access_token

    def _headers(self):
        return {
            "Authorization": f"Bearer {self._get_access_token()}",
            "Content-Type": "application/json",
        }

    def _to_utc_iso(self, when_dt: datetime) -> str:
        tz = pytz.timezone(self.tz)
        local = tz.localize(when_dt) if when_dt.tzinfo is None else when_dt.astimezone(tz)
        return local.astimezone(pytz.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    # ---------- API ----------
    def create_meeting(self, topic: str, when_dt: datetime, duration_min: int = 60) -> dict:
        payload = {
            "topic": topic or "Встреча",
            "type": 2,
            "start_time": self._to_utc_iso(when_dt),
            "duration": duration_min,
            "timezone": self.tz,
            "settings": {"waiting_room": True},
        }
        r = requests.post(
            f"{self.API_BASE}/users/{self.host_email}/meetings",
            headers=self._headers(),
            json=payload,
            timeout=20,
        )
        r.raise_for_status()
        return r.json()

    def list_meetings(self, status: str = "upcoming", page_size: int = 20) -> list[dict]:
        r = requests.get(
            f"{self.API_BASE}/users/{self.host_email}/meetings",
            headers=self._headers(),
            params={"type": status, "page_size": page_size},
            timeout=20,
        )
        r.raise_for_status()
        return r.json().get("meetings", [])

    def delete_meeting(self, meeting_id: str) -> bool:
        r = requests.delete(
            f"{self.API_BASE}/meetings/{meeting_id}",
            headers=self._headers(),
            timeout=20,
        )
        if r.status_code not in (200, 204):
            r.raise_for_status()
        return True


# ----- утилиты для чата -----

def _fmt_meetings(items: list[dict], tz_name: str) -> str:
    if not items:
        return "🗓️ Встреч нет."
    tz = pytz.timezone(tz_name)
    lines = []
    for i, m in enumerate(items, 1):
        start = m.get("start_time")
        when = "—"
        if start:
            dt = datetime.fromisoformat(start.replace("Z", "+00:00")).astimezone(tz)
            when = dt.strftime("%d.%m.%Y %H:%M")
        lines.append(f"{i}. {m.get('topic') or 'Без темы'} • ID: {m.get('id')} • {when}")
    return "🗓️ Ближайшие встречи:\n" + "\n".join(lines)


def handle_zoom_intents(zoom: ZoomClient, text: str) -> str | None:
    t = text.lower()

    # список встреч
    if any(k in t for k in ["список встреч", "мои встречи", "покажи встречи", "встречи в зум", "встречи zoom"]):
        items = zoom.list_meetings("upcoming", 20)
        return _fmt_meetings(items, zoom.tz)

    # отмена по ID
    m = re.search(r"(отмени|удали)\s+встреч[ауы]?\s+(\d{6,})", t)
    if m:
        mid = m.group(2)
        zoom.delete_meeting(mid)
        return f"🗑️ Встреча **{mid}** отменена."

    # создание
    if "встреч" in t and any(k in t for k in ["zoom", "зум"]):
        if any(k in t for k in ["создай", "создать", "сделай", "запланируй"]):
            when = dateparser.parse(t, languages=["ru"], settings={"PREFER_DATES_FROM": "future"}) or datetime.now()
            data = zoom.create_meeting("Встреча", when, 60)
            when_str = when.strftime("%d.%m.%Y %H:%M")
            pwd = f"\nПароль: {data.get('password')}" if data.get('password') else ""
            return f"✅ Встреча в Zoom создана на {when_str} ({zoom.tz}).\nСсылка: {data.get('join_url')}\nID: {data.get('id')}{pwd}"

    return None
