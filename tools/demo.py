#!/usr/bin/env python3
"""tools/demo.py - the whole loop on the bundled fixtures, zero credentials.

    make demo
    python3 tools/demo.py

Forces `llm.provider=mock`, `mode=shadow` and the `mock` adapter for every
system (`load_settings(demo=True)`), and always builds the same fixed week
(`scheduling.DEMO_WEEK_START`, "week of 2026-08-31") from `fixtures/hotel/*`
regardless of what config/hotel.yaml or config/agent.yaml say and whatever
day it actually is - so this always works on a fresh clone with a blank
.env, and never reaches a real HR export or PMS even once a hotel has
pointed those at something real. Runs against its own database
(data/demo/demo.db), never data/agent.db.

Nothing is published or reassigned - mode is shadow and demo never calls
`tools/review.py send`. Step 4's Staff Briefing preview computes briefs
straight off the in-memory plan (the sub-agent is off by default and
schedule_shifts only fills in once a week is actually published).
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.config import ConfigError, load_settings, sub_data_dir  # noqa: E402
from core.log import summary_line  # noqa: E402
from core.store import Store  # noqa: E402

import briefing  # noqa: E402
import engine  # noqa: E402
import scheduling  # noqa: E402
import store_ext  # noqa: E402
import swaps  # noqa: E402


def main() -> int:
    try:
        settings = load_settings(demo=True)
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 1

    demo_db = sub_data_dir("demo") / "demo.db"
    if demo_db.exists():
        demo_db.unlink()
    store = Store(settings, path=demo_db)
    store_ext.ensure_schema(store)

    currency = settings.hotel.currency
    stats = {"processed": 0, "drafted": 0, "needs_human": 0, "sent": 0}

    print(f"The Planner demo - {settings.hotel.name}, week of {scheduling.DEMO_WEEK_START}\n")

    print("1) Building next week's rota from fixtures/hotel/*.json")
    item, _ = scheduling.build_weekly_rota(settings, store, week_start=scheduling.DEMO_WEEK_START,
                                           provider="mock", source="fixtures")
    plan = (item.draft or {}).get("plan", {})
    for day in plan.get("days", []):
        staff_n = len({s["staff_id"] for s in day["shifts"]})
        flag = f" - {len(day['warnings'])} warning(s)" if day["warnings"] else ""
        print(f"  {day['weekday']} {day['date']}: {staff_n} staff, {day['total_hours']}h, "
             f"{store_ext.money(day['total_cost'], currency)}{flag}")
    stats["processed"] += 1
    stats["drafted"] += 1
    if item.review_status == "needs_human":
        stats["needs_human"] += 1
    narrative = (item.draft or {}).get("narrative", {})
    print(f"\n  AI briefing for the duty manager:\n  \"{narrative.get('note', '')}\"")
    print(f"\n  Week total: {plan.get('staff_on_shift')} staff, {plan.get('total_hours')}h, "
         f"{store_ext.money(plan.get('total_cost', 0), currency)}, "
         f"{plan.get('warning_count')} warning(s) -> status {item.review_status}")

    print("\n2) A rule toggle vs a hard limit (quota-hard-cap vs the quota floor)")
    staff = store_ext.load_staff(source="fixtures")
    rooms = store_ext.load_room_status(source="fixtures")
    covers = store_ext.load_restaurant_covers(source="fixtures")
    rules_on = dict(settings.agent_get("rules", {}) or {})
    rules_off = {**rules_on, "quota-hard-cap": False}
    plan_on = engine.build_week_plan(staff, rooms, covers, rules_on, settings.agent,
                                     scheduling.DEMO_WEEK_START)
    plan_off = engine.build_week_plan(staff, rooms, covers, rules_off, settings.agent,
                                      scheduling.DEMO_WEEK_START)
    floor_on = plan_on.days[0].excluded_counts["quota"]
    floor_off = plan_off.days[0].excluded_counts["quota"]
    # The quota headroom floor is a hard, unconditional limit (like
    # max-consecutive-days and min-rest) - it never moves with this toggle.
    # See tools/engine.py:available_staff and factory/workflows/build-repo.md
    # section 5. If this ever fails, the floor has been made toggleable again.
    assert floor_on == floor_off, (
        "quota headroom floor changed with the quota-hard-cap toggle - "
        "the floor must be unconditional, see tools/engine.py:available_staff")
    print(f"  Monday, quota headroom floor excludes {floor_on} staff whether "
         f"quota-hard-cap is ON or OFF ({floor_off}) - it is a hard limit, never a "
         f"rule toggle.")
    print("  quota-hard-cap only changes who is picked first among people who are "
         "already above that floor but close to it - never who is excluded. See "
         "docs/how-it-works.md \"Design decisions\".")
    print("  Nothing was written - this is a side-by-side comparison only.")

    print("\n3) Resolving swap and sick-day requests from fixtures/inbound/*.json")
    for req in swaps.load_requests(source="fixtures"):
        s_item, _ = swaps.process_request(settings, store, req, source="fixtures")
        stats["processed"] += 1
        stats["drafted"] += 1
        if s_item.review_status == "needs_human":
            stats["needs_human"] += 1
        draft = s_item.draft or {}
        print(f"  {req['id']} ({req['reason']}, {req['staff_id']} on {req['date']}): "
             f"{draft.get('candidate_name') or 'NO ELIGIBLE COVER'} -> {s_item.review_status}")

    briefing_on = bool(settings.agent_get("subagents.staff_briefing.enabled", False))
    briefing_state = "ON" if briefing_on else "OFF (the default)"
    print(f"\n4) Staff Briefing preview (sub-agent is {briefing_state} in this demo's own "
         "bundled config/agent.example.yaml - not your real config/agent.yaml, which "
         "`make demo` never reads; see config/agent.yaml: subagents.staff_briefing.enabled)")
    day0 = plan["days"][0]
    guest_notes_day0 = store_ext.load_guest_notes(source="fixtures").get("0", {})
    covers_day0 = covers.get("0", [])
    staff_by_id = {s.id: s for s in staff}
    allowed_languages = list(settings.agent_get("subagents.staff_briefing.languages", []) or [])
    default_lang = settings.hotel.languages[0] if settings.hotel.languages else "en"
    shown = day0["shifts"][:3]
    for raw in shown:
        shift = engine.ShiftAssignment(**raw)
        member = staff_by_id.get(shift.staff_id)
        staff_language = member.language if member else ""
        language, _reason = briefing.resolve_briefing_language(staff_language, allowed_languages,
                                                                default_lang)
        notes = briefing.relevant_notes(shift, guest_notes_day0, covers_day0)
        text = briefing.brief_for_shift(settings, store, item.id, shift, notes, language, "mock")
        print(f"  {shift.staff_name} ({language}): {text}")
    print(f"  ({len(shown)} of {len({s['staff_id'] for s in day0['shifts']})} on-duty people shown "
         "- turn the sub-agent on and publish a week to send these for real.)")

    print(f"\n{stats['needs_human']} of {stats['processed']} item(s) need a person to look first "
         "before anything is published (see docs/safety.md).")
    print("Nothing was sent or published: mode is shadow, and demo never calls "
         "`tools/review.py send` at all.")
    print("Next: `make review` to see what is waiting, or read workflows/10-scheduling.md.\n")

    print(f"DEMO OK — {summary_line(stats, settings.mode)}")
    store.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
