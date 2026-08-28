#!/usr/bin/env python3
"""tools/scheduling.py - I/O around the weekly rota engine (tools/engine.py).

    python3 tools/scheduling.py build [--provider mock]

Loads staff, the room board and the restaurant book (``tools/store_ext.py``),
calls ``engine.build_week_plan()``, asks ``core.llm.complete()`` for the
duty-manager narrative (``prompts/duty-manager-briefing.md``), and queues
the result as one review item for the whole week. Nothing is published
until a human approves it and runs ``tools/review.py send`` -
``dispatch_weekly_rota`` below is the only thing that ever writes to
``schedule_shifts`` or notifies staff. See docs/how-it-works.md.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from datetime import date, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.adapters import get_messaging, get_sheets  # noqa: E402
from core.config import ConfigError, Settings, load_settings  # noqa: E402
from core.llm import LLMError, LLMPendingInteractive, LLMResult, complete  # noqa: E402
from core.review import assert_write_allowed  # noqa: E402
from core.store import Item, Store, StoreError  # noqa: E402
from core.templates import build_prompt  # noqa: E402

import engine  # noqa: E402
import store_ext  # noqa: E402

SCHEMAS_DIR = REPO_ROOT / "prompts" / "schemas"
BRIEFING_SCHEMA = json.loads((SCHEMAS_DIR / "duty-manager-briefing.json").read_text(encoding="utf-8"))
#: fixed reference week the demo always builds - see docs/how-it-works.md
#: "Design decisions" point 2 and fixtures/hotel/room_status.json.
DEMO_WEEK_START = date(2026, 8, 31)


def next_monday(today: date | None = None) -> date:
    """The coming Monday - today itself if today already is one."""
    today = today or date.today()
    days_ahead = (7 - today.weekday()) % 7
    return today + timedelta(days=days_ahead)


def _plan_to_dict(plan: engine.WeekPlan) -> dict:
    return {"week_start": plan.week_start, "days": [asdict(d) for d in plan.days],
           "total_hours": plan.total_hours, "total_cost": plan.total_cost,
           "total_cost_saved": plan.total_cost_saved, "warning_count": plan.warning_count,
           "staff_on_shift": plan.staff_on_shift}


def _plan_from_dict(d: dict) -> engine.WeekPlan:
    days = []
    for day in d["days"]:
        shifts = [engine.ShiftAssignment(**sh) for sh in day["shifts"]]
        days.append(engine.DayPlan(**{**day, "shifts": shifts}))
    return engine.WeekPlan(week_start=d["week_start"], days=days)


def build_weekly_rota(settings: Settings, store: Store, *, week_start: date | None = None,
                      provider: str | None = None, source: str = "auto") -> tuple[Item, bool]:
    """Build (or return) the review item for one week's rota.

    Idempotent per ISO week: a second call for the same ``week_start`` on
    fully-configured fixtures/CSV returns the existing item untouched.
    ``source="fixtures"`` is what ``tools/demo.py`` always passes - never a
    hotel's own ``data/imports/*.csv``.
    """
    week_start = week_start or next_monday()
    external_id = week_start.isoformat()
    item = store.upsert_item("weekly-rota", external_id, kind="weekly_rota",
                             payload={"week_start": external_id})
    if item.intent:
        return item, False

    staff = store_ext.load_staff(source=source)
    rooms_by_day = store_ext.load_room_status(source=source)
    covers_by_day = store_ext.load_restaurant_covers(source=source)
    rules = dict(settings.agent_get("rules", {}) or {})
    plan = engine.build_week_plan(staff, rooms_by_day, covers_by_day, rules, settings.agent,
                                  week_start)
    plan_dict = _plan_to_dict(plan)
    summary = engine.week_summary(plan)

    prompt = build_prompt("duty-manager-briefing", settings=settings, item=summary)
    result: LLMResult = complete("duty-manager-briefing", prompt, BRIEFING_SCHEMA,
                                 settings=settings, provider=provider, store=store,
                                 item_id=item.id, fixture_id="week-briefing")
    narrative = result.data or {}

    store.set_fields(item.id, intent="weekly_rota", confidence=1.0,
                     draft={"plan": plan_dict, "narrative": narrative})
    needs_human = plan.warning_count > 0
    status = "needs_human" if needs_human else "pending_review"
    updated = store.transition(item.id, status, actor="agent",
                               detail={"staff": plan.staff_on_shift,
                                      "warnings": plan.warning_count})
    return updated, True


def dispatch_weekly_rota(settings: Settings, store: Store, item: Item) -> dict:
    """Called by ``tools/review.py send`` once the week is approved/edited.

    Seeds ``schedule_shifts``, exports the week to a sheet for the manager's
    records, and sends every rostered person one message with their shifts
    for the week - the roster's own "notifies every staff member".

    Guarded up front with the "publish" action (the same one
    ``review.require_approval_for`` names by default) so shadow mode - or a
    live item that somehow reached here unapproved - blocks before
    ``schedule_shifts`` is touched at all, not only before the outbound
    send. See docs/how-it-works.md "Deciding what needs a human".
    """
    assert_write_allowed(settings, "publish", item)
    plan = _plan_from_dict((item.draft or {}).get("plan", {}))
    store_ext.ensure_schema(store)
    store_ext.seed_shifts_from_week_plan(store, item.id, plan)

    currency = settings.hotel.currency
    sheets = get_sheets(settings)
    rows = [["date", "weekday", "staff", "department", "role", "assignment", "start", "end",
            "hours", "cost"]]
    for day in plan.days:
        for sh in day.shifts:
            rows.append([day.date, day.weekday, sh.staff_name, sh.department, sh.role_in_shift,
                        sh.assignment, sh.start_time, sh.end_time, sh.hours,
                        store_ext.money(sh.cost, currency)])
    sheet_name = f"weekly-rota-{plan.week_start}"
    sheets.write(sheet_name, rows, item=item)

    messaging = get_messaging(settings)
    by_staff: dict[str, list[engine.ShiftAssignment]] = {}
    names: dict[str, str] = {}
    for day in plan.days:
        for sh in day.shifts:
            by_staff.setdefault(sh.staff_id, []).append(sh)
            names[sh.staff_id] = sh.staff_name
    notified = 0
    for staff_id, shifts in by_staff.items():
        lines = [f"Your shifts for the week of {plan.week_start}:"]
        for day in plan.days:
            mine = [sh for sh in day.shifts if sh.staff_id == staff_id]
            for sh in mine:
                lines.append(f"- {day.weekday} {day.date}: {sh.assignment} "
                            f"({sh.start_time}-{sh.end_time}), {sh.role_in_shift}")
        messaging.send(staff_id, "\n".join(lines), item=item)
        notified += 1

    return {"message_id": sheet_name, "notified": notified}


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------
def cmd_build(store: Store, settings: Settings, args: argparse.Namespace) -> int:
    week_start = date.fromisoformat(args.week_start) if args.week_start else next_monday()
    item, is_new = build_weekly_rota(settings, store, week_start=week_start, provider=args.provider)
    if not is_new:
        print(f"The week of {week_start} was already built: {item.id} ({item.review_status}).")
        return 0
    plan = (item.draft or {}).get("plan", {})
    print(f"Weekly rota {item.id} for the week of {week_start}: status {item.review_status}.")
    print(f"  {plan.get('staff_on_shift')} staff, {plan.get('total_hours')} hours, "
         f"{store_ext.money(plan.get('total_cost', 0), settings.hotel.currency)}, "
         f"{plan.get('warning_count')} warning(s).")
    print("Run `make review` to see it, then `python3 tools/review.py approve "
         f"{item.id}` and `python3 tools/review.py send` to publish it.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)

    p_build = sub.add_parser("build", help="build (or show) the next week's rota")
    p_build.add_argument("--week-start", default=None, help="ISO date of the Monday to build")
    p_build.add_argument("--provider", default=None)

    args = parser.parse_args(argv)
    try:
        settings = load_settings(provider=getattr(args, "provider", None))
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 1

    store = Store(settings)
    store_ext.ensure_schema(store)
    try:
        if args.command == "build":
            return cmd_build(store, settings, args)
        parser.error(f"unknown command {args.command}")
        return 2
    except LLMPendingInteractive as exc:
        print(str(exc))
        return 3
    except StoreError as exc:
        print(f"cannot do that: {exc}", file=sys.stderr)
        return 1
    except LLMError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    finally:
        store.close()


if __name__ == "__main__":
    raise SystemExit(main())
