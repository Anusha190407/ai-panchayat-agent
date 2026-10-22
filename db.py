"""SQLite storage for tickets, citizen reports, agent events and notifications."""
from __future__ import annotations

import os
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS tickets (
    id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    status TEXT NOT NULL,
    category TEXT NOT NULL,
    title TEXT NOT NULL,
    summary_en TEXT NOT NULL DEFAULT '',
    summary_ta TEXT NOT NULL DEFAULT '',
    ward INTEGER NOT NULL DEFAULT 0,
    location TEXT NOT NULL DEFAULT '',
    priority TEXT NOT NULL,
    priority_reason TEXT NOT NULL DEFAULT '',
    officer_id TEXT NOT NULL,
    officer_instructions TEXT NOT NULL DEFAULT '',
    sla_due_at TEXT NOT NULL,
    escalation_level INTEGER NOT NULL DEFAULT 0,
    escalated_at TEXT,
    report_count INTEGER NOT NULL DEFAULT 1,
    language TEXT NOT NULL DEFAULT 'en',
    emergency INTEGER NOT NULL DEFAULT 0,
    engine TEXT NOT NULL DEFAULT '',
    resolution_note TEXT NOT NULL DEFAULT '',
    resolved_at TEXT
);
CREATE TABLE IF NOT EXISTS reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticket_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    citizen_name TEXT NOT NULL DEFAULT '',
    phone TEXT NOT NULL DEFAULT '',
    text TEXT NOT NULL,
    language TEXT NOT NULL DEFAULT 'en',
    has_photo INTEGER NOT NULL DEFAULT 0,
    photo_observation TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticket_id TEXT NOT NULL,
    ts TEXT NOT NULL,
    actor TEXT NOT NULL,
    action TEXT NOT NULL,
    detail TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS notifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticket_id TEXT NOT NULL,
    ts TEXT NOT NULL,
    channel TEXT NOT NULL,
    recipient TEXT NOT NULL,
    language TEXT NOT NULL DEFAULT 'en',
    message TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_reports_ticket ON reports(ticket_id);
CREATE INDEX IF NOT EXISTS idx_events_ticket ON events(ticket_id);
CREATE INDEX IF NOT EXISTS idx_notifications_ticket ON notifications(ticket_id);
"""

TICKET_FIELDS = (
    "id", "created_at", "updated_at", "status", "category", "title", "summary_en", "summary_ta",
    "ward", "location", "priority", "priority_reason", "officer_id", "officer_instructions",
    "sla_due_at", "escalation_level", "escalated_at", "report_count", "language", "emergency", "engine",
    "resolution_note", "resolved_at",
)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


def parse_ts(ts: str) -> datetime:
    dt = datetime.fromisoformat(ts)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def sla_due(created_at: datetime, priority: str) -> datetime:
    return created_at + timedelta(hours=config.PRIORITIES[priority]["sla_hours"])


class Store:
    """Small repository over one SQLite file. Safe to share across threads."""

    def __init__(self, path: str):
        self.path = path
        folder = os.path.dirname(path)
        if folder:
            os.makedirs(folder, exist_ok=True)
        self._lock = threading.RLock()
        self.lock = self._lock  # for multi-step read-modify-write sequences
        with self._conn() as c:
            c.execute("PRAGMA journal_mode=WAL")
            c.executescript(SCHEMA)
            cols = {r["name"] for r in c.execute("PRAGMA table_info(tickets)")}
            if "escalated_at" not in cols:  # databases created by an earlier version
                c.execute("ALTER TABLE tickets ADD COLUMN escalated_at TEXT")

    @contextmanager
    def _conn(self):
        conn = sqlite3.connect(self.path, timeout=15)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    # ------------------------------------------------------------------ clock
    def clock_offset_hours(self) -> float:
        with self._conn() as c:
            row = c.execute("SELECT value FROM settings WHERE key='clock_offset_hours'").fetchone()
        return float(row["value"]) if row else 0.0

    def set_clock_offset(self, hours: float) -> None:
        with self._lock, self._conn() as c:
            c.execute(
                "INSERT INTO settings(key, value) VALUES('clock_offset_hours', ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (str(float(hours)),),
            )

    def now(self) -> datetime:
        """Demo clock: real time plus the fast-forward offset used to demo SLAs."""
        return utcnow() + timedelta(hours=self.clock_offset_hours())

    # ---------------------------------------------------------------- tickets
    def next_ticket_id(self) -> str:
        with self._conn() as c:
            rows = c.execute("SELECT id FROM tickets").fetchall()
        numbers = [int(r["id"].split("-")[1]) for r in rows if r["id"].startswith("AP-") and r["id"].split("-")[1].isdigit()]
        return f"AP-{(max(numbers) + 1) if numbers else 1001}"

    def insert_ticket(self, ticket: dict) -> dict:
        data = {k: ticket.get(k) for k in TICKET_FIELDS}
        data.setdefault("escalation_level", 0)
        for key, default in (("escalation_level", 0), ("report_count", 1), ("emergency", 0), ("resolution_note", ""),
                             ("summary_en", ""), ("summary_ta", ""), ("priority_reason", ""), ("officer_instructions", ""),
                             ("engine", ""), ("language", "en"), ("location", ""), ("ward", 0)):
            if data.get(key) is None:
                data[key] = default
        data["emergency"] = int(bool(data["emergency"]))
        cols = ", ".join(data)
        marks = ", ".join("?" for _ in data)
        with self._lock, self._conn() as c:
            c.execute(f"INSERT INTO tickets ({cols}) VALUES ({marks})", tuple(data.values()))
        return self.get_ticket(data["id"])

    def create_ticket(self, ticket: dict) -> dict:
        """Allocate the next ticket ID and insert, atomically."""
        with self._lock:
            return self.insert_ticket({**ticket, "id": self.next_ticket_id()})

    def update_ticket(self, ticket_id: str, **fields) -> dict | None:
        fields = {k: v for k, v in fields.items() if k in TICKET_FIELDS and k != "id"}
        if not fields:
            return self.get_ticket(ticket_id)
        if "emergency" in fields:
            fields["emergency"] = int(bool(fields["emergency"]))
        sets = ", ".join(f"{k}=?" for k in fields)
        with self._lock, self._conn() as c:
            c.execute(f"UPDATE tickets SET {sets} WHERE id=?", (*fields.values(), ticket_id))
        return self.get_ticket(ticket_id)

    def get_ticket(self, ticket_id: str) -> dict | None:
        with self._conn() as c:
            row = c.execute("SELECT * FROM tickets WHERE id=?", (ticket_id,)).fetchone()
        return dict(row) if row else None

    def list_tickets(self, ward=None, category=None, status=None, priority=None, q=None) -> list[dict]:
        sql = "SELECT * FROM tickets WHERE 1=1"
        args: list = []
        if ward:
            sql += " AND ward=?"
            args.append(int(ward))
        if category:
            sql += " AND category=?"
            args.append(category)
        if status == "OPEN_ANY":
            sql += f" AND status IN ({','.join('?' for _ in config.OPEN_STATUSES)})"
            args.extend(config.OPEN_STATUSES)
        elif status:
            sql += " AND status=?"
            args.append(status)
        if priority:
            sql += " AND priority=?"
            args.append(priority)
        if q:
            sql += " AND (id LIKE ? OR title LIKE ? OR location LIKE ? OR summary_en LIKE ? OR summary_ta LIKE ?)"
            like = f"%{q}%"
            args.extend([like] * 5)
        sql += " ORDER BY CASE status WHEN 'RESOLVED' THEN 1 ELSE 0 END, priority ASC, sla_due_at ASC"
        with self._conn() as c:
            return [dict(r) for r in c.execute(sql, args).fetchall()]

    def open_tickets(self) -> list[dict]:
        return self.list_tickets(status="OPEN_ANY")

    def count_tickets(self) -> int:
        with self._conn() as c:
            return c.execute("SELECT COUNT(*) AS n FROM tickets").fetchone()["n"]

    # ----------------------------------------------------- reports / events
    def add_report(self, ticket_id, text, *, created_at=None, citizen_name="", phone="", language="en",
                   has_photo=False, photo_observation="") -> None:
        with self._lock, self._conn() as c:
            c.execute(
                "INSERT INTO reports(ticket_id, created_at, citizen_name, phone, text, language, has_photo, photo_observation)"
                " VALUES (?,?,?,?,?,?,?,?)",
                (ticket_id, created_at or iso(self.now()), citizen_name or "", phone or "", text, language,
                 int(bool(has_photo)), photo_observation or ""),
            )

    def reports_for(self, ticket_id: str) -> list[dict]:
        with self._conn() as c:
            return [dict(r) for r in c.execute("SELECT * FROM reports WHERE ticket_id=? ORDER BY created_at, id", (ticket_id,))]

    def add_event(self, ticket_id: str, actor: str, action: str, detail: str = "", ts: str | None = None) -> None:
        with self._lock, self._conn() as c:
            c.execute(
                "INSERT INTO events(ticket_id, ts, actor, action, detail) VALUES (?,?,?,?,?)",
                (ticket_id, ts or iso(self.now()), actor, action, detail or ""),
            )

    def events_for(self, ticket_id: str) -> list[dict]:
        with self._conn() as c:
            return [dict(r) for r in c.execute("SELECT * FROM events WHERE ticket_id=? ORDER BY ts, id", (ticket_id,))]

    def recent_events(self, limit: int = 40, actor_like: str | None = None) -> list[dict]:
        sql = "SELECT * FROM events"
        args: list = []
        if actor_like:
            sql += " WHERE actor LIKE ?"
            args.append(f"%{actor_like}%")
        sql += " ORDER BY ts DESC, id DESC LIMIT ?"
        args.append(limit)
        with self._conn() as c:
            return [dict(r) for r in c.execute(sql, args)]

    def add_notification(self, ticket_id: str, channel: str, recipient: str, message: str, language: str = "en",
                         ts: str | None = None) -> None:
        with self._lock, self._conn() as c:
            c.execute(
                "INSERT INTO notifications(ticket_id, ts, channel, recipient, language, message) VALUES (?,?,?,?,?,?)",
                (ticket_id, ts or iso(self.now()), channel, recipient, language, message),
            )

    def notifications_for(self, ticket_id: str) -> list[dict]:
        with self._conn() as c:
            return [dict(r) for r in c.execute("SELECT * FROM notifications WHERE ticket_id=? ORDER BY ts, id", (ticket_id,))]

    def last_event_time(self, ticket_id: str, action: str) -> datetime | None:
        with self._conn() as c:
            row = c.execute(
                "SELECT ts FROM events WHERE ticket_id=? AND action=? ORDER BY ts DESC, id DESC LIMIT 1",
                (ticket_id, action),
            ).fetchone()
        return parse_ts(row["ts"]) if row else None

    # ------------------------------------------------------------------ admin
    def wipe(self) -> None:
        with self._lock, self._conn() as c:
            for table in ("tickets", "reports", "events", "notifications", "settings"):
                c.execute(f"DELETE FROM {table}")
            c.execute("DELETE FROM sqlite_sequence")
