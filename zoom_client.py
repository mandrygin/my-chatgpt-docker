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
_TIME_FIXES = [
    (r"(\b\d{1,2})\s*[:.,\- ]\s*(\d{2})", r"\1:\2"),  # 17 00 -> 17:00, 17-00 -> 17:00 и т.п.
    (r"\b(\d{1,2})\s*ч\b", r"\1:00"),                 # 14ч -> 14:00
    (r"\bв\s+(\d{1,2})\b", r"в \1:00"),               # "в 14" -> "в 14:00"
]

def _normalize_time_tokens(t: str) -> str:
    s = t
    # 17 00, 17-00, 17.00 → 17:00
    s = re.sub(r"\b(\d{1,2})[\s\.\-:,](\d{2})\b", r"\1:\2", s)
    # 14ч → 14:00
    s = re.sub(r"\b(\d{1,2})\s*ч\b", r"\1:00", s)
    # "в 14" → "в 14:00"
    s = re.sub(r"\bв\s+(\d{1,2})(?!:)", r"в \1:00", s)

    # Если встречается "завтра" и нет явной даты → прибавим слово "завтра"
    if "завтра" in s and not re.search(r"\d{1,2}\.\d{1,2}|\d{4}-\d{2}-\d{2}", s):
        # ничего не меняем, просто оставляем "завтра" для dateparser
        pass

    # Если нет времени, но есть "сегодня/завтра/послезавтра" → добавим 10:00
    if re.search(r"\b(сегодня|завтра|послезавтра)\b", s) and not re.search(r"\d{1,2}:\d{2}", s):
        s += " в 10:00"

    return s

def _extract_topic(text: str) -> str | None:
    # тема в кавычках
    m = re.search(r"[«\"']([^\"'»]{3,120})[\"'»]", text)
    if m:
        return m.group(1).strip()
    # или после ключевого слова
    m = re.search(r"(тема|о теме|на тему)\s*[:\-]?\s*(.+)$", text, flags=re.IGNORECASE)
    if m:
        return m.group(2).strip()
    return None

def _parse_when_ru(text: str, tz_name: str) -> datetime | None:
    normalized = _normalize_time_tokens(text.lower())
    tz = pytz.timezone(tz_name)

    settings = {
        "PREFER_DATES_FROM": "future",
        "RELATIVE_BASE": datetime.now(tz),
        "TIMEZONE": tz_name,
        "RETURN_AS_TIMEZONE_AWARE": True,
    }
    dt = dateparser.parse(normalized, languages=["ru"], settings=settings)

    if not dt:
        return None

    # если без tz — проставим
    if dt.tzinfo is None:
        dt = tz.localize(dt)

    # --- Хак: слово "завтра" явно есть, а дата совпала с сегодня ---
    if "завтра" in normalized and dt.date() == datetime.now(tz).date():
        dt = dt + timedelta(days=1)

    return dt



def handle_zoom_intents(zoom: ZoomClient, text: str) -> str | None:
    t = (text or "").lower().strip()

    # --- список встреч ---
    if re.search(r"\b(список|мои|покажи)\s+встреч", t) or "встречи zoom" in t or "встречи зум" in t:
        items = zoom.list_meetings("upcoming", 20)
        return _fmt_meetings(items, zoom.tz)

    # --- удалить все встречи ---
    if re.search(r"(отмени|удали)\s+все\s+встреч", t):
        items = zoom.list_meetings("upcoming", 50)
        if not items:
            return "🗑️ Нет встреч для удаления."
        for m in items:
            zoom.delete_meeting(m["id"])
        return f"🗑️ Удалено {len(items)} встреч."

    # --- удалить по ID ---
    m = re.search(r"(отмени|удали)\s+встреч[ауые]?\s+(\d{6,})", t)
    if m:
        mid = m.group(2)
        zoom.delete_meeting(mid)
        return f"🗑️ Встреча **{mid}** отменена."

    # --- создание встречи ---
    if re.search(r"\b(создай|создать|сделай|запланируй)\b.*\bвстреч[ауые]?\b", t) \
       or (("в зум" in t or "в zoom" in t) and "встреч" in t):

        when = _parse_when_ru(text, zoom.tz) or datetime.now(pytz.timezone(zoom.tz))
        topic = _extract_topic(text) or "Встреча"

        try:
            data = zoom.create_meeting(topic, when, 60)
        except requests.HTTPError as e:
            # покажем причину от Zoom (права, неверный email и т.д.)
            return f"❌ Zoom API: {e.response.status_code} {e.response.text}"

        when_str = when.astimezone(pytz.timezone(zoom.tz)).strftime("%d.%m.%Y %H:%M")
        pwd = f"\nПароль: {data.get('password')}" if data.get('password') else ""
        return (
            f"✅ Встреча «{topic}» создана на {when_str} ({zoom.tz}).\n"
            f"Ссылка: {data.get('join_url')}\nID: {data.get('id')}{pwd}"
        )

    return None
