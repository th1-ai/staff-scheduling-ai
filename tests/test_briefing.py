"""Tests for tools/briefing.py - the Staff Briefing sub-agent ("The Sergeant")."""

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
from core.review import approve  # noqa: E402
from core.store import Store  # noqa: E402

import briefing  # noqa: E402
import engine  # noqa: E402
import scheduling  # noqa: E402
import store_ext  # noqa: E402

WEEK_START = date(2026, 8, 31)


def _settings(mode="shadow"):
    return load_settings(provider="mock", mode=mode)


def test_relevant_notes_matches_housekeeping_by_room():
    shift = engine.ShiftAssignment(staff_id="hk-08", staff_name="Priya Nair",
                                   department="housekeeping", assignment="Floor 1",
                                   role_in_shift="Attendant", start_time="08:00",
                                   end_time="16:30", hours=8.5, cost=0.0,
                                   rooms=["101", "102", "103"])
    guest_notes_day0 = store_ext.load_guest_notes(source="fixtures")["0"]
    notes = briefing.relevant_notes(shift, guest_notes_day0, [])
    assert any("101" in n and "anniversary" in n.lower() for n in notes)


def test_relevant_notes_ignores_rooms_not_on_the_shift():
    shift = engine.ShiftAssignment(staff_id="x", staff_name="X", department="housekeeping",
                                   assignment="Floor 3", role_in_shift="Attendant",
                                   start_time="08:00", end_time="16:30", hours=8.5, cost=0.0,
                                   rooms=["301", "302"])
    guest_notes_day0 = store_ext.load_guest_notes(source="fixtures")["0"]
    notes = briefing.relevant_notes(shift, guest_notes_day0, [])
    assert notes == []


def test_relevant_notes_matches_fnb_by_service():
    shift = engine.ShiftAssignment(staff_id="fb-02", staff_name="Grace Adeyemi",
                                   department="fnb", assignment="Dinner",
                                   role_in_shift="Senior server", start_time="17:00",
                                   end_time="23:00", hours=6.0, cost=0.0, service="dinner")
    covers_day2 = store_ext.load_restaurant_covers(source="fixtures")["2"]  # the group/dietary day
    notes = briefing.relevant_notes(shift, {}, covers_day2)
    assert notes and "group booking" in notes[0]


def test_build_daily_briefing_returns_none_before_the_week_is_published():
    settings = _settings()
    store = Store(settings)
    store_ext.ensure_schema(store)
    try:
        item, is_new = briefing.build_daily_briefing(settings, store, date_str="2026-08-31",
                                                      day_offset=0, provider="mock",
                                                      source="fixtures")
        assert item is None
        assert is_new is False
    finally:
        store.close()


def test_build_daily_briefing_after_publish_writes_one_brief_per_person():
    settings = load_settings(provider="mock", mode="live")
    store = Store(settings)
    store_ext.ensure_schema(store)
    try:
        rota, _ = scheduling.build_weekly_rota(settings, store, week_start=WEEK_START,
                                               provider="mock", source="fixtures")
        approve(store, rota.id)
        [claimed] = store.claim_for_send(limit=1)
        scheduling.dispatch_weekly_rota(settings, store, claimed)

        item, is_new = briefing.build_daily_briefing(settings, store, date_str="2026-08-31",
                                                      day_offset=0, provider="mock",
                                                      source="fixtures")
        assert is_new
        assert item.kind == "staff_briefing"
        assert len(item.draft["briefs"]) == 29  # Monday's on-duty headcount
        first = item.draft["briefs"][0]
        assert first["brief"]
    finally:
        store.close()


# --------------------------------------------------------------------------
# language allowlist (subagents.staff_briefing.languages) - MAJOR finding 3
# --------------------------------------------------------------------------
def test_resolve_briefing_language_passes_through_an_allowed_language():
    language, reason = briefing.resolve_briefing_language("fr", ["en", "fr", "pt"], "en")
    assert language == "fr"
    assert reason is None


def test_resolve_briefing_language_unsupported_code_falls_back_with_a_reason():
    language, reason = briefing.resolve_briefing_language("ja", ["en", "fr", "pt"], "en")
    assert language == "en"
    assert language != "mock"  # never the mock provider's free-text placeholder
    assert reason and "ja" in reason and "en" in reason


def test_resolve_briefing_language_blank_falls_back_with_a_reason():
    language, reason = briefing.resolve_briefing_language("", ["en", "fr", "pt"], "en")
    assert language == "en"
    assert reason and "blank" in reason


def test_resolve_briefing_language_empty_allowlist_is_permissive_not_a_lockout():
    # A misconfigured (empty) allowlist should not brick every brief - it is
    # treated as "no restriction configured", not "nothing is allowed".
    language, reason = briefing.resolve_briefing_language("ja", [], "en")
    assert language == "ja"
    assert reason is None


def test_build_daily_briefing_flags_needs_human_for_an_unsupported_language():
    """Regression for the MAJOR finding: a staff member whose language is not
    in subagents.staff_briefing.languages gets the hotel default language
    and the whole day's batch is flagged needs_human with the reason -
    never a literal "mock" or a blank string handed to the model.
    """
    settings = load_settings(provider="mock", mode="live")
    # hk-05 Lucia Ferreira's language is "pt" in fixtures/hotel/staff.json -
    # remove it from the allowlist so her language is no longer supported.
    languages = settings.agent_get("subagents.staff_briefing.languages", [])
    settings.agent.setdefault("subagents", {}).setdefault("staff_briefing", {})["languages"] = [
        lang for lang in languages if lang != "pt"]
    store = Store(settings)
    store_ext.ensure_schema(store)
    try:
        rota, _ = scheduling.build_weekly_rota(settings, store, week_start=WEEK_START,
                                               provider="mock", source="fixtures")
        approve(store, rota.id)
        [claimed] = store.claim_for_send(limit=1)
        scheduling.dispatch_weekly_rota(settings, store, claimed)

        item, is_new = briefing.build_daily_briefing(settings, store, date_str="2026-08-31",
                                                      day_offset=0, provider="mock",
                                                      source="fixtures")
        assert is_new
        assert item.review_status == "needs_human"
        assert item.draft["warnings"]
        assert any("pt" in w and "Lucia Ferreira" in w for w in item.draft["warnings"])

        lucia = next(b for b in item.draft["briefs"] if b["staff_id"] == "hk-05")
        default_lang = settings.hotel.languages[0]
        assert lucia["language"] == default_lang
        assert lucia["language"] != "pt"
        assert lucia["language"] != "mock"
    finally:
        store.close()
