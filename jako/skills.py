"""Jako's skills: each is a tool Claude can call. All read-only except your own reminders, to-dos,
facts and marking a job as applied. Jako never sends email, never deletes mail and never applies to jobs."""
from __future__ import annotations

import email
import imaplib
import re
from datetime import datetime, timedelta
from email.header import decode_header, make_header

import requests

from .config import JakoConfig
from .memory import Memory

WEATHER_CODES = {0: "clear", 1: "mostly clear", 2: "partly cloudy", 3: "cloudy", 45: "fog", 48: "fog",
                 51: "light drizzle", 53: "drizzle", 55: "heavy drizzle", 61: "light rain", 63: "rain",
                 65: "heavy rain", 66: "freezing rain", 67: "freezing rain", 71: "light snow", 73: "snow",
                 75: "heavy snow", 77: "snow grains", 80: "rain showers", 81: "rain showers",
                 82: "heavy rain showers", 85: "snow showers", 86: "snow showers", 95: "thunderstorm",
                 96: "thunderstorm with hail", 99: "thunderstorm with hail"}

TOOLS = [
    {"name": "get_weather", "description": "Weather now and forecast for the user's city (or another city).",
     "input_schema": {"type": "object", "properties": {
         "days": {"type": "integer", "description": "forecast days, 1-7", "default": 1},
         "city": {"type": "string", "description": "only if the user names another city"}}}},
    {"name": "add_reminder", "description": "Set a reminder. Convert relative times ('in 2 hours', "
     "'tomorrow at 9') to an exact local date-time yourself, using the current time given in the system prompt.",
     "input_schema": {"type": "object", "required": ["text", "when"], "properties": {
         "text": {"type": "string"}, "when": {"type": "string", "description": "local time, YYYY-MM-DDTHH:MM"}}}},
    {"name": "list_reminders", "description": "Open reminders.", "input_schema": {"type": "object", "properties": {}}},
    {"name": "cancel_reminder", "description": "Cancel a reminder by id.",
     "input_schema": {"type": "object", "required": ["id"], "properties": {"id": {"type": "integer"}}}},
    {"name": "add_todo", "description": "Add an item to the to-do list.",
     "input_schema": {"type": "object", "required": ["text"], "properties": {"text": {"type": "string"}}}},
    {"name": "list_todos", "description": "Open to-dos.", "input_schema": {"type": "object", "properties": {}}},
    {"name": "complete_todo", "description": "Mark a to-do as done by id.",
     "input_schema": {"type": "object", "required": ["id"], "properties": {"id": {"type": "integer"}}}},
    {"name": "remember", "description": "Save a lasting fact or preference the user tells you about themselves.",
     "input_schema": {"type": "object", "required": ["fact"], "properties": {"fact": {"type": "string"}}}},
    {"name": "forget", "description": "Forget saved facts that contain this text.",
     "input_schema": {"type": "object", "required": ["text"], "properties": {"text": {"type": "string"}}}},
    {"name": "jobs_overview", "description": "Job hunter status: open jobs, prepared, applied, interviews, "
     "and the best open jobs with score and id.",
     "input_schema": {"type": "object", "properties": {"top": {"type": "integer", "default": 5}}}},
    {"name": "job_details", "description": "Details of one job by id (first 8 characters) or company name.",
     "input_schema": {"type": "object", "required": ["query"], "properties": {"query": {"type": "string"}}}},
    {"name": "mark_job", "description": "Change a job's status, e.g. applied, interview, rejected, offer, skipped. "
     "Only when the user clearly says so.",
     "input_schema": {"type": "object", "required": ["query", "status"], "properties": {
         "query": {"type": "string"}, "status": {"type": "string",
                                                 "enum": ["applied", "interview", "rejected", "offer", "skipped"]}}}},
    {"name": "read_email", "description": "Read-only: recent or unread Gmail messages (sender, subject, short "
     "text), optionally filtered by a Gmail search like 'from:amazon' or 'interview'.",
     "input_schema": {"type": "object", "properties": {
         "query": {"type": "string", "description": "Gmail search words, optional"},
         "unread_only": {"type": "boolean", "default": True}, "limit": {"type": "integer", "default": 8}}}},
    {"name": "calendar", "description": "Events from the user's Google Calendar for the next days.",
     "input_schema": {"type": "object", "properties": {"days": {"type": "integer", "default": 1}}}},
]


class Skills:
    def __init__(self, cfg: JakoConfig, memory: Memory, session=None):
        self.cfg, self.mem = cfg, memory
        self.http = session or requests.Session()

    def run(self, name: str, args: dict) -> str:
        fn = getattr(self, name, None)
        if not fn or name.startswith("_") or name == "run":
            return f"Unknown tool {name}"
        try:
            return fn(**(args or {}))
        except Exception as exc:                      # tell Claude, so it can explain or retry
            return f"Error in {name}: {exc}"

    def _now(self) -> datetime:
        return datetime.now(self.cfg.tz)

    # ----------------------------------------------------------------- weather
    def get_weather(self, days: int = 1, city: str = "") -> str:
        lat, lon, place = self.cfg.latitude, self.cfg.longitude, self.cfg.city
        if city and city.lower() != self.cfg.city.lower():
            geo = self.http.get("https://geocoding-api.open-meteo.com/v1/search",
                                params={"name": city, "count": 1}, timeout=15).json().get("results") or []
            if not geo:
                return f"I couldn't find {city}."
            lat, lon, place = geo[0]["latitude"], geo[0]["longitude"], geo[0]["name"]
        data = self.http.get("https://api.open-meteo.com/v1/forecast", timeout=15, params={
            "latitude": lat, "longitude": lon, "timezone": self.cfg.timezone, "forecast_days": max(1, min(7, days)),
            "current": "temperature_2m,apparent_temperature,weather_code,wind_speed_10m",
            "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max"}).json()
        cur = data.get("current", {})
        lines = [f"{place} now: {cur.get('temperature_2m')}°C (feels {cur.get('apparent_temperature')}°C), "
                 f"{WEATHER_CODES.get(cur.get('weather_code'), 'unknown')}, wind {cur.get('wind_speed_10m')} km/h."]
        d = data.get("daily", {})
        for i, day in enumerate(d.get("time", [])):
            lines.append(f"{day}: {WEATHER_CODES.get(d['weather_code'][i], '?')}, {d['temperature_2m_min'][i]}"
                         f"–{d['temperature_2m_max'][i]}°C, rain chance {d['precipitation_probability_max'][i]}%.")
        return "\n".join(lines)

    # ----------------------------------------------------------------- reminders & to-dos
    def add_reminder(self, text: str, when: str) -> str:
        due = datetime.fromisoformat(when)
        if due.tzinfo is None:
            due = due.replace(tzinfo=self.cfg.tz)
        if due < self._now() - timedelta(minutes=1):
            return f"That time ({due:%d.%m. %H:%M}) is in the past. Ask the user for a new time."
        rid = self.mem.add_reminder(text, due)
        return f"Reminder {rid} set for {due.astimezone(self.cfg.tz):%A %d.%m. at %H:%M}: {text}"

    def list_reminders(self) -> str:
        rows = self.mem.open_reminders()
        if not rows:
            return "No open reminders."
        return "\n".join(f"{r['id']}: {datetime.fromisoformat(r['due_at']).astimezone(self.cfg.tz):%a %d.%m. %H:%M}"
                         f" – {r['text']}" for r in rows)

    def cancel_reminder(self, id: int) -> str:
        return f"Reminder {id} cancelled." if self.mem.finish_reminder(id) else f"No open reminder {id}."

    def add_todo(self, text: str) -> str:
        return f"Added to-do {self.mem.add_todo(text)}: {text}"

    def list_todos(self) -> str:
        rows = self.mem.open_todos()
        return "\n".join(f"{r['id']}: {r['text']}" for r in rows) if rows else "The to-do list is empty."

    def complete_todo(self, id: int) -> str:
        return f"To-do {id} done." if self.mem.finish_todo(id) else f"No open to-do {id}."

    def remember(self, fact: str) -> str:
        self.mem.remember(fact)
        return "Saved."

    def forget(self, text: str) -> str:
        return f"Forgot {self.mem.forget(text)} fact(s)."

    # ----------------------------------------------------------------- job hunter
    def _store(self):
        from jobhunter.storage import Store
        return Store(self.cfg.jobs_database_url or f"sqlite:///{self.cfg.data_dir / 'jobs.db'}")

    def _find_job(self, store, query: str):
        rows = store.list(limit=100_000, include_skipped=True)
        q = query.strip().lower()
        exact = [r for r in rows if r["uid"].startswith(q)]
        if exact:
            return exact[0]
        hits = [r for r in rows if q in (r["company"] or "").lower() or q in (r["title"] or "").lower()]
        hits.sort(key=lambda r: (r["status"] not in ("new", "prepared"), -(r["score"] or 0)))
        return hits[0] if hits else None

    def jobs_overview(self, top: int = 5) -> str:
        rows = self._store().list(limit=100_000, include_skipped=True)
        count = {}
        for r in rows:
            count[r["status"]] = count.get(r["status"], 0) + 1
        open_jobs = sorted([r for r in rows if r["status"] in ("new", "prepared")
                            and len(r.get("description") or "") >= 100], key=lambda r: -(r["score"] or 0))
        today = self._now().date().isoformat()
        applied_today = sum(1 for r in rows if (r.get("applied_at") or "").startswith(today))
        lines = [f"Open: {count.get('new', 0) + count.get('prepared', 0)}, applied: {count.get('applied', 0)} "
                 f"(today {applied_today}), interviews: {count.get('interview', 0)}, offers: {count.get('offer', 0)}."]
        for r in open_jobs[: max(1, min(15, top))]:
            lines.append(f"{r['uid'][:8]} | {r['score']} | {r['company']} – {r['title']} ({r['location']})")
        return "\n".join(lines)

    def job_details(self, query: str) -> str:
        row = self._find_job(self._store(), query)
        if not row:
            return f"No job matching '{query}'."
        return (f"{row['uid'][:8]} | {row['company']} – {row['title']} | {row['location']} | score {row['score']} | "
                f"status {row['status']}\nApply: {row.get('apply_url') or row['url']}\n"
                f"Ad: {(row.get('description') or '')[:1500]}")

    def mark_job(self, query: str, status: str) -> str:
        store = self._store()
        row = self._find_job(store, query)
        if not row:
            return f"No job matching '{query}'."
        if status == "applied":
            store.mark_applied(row["uid"])
        else:
            store.update(row["uid"], status=status)
        return f"{row['company']} – {row['title']} is now '{status}'."

    # ----------------------------------------------------------------- email (read-only)
    def read_email(self, query: str = "", unread_only: bool = True, limit: int = 8) -> str:
        if not (self.cfg.gmail_address and self.cfg.gmail_password):
            return "Gmail is not set up (GMAIL_ADDRESS / GMAIL_APP_PASSWORD missing)."
        out = []
        with imaplib.IMAP4_SSL("imap.gmail.com") as imap:
            imap.login(self.cfg.gmail_address, self.cfg.gmail_password)
            imap.select("INBOX", readonly=True)                 # read-only: nothing is marked as read
            terms = []
            if unread_only:
                terms.append("is:unread")
            if query:
                terms.append(query)
            terms.append("newer_than:7d")
            typ, data = imap.search(None, "X-GM-RAW", f'"{" ".join(terms)}"')
            ids = (data[0].split() if typ == "OK" and data and data[0] else [])[-max(1, min(20, limit)):]
            for mid in reversed(ids):
                typ, msg_data = imap.fetch(mid, "(BODY.PEEK[])")
                if typ != "OK":
                    continue
                msg = email.message_from_bytes(msg_data[0][1])
                text = _plain_text(msg)
                out.append(f"From: {_hdr(msg['From'])}\nSubject: {_hdr(msg['Subject'])}\nDate: {msg['Date']}\n"
                           f"{text[:400]}")
        return "\n\n".join(out) if out else "No matching emails."

    # ----------------------------------------------------------------- calendar (read-only)
    def calendar(self, days: int = 1) -> str:
        if not self.cfg.calendar_ics_url:
            return ("Google Calendar is not connected yet. The user can add the calendar's secret iCal address "
                    "as GOOGLE_CALENDAR_ICS_URL in .env.")
        import icalendar
        import recurring_ical_events
        raw = self.http.get(self.cfg.calendar_ics_url, timeout=20).content
        cal = icalendar.Calendar.from_ical(raw)
        start = self._now().replace(hour=0, minute=0, second=0, microsecond=0)
        end = start + timedelta(days=max(1, min(14, days)))
        events = recurring_ical_events.of(cal).between(start, end)
        lines = []
        for ev in sorted(events, key=lambda e: str(e.get("DTSTART").dt)):
            dt = ev.get("DTSTART").dt
            when = dt.astimezone(self.cfg.tz).strftime("%a %d.%m. %H:%M") if isinstance(dt, datetime) \
                else dt.strftime("%a %d.%m. (all day)")
            loc = f" @ {ev.get('LOCATION')}" if ev.get("LOCATION") else ""
            lines.append(f"{when} – {ev.get('SUMMARY')}{loc}")
        return "\n".join(lines) if lines else "No events."


def _hdr(value) -> str:
    try:
        return str(make_header(decode_header(value or "")))
    except Exception:
        return value or ""


def _plain_text(msg) -> str:
    parts = msg.walk() if msg.is_multipart() else [msg]
    for part in parts:
        if part.get_content_type() == "text/plain":
            payload = part.get_payload(decode=True) or b""
            return re.sub(r"\s+", " ", payload.decode(part.get_content_charset() or "utf-8", "replace")).strip()
    for part in (msg.walk() if msg.is_multipart() else [msg]):
        if part.get_content_type() == "text/html":
            from bs4 import BeautifulSoup
            html = (part.get_payload(decode=True) or b"").decode(part.get_content_charset() or "utf-8", "replace")
            return re.sub(r"\s+", " ", BeautifulSoup(html, "html.parser").get_text(" ")).strip()
    return ""
