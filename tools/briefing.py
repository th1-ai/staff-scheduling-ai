#!/usr/bin/env python3
"""tools/briefing.py - Staff Briefing AI ("The Sergeant"), folded into this repo.

    python3 tools/briefing.py build --day-offset 0 [--provider mock]

Off by default (``config/agent.yaml: subagents.staff_briefing.enabled`` -
turn it on once you also want a personal daily brief per person; The
Planner is fully useful without it). Reads the *published* day's on-duty
staff (``schedule_shifts``, seeded when a weekly rota is dispatched) plus
that day's arrivals/departures/VIP notes/maintenance
(``fixtures/hotel/guest_notes.json``), and asks ``core.llm.complete()`` for
one short personal brief per person, in their own language - the numbers
never come from the model, only the words. See docs/how-it-works.md.

Composing is automatic; sending still goes through the review queue like
everything else in this family, once per day as one batch - see
docs/how-it-works.md point "9a".
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.adapters import get_messaging  # noqa: E402
from core.config import ConfigError, Settings, load_settings  # noqa: E402
from core.llm import LLMError, LLMPendingInteractive, LLMResult, complete  # noqa: E402
from core.review import assert_write_allowed  # noqa: E402
from core.store import Item, Store, StoreError  # noqa: E402
from core.templates import build_prompt  # noqa: E402

import engine  # noqa: E402
import store_ext  # noqa: E402

SCHEMAS_DIR = REPO_ROOT / "prompts" / "schemas"
BRIEF_SCHEMA = json.loads((SCHEMAS_DIR / "staff-brief.json").read_text(encoding="utf-8"))
_LABELS = {"arrivals": "Arrival", "departures": "Departure", "vip_notes": "VIP",
          "maintenance": "Maintenance"}


def _row_to_shift(row: dict) -> engine.ShiftAssignment:
    return engine.ShiftAssignment(
        staff_id=row["staff_id"], staff_name=row["staff_name"], department=row["department"],
        assignment=row["assignment"], role_in_shift=row["role_in_shift"],
        start_time=row["start_time"], end_time=row["end_time"], hours=row["hours"],
        cost=row["cost"], rooms=json.loads(row["rooms_json"] or "[]"),
        service=row["service"] or "", note=row["note"] or "")


def relevant_notes(shift: engine.ShiftAssignment, guest_notes_day: dict,
                   covers_day: list[engine.CoverService]) -> list[str]:
    """What THIS person needs to know today - nothing invented, only what is
    tied to their own rooms or their own service. See docs/how-it-works.md
    point 8.
    """
    notes: list[str] = []
    if shift.department == "housekeeping":
        room_set = set(shift.rooms)
        for key, label in _LABELS.items():
            for entry in guest_notes_day.get(key, []):
                if entry.get("room") in room_set and entry.get("note"):
                    notes.append(f"{label} room {entry['room']}: {entry['note']}")
    else:
        cov = next((c for c in covers_day if c.service == shift.service), None)
        if cov and (cov.is_group or cov.dietary or cov.occasion):
            bits = []
            if cov.is_group:
                bits.append("group booking")
            if cov.dietary:
                bits.append("dietary: " + ", ".join(cov.dietary))
            if cov.occasion:
                bits.append(cov.occasion)
            notes.append(f"{cov.covers} covers - " + "; ".join(bits))
    return notes


def resolve_briefing_language(staff_language: str, allowed_languages: list[str],
                              default_language: str) -> tuple[str, str | None]:
    """The language a brief is written in, plus a ``needs_human`` reason if
    it had to fall back.

    ``config/agent.yaml: subagents.staff_briefing.languages`` is an
    allowlist, not a suggestion - a blank language (unknown staff id, or an
    empty ``language`` cell in a real CSV import) or a code that is not in
    the list falls back to the hotel's own default language
    (``hotel.languages[0]``), never a literal placeholder like the mock
    LLM provider's ``"mock"`` free-text default. See docs/how-it-works.md
    and workflows/25-staff-briefing.md.
    """
    lang = (staff_language or "").strip()
    if lang and (not allowed_languages or lang in allowed_languages):
        return lang, None
    reason = (f"language '{lang or '(blank)'}' is not in "
             f"subagents.staff_briefing.languages - used the hotel default "
             f"'{default_language}' instead")
    return default_language, reason


def brief_for_shift(settings: Settings, store: Store, item_id: str,
                    shift: engine.ShiftAssignment, notes: list[str], language: str,
                    provider: str | None) -> str:
    payload = {"staff_name": shift.staff_name, "role": shift.role_in_shift,
              "assignment": shift.assignment, "rooms": shift.rooms, "service": shift.service,
              "relevant_notes": notes, "language": language}
    prompt = build_prompt("staff-brief", settings=settings, item=payload)
    result: LLMResult = complete("staff-brief", prompt, BRIEF_SCHEMA, settings=settings,
                                 provider=provider, store=store, item_id=item_id,
                                 fixture_id=f"brief-{shift.staff_id}-day0")
    return (result.data or {}).get("brief", "")


def build_daily_briefing(settings: Settings, store: Store, *, date_str: str,
                         day_offset: int, provider: str | None = None,
                         source: str = "auto") -> tuple[Item | None, bool]:
    """One review item for the whole day's briefs. ``None`` (not an item) if
    nothing is published for that date yet - see workflows/25-staff-briefing.md.
    """
    shifts_raw = store_ext.list_shifts_for_date(store, date_str)
    if not shifts_raw:
        return None, False

    item = store.upsert_item("staff-briefing", date_str, kind="staff_briefing",
                             payload={"date": date_str, "day_offset": day_offset})
    if item.intent:
        return item, False

    guest_notes_day = store_ext.load_guest_notes(source=source).get(str(day_offset), {})
    covers_day = store_ext.load_restaurant_covers(source=source).get(str(day_offset), [])
    staff_by_id = {s.id: s for s in store_ext.load_staff(source=source)}
    default_lang = settings.hotel.languages[0] if settings.hotel.languages else "en"
    allowed_languages = list(settings.agent_get("subagents.staff_briefing.languages", []) or [])

    briefs = []
    warnings: list[str] = []
    for row in shifts_raw:
        shift = _row_to_shift(row)
        member = staff_by_id.get(shift.staff_id)
        staff_language = member.language if member else ""
        language, reason = resolve_briefing_language(staff_language, allowed_languages,
                                                      default_lang)
        if reason:
            warnings.append(f"{shift.staff_name}: {reason}")
        notes = relevant_notes(shift, guest_notes_day, covers_day)
        text = brief_for_shift(settings, store, item.id, shift, notes, language, provider)
        briefs.append({"staff_id": shift.staff_id, "staff_name": shift.staff_name,
                      "language": language, "brief": text, "notes": notes})

    store.set_fields(item.id, intent="staff_briefing", confidence=1.0,
                     draft={"date": date_str, "briefs": briefs, "warnings": warnings})
    needs_human = bool(warnings)
    status = "needs_human" if needs_human else "pending_review"
    updated = store.transition(item.id, status, actor="agent",
                               detail={"briefs": len(briefs), "language_warnings": len(warnings)})
    return updated, True


def dispatch_daily_briefing(settings: Settings, store: Store, item: Item) -> dict:
    """Called by ``tools/review.py send`` once the batch is approved/edited.

    Sends each person their own brief, then one manager copy of all of
    them, and records delivery in ``schedule_briefs`` - see
    docs/how-it-works.md point 7. Guarded up front so shadow mode blocks
    before anything is sent or recorded as delivered.
    """
    assert_write_allowed(settings, "send_message", item)
    draft = item.draft or {}
    date_str = draft.get("date", "")
    briefs = draft.get("briefs", [])
    messaging = get_messaging(settings)
    sent = 0
    digest_lines = [f"Staff briefs for {date_str}:"]
    for b in briefs:
        messaging.send(b["staff_id"], b["brief"], item=item)
        store_ext.record_brief(store, date=date_str, staff_id=b["staff_id"],
                               staff_name=b["staff_name"], language=b["language"],
                               brief_text=b["brief"], delivered=True, item_id=item.id)
        digest_lines.append(f"- {b['staff_name']} ({b['language']}): {b['brief']}")
        sent += 1
    if briefs:
        messaging.notify_staff("\n".join(digest_lines), item=item)
    return {"message_id": f"briefing-{date_str}", "sent": sent}


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------
def cmd_build(store: Store, settings: Settings, args: argparse.Namespace) -> int:
    if not settings.agent_get("subagents.staff_briefing.enabled", False):
        print("The Staff Briefing sub-agent is off (config/agent.yaml: "
             "subagents.staff_briefing.enabled: false). Nothing to do - see "
             "workflows/25-staff-briefing.md to turn it on.")
        return 0
    day = date.today() + timedelta(days=args.day_offset) if args.date is None \
        else date.fromisoformat(args.date)
    item, is_new = build_daily_briefing(settings, store, date_str=day.isoformat(),
                                        day_offset=args.day_offset, provider=args.provider)
    if item is None:
        print(f"No published shifts for {day.isoformat()} yet - publish the weekly rota first "
             "(workflows/10-scheduling.md). To see the shape of a brief before then, run "
             "`make demo` (Staff Briefing preview) - it always works on the bundled example "
             "hotel, in shadow, whatever your own config says.")
        return 0
    if not is_new:
        print(f"Briefs for {day.isoformat()} were already built: {item.id} ({item.review_status}).")
        return 0
    for b in item.draft.get("briefs", []):
        print(f"  {b['staff_name']} ({b['language']}): {b['brief']}")
    for w in item.draft.get("warnings", []):
        print(f"  needs a human: {w}")
    print(f"\nBriefing batch {item.id}: {len(item.draft.get('briefs', []))} people, "
         f"status {item.review_status}.")
    print("Run `make review` to see it, then `python3 tools/review.py approve "
         f"{item.id}` and `python3 tools/review.py send` to deliver it.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)

    p_build = sub.add_parser("build", help="compose today's per-person briefs")
    p_build.add_argument("--day-offset", type=int, default=0)
    p_build.add_argument("--date", default=None, help="ISO date (overrides --day-offset)")
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
