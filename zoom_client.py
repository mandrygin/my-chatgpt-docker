# zoom_client.py
import time
import re
from datetime import datetime, timedelta
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


# --- нормализация и извлечение даты/времени ---

MONTHS_RU = {
    "январ": 1, "феврал": 2, "март": 3, "апрел": 4, "ма": 5,
    "июн": 6, "июл": 7, "август": 8, "сентябр": 9, "октябр": 10, "ноябр": 11, "декабр": 12
}

def _strip_trailing_timestamp(text: str) -> str:
    """
    Срезаем хвост '15:12' / '15 12' / '15-12' ТОЛЬКО если это второе (или дальше) время
    в строке и стоит в самом конце. Если время одно — не трогаем.
    """
    if not text:
        return text
    pattern = r"\b\d{1,2}[:\.\- ]\d{2}\b"
    matches = list(re.finditer(pattern, text))
    if len(matches) >= 2 and re.search(pattern + r"\s*$", text):
        last = matches[-1]
        return text[:last.start()].rstrip()
    return text



def _normalize_time_tokens(s: str) -> str:
    # 17 00, 17-00, 17.00 → 17:00
    s = re.sub(r"\b(\d{1,2})[\s\.\-:,](\d{2})\b", r"\1:\2", s)
    # 14ч → 14:00
    s = re.sub(r"\b(\d{1,2})\s*ч\b", r"\1:00", s)
    # "в 14" → "в 14:00"
    s = re.sub(r"\bв\s+(\d{1,2})(?!:)", r"в \1:00", s)
    # если есть сегодня/завтра/послезавтра без времени — добавим 10:00 по умолчанию
    if re.search(r"\b(сегодня|завтра|послезавтра)\b", s) and not re.search(r"\d{1,2}:\d{2}", s):
        s += " в 10:00"
    return s

def _extract_topic(text: str) -> str | None:
    m = re.search(r"[«\"']([^\"'»]{3,120})[\"'»]", text)
    if m:
        return m.group(1).strip()
    m = re.search(r"(?:тема|на тему|о теме)\s*[:\-]?\s*(.+)$", text, flags=re.IGNORECASE)
    if m:
        return m.group(2).strip()
    return None

def _parse_explicit_date(text: str, base: datetime) -> datetime | None:
    t = text.lower()

    # 1) dd.mm(.yyyy)?
    m = re.search(r"\b(\d{1,2})\.(\d{1,2})(?:\.(\d{4}))?\b", t)
    if m:
        d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3) or base.year)
        try:
            return base.tzinfo.localize(datetime(y, mo, d)) if base.tzinfo else datetime(y, mo, d)
        except ValueError:
            return None

    # 2) dd <месяц-словом> (yyyy)?
    m = re.search(r"\b(\d{1,2})\s+([а-яё]+)(?:\s+(\d{4}))?\b", t)
    if m:
        d = int(m.group(1))
        mon_word = m.group(2)
        y = int(m.group(3) or base.year)
        mon = None
        for stem, num in MONTHS_RU.items():
            if mon_word.startswith(stem):
                mon = num
                break
        if mon:
            try:
                return base.tzinfo.localize(datetime(y, mon, d)) if base.tzinfo else datetime(y, mon, d)
            except ValueError:
                return None

    # 3) относительные ключевые слова
    if "послезавтра" in t:
        return (base + timedelta(days=2)).replace(hour=0, minute=0, second=0, microsecond=0)
    if "завтра" in t:
        return (base + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    if "сегодня" in t:
        return base.replace(hour=0, minute=0, second=0, microsecond=0)

    return None

def _extract_time(text: str) -> tuple[int, int] | None:
    """
    Извлекаем время. Поддерживает: 11:00, 11.00, 11-00, '11 00', '11ч', 'в 11', 'в 11 утра/вечера'.
    Не путает '06.09.2025' с '06:09'.
    """
    s = text.lower()

    # 11:00 / 11.00 / 11-00 / 11 00 (но не часть dd.mm.yyyy: после минут — конец слова/строки)
    m = re.search(r"\b(\d{1,2})[:\.\- ](\d{2})\b(?!\.)", s)
    if m:
        hh, mm = int(m.group(1)), int(m.group(2))
        if 0 <= hh <= 23 and 0 <= mm <= 59:
            return hh, mm

    # 11ч / 11 ч
    m = re.search(r"\b(\d{1,2})\s*ч\b", s)
    if m:
        hh = int(m.group(1))
        if 0 <= hh <= 23:
            return hh, 0

    # "в 11" → 11:00
    m = re.search(r"\bв\s+(\d{1,2})(?!\d)", s)
    if m:
        hh = int(m.group(1))
        if 0 <= hh <= 23:
            return hh, 0

    return None



def _parse_when_ru(text: str, tz_name: str) -> datetime | None:
    text = _strip_trailing_timestamp(text or "")
    text = _normalize_time_tokens(text)
    tz = pytz.timezone(tz_name)
    now = datetime.now(tz)

    # 1) явная дата?
    day = _parse_explicit_date(text, now)

    # 2) время?
    tm = _extract_time(text)

    if day and tm:
        dt = day.replace(hour=tm[0], minute=tm[1])
        # если дата без года и получилась в прошлом — переносим на следующий год
        if dt < now and re.search(r"\b\d{1,2}\.\d{1,2}\b", text):
            try:
                dt = dt.replace(year=dt.year + 1)
            except ValueError:
                pass
        return dt

    if day and not tm:
        # дата есть, времени нет — берём 10:00
        return day.replace(hour=10, minute=0)

    if not day and tm:
        # только время: если уже прошло — завтра
        dt = now.replace(hour=tm[0], minute=tm[1], second=0, microsecond=0)
        if dt <= now:
            dt = dt + timedelta(days=1)
        # если явно было «завтра» — форсируем сдвиг
        if "завтра" in text and dt.date() == now.date():
            dt = dt + timedelta(days=1)
        return dt

    # fallback на dateparser
    settings = {
        "PREFER_DATES_FROM": "future",
        "DATE_ORDER": "DMY",
        "RELATIVE_BASE": now,
        "TIMEZONE": tz_name,
        "RETURN_AS_TIMEZONE_AWARE": True,
    }
    dt = dateparser.parse(text, languages=["ru", "ru"], settings=settings)  # ru на всякий
    if not dt:
        return None
    if dt.tzinfo is None:
        dt = tz.localize(dt)
    if "завтра" in text and dt.date() == now.date():
        dt = dt + timedelta(days=1)
    return dt


# ----- интенты -----

def handle_zoom_intents(zoom: ZoomClient, text: str) -> str | None:
    original_text = text or ""
    t = (original_text or "").lower().strip()

    # список встреч
    if re.search(r"\b(список|мои|покажи)\s+встреч", t) or "встречи zoom" in t or "встречи зум" in t:
        items = zoom.list_meetings("upcoming", 20)
        return _fmt_meetings(items, zoom.tz)

    # удалить все встречи
    if re.search(r"(отмени|удали)\s+все\s+встреч", t):
        items = zoom.list_meetings("upcoming", 50)
        if not items:
            return "🗑️ Нет встреч для удаления."
        for m in items:
            zoom.delete_meeting(m["id"])
        return f"🗑️ Удалено {len(items)} встреч."

    # удалить по ID
    m = re.search(r"(отмени|удали)\s+встреч[ауые]?\s+(\d{6,})", t)
    if m:
        mid = m.group(2)
        zoom.delete_meeting(mid)
        return f"🗑️ Встреча **{mid}** отменена."

    # создание
    if re.search(r"\b(создай|создать|сделай|запланируй)\b.*\bвстреч[ауые]?\b", t) \
       or (("в зум" in t or "в zoom" in t) and "встреч" in t):
        when = _parse_when_ru(original_text, zoom.tz) or datetime.now(pytz.timezone(zoom.tz)).replace(minute=0, second=0, microsecond=0)
        topic = _extract_topic(original_text) or "Встреча"

        try:
            data = zoom.create_meeting(topic, when, 60)
        except requests.HTTPError as e:
            # вернём детальнейшую ошибку Zoom, если что-то с правами/почтой
            return f"❌ Zoom API: {e.response.status_code} {e.response.text}"

        when_str = when.astimezone(pytz.timezone(zoom.tz)).strftime("%d.%m.%Y %H:%M")
        pwd = f"\nПароль: {data.get('password')}" if data.get('password') else ""
        return (
            f"✅ Встреча «{topic}» создана на {when_str} ({zoom.tz}).\n"
            f"Ссылка: {data.get('join_url')}\nID: {data.get('id')}{pwd}"
        )

    return None
