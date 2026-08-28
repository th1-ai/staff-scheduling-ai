"""Tests for tools/engine.py - the deterministic weekly rota and swap engine.

Pure unit tests, no store, no LLM: every test builds its own small
StaffMember/RoomStatus/CoverService list so the rule under test is obvious
from the test itself. See docs/how-it-works.md.
"""

from __future__ import annotations

import sys
from datetime import date, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(REPO_ROOT / "tools") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "tools"))

import engine  # noqa: E402

RULES = {"personal-rules": True, "quota-hard-cap": True, "fairness-quota": True,
        "cost-optimise": True, "hk-team-mix": True, "hk-vip-floor": True,
        "hk-supervisor-span": True, "fnb-ratios": True, "fnb-group-senior": True,
        "fnb-sommelier": True}
ALL_RULES_OFF = {k: False for k in RULES}
CONFIG = {
    "shift_minutes": 390, "service_minutes": {"checkout": 35, "stayover": 20, "arrival": 10},
    "housekeeping": {"max_team_size": 4, "senior_years": 8, "max_supervisors": 2,
                     "max_public_area": 2, "max_laundry": 2, "shift_start": "08:00",
                     "shift_end": "16:30"},
    "fnb": {"covers_per_server": 15, "flat_ratio": 20, "runner_ratio": 5,
           "bartenders_dinner": 1, "bartenders_dinner_ratios": 2, "baristas_breakfast": 1,
           "host_covers_floor": 40, "sommelier_covers_floor": 40, "group_senior_years": 5},
    "working_time": {"max_consecutive_days": 5, "min_rest_hours": 11,
                     "quota_headroom_floor_hours": 8},
    "cost_savings_floor": 100,
}


def _staff(id, role, department="housekeeping", years=3, cost=15.0, quota=173, mtd=100,
          unavailable=None, weekend_rule="any", tags=None):
    return engine.StaffMember(id=id, name=id.title(), department=department, role=role,
                              years_experience=years, hourly_cost=cost,
                              monthly_quota_hours=quota, hours_worked_mtd=mtd,
                              days_unavailable=unavailable or [], weekend_rule=weekend_rule,
                              tags=tags or [])


# --------------------------------------------------------------------------
# availability
# --------------------------------------------------------------------------
def test_personal_day_off_excludes_staff_on_that_weekday():
    staff = [_staff("a", "Room Attendant", unavailable=["Wed"])]
    available, counts = engine.available_staff(staff, "Wed", RULES, CONFIG, {})
    assert available == []
    assert counts["day_off"] == 1


def test_no_weekends_excludes_on_saturday_and_sunday_only():
    staff = [_staff("a", "Room Attendant", weekend_rule="no_weekends")]
    for weekday in ("Sat", "Sun"):
        available, _ = engine.available_staff(staff, weekday, RULES, CONFIG, {})
        assert available == []
    for weekday in ("Mon", "Tue", "Wed", "Thu", "Fri"):
        available, _ = engine.available_staff(staff, weekday, RULES, CONFIG, {})
        assert available == [staff[0]]


def test_quota_floor_is_unconditional_the_quota_hard_cap_toggle_cannot_remove_it():
    """The quota headroom floor is a hard constraint like max-consecutive-days
    and min-rest: it is never gated by rules.quota-hard-cap (or any other
    toggle) - see tools/engine.py:available_staff and
    factory/workflows/build-repo.md section 5. Regression for the demo's
    former "quota-hard-cap OFF -> 0 staff unavailable" bug.
    """
    staff = [_staff("a", "Room Attendant", quota=173, mtd=170)]  # headroom = 3 < floor 8
    available_on, counts_on = engine.available_staff(staff, "Mon", RULES, CONFIG, {})
    rules_off = {**RULES, "quota-hard-cap": False}
    available_off, counts_off = engine.available_staff(staff, "Mon", rules_off, CONFIG, {})
    assert counts_on["quota"] == 1 and available_on == []
    assert counts_off["quota"] == 1 and available_off == []


def test_all_rules_off_still_enforces_the_quota_floor():
    """Regression: with every one of the ten rule toggles off, a below-floor
    person is still excluded from the candidate pool - the floor is not a
    rule, it cannot be turned off by turning rules off.
    """
    staff = [_staff("a", "Room Attendant", quota=173, mtd=170)]  # headroom = 3 < floor 8
    available, counts = engine.available_staff(staff, "Mon", ALL_RULES_OFF, CONFIG, {})
    assert available == []
    assert counts["quota"] == 1


def test_all_rules_off_a_full_week_plan_never_assigns_a_below_floor_person():
    """Same regression, end to end: build_week_plan() over a full week, every
    rule off, must never put the below-floor person on a single shift."""
    below_floor = _staff("under", "Room Attendant", quota=173, mtd=170)  # headroom = 3 < floor 8
    ok = _staff("ok", "Room Attendant", quota=173, mtd=50)               # headroom = 123
    staff = [below_floor, ok]
    rooms_by_day = {str(i): [engine.RoomStatus(room_number="101", floor=1, status="checkout")]
                    for i in range(7)}
    plan = engine.build_week_plan(staff, rooms_by_day, {}, ALL_RULES_OFF, CONFIG,
                                  date(2026, 8, 31))
    assigned_ids = {sh.staff_id for day in plan.days for sh in day.shifts}
    assert "under" not in assigned_ids


def test_quota_hard_cap_is_a_soft_preference_never_excludes_above_floor_people():
    """quota-hard-cap ON only re-orders who is picked first among people who
    are already above the hard floor - it never removes anyone from
    eligibility. Off, ranking falls back to roster order (id).
    """
    requester = _staff("req", "Room Attendant")
    near_quota = _staff("a-near-quota", "Room Attendant", quota=173, mtd=155)  # headroom = 18
    far_quota = _staff("z-far-quota", "Room Attendant", quota=173, mtd=50)    # headroom = 123
    staff = [requester, near_quota, far_quota]
    request = {"id": "t-quota-soft", "staff_id": "req", "date": "2026-09-02", "reason": "swap"}
    rules_on = {**RULES, "quota-hard-cap": True, "fairness-quota": False, "cost-optimise": False}
    rules_off = {**RULES, "quota-hard-cap": False, "fairness-quota": False, "cost-optimise": False}

    resolution_on = engine.resolve_swap(staff, request, set(), rules_on, CONFIG)
    resolution_off = engine.resolve_swap(staff, request, set(), rules_off, CONFIG)

    # Both candidates are above the floor - the soft preference only changes
    # the pick order, never who is eligible.
    assert resolution_on.candidate_id == "z-far-quota"
    assert resolution_off.candidate_id == "a-near-quota"  # roster order (id) with no preference


def test_resolve_swap_all_rules_off_never_offers_a_below_floor_replacement():
    """Same regression as the availability tests, for the swap path: with
    every rule off, resolve_swap must still refuse a below-floor candidate.
    """
    requester = _staff("req", "Room Attendant")
    below_floor = _staff("under", "Room Attendant", quota=173, mtd=170)  # headroom = 3 < floor 8
    staff = [requester, below_floor]
    request = {"id": "t-quota-swap-off", "staff_id": "req", "date": "2026-09-02",
              "reason": "swap"}
    resolution = engine.resolve_swap(staff, request, set(), ALL_RULES_OFF, CONFIG)
    assert resolution.candidate_id is None
    assert "no eligible cover" in resolution.warnings[0].lower()


def test_max_consecutive_days_excludes_on_the_sixth_day():
    staff = [_staff("a", "Room Attendant")]
    history = {"a": {"consecutive_days": 5, "last_shift_end": None}}
    available, counts = engine.available_staff(staff, "Sat", RULES, CONFIG, history)
    assert available == []
    assert counts["consecutive_days"] == 1


# --------------------------------------------------------------------------
# housekeeping team assembly
# --------------------------------------------------------------------------
def test_vip_floor_promotes_most_experienced_junior_when_no_vip_cleared_senior_free():
    rooms = [engine.RoomStatus(room_number="101", floor=1, status="checkout", vip=True)]
    pool = [_staff("junior-a", "Room Attendant", years=2),
           _staff("junior-b", "Room Attendant", years=5)]
    shifts, warnings, _log = engine.build_housekeeping(pool, rooms, RULES, CONFIG)
    leads = [s for s in shifts if s.role_in_shift == "Team lead"]
    assert leads and leads[0].staff_id == "junior-b"  # most experienced of the two
    assert any("No vip_cleared senior" in w for w in warnings)
    assert any("acting" in s.note for s in leads)


def test_short_staffed_floor_is_flagged_not_hidden():
    rooms = [engine.RoomStatus(room_number=f"10{i}", floor=1, status="checkout")
            for i in range(4)]  # 4 checkouts = 140 min -> needed 1 (<390min) but team-mix wants 2
    shifts, warnings, _log = engine.build_housekeeping([], rooms, RULES, CONFIG)
    assert shifts == []
    assert any("short-staffed" in w for w in warnings)


# --------------------------------------------------------------------------
# restaurant
# --------------------------------------------------------------------------
def test_group_and_dietary_flag_pulls_senior_server_first():
    covers = [engine.CoverService(service="dinner", covers=20, dietary=["nut allergy"],
                                  is_group=True, occasion="anniversary")]
    pool = [_staff("junior", "Server", department="fnb", years=2),
           _staff("senior", "Senior Server", department="fnb", years=6, tags=["allergy_trained"])]
    shifts, _warnings, log = engine.build_fnb(pool, covers, RULES, CONFIG, {}, date(2026, 9, 2))
    senior_shift = next(s for s in shifts if s.staff_id == "senior")
    assert senior_shift.role_in_shift == "Senior server"
    assert any("senior" in line.lower() for line in log)


def test_min_rest_excludes_dinner_worker_from_next_days_breakfast():
    covers = [engine.CoverService(service="breakfast", covers=20)]
    pool = [_staff("a", "Server", department="fnb")]
    history = {"a": {"consecutive_days": 1,
                     "last_shift_end": datetime(2026, 9, 1, 23, 0)}}  # dinner ended 23:00
    shifts, warnings, _log = engine.build_fnb(pool, covers, RULES, CONFIG, history,
                                              date(2026, 9, 2))  # breakfast starts 06:30 -> 7.5h rest
    assert not any(s.staff_id == "a" for s in shifts)
    assert any("rested only" in w for w in warnings)


def test_sommelier_pulled_for_wine_occasion_even_under_the_covers_floor():
    covers = [engine.CoverService(service="dinner", covers=10, occasion="wine tasting dinner")]
    pool = [_staff("som", "Sommelier", department="fnb")]
    shifts, _warnings, _log = engine.build_fnb(pool, covers, RULES, CONFIG, {}, date(2026, 9, 1))
    assert any(s.role_in_shift == "Sommelier" for s in shifts)
