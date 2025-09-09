import os
import time
import re
import requests
import pytz
from datetime import datetime, timedelta


class TelemostClient:
    """
    Клиент к Яндекс Телемост API.
    Создание встречи = создание комнаты (без темы/даты в самом API).
    Авторизация:
      - предпочтительно: YANDEX_OAUTH_TOKEN = y0_...
      - запасной вариант: YANDEX_CLIENT_ID / YANDEX_CLIENT_SECRET (client_credentials)
    """

    API_BASE = "https://cloud-api.yandex.net/v1/telemost-api"
    TOKEN_URL = "https://oauth.yandex.ru/token"

    def __init__(self, tz: str = "Europe/Moscow"):
        self.tz = tz

        # 1) статический OAuth-токен (желательно)
        self._static_token = os.getenv("YANDEX_OAUTH_TOKEN")

        # 2) client_credentials (как запасной вариант)
        self.client_id = os.getenv("YANDEX_CLIENT_ID")
        self.client_secret = os.getenv("YANDEX_CLIENT_SECRET")

        if not self._static_token and not (self.client_id and self.client_secret):
            raise ValueError(
                "Нужен YANDEX_OAUTH_TOKEN ИЛИ пара YANDEX_CLIENT_ID/SECRET "
                "(для потока client_credentials)."
            )

        self._access_token = None
        self._exp_ts = 0  # unix-время истечения токена

    # ---------- токен ----------
    def _get_access_token(self) -> str:
        # если задан статический токен — используем его
        if self._static_token:
            return self._static_token

        # если уже получали и ещё не просрочен — вернём кэш
        if self._access_token and time.time() < self._exp_ts - 60:
            return self._access_token

        # иначе берём новый по client_credentials
        r = requests.post(
            self.TOKEN_URL,
            data={
                "grant_type": "client_credentials",
                "client_id": self.client_id,
                "client_secret": self.client_secret,
            },
            timeout=20,
        )
        try:
            r.raise_for_status()
        except requests.HTTPError as e:
            raise RuntimeError(f"Не удалось получить OAuth-токен: HTTP {r.status_code} {r.text}") from e

        data = r.json()
        self._access_token = data["access_token"]
        self._exp_ts = time.time() + int(data.get("expires_in", 3600))
        return self._access_token

    def _headers(self):
        # Для Telemost достаточно Authorization: OAuth <token>
        return {
            "Authorization": f"OAuth {self._get_access_token()}",
            "Content-Type": "application/json",
        }

    # ---------- API ----------
    def create_meeting(self, *_, waiting_room_level: str = "PUBLIC") -> dict:
        """
        Создаёт комнату Телемоста.
        Позиционные параметры *_, которые передаются как (topic, when_dt, duration_min),
        намеренно игнорируются — в Telemost этих полей нет.
        Возвращает JSON, где как минимум: {"id": "...", "join_url": "..."}.
        """
        payload = {"waiting_room_level": waiting_room_level}
        r = requests.post(
            f"{self.API_BASE}/conferences",
            headers=self._headers(),
            json=payload,
            timeout=30,
        )
        try:
            r.raise_for_status()
        except requests.HTTPError as e:
            raise RuntimeError(f"Telemost create: HTTP {r.status_code}: {r.text}") from e
        return r.json()

    def get_meeting(self, conf_id: str) -> dict:
        r = requests.get(
            f"{self.API_BASE}/conferences/{conf_id}",
            headers=self._headers(),
            timeout=20,
        )
        try:
            r.raise_for_status()
        except requests.HTTPError as e:
            raise RuntimeError(f"Telemost get: HTTP {r.status_code}: {r.text}") from e
        return r.json()

    def delete_meeting(self, conf_id: str) -> bool:
        r = requests.delete(
            f"{self.API_BASE}/conferences/{conf_id}",
            headers=self._headers(),
            timeout=20,
        )
        if r.status_code not in (200, 204):
            raise RuntimeError(f"Telemost delete: HTTP {r.status_code}: {r.text}")
        return True

    # Публичной ручки "список встреч" у Telemost нет — возвращаем пустой список.
    def list_meetings(self) -> list[dict]:
        return []


# ---------------- ВСПОМОГАТЕЛЬНОЕ ----------------

def _extract_topic(text: str) -> str | None:
    """Тему достаём из кавычек или после слов 'тема/на тему/о теме'."""
    m = re.search(r"[«\"']([^\"'»]{3,120})[\"'»]", text or "")
    if m:
        return m.group(1).strip()
    m = re.search(r"(?:тема|на тему|о теме)\s*[:\-]?\s*(.+)$", text or "", flags=re.IGNORECASE)
    if m:
        return m.group(1).strip()
    return None


# ---------------- ИНТЕНТЫ ----------------

def handle_telemost_intents(tm: TelemostClient, text: str) -> str | None:
    """
    Простейшие интенты для Телемоста.
    Триггер — наличие слова 'телемост' в запросе.
    """
    original = text or ""
    t = original.lower().strip()

    # реагируем только если явно упомянули телемост
    if not re.search(r"\bтелемост\w*\b", t):
        return None

    # показать список — публичного списка нет
    if re.search(r"\b(список|мои|покажи)\s+встреч", t):
        return "🗓️ В публичном API Телемоста нет команды показать все встречи.\n" \
               "Я могу создать новую комнату или удалить по конкретному ID."

    # удалить все — не могу без списка
    if re.search(r"(отмени|удали)\s+все\s+встреч", t):
        return "🗑️ Не могу удалить все: в публичном API нет списка встреч. " \
               "Назови ID для удаления, например: «телемост удали встречу abc123»."

    # удалить по ID
    m = re.search(r"(отмени|удали)\s+встреч[ауые]?\s+([a-z0-9\-]{6,})", t)
    if m:
        cid = m.group(2)
        tm.delete_meeting(cid)
        return f"🗑️ Встреча Телемоста **{cid}** отменена."

    # создать
    if re.search(r"\b(создай|создать|сделай|запланируй)\b.*\bвстреч", t):
        # тема/время тут не используются API, но можем красиво показать тему пользователю
        topic = _extract_topic(original) or "Встреча"
        data = tm.create_meeting()  # создаём комнату
        link = data.get("join_url") or (data.get("links") or {}).get("join") or "—"
        return (
            f"✅ Создал встречу в Телемосте: «{topic}».\n"
            f"Ссылка: {link}\nID: {data.get('id')}"
        )

    # ничего не подошло
    return None
