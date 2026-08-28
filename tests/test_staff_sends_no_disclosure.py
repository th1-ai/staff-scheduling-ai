"""Staff-only chat sends must never carry the guest-facing EU AI Act
Article 50 disclosure line - a rota, a personal brief or a swap
confirmation goes to a colleague, not a guest (factory/workflows/build-repo.md
section 5: "Staff-only chat sends pass guest_facing=False"). Core's
``Messaging.send(chat_id, text, *, guest_facing=True)`` only appends
``knowledge/disclosure.md`` when ``guest_facing`` is left True; the shared
core test (test_core_adapters_mock_csv.py::test_staff_chat_gets_no_guest_disclosure)
proves that primitive works. This file proves the three real call sites in
this repo actually pass ``guest_facing=False``:
``tools/briefing.py:dispatch_daily_briefing``,
``tools/scheduling.py:dispatch_weekly_rota`` and
``tools/swaps.py:dispatch_swap`` - end to end, through the mock messaging
adapter's own outbox (``data/exports/sent_messages.jsonl``).
"""

from __future__ import annotations

import json
import os
import sys
from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(REPO_ROOT / "tools") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "tools"))

from core.config import load_settings, sub_data_dir  # noqa: E402
from core.review import approve  # noqa: E402
from core.store import Store  # noqa: E402

import briefing  # noqa: E402
import scheduling  # noqa: E402
import store_ext  # noqa: E402
import swaps  # noqa: E402

WEEK_START = date(2026, 8, 31)
#: distinctive marker, so a false pass ("no file -> nothing appended
#: either way") cannot hide a bug - test_core_adapters_mock_csv.py already
#: proves with_disclosure() itself works once the file exists.
DISCLOSURE = "DISCLOSURE-MARKER: drafted with AI assistance."


def _settings():
    return load_settings(provider="mock", mode="live")


def _seed_disclosure_file() -> None:
    """The `_hermetic_agent_repo_root` autouse fixture (conftest.py) already
    pointed AGENT_REPO_ROOT at a throwaway copy of knowledge/ for this test -
    just add the one file `with_disclosure` looks for and does not ship by
    default (knowledge/disclosure.md is gitignored, only the .example ships)."""
    root = Path(os.environ["AGENT_REPO_ROOT"])
    knowledge = root / "knowledge"
    knowledge.mkdir(parents=True, exist_ok=True)
    (knowledge / "disclosure.md").write_text(DISCLOSURE, encoding="utf-8")


def _sent_texts() -> list[str]:
    outbox = sub_data_dir("exports") / "sent_messages.jsonl"
    if not outbox.exists():
        return []
    return [json.loads(line)["text"]
            for line in outbox.read_text(encoding="utf-8").splitlines() if line]


def test_weekly_rota_notification_carries_no_disclosure():
    _seed_disclosure_file()
    settings = _settings()
    store = Store(settings)
    store_ext.ensure_schema(store)
    try:
        item, _ = scheduling.build_weekly_rota(settings, store, week_start=WEEK_START,
                                               provider="mock", source="fixtures")
        approve(store, item.id)
        [claimed] = store.claim_for_send(limit=1)
        result = scheduling.dispatch_weekly_rota(settings, store, claimed)
        assert result["notified"] > 0
        texts = _sent_texts()
        assert texts, "expected at least one staff shift notification"
        assert not any(DISCLOSURE in t for t in texts)
    finally:
        store.close()


def test_daily_briefing_carries_no_disclosure():
    _seed_disclosure_file()
    settings = _settings()
    store = Store(settings)
    store_ext.ensure_schema(store)
    try:
        rota, _ = scheduling.build_weekly_rota(settings, store, week_start=WEEK_START,
                                               provider="mock", source="fixtures")
        approve(store, rota.id)
        [claimed_rota] = store.claim_for_send(limit=1)
        scheduling.dispatch_weekly_rota(settings, store, claimed_rota)

        item, is_new = briefing.build_daily_briefing(settings, store, date_str="2026-08-31",
                                                      day_offset=0, provider="mock",
                                                      source="fixtures")
        assert is_new
        approve(store, item.id)
        [claimed_brief] = store.claim_for_send(limit=1)
        briefing.dispatch_daily_briefing(settings, store, claimed_brief)
        texts = _sent_texts()
        assert texts, "expected at least one personal brief"
        assert not any(DISCLOSURE in t for t in texts)
    finally:
        store.close()


def test_shift_swap_notification_carries_no_disclosure():
    _seed_disclosure_file()
    settings = _settings()
    store = Store(settings)
    store_ext.ensure_schema(store)
    try:
        req = {"id": "t-swap-nodisc", "staff_id": "hk-06", "date": "2026-09-02", "reason": "swap"}
        item, _ = swaps.process_request(settings, store, req, source="fixtures")
        approve(store, item.id)
        [claimed] = store.claim_for_send(limit=1)
        swaps.dispatch_swap(settings, store, claimed)
        texts = _sent_texts()
        assert texts, "expected at least one swap-cover notification"
        assert not any(DISCLOSURE in t for t in texts)
    finally:
        store.close()
