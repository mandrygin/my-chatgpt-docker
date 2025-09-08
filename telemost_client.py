# telemost_client.py
import os
import re
from datetime import datetime, timedelta
import pytz
import requests
from typing import Optional, Tuple, List, Dict


class TelemostClient:
    """
    Минимальный клиент Яндекс.Телемост по OAuth.
    Обязательная переменная окружения: YANDEX_OAUTH_TOKEN (значение access_token).
    """
    API_BASE = "https://cloud-api.yandex.net/v1/telemost-api"

    def __init__(self, tz: str = "Europe/Moscow"):
        token = os.getenv("YANDEX_OAUTH_TOKEN")
        if not token:
            raise ValueError("YANDEX_OAUTH_TOKEN not set")
        self.tz = tz
        self._token = token

    def _headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"OAuth {self._token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def create_meeting(self, title: str, when_dt: datetime, duration_min: int = 60) -> Dict:
        """
        Создает комнату Телемоста (в API нет полей start_time/duration).
        Мы храним/показываем время только в ответе ассистента.
        """
        payload = {
            # PUBLIC / ORGANIZATION / ADMINS — кого пропускать без модерации
            "waiting_room_level": "PUBLIC"
            # Можно добавлять cohosts/live_stream и т.д., если нужно
        }
        url = f"{self.API_BASE}/conferences"
        r = requests.post(url, headers=self._headers(), json=payload, timeout=20)
        if not r.ok:
            raise requests.HTTPError(f"{r.status_code} {r.text}")
        data = r.json()

        # Добавим «косметику», чтобы дальше было удобно форматировать ответ
        data.setdefault("title", title or "Встреча")
        data.setdefault("when_local", when_dt.astimezone(pytz.timezone(self.tz)).isoformat())
        return data

    def list_meetings(self) -> List[Dict]:
        """Список конференций (комнат)."""
        url = f"{self.API_BASE}/conferences"
        r = requests.get(url, headers=self._headers(), timeout=20)
        if not r.ok:
            raise requests.HTTPError(f"{r.status_code} {r.text}")
        data = r.json()
        return data.get("conferences") or data.get("items") or data or []

    def delete_meeting(self, meeting_id: str) -> bool:
        """Удаление комнаты по ID."""
        url = f"{self.API_BASE}/conferences/{meeting_id}"
        r = requests.delete(url, headers=self._headers(), timeout=20)
        if r.status_code not in (200, 204):
            raise requests.HTTPError(f"{r.status_code} {r.text}")
        return True


# ===== Парсинг времени (упрощенный, как в zoom_client) =====

_MONTHS_RU = {
    "январ": 1, "феврал": 2, "март": 3, "апрел": 4, "ма": 5,
    "июн": 6, "июл": 7, "август": 8, "сентябр": 9, "октябр": 10, "ноябр": 11, "декабр": 12
}
_SPACE_CLASS = r"[\s\u00A0\u202F\u2009]"

def _extract_time(text: str) -> Optional[Tuple[int, int]]:
    s = (text or "").lower().replace("\u202f", " ").replace("\u00a0", " ").replace("\u2009", " ")
    m = re.search(rf"\b(\d{{1,2}})[:\-\.{_SPACE_CLASS}](\d{{2}})\b(?!\.)", s)
    if m:
        h, mm = int(m.group(1)), int(m.group(2))
        if 0 <= h <= 23 and 0 <= mm <= 59:
            return h, mm
    m = re.search(r"\b(\d{1,2})\s*ч\b", s)
    if m:
        h = int(m.group(1))
        if 0 <= h <= 23:
            return h, 0
    m = re.search(r"\bв\s+(\d{1,2})(?!\d)", s)
    if m:
        h = int(m.group(1))
        if 0 <= h <= 23:
            return h, 0
    return None

def _parse_explicit_date(text: str, now: datetime) -> Optional[datetime]:
    t = (text or "").lower()
    m = re.search(r"\b(\d{1,2})\.(\d{1,2})(?:\.(\d{4}))?\b", t)
    if m:
        d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3) or now.year)
        return now.replace(year=y, month=mo, day=d, hour=0, minute=0, second=0, microsecond=0)
    m = re.search(r"\b(\d{1,2})\s+([а-яё]+)(?:\s+(\d{4}))?\b", t)
    if m:
        d, mon_word, y = int(m.group(1)), m.group(2), int(m.group(3) or now.year)
        mon = None
        for stem, num in _MONTHS_RU.items():
            if mon_word.startswith(stem):
                mon = num; break
        if mon:
            return now.replace(year=y, month=mon, day=d, hour=0, minute=0, second=0, microsecond=0)
    if "послезавтра" in t: return now + timedelta(days=2)
    if "завтра" in t:      return now + timedelta(days=1)
    if "сегодня" in t:     return now
    return None

def _parse_when(text: str, tz_name: str) -> Optional[datetime]:
    tz = pytz.timezone(tz_name)
    now = datetime.now(tz)
    day = _parse_explicit_date(text, now)
    tm  = _extract_time(text)
    if day and tm: return day.replace(hour=tm[0], minute=tm[1], second=0, microsecond=0)
    if day and not tm: return day.replace(hour=10, minute=0, second=0, microsecond=0)
    if not day and tm:
        dt = now.replace(hour=tm[0], minute=tm[1], second=0, microsecond=0)
        if dt <= now: dt = dt + timedelta(days=1)
        return dt
    return None

def _fmt(items: List[Dict], tz_name: str) -> str:
    if not items:
        return "🗓️ Встреч нет."
    tz = pytz.timezone(tz_name)
    out = ["🗓️ Комнаты Телемоста:"]
    for i, m in enumerate(items, 1):
        # Телемост не хранит время начала — покажем то, что есть
        # (если в ответе от API появится поле со временем — красиво отформатируем)
        topic = m.get("title") or m.get("topic") or "Без темы"
        mid   = m.get("id") or m.get("meeting_id") or "—"
        join  = m.get("join_url") or m.get("link") or m.get("url") or ""
        out.append(f"{i}. {topic} • ID: {mid} • {join}")
    return "\n".join(out)


# ===== Интенты ассистента (строго по слову «телемост») =====

def handle_telemost_intents(tm: TelemostClient, text: str) -> Optional[str]:
    t = (text or "").lower().strip()

    # Гейт: без слова "телемост" не реагируем
    if "телемост" not in t:
        return None

    # список
    if re.search(r"\b(список|мои|покажи)\s+встреч", t):
        items = tm.list_meetings()
        return _fmt(items, tm.tz)

    # удалить по ID
    m = re.search(r"(отмени|удали)\s+встреч[ауые]?\s+([A-Za-z0-9_-]+)", t)
    if m:
        mid = m.group(2)
        tm.delete_meeting(mid)
        return f"🗑️ Встреча **{mid}** удалена (Телемост)."

    # создать
    if re.search(r"\b(создай|создать|сделай|запланируй)\b.*\bвстреч[ауые]?\b", t):
        when = _parse_when(text, tm.tz)
        title = "Встреча"
        if not when:
            return "Не понял дату/время. Пример: «создай встречу в телемосте сегодня в 15 45»."
        data = tm.create_meeting(title, when, 60)
        join = data.get("join_url") or data.get("link") or data.get("url") or "—"
        mid  = data.get("id") or data.get("meeting_id") or "—"
        when_str = when.strftime("%d.%m.%Y %H:%M")
        return (
            f"✅ Встреча «{title}» на {when_str}.<br>"
            f"🔗 <a href=\"{join}\" target=\"_blank\" rel=\"noopener\">Перейти в Телемост</a><br>"
            f"ID: {mid}"
        )

    return None
