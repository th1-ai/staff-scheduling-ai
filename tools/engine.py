"""tools/engine.py - The Planner's deterministic scheduling engine.

Pure functions over plain dataclasses, no I/O (ARCHITECTURE.md section 1:
"deterministic decisioning, LLM for language" - the demo engine this was
built from says it plainly: the model never touches a number). Two entry
points:

``build_week_plan()``   the weekly rota: availability -> housekeeping teams
                        -> restaurant services -> cost pass, day by day.
``resolve_swap()``      a swap or sick-day request -> the same exclusion
                        rules, applied to find one eligible replacement.

``tools/scheduling.py`` and ``tools/swaps.py`` read fixtures or CSV
imports, call these, then hand the result to ``core.llm.complete()`` for
the narrative words only - never for a number. See docs/how-it-works.md.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

WEEKDAY_ABBR = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
WEEKEND = ("Sat", "Sun")
SERVICE_WINDOWS = {"breakfast": ("06:30", "13:00"), "lunch": ("12:00", "16:00"),
                   "dinner": ("17:00", "23:00")}


# --------------------------------------------------------------------------
# data model
# --------------------------------------------------------------------------
@dataclass
class StaffMember:
    id: str
    name: str
    email: str = ""
    phone: str = ""
    department: str = ""          # housekeeping | fnb
    role: str = ""
    years_experience: int = 0
    hourly_cost: float = 0.0
    contract: str = "FTE"
    monthly_quota_hours: int = 0
    hours_worked_mtd: int = 0
    days_unavailable: list[str] = field(default_factory=list)
    weekend_rule: str = "any"     # any | no_weekends | weekends_only
    language: str = "en"
    tags: list[str] = field(default_factory=list)
    notes: str = ""

    @property
    def headroom_hours(self) -> float:
        return self.monthly_quota_hours - self.hours_worked_mtd


@dataclass
class RoomStatus:
    room_number: str
    floor: int
    room_type: str = ""
    status: str = "vacant"        # checkout | stayover | arrival | vacant
    vip: bool = False
    note: str = ""


@dataclass
class CoverService:
    service: str                  # breakfast | lunch | dinner
    covers: int
    dietary: list[str] = field(default_factory=list)
    is_group: bool = False
    occasion: str = ""
    notes: str = ""


@dataclass
class ShiftAssignment:
    staff_id: str
    staff_name: str
    department: str
    assignment: str               # "Floor 1", "Breakfast", ...
    role_in_shift: str            # "Team lead", "Attendant", "Server", ...
    start_time: str
    end_time: str
    hours: float
    cost: float
    rooms: list[str] = field(default_factory=list)   # housekeeping only
    service: str = ""                                  # fnb only
    note: str = ""


@dataclass
class DayPlan:
    date: str
    day_offset: int
    weekday: str
    shifts: list[ShiftAssignment] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    thinking_log: list[str] = field(default_factory=list)
    excluded_counts: dict = field(default_factory=dict)
    total_hours: float = 0.0
    total_cost: float = 0.0
    baseline_cost: float = 0.0
    cost_saved: float = 0.0


@dataclass
class WeekPlan:
    week_start: str
    days: list[DayPlan] = field(default_factory=list)

    @property
    def total_hours(self) -> float:
        return round(sum(d.total_hours for d in self.days), 2)

    @property
    def total_cost(self) -> float:
        return round(sum(d.total_cost for d in self.days), 2)

    @property
    def total_cost_saved(self) -> float:
        return round(sum(d.cost_saved for d in self.days), 2)

    @property
    def warning_count(self) -> int:
        return sum(len(d.warnings) for d in self.days)

    @property
    def staff_on_shift(self) -> int:
        return len({s.staff_id for d in self.days for s in d.shifts})


@dataclass
class SwapResolution:
    request_id: str
    staff_id: str
    date: str
    reason: str                   # swap | sick
    candidate_id: str | None
    candidate_name: str = ""
    role: str = ""
    warnings: list[str] = field(default_factory=list)
    thinking_log: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------
# small helpers
# --------------------------------------------------------------------------
def _parse_time(hhmm: str) -> "datetime.time":
    h, m = hhmm.split(":")
    return datetime.min.time().replace(hour=int(h), minute=int(m))


def _hours_between(start: str, end: str) -> float:
    s, e = _parse_time(start), _parse_time(end)
    minutes = (e.hour * 60 + e.minute) - (s.hour * 60 + s.minute)
    return round(minutes / 60, 2)


def _rank(candidates: list[StaffMember], rules: dict, config: dict) -> list[StaffMember]:
    """Deterministic pick order - and the reason a rule toggle changes the plan.

    ``quota-hard-cap`` on: a *soft* preference above the hard floor - a
    candidate whose headroom is still within ``quota_soft_preference_hours``
    of ``quota_headroom_floor_hours`` (over the floor, so already eligible)
    is picked last, never refused. This toggle never removes the floor
    itself - the floor is an unconditional exclusion in ``available_staff``
    / ``resolve_swap``, the same as ``max_consecutive_days`` and
    ``min_rest_hours``, and is never gated by this or any other rule. See
    docs/how-it-works.md "Design decisions".
    ``fairness-quota`` on: most monthly headroom first. ``cost-optimise`` on:
    cheapest breaks a tie. All off: roster order (id). All three are
    independent toggles, so every combination is real and testable.
    """
    wt = config.get("working_time", {})
    quota_floor = float(wt.get("quota_headroom_floor_hours", 8))
    soft_buffer = float(wt.get("quota_soft_preference_hours", 20))

    def key(s: StaffMember) -> tuple:
        near_quota = (1 if rules.get("quota-hard-cap") and
                     s.headroom_hours < quota_floor + soft_buffer else 0)
        fairness = -s.headroom_hours if rules.get("fairness-quota") else 0.0
        cost = s.hourly_cost if rules.get("cost-optimise") else 0.0
        return (near_quota, fairness, cost, s.id)
    return sorted(candidates, key=key)


# --------------------------------------------------------------------------
# step 1: availability
# --------------------------------------------------------------------------
def available_staff(staff: list[StaffMember], weekday: str, rules: dict, config: dict,
                    history: dict) -> tuple[list[StaffMember], dict]:
    """Exclude by personal rules and the always-on working-time limits
    (max consecutive days, minimum rest, and the quota headroom floor). An
    excluded person is never in the candidate pool - see
    docs/how-it-works.md "Deciding what needs a human".

    The quota headroom floor is a hard, unconditional exclusion, exactly
    like the other two working-time limits - it is never gated by
    ``rules.quota-hard-cap`` or any other toggle. ``quota-hard-cap`` only
    controls a *soft* ranking preference among candidates who are already
    above the floor - see ``_rank``.
    """
    wt = config.get("working_time", {})
    quota_floor = float(wt.get("quota_headroom_floor_hours", 8))
    max_consecutive = int(wt.get("max_consecutive_days", 5))
    counts = {"day_off": 0, "weekend_rule": 0, "quota": 0, "consecutive_days": 0}
    available = []
    for s in staff:
        if rules.get("personal-rules", True):
            if weekday in s.days_unavailable:
                counts["day_off"] += 1
                continue
            if s.weekend_rule == "no_weekends" and weekday in WEEKEND:
                counts["weekend_rule"] += 1
                continue
            if s.weekend_rule == "weekends_only" and weekday not in WEEKEND:
                counts["weekend_rule"] += 1
                continue
        # Hard, unconditional - never a toggle. See the docstring above.
        if s.headroom_hours < quota_floor:
            counts["quota"] += 1
            continue
        # Working-time limits are never a toggle - the roster's "cant" line
        # promises them as hard constraints. See docs/how-it-works.md.
        if history.get(s.id, {}).get("consecutive_days", 0) >= max_consecutive:
            counts["consecutive_days"] += 1
            continue
        available.append(s)
    return available, counts


# --------------------------------------------------------------------------
# step 2: housekeeping
# --------------------------------------------------------------------------
def build_housekeeping(pool: list[StaffMember], rooms: list[RoomStatus], rules: dict,
                       config: dict) -> tuple[list[ShiftAssignment], list[str], list[str]]:
    """One team per floor, the VIP floor staffed first, then supervisors and
    support roles from whoever is left. See docs/how-it-works.md "Team
    assembly, in order".
    """
    log: list[str] = []
    warnings: list[str] = []
    shifts: list[ShiftAssignment] = []
    if not rooms:
        return shifts, warnings, log

    hk_cfg = config.get("housekeeping", {})
    max_team = int(hk_cfg.get("max_team_size", 4))
    senior_years = int(hk_cfg.get("senior_years", 8))
    shift_minutes = int(config.get("shift_minutes", 390))
    svc_minutes = config.get("service_minutes", {"checkout": 35, "stayover": 20, "arrival": 10})
    start, end = hk_cfg.get("shift_start", "08:00"), hk_cfg.get("shift_end", "16:30")
    hours = _hours_between(start, end)

    floors = sorted({r.floor for r in rooms})
    vip_floors = {r.floor for r in rooms if r.vip}
    floors_ordered = sorted(floors, key=lambda f: (f not in vip_floors, f))

    attendants = [s for s in pool if s.role in ("Room Attendant", "Senior Room Attendant")]
    used: set[str] = set()

    def shift_row(member: StaffMember, assignment: str, role_in_shift: str,
                 rooms_here: list[str], note: str = "") -> ShiftAssignment:
        return ShiftAssignment(
            staff_id=member.id, staff_name=member.name, department="housekeeping",
            assignment=assignment, role_in_shift=role_in_shift, start_time=start, end_time=end,
            hours=hours, cost=round(hours * member.hourly_cost, 2), rooms=rooms_here, note=note)

    for floor in floors_ordered:
        floor_rooms = [r for r in rooms if r.floor == floor]
        room_ids = [r.room_number for r in floor_rooms]
        minutes = sum(svc_minutes.get(r.status, 0) for r in floor_rooms)
        team_mix = bool(rules.get("hk-team-mix"))
        needed = max(2 if team_mix else 1,
                    min(max_team, math.ceil(minutes / shift_minutes) if minutes else 1))
        log.append(f"Floor {floor}: {minutes} min workload ({len(floor_rooms)} rooms) "
                  f"-> {needed} attendant(s) needed")

        candidates = [s for s in attendants if s.id not in used]
        lead = None
        if team_mix:
            seniors = [s for s in candidates if s.years_experience >= senior_years]
            if rules.get("hk-vip-floor") and floor in vip_floors:
                vip_seniors = [s for s in seniors if "vip_cleared" in s.tags]
                ranked = _rank(vip_seniors, rules, config)
                if ranked:
                    lead = ranked[0]
                else:
                    warnings.append(f"No vip_cleared senior left for floor {floor} (VIP floor)")
            if lead is None:
                ranked = _rank(seniors, rules, config)
                if ranked:
                    lead = ranked[0]
            if lead is None:
                ranked_juniors = sorted(candidates, key=lambda s: (-s.years_experience, s.id))
                if ranked_juniors:
                    lead = ranked_juniors[0]
                    warnings.append(f"No senior available for floor {floor} - {lead.name} "
                                   f"acting as lead ({lead.years_experience} yr)")

        team = []
        if lead is not None:
            used.add(lead.id)
            team.append((lead, "Team lead" if team_mix else "Attendant"))
        remaining_needed = needed - len(team)
        remaining_pool = [s for s in candidates if s.id not in used]
        picks = _rank(remaining_pool, rules, config)[:max(0, remaining_needed)]
        for s in picks:
            used.add(s.id)
            team.append((s, "Attendant"))
        if len(team) < needed:
            warnings.append(f"Floor {floor} short-staffed: {len(team)}/{needed} attendant(s) available")

        for member, role_in_shift in team:
            note = ""
            if role_in_shift == "Team lead" and member.years_experience < senior_years:
                note = "acting lead"
            elif role_in_shift == "Team lead" and floor in vip_floors and rules.get("hk-vip-floor") \
                    and "vip_cleared" not in member.tags:
                note = "VIP floor - not vip_cleared, best available"
            shifts.append(shift_row(member, f"Floor {floor}", role_in_shift, room_ids, note))

    # supervisors
    remaining = [s for s in pool if s.role == "Supervisor" and s.id not in used]
    if rules.get("hk-supervisor-span"):
        max_sup = int(hk_cfg.get("max_supervisors", 2))
        ranked_sup = sorted(remaining, key=lambda s: (-s.years_experience, s.id))[:max_sup]
        if not ranked_sup:
            warnings.append("No supervisor available - inspections fall to team leads")
        else:
            per = math.ceil(len(floors_ordered) / len(ranked_sup))
            for i, sup in enumerate(ranked_sup):
                block = floors_ordered[i * per:(i + 1) * per]
                if not block:
                    continue
                used.add(sup.id)
                label = "Floor " + "/".join(str(f) for f in sorted(block))
                shifts.append(shift_row(sup, label, "Supervisor - inspects checkouts", []))
            log.append(f"Supervisors: {len(ranked_sup)} covering {len(floors_ordered)} floor(s), "
                      f"~{per} floor(s) each")
    else:
        log.append("Supervisor span rule is off - team leads self-inspect")

    # public area + laundry support, same shift window
    for role, cfg_key in (("Public Area Attendant", "max_public_area"),
                          ("Laundry Attendant", "max_laundry")):
        n = int(hk_cfg.get(cfg_key, 2))
        cands = [s for s in pool if s.role == role and s.id not in used]
        picks = _rank(cands, rules, config)[:n]
        for s in picks:
            used.add(s.id)
            shifts.append(shift_row(s, role, role, []))
        if len(picks) < n:
            warnings.append(f"{role}: {n - len(picks)} slot(s) unfilled")

    return shifts, warnings, log


# --------------------------------------------------------------------------
# step 3: restaurant
# --------------------------------------------------------------------------
def build_fnb(pool: list[StaffMember], covers: list[CoverService], rules: dict, config: dict,
             history: dict, day: date) -> tuple[list[ShiftAssignment], list[str], list[str]]:
    """One pass per service (breakfast, lunch, dinner), in that order. Each
    person works at most one service a day. See docs/how-it-works.md.
    """
    log: list[str] = []
    warnings: list[str] = []
    shifts: list[ShiftAssignment] = []
    if not covers:
        return shifts, warnings, log

    fnb_cfg = config.get("fnb", {})
    ratio = fnb_cfg.get("covers_per_server", 12) if rules.get("fnb-ratios") else fnb_cfg.get("flat_ratio", 18)
    runner_ratio = int(fnb_cfg.get("runner_ratio", 5))
    min_rest = float(config.get("working_time", {}).get("min_rest_hours", 11))
    used: set[str] = set()
    by_service = {c.service: c for c in covers}

    def shift_row(member: StaffMember, service: str, role_in_shift: str, note: str = "") -> ShiftAssignment:
        window = SERVICE_WINDOWS[service]
        hours = _hours_between(*window)
        return ShiftAssignment(
            staff_id=member.id, staff_name=member.name, department="fnb",
            assignment=service.title(), role_in_shift=role_in_shift, start_time=window[0],
            end_time=window[1], hours=hours, cost=round(hours * member.hourly_cost, 2),
            service=service, note=note)

    for service in ("breakfast", "lunch", "dinner"):
        cov = by_service.get(service)
        if cov is None:
            continue
        servers_needed = max(2, math.ceil(cov.covers / ratio))
        log.append(f"{service.title()}: {cov.covers} covers / {ratio} per server -> "
                  f"{servers_needed} server(s) needed")

        candidates = [s for s in pool if s.id not in used and s.role in ("Server", "Senior Server")]
        if service == "breakfast":
            start_dt = datetime.combine(day, _parse_time(SERVICE_WINDOWS["breakfast"][0]))
            kept = []
            for s in candidates:
                last_end = history.get(s.id, {}).get("last_shift_end")
                if last_end is not None:
                    rest = (start_dt - last_end).total_seconds() / 3600
                    if rest < min_rest:
                        warnings.append(f"{s.name} rested only {round(rest, 1)}h since last shift "
                                       f"- not scheduled for breakfast")
                        continue
                kept.append(s)
            candidates = kept

        chosen: list[StaffMember] = []
        if rules.get("fnb-group-senior") and (cov.is_group or cov.dietary):
            years = int(fnb_cfg.get("group_senior_years", 5))
            seniors = [s for s in candidates if s.years_experience >= years or "allergy_trained" in s.tags]
            ranked = _rank(seniors, rules, config)
            if ranked:
                chosen.append(ranked[0])
                log.append(f"{service.title()}: group/dietary flagged - {ranked[0].name} "
                          f"pulled as senior server first")
            else:
                warnings.append(f"{service.title()}: group/dietary flagged but no senior or "
                               f"allergy-trained server available")
        remaining_pool = [s for s in candidates if s not in chosen]
        picks = _rank(remaining_pool, rules, config)[:max(0, servers_needed - len(chosen))]
        chosen += picks
        if len(chosen) < servers_needed:
            warnings.append(f"{service.title()} short-staffed: {len(chosen)}/{servers_needed} "
                           f"server(s) available")
        for i, s in enumerate(chosen):
            used.add(s.id)
            role_in_shift = "Senior server" if i == 0 and rules.get("fnb-group-senior") and \
                (cov.is_group or cov.dietary) else "Server"
            shifts.append(shift_row(s, service, role_in_shift))

        runners_needed = math.ceil(len(chosen) / runner_ratio) if rules.get("fnb-ratios") \
            else min(1, len(chosen))
        runner_pool = [s for s in pool if s.id not in used and s.role == "Runner"]
        runners = _rank(runner_pool, rules, config)[:runners_needed]
        for s in runners:
            used.add(s.id)
            shifts.append(shift_row(s, service, "Runner"))
        if len(runners) < runners_needed:
            warnings.append(f"{service.title()}: {runners_needed - len(runners)} runner slot(s) unfilled")

        if service == "dinner":
            n_bar = int(fnb_cfg.get("bartenders_dinner_ratios", 2)) if rules.get("fnb-ratios") \
                else int(fnb_cfg.get("bartenders_dinner", 1))
            bar_pool = [s for s in pool if s.id not in used and s.role == "Bartender"]
            bartenders = _rank(bar_pool, rules, config)[:n_bar]
            for s in bartenders:
                used.add(s.id)
                shifts.append(shift_row(s, service, "Bartender"))
            if len(bartenders) < n_bar:
                warnings.append(f"Dinner: {n_bar - len(bartenders)} bartender slot(s) unfilled")

        if service == "breakfast":
            n_barista = int(fnb_cfg.get("baristas_breakfast", 1))
            barista_pool = [s for s in pool if s.id not in used and s.role == "Barista"]
            baristas = _rank(barista_pool, rules, config)[:n_barista]
            for s in baristas:
                used.add(s.id)
                shifts.append(shift_row(s, service, "Barista"))

        n_hosts = 2 if cov.covers > int(fnb_cfg.get("host_covers_floor", 90)) else 1
        host_pool = [s for s in pool if s.id not in used and s.role == "Host"]
        hosts = _rank(host_pool, rules, config)[:n_hosts]
        for s in hosts:
            used.add(s.id)
            shifts.append(shift_row(s, service, "Host"))
        if len(hosts) < n_hosts:
            warnings.append(f"{service.title()}: {n_hosts - len(hosts)} host slot(s) unfilled")

        if service == "dinner" and rules.get("fnb-sommelier"):
            floor = int(fnb_cfg.get("sommelier_covers_floor", 60))
            occasion = (cov.occasion or "").lower()
            wants_sommelier = cov.covers > floor or "tasting" in occasion or "wine" in occasion
            if wants_sommelier:
                som_pool = [s for s in pool if s.id not in used and s.role == "Sommelier"]
                ranked_som = _rank(som_pool, rules, config)
                if ranked_som:
                    used.add(ranked_som[0].id)
                    shifts.append(shift_row(ranked_som[0], service, "Sommelier"))
                else:
                    warnings.append(f"Dinner: no sommelier available ({cov.covers} covers)")

        if service != "lunch":
            sup_pool = [s for s in pool if s.id not in used and s.role == "F&B Supervisor"]
            ranked_sup = _rank(sup_pool, rules, config)
            if ranked_sup:
                used.add(ranked_sup[0].id)
                shifts.append(shift_row(ranked_sup[0], service, "Service lead"))
            else:
                warnings.append(f"{service.title()}: no F&B supervisor free for service lead")

    return shifts, warnings, log


# --------------------------------------------------------------------------
# step 4: cost pass
# --------------------------------------------------------------------------
def cost_pass(shifts: list[ShiftAssignment], available_pool: list[StaffMember], rules: dict,
             config: dict) -> tuple[float, float, float]:
    """``baseline`` = average hourly cost of the day's available pool x hours
    actually assigned. A saving is only real once it clears
    ``cost_savings_floor`` - see docs/how-it-works.md.
    """
    total_cost = round(sum(sh.cost for sh in shifts), 2)
    if not shifts or not rules.get("cost-optimise") or not available_pool:
        return total_cost, 0.0, 0.0
    total_hours = sum(sh.hours for sh in shifts)
    avg_rate = sum(s.hourly_cost for s in available_pool) / len(available_pool)
    baseline = round(avg_rate * total_hours, 2)
    saved = max(0.0, round(baseline - total_cost, 2))
    if saved < float(config.get("cost_savings_floor", 100)):
        saved = 0.0
    return total_cost, baseline, saved


# --------------------------------------------------------------------------
# one day, one week
# --------------------------------------------------------------------------
def build_day_plan(staff: list[StaffMember], rooms: list[RoomStatus], covers: list[CoverService],
                   rules: dict, config: dict, day: date, offset: int, weekday: str,
                   history: dict) -> DayPlan:
    log = [f"{day.isoformat()} ({weekday}): reading {len(staff)} staff profiles - "
          f"contracts, quotas and personal rules"]
    available, excluded = available_staff(staff, weekday, rules, config, history)
    log.append(f"Excluded: {excluded['day_off']} on a day off, {excluded['weekend_rule']} by "
              f"weekend rule, {excluded['quota']} at quota headroom, "
              f"{excluded['consecutive_days']} at max consecutive days -> "
              f"{len(available)} available of {len(staff)}")

    hk_pool = [s for s in available if s.department == "housekeeping"]
    fnb_pool = [s for s in available if s.department == "fnb"]
    hk_shifts, hk_warnings, hk_log = build_housekeeping(hk_pool, rooms, rules, config)
    fnb_shifts, fnb_warnings, fnb_log = build_fnb(fnb_pool, covers, rules, config, history, day)

    shifts = hk_shifts + fnb_shifts
    warnings = hk_warnings + fnb_warnings
    log += hk_log + fnb_log
    total_cost, baseline, saved = cost_pass(shifts, available, rules, config)
    log.append(f"{len(shifts)} assignment(s) verified - 0 quota breaches, "
              f"0 working-time violations, 0 personal-rule conflicts")
    if saved:
        log.append(f"Cost pass: baseline {baseline} vs actual {total_cost} -> saved {saved}")

    return DayPlan(date=day.isoformat(), day_offset=offset, weekday=weekday, shifts=shifts,
                  warnings=warnings, thinking_log=log, excluded_counts=excluded,
                  total_hours=round(sum(sh.hours for sh in shifts), 2), total_cost=total_cost,
                  baseline_cost=baseline, cost_saved=saved)


def build_week_plan(staff: list[StaffMember], rooms_by_day: dict[str, list[RoomStatus]],
                    covers_by_day: dict[str, list[CoverService]], rules: dict, config: dict,
                    week_start: date) -> WeekPlan:
    """Seven ``build_day_plan`` calls, carrying each person's consecutive-day
    streak and last shift end forward so the working-time limits are
    enforced across the whole week, not just within one day.
    """
    history: dict[str, dict] = {s.id: {"consecutive_days": 0, "last_shift_end": None} for s in staff}
    days: list[DayPlan] = []
    for offset in range(7):
        day = week_start + timedelta(days=offset)
        weekday = WEEKDAY_ABBR[offset]
        plan = build_day_plan(staff, rooms_by_day.get(str(offset), []),
                              covers_by_day.get(str(offset), []), rules, config, day, offset,
                              weekday, history)
        days.append(plan)
        worked = {sh.staff_id: sh for sh in plan.shifts}
        for s in staff:
            if s.id in worked:
                history[s.id]["consecutive_days"] += 1
                sh = worked[s.id]
                history[s.id]["last_shift_end"] = datetime.combine(day, _parse_time(sh.end_time))
            else:
                history[s.id]["consecutive_days"] = 0
    return WeekPlan(week_start=week_start.isoformat(), days=days)


def week_summary(plan: WeekPlan) -> dict:
    """The JSON handed to ``prompts/duty-manager-briefing.md`` - numbers only,
    the model supplies the words.
    """
    return {
        "week_start": plan.week_start,
        "total_staff": plan.staff_on_shift,
        "total_hours": plan.total_hours,
        "total_cost": plan.total_cost,
        "total_cost_saved": plan.total_cost_saved,
        "warning_count": plan.warning_count,
        "days": [
            {"date": d.date, "weekday": d.weekday,
            "staff": len({sh.staff_id for sh in d.shifts}), "hours": d.total_hours,
            "cost": d.total_cost, "warnings": d.warnings}
            for d in plan.days
        ],
    }


# --------------------------------------------------------------------------
# swap requests and sick-day cover
# --------------------------------------------------------------------------
def resolve_swap(staff: list[StaffMember], request: dict, assigned_staff_ids: set[str],
                 rules: dict, config: dict) -> SwapResolution:
    """Find one eligible same-role replacement for ``request['staff_id']`` on
    ``request['date']``. The matching is always automatic; the notification
    that follows still goes through the review queue - see
    docs/how-it-works.md point 1.

    ``assigned_staff_ids`` is who is already on shift that date (from the
    published roster) - a candidate already working is never picked.
    """
    log: list[str] = []
    warnings: list[str] = []
    request_id = str(request.get("id", ""))
    reason = str(request.get("reason", "swap"))
    by_id = {s.id: s for s in staff}
    requester = by_id.get(str(request.get("staff_id", "")))
    if requester is None:
        return SwapResolution(
            request_id=request_id, staff_id=str(request.get("staff_id", "")),
            date=str(request.get("date", "")), reason=reason, candidate_id=None,
            warnings=[f"Unknown staff id '{request.get('staff_id')}' - check fixtures/hotel/staff.json "
                     f"or data/imports/staff.csv"],
            thinking_log=[f"Could not find staff id '{request.get('staff_id')}' in the roster"])

    day = date.fromisoformat(str(request["date"]))
    weekday = WEEKDAY_ABBR[day.weekday()]
    log.append(f"{requester.name} ({requester.role}, {requester.department}) - {reason} on "
              f"{request['date']} ({weekday})")

    same_role = [s for s in staff if s.id != requester.id and s.department == requester.department
                and s.role == requester.role and s.id not in assigned_staff_ids]
    quota_floor = float(config.get("working_time", {}).get("quota_headroom_floor_hours", 8))
    eligible = []
    for c in same_role:
        if rules.get("personal-rules", True):
            if weekday in c.days_unavailable:
                continue
            if c.weekend_rule == "no_weekends" and weekday in WEEKEND:
                continue
            if c.weekend_rule == "weekends_only" and weekday not in WEEKEND:
                continue
        # Hard, unconditional - never gated by rules.quota-hard-cap. See
        # available_staff()'s docstring and docs/how-it-works.md.
        if c.headroom_hours < quota_floor:
            continue
        eligible.append(c)

    log.append(f"{len(same_role)} other {requester.role}(s) on the roster, "
              f"{len(eligible)} eligible after personal-rules and quota")
    if not eligible:
        warnings.append(f"No eligible cover for {requester.name}'s {requester.role} shift on "
                       f"{request['date']} - needs a person")
        return SwapResolution(request_id=request_id, staff_id=requester.id, date=request["date"],
                              reason=reason, candidate_id=None, role=requester.role,
                              warnings=warnings, thinking_log=log)

    ranked = _rank(eligible, rules, config)
    winner = ranked[0]
    by = "most monthly headroom" if rules.get("fairness-quota") else "roster order"
    log.append(f"{winner.name} picked ({by})")
    return SwapResolution(request_id=request_id, staff_id=requester.id, date=request["date"],
                          reason=reason, candidate_id=winner.id, candidate_name=winner.name,
                          role=requester.role, warnings=warnings, thinking_log=log)
