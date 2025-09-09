import os
import time
import requests
from datetime import datetime, timedelta
import pytz


class TelemostClient:
    """
    Лёгкий клиент к Яндекс Телемост API.
    ВАЖНО: у Телемоста при создании встречи нет полей названия/даты/времени.
    Создаётся "комната", в ответе приходит join_url и id.

    Авторизация:
      - предпочтительно: переменная окружения YANDEX_OAUTH_TOKEN = y0_...
      - запасной вариант: пара YANDEX_CLIENT_ID / YANDEX_CLIENT_SECRET,
        тогда токен берётся по client_credentials.
    """

    API_BASE = "https://cloud-api.yandex.net/v1/telemost-api"
    TOKEN_URL = "https://oauth.yandex.ru/token"

    def __init__(self, tz: str = "Europe/Moscow"):
        self.tz = tz

        # 1) статический OAuth-токен (предпочтительно)
        self._static_token = os.getenv("YANDEX_OAUTH_TOKEN")

        # 2) fallback — получить токен по client_credentials
        self.client_id = os.getenv("YANDEX_CLIENT_ID")
        self.client_secret = os.getenv("YANDEX_CLIENT_SECRET")

        if not self._static_token and not (self.client_id and self.client_secret):
            raise ValueError(
                "Нужен YANDEX_OAUTH_TOKEN ИЛИ пара YANDEX_CLIENT_ID/SECRET "
                "(для потока client_credentials)."
            )

        # кэш для client_credentials
        self._access_token = None
        self._exp_ts = 0  # unix-время истечения токена

    # ---------- Внутреннее: токен ----------
    def _get_access_token(self) -> str:
        # Если явно задан статический токен — используем его
        if self._static_token:
            return self._static_token

        # Если ранее уже получали и он ещё жив — вернём из кэша
        if self._access_token and time.time() < self._exp_ts - 60:
            return self._access_token

        # Иначе берём новый по client_credentials
        if not (self.client_id and self.client_secret):
            raise RuntimeError("Нет ни статического токена, ни client_id/secret для получения токена.")

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
        # Согласно публичной доке Telemost — достаточно Authorization: OAuth <token>
        return {
            "Authorization": f"OAuth {self._get_access_token()}",
            "Content-Type": "application/json",
        }

    # ---------- API ----------
    def create_meeting(self, *_, waiting_room_level: str = "PUBLIC") -> dict:
        """
        Создаёт комнату Телемоста.
        Параметры *_, передаются для совместимости с твоими вызовами (topic, when_dt, duration_min),
        но в запросе НЕ используются — у Telemost этих полей нет.

        Возвращает JSON с полями как минимум: {"id": "...", "join_url": "..."}.
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
        """Получить информацию о конкретной комнате по ID."""
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
        """Удалить комнату по ID."""
        r = requests.delete(
            f"{self.API_BASE}/conferences/{conf_id}",
            headers=self._headers(),
            timeout=20,
        )
        if r.status_code not in (200, 204):
            raise RuntimeError(f"Telemost delete: HTTP {r.status_code}: {r.text}")
        return True

    # В публичной доке нет ручки "список всех встреч".
    # Оставляем метод-«заглушку», чтобы твой код не ломался.
    def list_meetings(self) -> list[dict]:
        """
        Телемост публично НЕ предоставляет endpoint для списка всех встреч.
        Возвращаем пустой список. Если нужен реальный список — храни ID у себя.
        """
        return []


# ===== Доп. утилиты (если понадобятся) =====
def to_local(dt: datetime | None, tz_name: str) -> datetime | None:
    """Перевод времени к локальной TZ (помощь для отображения)."""
    if dt is None:
        return None
    tz = pytz.timezone(tz_name)
    return dt.astimezone(tz) if dt.tzinfo else tz.localize(dt)
