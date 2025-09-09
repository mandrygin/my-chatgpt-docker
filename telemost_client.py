import os
import re
import requests
import pytz
from datetime import datetime, timedelta

class TelemostClient:
    """
    Простой клиент API Яндекс Телемост.
    Требуется переменная окружения YANDEX_OAUTH_TOKEN (OAuth-токен организации).
    """
    API_BASE = "https://api.telemost.yandex.net/v1"

    def __init__(self, tz: str = "Europe/Moscow"):
        self.oauth_token = os.getenv("YANDEX_OAUTH_TOKEN")
        self.tz = tz
        if not self.oauth_token:
            raise ValueError("❌ Нет OAuth токена. Добавь YANDEX_OAUTH_TOKEN в переменные окружения.")

    def _headers(self):
        return {
            "Authorization": f"OAuth {self.oauth_token}",
            "Content-Type": "application/json",
        }

    def create_meeting(self, title: str, when_dt: datetime, duration_min: int = 60) -> dict:
        tz = pytz.timezone(self.tz)
        local_dt = tz.localize(when_dt) if when_dt.tzinfo is None else when_dt.astimezone(tz)
        end_dt = local_dt + timedelta(minutes=duration_min)

        payload = {
            "title": title or "Встреча",
            "start_time": local_dt.isoformat(),
            "end_time": end_dt.isoformat(),
            "auto_record": False
        }
        r = requests.post(f"{self.API_BASE}/conferences", headers=self._headers(), json=payload, timeout=20)
        r.raise_for_status()
        return r.json()

    def list_meetings(self) -> list[dict]:
        r = requests.get(f"{self.API_BASE}/conferences", headers=self._headers(), timeout=20)
        r.raise_for_status()
        return r.json().get("conferences", [])

    def delete_meeting(self, conf_id: str) -> bool:
        r = requests.delete(f"{self.API_BASE}/conferences/{conf_id}", headers=self._headers(), timeout=20)
        if r.status_code not in (200, 204):
            r.raise_for_status()
        return True


# ==================== парсинг русского "когда" (минимально автономный) ====================

_MONTHS = {
    "январ": 1, "феврал": 2, "март": 3, "апрел": 4, "ма": 5,
    "июн": 6, "июл": 7, "август": 8, "сентябр": 9, "октябр": 10, "ноябр": 11, "декабр": 12
}

_SPACE = r"[\s\u00A0\u202F\u2009]"

def _extract_topic(text: str) -> str | None:
    m = re.search(r"[«\"']([^\"'»]{3,120})[\"'»]", text)
    if m:
        return m.group(1).strip()
    m = re.search(r"(?:тема|на тему|о теме)\s*[:\-]?\s*(.+)$", text, flags=re.IGNORECASE)
    if m:
        return m.group(2).strip()
    return None

def _extract_time(s: str) -> tuple[int, int] | None:
    s = (s or "").replace("\u202f", " ").replace("\u00a0", " ").replace("\u2009", " ")
    # HH:MM, H:MM, HH-MM, HH.MM, HH MM
    m = re.search(rf"\b(\d{{1,2}})[\:\-\.{_SPACE}](\d{{2}})\b", s)
    if m:
        h, mnt = int(m.group(1)), int(m.group(2))
        if 0 <= h <= 23 and 0 <= mnt <= 59:
            return h, mnt
    # "14ч"
    m = re.search(r"\b(\d{1,2})\s*ч\b", s)
    if m:
        hh = int(m.group(1))
        if 0 <= hh <= 23:
            return hh, 0
    # "в 11"
    m = re.search(r"\bв\s+(\d{1,2})(?!\d)", s)
    if m:
        hh = int(m.group(1))
        if 0 <= hh <= 23:
            return hh, 0
    return None

def _parse_when_ru(text: str, tz_name: str) -> datetime | None:
    """Очень простой разбор: сегодня/завтра/послезавтра + явные даты + время."""
    s = (text or "").lower()
    tz = pytz.timezone(tz_name)
    now = datetime.now(tz)

    # относительные дни
    if "послезавтра" in s:
        day = (now + timedelta(days=2)).replace(hour=0, minute=0, second=0, microsecond=0)
    elif "завтра" in s:
        day = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    elif "сегодня" in s:
        day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    else:
        day = None

    # dd.mm(.yyyy)?
    if day is None:
        m = re.search(r"\b(\d{1,2})\.(\d{1,2})(?:\.(\d{4}))?\b", s)
        if m:
            d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3) or now.year)
            try:
                day = tz.localize(datetime(y, mo, d))
            except ValueError:
                day = None

    # dd <месяц>( yyyy)?
    if day is None:
        m = re.search(r"\b(\d{1,2})\s+([а-яё]+)(?:\s+(\d{4}))?\b", s)
        if m:
            d = int(m.group(1))
            mon_word = m.group(2)
            y = int(m.group(3) or now.year)
            mon = next((num for stem, num in _MONTHS.items() if mon_word.startswith(stem)), None)
            if mon:
                try:
                    day = tz.localize(datetime(y, mon, d))
                except ValueError:
                    day = None

    tm = _extract_time(s)

    if day and tm:
        return day.replace(hour=tm[0], minute=tm[1])
    if day and not tm:
        return day.replace(hour=10, minute=0)
    if not day and tm:
        dt = now.replace(hour=tm[0], minute=tm[1], second=0, microsecond=0)
        if dt <= now:
            dt += timedelta(days=1)
        return dt
    return None


# ==================== интенты Телемоста (только при слове "телемост") ====================

def handle_telemost_intents(tm: TelemostClient, text: str) -> str | None:
    original = text or ""
    t = original.lower().strip()

    # срабатываем только если упомянут "телемост" (любая форма)
    if not re.search(r"\bтелемост\w*\b", t):
        return None

    # список встреч
    if re.search(r"\b(список|мои|покажи)\s+встреч", t):
        items = tm.list_meetings()
        if not items:
            return "🗓️ В Телемосте встреч нет."
        tz = pytz.timezone(tm.tz)
        lines = []
        for i, m in enumerate(items, 1):
            start = m.get("start_time")
            when = "—"
            if start:
                try:
                    dt = datetime.fromisoformat(start).astimezone(tz)
                    when = dt.strftime("%d.%m.%Y %H:%M")
                except Exception:
                    pass
            lines.append(f"{i}. {m.get('title') or 'Без темы'} • ID: {m.get('id')} • {when}")
        return "🗓️ Ближайшие встречи (Телемост):\n" + "\n".join(lines)

    # удалить все встречи
    if re.search(r"(отмени|удали)\s+все\s+встреч", t):
        items = tm.list_meetings()
        for m in items:
            tm.delete_meeting(m["id"])
        return f"🗑️ В Телемосте удалено {len(items)} встреч."

    # удалить по ID (UUID-подобные или строковые ID)
    m = re.search(r"(отмени|удали)\s+встреч[ауые]?\s+([a-z0-9\-]{6,})", t)
    if m:
        cid = m.group(2)
        tm.delete_meeting(cid)
        return f"🗑️ Встреча Телемоста **{cid}** отменена."

    # создать встречу
    if re.search(r"\b(создай|создать|сделай|запланируй)\b.*\bвстреч", t):
        when = _parse_when_ru(original, tm.tz) or datetime.now(pytz.timezone(tm.tz)).replace(minute=0, second=0, microsecond=0)
        topic = _extract_topic(original) or "Встреча"
        data = tm.create_meeting(topic, when, 60)

        tz = pytz.timezone(tm.tz)
        when_str = when.astimezone(tz).strftime("%d.%m.%Y %H:%M")
        link = (data.get("links") or {}).get("join") or data.get("join_url") or "—"
        return (
            f"✅ Встреча (Телемост) «{topic}» создана на {when_str} ({tm.tz}).\n"
            f"Ссылка: {link}\nID: {data.get('id')}"
        )

    return None
