#!/usr/bin/env python3
"""tools/swaps.py - swap requests and sick-day cover (tools/engine.py:resolve_swap).

    python3 tools/swaps.py check                     # process every new request
    python3 tools/swaps.py request --staff-id hk-06 --date 2026-09-02 --reason swap \\
        --note "wants Wednesday off"

The matching is always automatic - deterministic, same-role, respects the
same personal-rules/quota exclusions as the weekly rota (tools/engine.py).
The notification that follows a match is still an outbound message and
waits for approval in the review queue like anything else in this family -
see docs/how-it-works.md "Design decisions" point 1.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
import uuid
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.adapters import get_messaging  # noqa: E402
from core.config import ConfigError, Settings, load_settings, sub_data_dir  # noqa: E402
from core.review import assert_write_allowed  # noqa: E402
from core.store import Item, Store, StoreError  # noqa: E402

import engine  # noqa: E402
import store_ext  # noqa: E402


def load_requests(source: str = "auto") -> list[dict]:
    """New swap/sick requests.

    ``"auto"`` (what a real run - ``make run``, ``tools/run.py``,
    ``tools/swaps.py check`` with no ``--source`` - always uses) reads
    **only** ``data/imports/swap_requests.csv``. There is no fixture
    fallback here: the bundled example hotel's ``fixtures/inbound/*.json``
    must never leak into a real property's review queue, resolved against
    its real roster, just because it has not connected this file yet. With
    no file connected, a real run processes zero requests and says so - see
    README "Run it" / docs/integrations.md "Your staff, rooms and covers
    data" / workflows/00-setup.md step 4.

    ``"fixtures"`` (what ``tools/demo.py`` always passes, and what
    ``--source fixtures`` asks for explicitly) reads only the bundled
    fixtures - demo/tests only, never a real run.
    """
    if source not in ("auto", "fixtures"):
        raise ValueError(f"unknown source '{source}', expected 'auto' or 'fixtures'")
    if source == "fixtures":
        out = []
        for path in sorted((REPO_ROOT / "fixtures" / "inbound").glob("swap-*.json")):
            out.append(json.loads(path.read_text(encoding="utf-8")))
        return out

    csv_path = sub_data_dir("imports") / "swap_requests.csv"
    if not csv_path.exists():
        print("no swap requests file connected - create data/imports/swap_requests.csv "
             "to process real swap/sick-day requests (columns: id, staff_id, date, reason, "
             "note - see docs/integrations.md); processing none this run")
        return []
    with csv_path.open(newline="", encoding="utf-8-sig") as fh:
        rows = list(csv.DictReader(fh))
    return [{"id": str(r.get("id") or uuid.uuid4().hex[:8]), "staff_id": r.get("staff_id", ""),
            "date": r.get("date", ""), "reason": r.get("reason", "swap"),
            "note": r.get("note", "")} for r in rows if r.get("staff_id")]


def process_request(settings: Settings, store: Store, request: dict,
                    source: str = "auto") -> tuple[Item, bool]:
    """Resolve one request and queue it for review. Idempotent on the
    request's own ``id`` - a re-submitted or re-read request is a no-op.
    """
    item = store.upsert_item("swap-request", str(request["id"]), kind="swap", payload=request)
    if item.intent:
        return item, False

    staff = store_ext.load_staff(source=source)
    assigned = store_ext.assigned_staff_ids(store, str(request.get("date", "")))
    rules = dict(settings.agent_get("rules", {}) or {})
    resolution = engine.resolve_swap(staff, request, assigned, rules, settings.agent)

    store.set_fields(item.id, intent=resolution.reason, confidence=1.0, draft={
        "request_id": resolution.request_id, "staff_id": resolution.staff_id,
        "date": resolution.date, "reason": resolution.reason,
        "candidate_id": resolution.candidate_id, "candidate_name": resolution.candidate_name,
        "role": resolution.role, "warnings": resolution.warnings,
        "thinking_log": resolution.thinking_log,
    })
    needs_human = resolution.candidate_id is None
    status = "needs_human" if needs_human else "pending_review"
    updated = store.transition(item.id, status, actor="agent",
                               detail={"candidate": resolution.candidate_name})
    return updated, True


def dispatch_swap(settings: Settings, store: Store, item: Item) -> dict:
    """Called by ``tools/review.py send`` once the swap is approved/edited.

    Reassigns the shift in ``schedule_shifts`` and notifies both the
    original person and their cover. Guarded up front (action "publish") so
    shadow mode blocks before the reassignment is written, not only before
    the notifications - see docs/how-it-works.md "Design decisions" point 1.
    """
    assert_write_allowed(settings, "publish", item)
    draft = item.draft or {}
    candidate_id = draft.get("candidate_id")
    if not candidate_id:
        return {"message_id": "", "note": "no candidate - nothing to reassign"}

    staff = store_ext.load_staff(source="auto")
    by_id = {s.id: s for s in staff}
    candidate = by_id.get(candidate_id)
    requester = by_id.get(draft.get("staff_id"))
    if candidate is None:
        return {"message_id": "", "note": f"candidate {candidate_id} not found in the roster"}

    reason = draft.get("reason", "swap")
    note = f"covering for {requester.name if requester else draft.get('staff_id')} ({reason})"
    rows = store_ext.reassign_shift(store, draft["date"], draft["staff_id"], candidate, note=note)

    messaging = get_messaging(settings)
    # Both notifications go to colleagues, not guests - the EU AI Act
    # Article 50 disclosure line belongs on guest-facing text only.
    if requester is not None:
        messaging.send(requester.id, f"Your {draft.get('role', 'shift')} on {draft['date']} is "
                       f"now covered by {candidate.name}.", item=item, guest_facing=False)
    messaging.send(candidate.id, f"You are covering {requester.name if requester else 'a colleague'}'s "
                   f"{draft.get('role', 'shift')} on {draft['date']}.", item=item,
                   guest_facing=False)
    return {"message_id": f"swap-{draft['request_id']}", "shifts_updated": rows}


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------
def cmd_check(store: Store, settings: Settings, args: argparse.Namespace) -> int:
    requests = load_requests(source=args.source)
    processed = 0
    for req in requests:
        item, is_new = process_request(settings, store, req, source=args.source)
        if not is_new:
            continue
        processed += 1
        draft = item.draft or {}
        print(f"  {req['id']}: {draft.get('candidate_name') or 'NO CANDIDATE'} "
             f"-> status {item.review_status}")
    if not processed:
        print("No new swap/sick requests.")
    return 0


def cmd_request(store: Store, settings: Settings, args: argparse.Namespace) -> int:
    request = {"id": args.id or uuid.uuid4().hex[:8], "staff_id": args.staff_id,
              "date": args.date, "reason": args.reason, "note": args.note or ""}
    item, _ = process_request(settings, store, request, source="auto")
    draft = item.draft or {}
    print(f"{request['id']}: {draft.get('candidate_name') or 'no eligible cover found'} "
         f"-> status {item.review_status}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)

    p_check = sub.add_parser("check", help="process every new swap/sick request")
    p_check.add_argument("--source", default="auto", choices=["auto", "fixtures"])

    p_req = sub.add_parser("request", help="log one swap/sick request right now")
    p_req.add_argument("--staff-id", required=True)
    p_req.add_argument("--date", required=True, help="ISO date")
    p_req.add_argument("--reason", default="swap", choices=["swap", "sick"])
    p_req.add_argument("--note", default="")
    p_req.add_argument("--id", default=None)

    args = parser.parse_args(argv)
    try:
        settings = load_settings()
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 1

    store = Store(settings)
    store_ext.ensure_schema(store)
    try:
        if args.command == "check":
            return cmd_check(store, settings, args)
        if args.command == "request":
            return cmd_request(store, settings, args)
        parser.error(f"unknown command {args.command}")
        return 2
    except StoreError as exc:
        print(f"cannot do that: {exc}", file=sys.stderr)
        return 1
    finally:
        store.close()


if __name__ == "__main__":
    raise SystemExit(main())
