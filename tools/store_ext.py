"""tools/store_ext.py - The Planner's own tables, layered on core.store.Store.

The generic ``items`` table (core/store.py) is the review queue: one row per
weekly rota or swap/sick request waiting on a human. It is not a shift
roster. This module adds two tables a hotel actually needs to query -
``schedule_shifts`` (who is on duty, per day) and ``schedule_briefs`` (the
Staff Briefing sub-agent's delivery log) - plus the loaders for staff, the
room board, the restaurant book and guest notes.

Call :func:`ensure_schema` once per ``Store`` right after constructing it;
every tool in this repo does it. Nothing here replaces ``core.store`` - it
is additive, using the same connection (``store.db``) and the same
``utcnow()`` timestamp convention core.store itself uses.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from core.config import repo_root, sub_data_dir
from core.store import Store, utcnow

import engine

SCHEMA = """
CREATE TABLE IF NOT EXISTS schedule_shifts (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  run_item_id   TEXT NOT NULL,
  week_start    TEXT NOT NULL,
  date          TEXT NOT NULL,
  day_offset    INTEGER NOT NULL,
  staff_id      TEXT NOT NULL,
  staff_name    TEXT NOT NULL,
  department    TEXT NOT NULL,
  assignment    TEXT NOT NULL,
  role_in_shift TEXT NOT NULL,
  start_time    TEXT NOT NULL,
  end_time      TEXT NOT NULL,
  hours         REAL NOT NULL,
  cost          REAL NOT NULL,
  rooms_json    TEXT,
  service       TEXT,
  note          TEXT,
  status        TEXT NOT NULL DEFAULT 'planned',
  created_at    TEXT NOT NULL,
  updated_at    TEXT NOT NULL,
  UNIQUE(run_item_id, date, staff_id)
);
CREATE INDEX IF NOT EXISTS idx_schedule_shifts_date ON schedule_shifts (date);

CREATE TABLE IF NOT EXISTS schedule_briefs (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  date        TEXT NOT NULL,
  staff_id    TEXT NOT NULL,
  staff_name  TEXT NOT NULL,
  language    TEXT NOT NULL,
  brief_text  TEXT,
  delivered   INTEGER NOT NULL DEFAULT 0,
  item_id     TEXT,
  created_at  TEXT NOT NULL,
  UNIQUE(date, staff_id)
);
"""


def ensure_schema(store: Store) -> None:
    """Create both tables above if they do not already exist. Idempotent."""
    store.migrate(SCHEMA)


# --------------------------------------------------------------------------
# schedule_shifts
# --------------------------------------------------------------------------
def seed_shifts_from_week_plan(store: Store, run_item_id: str, plan: "engine.WeekPlan") -> int:
    """Write every ``ShiftAssignment`` in ``plan`` to ``schedule_shifts``.

    Idempotent on ``(run_item_id, date, staff_id)`` - calling this twice for
    the same approved week (a retried dispatch) inserts nothing new.
    """
    inserted = 0
    now = utcnow()
    for day in plan.days:
        for sh in day.shifts:
            existing = store.db.execute(
                "SELECT id FROM schedule_shifts WHERE run_item_id=? AND date=? AND staff_id=?",
                (run_item_id, day.date, sh.staff_id)).fetchone()
            if existing is not None:
                continue
            store.db.execute(
                "INSERT INTO schedule_shifts (run_item_id, week_start, date, day_offset, "
                "staff_id, staff_name, department, assignment, role_in_shift, start_time, "
                "end_time, hours, cost, rooms_json, service, note, status, created_at, "
                "updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (run_item_id, plan.week_start, day.date, day.day_offset, sh.staff_id,
                 sh.staff_name, sh.department, sh.assignment, sh.role_in_shift, sh.start_time,
                 sh.end_time, sh.hours, sh.cost, json.dumps(sh.rooms), sh.service, sh.note,
                 "planned", now, now))
            inserted += 1
    return inserted


def list_shifts_for_date(store: Store, date: str) -> list[dict]:
    rows = store.db.execute(
        "SELECT * FROM schedule_shifts WHERE date=? AND status != 'cancelled' "
        "ORDER BY department, assignment", (date,)).fetchall()
    return [dict(r) for r in rows]


def assigned_staff_ids(store: Store, date: str) -> set[str]:
    return {r["staff_id"] for r in list_shifts_for_date(store, date)}


def reassign_shift(store: Store, date: str, old_staff_id: str, new_staff: "engine.StaffMember",
                   note: str = "") -> int:
    """Move one day's shift from ``old_staff_id`` to ``new_staff``. Returns
    the number of rows updated (0 if that person had no shift that date -
    the caller should treat that as an error, not silently no-op).
    """
    cur = store.db.execute(
        "UPDATE schedule_shifts SET staff_id=?, staff_name=?, cost=hours*?, status='reassigned', "
        "note=?, updated_at=? WHERE date=? AND staff_id=?",
        (new_staff.id, new_staff.name, new_staff.hourly_cost, note, utcnow(), date, old_staff_id))
    return cur.rowcount


# --------------------------------------------------------------------------
# schedule_briefs
# --------------------------------------------------------------------------
def record_brief(store: Store, *, date: str, staff_id: str, staff_name: str, language: str,
                 brief_text: str, delivered: bool, item_id: str = "") -> None:
    store.db.execute(
        "INSERT INTO schedule_briefs (date, staff_id, staff_name, language, brief_text, "
        "delivered, item_id, created_at) VALUES (?,?,?,?,?,?,?,?) "
        "ON CONFLICT(date, staff_id) DO UPDATE SET brief_text=excluded.brief_text, "
        "delivered=excluded.delivered, item_id=excluded.item_id",
        (date, staff_id, staff_name, language, brief_text, int(delivered), item_id, utcnow()))


def briefs_for_date(store: Store, date: str) -> list[dict]:
    rows = store.db.execute(
        "SELECT * FROM schedule_briefs WHERE date=? ORDER BY staff_name", (date,)).fetchall()
    return [dict(r) for r in rows]


# --------------------------------------------------------------------------
# loaders: CSV import first, fixtures fallback (ARCHITECTURE.md section 5
# style - "auto" is what tools/run.py and tools/swaps.py use; "fixtures" is
# what tools/demo.py always passes, so `make demo` is the same fixed week
# ("Hotel Aurora, week of 2026-08-31") whether or not a hotel has already
# filled in its own data/imports/*.csv.
# --------------------------------------------------------------------------
def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _bool(value: Any) -> bool:
    return str(value).strip().lower() in ("1", "true", "yes", "y")


def _list(value: Any) -> list[str]:
    if isinstance(value, list):
        return value
    return [v.strip() for v in str(value or "").split(";") if v.strip()]


def _check_source(source: str) -> None:
    if source not in ("auto", "fixtures"):
        raise ValueError(f"unknown source '{source}', expected 'auto' or 'fixtures'")


def load_staff(source: str = "auto") -> list["engine.StaffMember"]:
    _check_source(source)
    csv_path = sub_data_dir("imports") / "staff.csv"
    if source == "auto" and csv_path.exists():
        with csv_path.open(newline="", encoding="utf-8-sig") as fh:
            rows = list(csv.DictReader(fh))
        return [engine.StaffMember(
            id=str(r.get("id", "")), name=str(r.get("name", "")), email=str(r.get("email", "")),
            phone=str(r.get("phone", "")), department=str(r.get("department", "")),
            role=str(r.get("role", "")), years_experience=int(r.get("years_experience") or 0),
            hourly_cost=float(r.get("hourly_cost") or 0), contract=str(r.get("contract", "FTE")),
            monthly_quota_hours=int(r.get("monthly_quota_hours") or 0),
            hours_worked_mtd=int(r.get("hours_worked_mtd") or 0),
            days_unavailable=_list(r.get("days_unavailable")),
            weekend_rule=str(r.get("weekend_rule", "any")), language=str(r.get("language", "en")),
            tags=_list(r.get("tags")), notes=str(r.get("notes", "")))
            for r in rows if r.get("id")]
    fixture = repo_root() / "fixtures" / "hotel" / "staff.json"
    if not fixture.exists():
        return []
    return [engine.StaffMember(**r) for r in _read_json(fixture).get("staff", [])]


def load_room_status(source: str = "auto") -> dict[str, list["engine.RoomStatus"]]:
    _check_source(source)
    csv_path = sub_data_dir("imports") / "room_status.csv"
    if source == "auto" and csv_path.exists():
        with csv_path.open(newline="", encoding="utf-8-sig") as fh:
            rows = list(csv.DictReader(fh))
        by_day: dict[str, list[engine.RoomStatus]] = {}
        for r in rows:
            day = str(r.get("day_offset", "0"))
            by_day.setdefault(day, []).append(engine.RoomStatus(
                room_number=str(r.get("room_number", "")), floor=int(r.get("floor") or 0),
                room_type=str(r.get("room_type", "")), status=str(r.get("status", "vacant")),
                vip=_bool(r.get("vip")), note=str(r.get("note", ""))))
        return by_day
    fixture = repo_root() / "fixtures" / "hotel" / "room_status.json"
    if not fixture.exists():
        return {}
    return {d: [engine.RoomStatus(**r) for r in rows]
            for d, rows in _read_json(fixture).get("days", {}).items()}


def load_restaurant_covers(source: str = "auto") -> dict[str, list["engine.CoverService"]]:
    _check_source(source)
    csv_path = sub_data_dir("imports") / "restaurant_covers.csv"
    if source == "auto" and csv_path.exists():
        with csv_path.open(newline="", encoding="utf-8-sig") as fh:
            rows = list(csv.DictReader(fh))
        by_day: dict[str, list[engine.CoverService]] = {}
        for r in rows:
            day = str(r.get("day_offset", "0"))
            by_day.setdefault(day, []).append(engine.CoverService(
                service=str(r.get("service", "")), covers=int(r.get("covers") or 0),
                dietary=_list(r.get("dietary")), is_group=_bool(r.get("is_group")),
                occasion=str(r.get("occasion", "")), notes=str(r.get("notes", ""))))
        return by_day
    fixture = repo_root() / "fixtures" / "hotel" / "restaurant_covers.json"
    if not fixture.exists():
        return {}
    return {d: [engine.CoverService(**r) for r in rows]
            for d, rows in _read_json(fixture).get("days", {}).items()}


def load_guest_notes(source: str = "auto") -> dict[str, dict]:
    """Arrivals, departures, VIP notes and maintenance tickets, per day -
    what the Staff Briefing sub-agent (tools/briefing.py) reads. No CSV path
    yet: this is aspirational PMS/maintenance-feed data, not something most
    hotels export today - see docs/integrations.md.
    """
    fixture = repo_root() / "fixtures" / "hotel" / "guest_notes.json"
    if not fixture.exists():
        return {}
    return _read_json(fixture).get("days", {})


# --------------------------------------------------------------------------
# shared formatting - every human-facing amount uses hotel.currency, never a
# hardcoded symbol (ARCHITECTURE.md / family convention).
# --------------------------------------------------------------------------
def money(amount: float, currency: str) -> str:
    return f"{currency} {amount:,.2f}"
