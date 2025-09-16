# yandex_calendar.py
import os
import pytz
from datetime import datetime, timedelta
from caldav import DAVClient
from icalendar import Calendar, Event, vCalAddress, vText

class YaCalClient:
    def __init__(self, url: str, username: str, password: str,
                 tz: str = "Europe/Moscow", calendar_name: str | None = None):
        self.url = url.rstrip("/")
        self.username = username
        self.password = password
        self.tz = tz
        self.calendar_name = calendar_name
        self._client = DAVClient(self.url, username=self.username, password=self.password)
        self._principal = self._client.principal()
        self._calendar = None

    @classmethod
    def from_env(cls, tz: str):
        url  = os.getenv("YXCAL_URL", "https://caldav.yandex.ru")
        user = os.getenv("YXCAL_USER")           # например api-bot@company.ru
        pwd  = os.getenv("YXCAL_PASSWORD")       # пароль приложения
        name = os.getenv("YXCAL_CALENDAR_NAME")  # опционально: имя календаря
        if not (user and pwd):
            return None
        return cls(url, user, pwd, tz=tz, calendar_name=name)

    def _ensure_calendar(self):
        if self._calendar is not None:
            return
        cals = self._principal.calendars()
        if not cals:
            raise RuntimeError("CalDAV: у пользователя нет календарей")
        if self.calendar_name:
            for c in cals:
                if (c.get_properties([("DAV:", "displayname")]).get(("DAV:", "displayname")) or "").strip() == self.calendar_name:
                    self._calendar = c
                    break
            if self._calendar is None:
                # не нашли по имени — берём первый
                self._calendar = cals[0]
        else:
            self._calendar = cals[0]

    def create_event(self, summary: str, start_dt: datetime,
                     duration_min: int = 60, description: str | None = None,
                     url: str | None = None, attendees: list[str] | None = None):
        """
        Создаёт VEVENT в выбранном календаре.
        start_dt — локальное время (с TZ или naive); мы проставим TZ.
        """
        self._ensure_calendar()
        tz = pytz.timezone(self.tz)
        if start_dt.tzinfo is None:
            start_dt = tz.localize(start_dt)
        end_dt = start_dt + timedelta(minutes=duration_min)

        cal = Calendar()
        cal.add("prodid", "-//ISE//help-gpt//RU")
        cal.add("version", "2.0")

        ev = Event()
        ev.add("summary", summary or "Встреча")
        ev.add("dtstart", start_dt)
        ev.add("dtend", end_dt)
        if description:
            ev.add("description", description)
        if url:
            ev.add("url", url)

        # Участники (опционально)
        if attendees:
            for a in attendees:
                addr = vCalAddress(f"MAILTO:{a}")
                addr.params["cn"] = vText(a)
                addr.params["role"] = vText("REQ-PARTICIPANT")
                ev.add("attendee", addr, encode=0)

        cal.add_component(ev)
        self._calendar.add_event(cal.to_ical())
        return True
