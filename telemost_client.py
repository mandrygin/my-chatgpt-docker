import os
import time
import re
import json
import requests
import pytz
from datetime import datetime, timedelta


class TelemostClient:
    """
    Клиент к Яндекс Телемост API.
    Плюс локальное хранилище (JSON), чтобы держать тему/время и список встреч.
    """

    API_BASE = "https://cloud-api.yandex.net/v1/telemost-api"
    TOKEN_URL = "https://oauth.yandex.ru/token"

    def __init__(self, tz: str = "Europe/Moscow", store_path: str | None = None, calendar=None):
        self.tz = tz
        self.session = requests.Session()

        # 1) статический OAuth-токен (желательно)
        self._static_token = os.getenv("YANDEX_OAUTH_TOKEN")

        # 2) client_credentials (как запасной вариант)
        self.client_id = os.getenv("YANDEX_CLIENT_ID")
        self.client_secret = os.getenv("YANDEX_CLIENT_SECRET")

        # 3) опционально — организация (если требуется политиками)
        self.org_id = os.getenv("YANDEX_ORG_ID")

        # 4) локальное хранилище (JSON)
        self.store_path = store_path or os.getenv("TELEMOST_STORE", "/app/telemost_store.json")

        # 5) календарь (CalDAV) — может быть None
        self.calendar = calendar

        if not self._static_token and not (self.client_id and self.client_secret):
            raise ValueError(
                "Нужен YANDEX_OAUTH_TOKEN ИЛИ пара YANDEX_CLIENT_ID/SECRET "
                "(для потока client_credentials)."
            )

        self._access_token = None
        self._exp_ts = 0  # unix-время истечения токена

    # ---------- токен ----------
    def _get_access_token(self) -> str:
        if self._static_token:
            return self._static_token

        if self._access_token and time.time() < self._exp_ts - 60:
            return self._access_token

        r = self.session.post(
            self.TOKEN_URL,
            data={
                "grant_type": "client_credentials",
                "client_id": self.client_id,
                "client_secret": self.client_secret,
            },
            timeout=20,
        )
        r.raise_for_status()
        data = r.json()
        self._access_token = data["access_token"]
        self._exp_ts = time.time() + int(data.get("expires_in", 3600))
        return self._access_token

    def _headers(self):
        h = {
            "Authorization": f"OAuth {self._get_access_token()}",
            "Content-Type": "application/json",
        }
        if self.org_id:
            # пример: если потребуется заголовок организации
            # h["X-Org-ID"] = self.org_id
            pass
        return h

    # ---------- локальное хранилище ----------
    def _load_store(self) -> list[dict]:
        try:
            with open(self.store_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except FileNotFoundError:
            return []
        except Exception:
            return []

    def _save_store(self, items: list[dict]):
        tmp = self.store_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(items, f, ensure_ascii=False, indent=2)
        os.replace(tmp, self.store_path)

    def _append_record(self, rec: dict):
        items = self._load_store()
        items = [x for x in items if str(x.get("id")) != str(rec.get("id"))]
        items.append(rec)
        def keyfn(x):
            st = x.get("start_time")
            if not st:
                return ("~",)
            return (st,)
        items.sort(key=keyfn)
        self._save_store(items)

    def _delete_record(self, conf_id: str):
        items = self._load_store()
        items = [x for x in items if str(x.get("id")) != str(conf_id)]
        self._save_store(items)

    def _list_records(self) -> list[dict]:
        return self._load_store()

    def get_local_record(self, conf_id: str) -> dict | None:
        for it in self._list_records():
            if str(it.get("id")) == str(conf_id):
                return it
        return None

    # ---------- API ----------
    def create_meeting(self,
                       topic: str | None = None,
                       when_dt: datetime | None = None,
                       duration_min: int = 60,
                       waiting_room_level: str = "PUBLIC") -> dict:
        """
        Telemost не хранит тему/дату, поэтому:
          - создаём комнату на стороне API,
          - сохраняем локально (topic/when/duration) в JSON.
        """
        payload = {"waiting_room_level": waiting_room_level}
        r = self.session.post(
            f"{self.API_BASE}/conferences",
            headers=self._headers(),
            json=payload,
            timeout=30,
        )
        r.raise_for_status()
        data = r.json()

        tz = pytz.timezone(self.tz)
        start_iso = when_dt.astimezone(tz).isoformat() if (when_dt and when_dt.tzinfo) else (
            tz.localize(when_dt).isoformat() if when_dt else None
        )
        rec = {
            "id": data.get("id"),
            "join_url": data.get("join_url") or (data.get("links") or {}).get("join"),
            "topic": topic or "Встреча",
            "start_time": start_iso,
            "duration": duration_min,
            "tz": self.tz,
            "created_at": datetime.now(tz).isoformat(),
        }
        self._append_record(rec)

        out = dict(data)
        out.update({"topic": rec["topic"], "start_time": rec["start_time"], "duration": duration_min, "tz": self.tz})
        return out

    def get_meeting(self, conf_id: str) -> dict:
        r = self.session.get(
            f"{self.API_BASE}/conferences/{conf_id}",
            headers=self._headers(),
            timeout=20,
        )
        r.raise_for_status()
        data = r.json()
        for it in self._list_records():
            if str(it.get("id")) == str(conf_id):
                data.update({k: it.get(k) for k in ("topic", "start_time", "duration", "tz")})
                break
        return data

    def delete_meeting(self, conf_id: str) -> bool:
        r = self.session.delete(
            f"{self.API_BASE}/conferences/{conf_id}",
            headers=self._headers(),
            timeout=20,
        )
        if r.status_code not in (200, 204):
            r.raise_for_status()
        self._delete_record(conf_id)
        return True

    def list_meetings(self, upcoming_only: bool = True, limit: int = 20) -> list[dict]:
        items = self._list_records()
        tz = pytz.timezone(self.tz)
        now = datetime.now(tz)

        def parse_local_iso(s):
            try:
                return datetime.fromisoformat(s)
            except Exception:
                return None

        out = []
        for it in items:
            st = it.get("start_time")
            dt = parse_local_iso(st) if st else None
            if upcoming_only and dt and dt < now:
                continue
            out.append(it)

        def keyfn(x):
            st = x.get("start_time")
            return (st is None, st or "")
        out.sort(key=keyfn)
        return out[:limit]


# ---------------- ВСПОМОГАТЕЛЬНОЕ ----------------

def _fmt_tm_meetings(items: list[dict], tz_name: str) -> str:
    if not items:
        return "🗓️ Встреч нет."
    tz = pytz.timezone(tz_name)
    lines = []
    for i, m in enumerate(items, 1):
        topic = m.get("topic") or "Без темы"
        mid = m.get("id")
        when = "—"
        st = m.get("start_time")
        if st:
            try:
                dt = datetime.fromisoformat(st).astimezone(tz)
                when = dt.strftime("%d.%m.%Y %H:%M")
            except Exception:
                pass
        lines.append(f"{i}. {topic} • ID: {mid} • {when}")
    return "🗓️ Встречи (локально сохранённые):\n" + "\n".join(lines)


MONTHS_RU = {
    "январ": 1, "феврал": 2, "март": 3, "апрел": 4, "ма": 5,
    "июн": 6, "июл": 7, "август": 8, "сентябр": 9, "октябр": 10, "ноябр": 11, "декабр": 12
}

_SPACE_CLASS = r"[\s\u00A0\u202F\u2009]"

def _build_time_patterns():
    pats = []
    for h in range(24):
        hh = f"{h:02d}"
        h1 = str(h)
        for m in range(60):
            mm = f"{m:02d}"
            fmt_strs = [
                rf"\b{hh}:{mm}\b",
                rf"\b{h1}:{mm}\b" if h1 != hh else None,
                rf"\b{hh}-{mm}\b",
                rf"\b{h1}-{mm}\b" if h1 != hh else None,
                rf"\b{hh}\.{mm}\b",
                rf"\b{h1}\.{mm}\b" if h1 != hh else None,
                rf"\b{hh}{_SPACE_CLASS}+{mm}\b",
                rf"\b{h1}{_SPACE_CLASS}+{mm}\b" if h1 != hh else None,
            ]
            for s in fmt_strs:
                if s is None:
                    continue
                pats.append((re.compile(s, re.IGNORECASE), (h, m)))
    return pats

_TIME_PATTERNS = _build_time_patterns()

def _extract_time(text: str) -> tuple[int, int] | None:
    if not text:
        return None
    s = (text.replace("\u202f", " ").replace("\u00a0", " ").replace("\u2009", " "))
    for rx, (h, m) in _TIME_PATTERNS:
        if rx.search(s):
            return h, m
    m = re.search(r"\b(\d{1,2})\s*ч\b", s)
    if m:
        hh = int(m.group(1))
        if 0 <= hh <= 23:
            return hh, 0
    m = re.search(r"\bв\s+(\d{1,2})(?!\d)", s.lower())
    if m:
        hh = int(m.group(1))
        if 0 <= hh <= 23:
            return hh, 0
    return None

def _extract_topic(text: str) -> str | None:
    m = re.search(r"[«\"']([^\"'»]{3,120})[\"'»]", text or "")
    if m:
        return m.group(1).strip()
    m = re.search(r"(?:тема|на тему|о теме)\s*[:\-]?\s*(.+)$", text or "", flags=re.IGNORECASE)
    if m:
        return m.group(1).strip()
    return None

def _parse_when_ru(text: str, tz_name: str) -> datetime | None:
    s = (text or "")
    s = s.replace("\u202f", " ").replace("\u00a0", " ").replace("\u2009", " ")
    s = re.sub(r"\b(\d{1,2})[\s\.\-:](\d{2})\b", r"\1:\2", s)
    s = re.sub(r"\b(\d{1,2})\s*ч\b", r"\1:00", s)
    s_low = s.lower()

    tz = pytz.timezone(tz_name)
    now = datetime.now(tz)

    day = None
    if "послезавтра" in s_low:
        day = (now + timedelta(days=2)).replace(hour=0, minute=0, second=0, microsecond=0)
    elif "завтра" in s_low:
        day = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    elif "сегодня" in s_low:
        day = now.replace(hour=0, minute=0, second=0, microsecond=0)

    if day is None:
        m = re.search(r"\b(\d{1,2})\.(\d{1,2})(?:\.(\d{4}))?\b", s_low)
        if m:
            d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3) or now.year)
            try:
                day = tz.localize(datetime(y, mo, d))
            except ValueError:
                day = None

    if day is None:
        m = re.search(r"\b(\d{1,2})\s+([а-яё]+)(?:\s+(\d{4}))?\b", s_low)
        if m:
            d = int(m.group(1))
            mon_word = m.group(2)
            y = int(m.group(3) or now.year)
            mon = None
            for stem, num in MONTHS_RU.items():
                if mon_word.startswith(stem):
                    mon = num
                    break
            if mon:
                try:
                    day = tz.localize(datetime(y, mon, d))
                except ValueError:
                    day = None

    tm = _extract_time(s_low)

    if day and tm:
        dt = day.replace(hour=tm[0], minute=tm[1])
        if dt < now and re.search(r"\b\d{1,2}\.\d{1,2}\b", s_low) and not re.search(r"\b\d{1,2}\.\d{1,2}\.\d{4}\b", s_low):
            try:
                dt = dt.replace(year=dt.year + 1)
            except ValueError:
                pass
        return dt

    if day and not tm:
        return day.replace(hour=10, minute=0)

    if not day and tm:
        dt = now.replace(hour=tm[0], minute=tm[1], second=0, microsecond=0)
        if dt <= now:
            dt = dt + timedelta(days=1)
        return dt

    return None


# ---------------- ИНТЕНТЫ ----------------

def handle_telemost_intents(tm: TelemostClient, text: str) -> str | None:
    original = text or ""
    t = original.lower().strip()

    if not re.search(r"\bтелемост\w*\b", t):
        return None

    # список встреч (из локального хранилища)
    if re.search(r"\b(список|мои|покажи)\s+встреч", t):
        items = tm.list_meetings(upcoming_only=True, limit=20)
        return _fmt_tm_meetings(items, tm.tz)

    # удалить все
    if re.search(r"(отмени|удали)\s+все\s+встреч", t):
        items = tm.list_meetings(upcoming_only=False, limit=9999)
        if not items:
            return "🗑️ Нет встреч для удаления."
        cnt = 0
        for m in list(items):
            mid = m.get("id")
            try:
                tm.delete_meeting(str(mid))
                cnt += 1
            except Exception:
                pass
        return f"🗑️ Удалено {cnt} встреч."

    # удалить по ID
    m = re.search(r"(отмени|удали)\s+встреч[ауые]?\s+([a-z0-9\-]{6,})", t)
    if m:
        cid = m.group(2)
        tm.delete_meeting(cid)
        return f"🗑️ Встреча Телемоста **{cid}** отменена."

    # создать встречу
    if re.search(r"\b(создай|создать|сделай|запланируй)\b.*\b(встреч|комнат|конференц|созвон)", t):
        when = _parse_when_ru(original, tm.tz)
        topic = _extract_topic(original) or "Встреча"
        data = tm.create_meeting(topic=topic, when_dt=when, duration_min=60)
        link = data.get("join_url") or (data.get("links") or {}).get("join") or "—"

        # создаём событие в календаре (если он подключён и время распознано)
        cal_msg = ""
        if when and tm.calendar:
            try:
                tm.calendar.create_event(
                    summary=topic,
                    start_dt=when,
                    duration_min=60,
                    description=f"Ссылка для подключения: {link}",
                    url=link,
                    attendees=None
                )
                cal_msg = "\n📆 Событие создано в Яндекс.Календаре."
            except Exception as e:
                cal_msg = f"\n⚠️ Не удалось добавить в календарь: {e}"

        # на всякий случай оставим .ics ссылку (можно импортировать вручную)
        ics_line = ""
        if when:
            base = os.getenv("APP_URL", "http://localhost:8080")
            ics_url = f"{base}/telemost/{data.get('id')}.ics"
            ics_line = f'\n📅 Добавить в календарь: <a href="{ics_url}" target="_blank">скачать .ics</a>'

        when_str = ""
        if when:
            when_str = " на " + when.astimezone(pytz.timezone(tm.tz)).strftime("%d.%m.%Y %H:%M")

        return (
            f"✅ Создал встречу в Телемосте: «{topic}»{when_str} ({tm.tz}).\n"
            f'Ссылка: <a href="{link}" target="_blank">{link}</a>\n'
            f"ID: {data.get('id')}{cal_msg}{ics_line}"
        )

    return None
