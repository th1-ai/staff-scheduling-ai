#!/usr/bin/env python3
"""tools/doctor.py - is The Planner configured and reachable right now?

    make doctor
    python3 tools/doctor.py

Runs the generic core.doctor checks (python, deps, config, .env, hotel
identity, mode, llm provider, every adapter, the store, knowledge) plus
this agent's own: the ten rule toggles, the working-time limits, the
prompt files, whether the Staff Briefing sub-agent is on, and whether
``data/imports/swap_requests.csv`` is connected. Exits 0 when everything
passed, 1 when a FAIL line needs fixing. Never a traceback.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.config import ConfigError, Settings, load_settings, sub_data_dir  # noqa: E402
from core.doctor import Check, FAIL, PASS, WARN, print_table, run_checks  # noqa: E402

RULE_KEYS = ("personal-rules", "quota-hard-cap", "fairness-quota", "cost-optimise",
            "hk-team-mix", "hk-vip-floor", "hk-supervisor-span", "fnb-ratios",
            "fnb-group-senior", "fnb-sommelier")


def check_rules(settings: Settings) -> Check:
    rules = settings.agent_get("rules", {}) or {}
    missing = [k for k in RULE_KEYS if k not in rules]
    if missing:
        return Check("scheduling rules", FAIL, f"missing {', '.join(missing)} in config/agent.yaml",
                     "Copy config/agent.example.yaml's rules: block - all ten keys must be present.")
    on = sum(1 for k in RULE_KEYS if rules.get(k))
    return Check("scheduling rules", PASS, f"{on}/{len(RULE_KEYS)} rule(s) on")


def check_working_time(settings: Settings) -> Check:
    wt = settings.agent_get("working_time", {}) or {}
    needed = ("max_consecutive_days", "min_rest_hours", "quota_headroom_floor_hours")
    missing = [k for k in needed if k not in wt]
    if missing:
        return Check("working-time limits", FAIL, f"missing {', '.join(missing)}",
                     "These are always-on hard constraints - see config/agent.example.yaml.")
    return Check("working-time limits", PASS,
                 f"max {wt['max_consecutive_days']} consecutive days, "
                 f"{wt['min_rest_hours']}h min rest, {wt['quota_headroom_floor_hours']}h "
                 f"quota floor")


def check_prompts() -> Check:
    missing = [p for p in ("prompts/duty-manager-briefing.md", "prompts/staff-brief.md",
                           "prompts/schemas/duty-manager-briefing.json",
                           "prompts/schemas/staff-brief.json")
              if not (REPO_ROOT / p).is_file()]
    if missing:
        return Check("prompts", FAIL, f"missing {', '.join(missing)}",
                     "These ship with the repo - restore them from git.")
    return Check("prompts", PASS, "duty-manager-briefing.md + staff-brief.md + schemas present")


def check_fixtures() -> Check:
    missing = [p for p in ("fixtures/hotel/staff.json", "fixtures/hotel/room_status.json",
                           "fixtures/hotel/restaurant_covers.json", "fixtures/hotel/guest_notes.json")
              if not (REPO_ROOT / p).is_file()]
    if missing:
        return Check("fixtures", FAIL, f"missing {', '.join(missing)}",
                     "These ship with the repo - restore them from git.")
    return Check("fixtures", PASS, "staff/room_status/restaurant_covers/guest_notes present")


def check_swap_requests_file() -> Check:
    """The exact file ``tools/swaps.py:load_requests`` reads on a real run
    (``source="auto"``) - never the bundled fixtures, see that function's
    docstring. WARN, not FAIL: a hotel with no swaps yet is a normal state,
    but real runs process zero requests until this file exists.
    """
    csv_path = sub_data_dir("imports") / "swap_requests.csv"
    if csv_path.exists():
        return Check("swap requests file", PASS, f"connected: {csv_path}")
    return Check("swap requests file", WARN, "no swap requests file connected",
                 "Real runs (`make run`, `tools/swaps.py check`) process zero swap/sick "
                 "requests until data/imports/swap_requests.csv exists - it is never "
                 "replaced by the bundled fixtures. Columns: id, staff_id, date, reason, "
                 "note. See docs/integrations.md.")


def check_staff_briefing(settings: Settings) -> Check:
    enabled = bool(settings.agent_get("subagents.staff_briefing.enabled", False))
    if enabled:
        return Check("Staff Briefing sub-agent", PASS,
                     "on - runs daily off the published rota (workflows/25-staff-briefing.md)")
    return Check("Staff Briefing sub-agent", WARN, "off (the default)",
                 "Turn it on in config/agent.yaml: subagents.staff_briefing.enabled once you "
                 "also want a personal daily brief per person - see workflows/25-staff-briefing.md.")


def main() -> int:
    try:
        settings = load_settings()
    except ConfigError as exc:
        checks = run_checks(None) + [Check("config", FAIL, str(exc),
                                           "Fix config/hotel.yaml or config/agent.yaml.")]
        return print_table(checks, title="The Planner - doctor")

    checks = run_checks(settings, extra=[check_rules, check_working_time, check_staff_briefing])
    checks.append(check_prompts())
    checks.append(check_fixtures())
    checks.append(check_swap_requests_file())
    return print_table(checks, title="The Planner - doctor")


if __name__ == "__main__":
    raise SystemExit(main())
