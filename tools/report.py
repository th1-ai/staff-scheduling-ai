#!/usr/bin/env python3
"""tools/report.py - what The Planner did, and what it cost.

    make report
    python3 tools/report.py [--since 2026-09-01]

Reads straight from core.store and tools/store_ext.py: weekly rotas and
swap/sick requests by status, the published shift ledger, brief delivery
(if the sub-agent is on), and LLM spend (zero for the mock/interactive
providers). See docs/benefits.md for what each number is meant to show.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.config import ConfigError, load_settings  # noqa: E402
from core.store import Store, StoreError  # noqa: E402

import store_ext  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--since", default=None, help="ISO date/time; spend only")
    args = parser.parse_args(argv)

    try:
        settings = load_settings()
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 1

    store = Store(settings)
    store_ext.ensure_schema(store)
    try:
        print(f"The Planner - report ({settings.hotel.name}, mode {settings.mode})\n")

        counts = store.counts()
        rotas = store.list_items(kind="weekly_rota", limit=1000)
        swap_items = store.list_items(kind="swap", limit=1000)
        briefing_items = store.list_items(kind="staff_briefing", limit=1000)
        print(f"Weekly rotas queued: {len(rotas)}")
        print(f"Swap/sick requests resolved: {len(swap_items)}")
        print(f"Staff briefing batches queued: {len(briefing_items)}")
        print(f"Review queue by status: "
             f"{', '.join(f'{k}={v}' for k, v in sorted(counts.items())) or '(empty)'}\n")

        published_weeks = {r["run_item_id"] for r in
                           store.db.execute("SELECT DISTINCT run_item_id FROM schedule_shifts")
                           .fetchall()}
        total_shifts = store.db.execute("SELECT COUNT(*) AS n FROM schedule_shifts").fetchone()["n"]
        print(f"Published weeks: {len(published_weeks)} ({total_shifts} shift row(s) total)")

        no_candidate = sum(1 for i in swap_items if not (i.draft or {}).get("candidate_id"))
        print(f"Swap/sick requests with no eligible cover: {no_candidate}/{len(swap_items)}")

        briefs_sent = store.db.execute(
            "SELECT COUNT(*) AS n FROM schedule_briefs WHERE delivered=1").fetchone()["n"]
        print(f"Staff briefs delivered: {briefs_sent}")

        usage = store.usage_totals(since=args.since)
        print(f"\nLLM calls: {usage['calls']}, tokens in/out: "
             f"{usage['input_tokens']}/{usage['output_tokens']}, "
             f"spend: {store_ext.money(usage['cost_usd'], settings.hotel.currency)}")
        if settings.llm.provider in ("mock", "interactive"):
            print(f"(provider is '{settings.llm.provider}' - spend is always zero)")
        return 0
    except StoreError as exc:
        print(f"cannot do that: {exc}", file=sys.stderr)
        return 1
    finally:
        store.close()


if __name__ == "__main__":
    raise SystemExit(main())
