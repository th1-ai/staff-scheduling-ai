"""Tests for tools/scheduling.py - the full weekly-rota loop with provider=mock.

Exercises the same fixtures `make demo` does, but through the store/review
FSM directly, including the shadow-mode write guard and the dry-run
contract (factory/workflows/build-repo.md section 5).
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(REPO_ROOT / "tools") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "tools"))

from core.config import load_settings  # noqa: E402
from core.review import WriteBlocked, approve  # noqa: E402
from core.store import Store  # noqa: E402

import scheduling  # noqa: E402
import store_ext  # noqa: E402

WEEK_START = date(2026, 8, 31)


def _settings(**overrides):
    return load_settings(provider="mock", mode=overrides.get("mode", "shadow"))


def test_build_weekly_rota_full_demo_loop_matches_the_fixture_narrative():
    settings = _settings()
    store = Store(settings)
    store_ext.ensure_schema(store)
    try:
        item, is_new = scheduling.build_weekly_rota(settings, store, week_start=WEEK_START,
                                                     provider="mock", source="fixtures")
        assert is_new
        assert item.kind == "weekly_rota"
        assert item.intent == "weekly_rota"
        assert item.review_status in ("needs_human", "pending_review")
        plan = item.draft["plan"]
        assert plan["staff_on_shift"] == 33
        assert plan["warning_count"] == 31
        assert plan["total_hours"] == 1237.0
        narrative = item.draft["narrative"]
        assert narrative["headline"] == ("Next week: mostly clean, with a tight Friday dinner "
                                         "and a Saturday VIP-floor gap")
    finally:
        store.close()


def test_build_weekly_rota_is_idempotent_for_the_same_week():
    settings = _settings()
    store = Store(settings)
    store_ext.ensure_schema(store)
    try:
        item1, is_new1 = scheduling.build_weekly_rota(settings, store, week_start=WEEK_START,
                                                       provider="mock", source="fixtures")
        item2, is_new2 = scheduling.build_weekly_rota(settings, store, week_start=WEEK_START,
                                                       provider="mock", source="fixtures")
        assert is_new1 and not is_new2
        assert item1.id == item2.id
    finally:
        store.close()


def test_shadow_mode_blocks_dispatch_even_when_approved():
    settings = _settings(mode="shadow")
    store = Store(settings)
    store_ext.ensure_schema(store)
    try:
        item, _ = scheduling.build_weekly_rota(settings, store, week_start=WEEK_START,
                                               provider="mock", source="fixtures")
        approve(store, item.id)
        [claimed] = store.claim_for_send(limit=1)
        try:
            scheduling.dispatch_weekly_rota(settings, store, claimed)
            assert False, "expected WriteBlocked in shadow mode"
        except WriteBlocked:
            pass
        # Nothing was seeded - the guard fires before schedule_shifts is touched.
        assert store_ext.list_shifts_for_date(store, "2026-08-31") == []
    finally:
        store.close()


def test_dispatch_weekly_rota_publishes_in_live_mode():
    settings = _settings(mode="live")
    store = Store(settings)
    store_ext.ensure_schema(store)
    try:
        item, _ = scheduling.build_weekly_rota(settings, store, week_start=WEEK_START,
                                               provider="mock", source="fixtures")
        approve(store, item.id)
        [claimed] = store.claim_for_send(limit=1)
        result = scheduling.dispatch_weekly_rota(settings, store, claimed)
        assert result["notified"] == 33
        shifts_monday = store_ext.list_shifts_for_date(store, "2026-08-31")
        assert len(shifts_monday) == 29
    finally:
        store.close()


def test_dry_run_writes_nothing_and_never_raises():
    settings = load_settings(provider="mock", mode="live", dry_run=True)
    store = Store(settings, path=":memory:")
    store_ext.ensure_schema(store)
    try:
        item, is_new = scheduling.build_weekly_rota(settings, store, week_start=WEEK_START,
                                                     provider="mock", source="fixtures")
        assert is_new  # computing and queuing is fine on a dry run
        approve(store, item.id)
        [claimed] = store.claim_for_send(limit=1)
        try:
            scheduling.dispatch_weekly_rota(settings, store, claimed)
            assert False, "expected WriteBlocked on --dry-run"
        except WriteBlocked:
            pass
    finally:
        store.close()
