"""Jako's memory: reminders, to-dos, facts about you and recent conversation, in one SQLite file."""
from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = """
create table if not exists reminders (id integer primary key, text text not null, due_at text not null,
                                      done integer default 0, created_at text);
create table if not exists todos (id integer primary key, text text not null, done integer default 0,
                                  created_at text);
create table if not exists facts (id integer primary key, text text not null unique, created_at text);
create table if not exists messages (id integer primary key, channel text, role text, content text, at text);
create table if not exists outbox (id integer primary key, text text, at text, spoken integer default 0);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Memory:
    def __init__(self, path: Path | str):
        self.db = sqlite3.connect(str(path), check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.lock = threading.Lock()
        with self.lock:
            self.db.executescript(SCHEMA)
        self._scrub_numbers()

    def _scrub_numbers(self) -> None:
        """Mask phone numbers that an older version kept in the conversation."""
        from .whatsapp import mask_numbers
        for r in self._q("select id, content from messages"):
            clean = mask_numbers(r["content"])
            if clean != r["content"]:
                self._q("update messages set content = ? where id = ?", (clean, r["id"]))

    def _q(self, sql: str, args: tuple = ()) -> list[dict]:
        with self.lock:
            cur = self.db.execute(sql, args)
            self.db.commit()
            return [dict(r) for r in cur.fetchall()]

    # reminders (due_at is an aware ISO datetime)
    def add_reminder(self, text: str, due_at: datetime) -> int:
        self._q("insert into reminders (text, due_at, created_at) values (?, ?, ?)",
                (text, due_at.astimezone(timezone.utc).isoformat(timespec="seconds"), _now()))
        return self._q("select max(id) as id from reminders")[0]["id"]

    def open_reminders(self) -> list[dict]:
        return self._q("select * from reminders where done = 0 order by due_at")

    def due_reminders(self, now: datetime) -> list[dict]:
        return self._q("select * from reminders where done = 0 and due_at <= ? order by due_at",
                       (now.astimezone(timezone.utc).isoformat(timespec="seconds"),))

    def finish_reminder(self, rid: int) -> bool:
        before = self._q("select id from reminders where id = ? and done = 0", (rid,))
        self._q("update reminders set done = 1 where id = ?", (rid,))
        return bool(before)

    # to-dos
    def add_todo(self, text: str) -> int:
        self._q("insert into todos (text, created_at) values (?, ?)", (text, _now()))
        return self._q("select max(id) as id from todos")[0]["id"]

    def open_todos(self) -> list[dict]:
        return self._q("select * from todos where done = 0 order by id")

    def finish_todo(self, tid: int) -> bool:
        before = self._q("select id from todos where id = ? and done = 0", (tid,))
        self._q("update todos set done = 1 where id = ?", (tid,))
        return bool(before)

    # facts Jako should remember about you
    def remember(self, text: str) -> None:
        self._q("insert or ignore into facts (text, created_at) values (?, ?)", (text.strip(), _now()))

    def forget(self, text: str) -> int:
        rows = self._q("select id from facts where text like ?", (f"%{text}%",))
        self._q("delete from facts where text like ?", (f"%{text}%",))
        return len(rows)

    def facts(self) -> list[str]:
        return [r["text"] for r in self._q("select text from facts order by id")]

    # conversation history per channel (laptop / telegram)
    def add_message(self, channel: str, role: str, content) -> None:
        from .whatsapp import mask_numbers                  # phone numbers are never kept in the conversation
        if isinstance(content, str):
            content = mask_numbers(content)
        self._q("insert into messages (channel, role, content, at) values (?, ?, ?, ?)",
                (channel, role, json.dumps(content), _now()))

    def history(self, channel: str, limit: int = 12) -> list[dict]:
        rows = self._q("select role, content from messages where channel = ? order by id desc limit ?",
                       (channel, limit))
        msgs = [{"role": r["role"], "content": json.loads(r["content"])} for r in reversed(rows)]
        while msgs and msgs[0]["role"] != "user":          # history must start with the user
            msgs.pop(0)
        return msgs

    def clear_history(self, channel: str) -> None:
        self._q("delete from messages where channel = ?", (channel,))

    # things the laptop page should say out loud (reminders, briefing)
    def say_later(self, text: str) -> None:
        self._q("insert into outbox (text, at) values (?, ?)", (text, _now()))

    def take_outbox(self) -> list[str]:
        rows = self._q("select id, text from outbox where spoken = 0 order by id")
        for r in rows:
            self._q("update outbox set spoken = 1 where id = ?", (r["id"],))
        return [r["text"] for r in rows]
