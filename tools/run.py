#!/usr/bin/env python3
"""tools/run.py - The Planner's main loop.

    python3 tools/run.py --once
    python3 tools/run.py --watch
    python3 tools/run.py --once --dry-run
    python3 tools/run.py --once --swaps-only
    python3 tools/run.py --once --provider mock

One pass: (1) build next week's rota if it has not been built yet
(``tools/scheduling.py``, idempotent - a no-op most days of the week), (2)
resolve every new swap/sick-day request (``tools/swaps.py``). Nothing is
published or reassigned until a human approves it - see
workflows/80-review.md. ``--swaps-only`` skips step 1, for the every-15-min
cadence in ``config/agent.yaml: schedule.swaps`` - see docs/how-it-works.md
"What runs when".

Exit codes: 0 ok, 3 waiting on an `interactive` answer (see the message),
1 a real error.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.adapters.base import AdapterError  # noqa: E402
from core.config import ConfigError, load_settings  # noqa: E402
from core.llm import LLMError, LLMPendingInteractive  # noqa: E402
from core.log import Run, get_logger, summary_line  # noqa: E402
from core.store import Store  # noqa: E402

import scheduling  # noqa: E402
import store_ext  # noqa: E402
import swaps  # noqa: E402

log = get_logger("run")


def one_pass(settings, store, *, provider: str | None, swaps_only: bool,
            source: str = "auto") -> tuple[int, dict]:
    stats = {"processed": 0, "drafted": 0, "needs_human": 0, "sent": 0, "swaps_checked": 0}
    with Run("main", settings, store) as run:
        if not swaps_only:
            try:
                item, is_new = scheduling.build_weekly_rota(settings, store, provider=provider,
                                                            source=source)
            except LLMPendingInteractive as exc:
                run.stats = dict(stats)
                print(str(exc))
                return 3, stats
            if is_new:
                stats["processed"] += 1
                stats["drafted"] += 1
                if item.review_status == "needs_human":
                    stats["needs_human"] += 1
                if not settings.dry_run:
                    log.info("weekly rota queued", item_id=item.id, status=item.review_status)

        for req in swaps.load_requests(source=source):
            item, is_new = swaps.process_request(settings, store, req, source=source)
            if not is_new:
                continue
            stats["swaps_checked"] += 1
            stats["processed"] += 1
            stats["drafted"] += 1
            if item.review_status == "needs_human":
                stats["needs_human"] += 1
            if not settings.dry_run:
                log.info("swap request resolved", item_id=item.id, status=item.review_status)

        reaped = store.reap_stuck_sending()
        if reaped and not settings.dry_run:
            log.warn("reaped stuck sends", count=len(reaped))
        run.stats = dict(stats)
    return 0, stats


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument("--once", action="store_true", help="run a single pass (default)")
    mode_group.add_argument("--watch", action="store_true",
                            help="keep running on the configured interval")
    parser.add_argument("--dry-run", action="store_true",
                        help="compute everything, write nothing, even in live mode")
    parser.add_argument("--swaps-only", action="store_true",
                        help="skip the weekly rota build - just resolve swap/sick requests")
    parser.add_argument("--provider", default=None,
                        help="override llm.provider for this run")
    parser.add_argument("--poll-seconds", type=int, default=None,
                        help="override the --watch interval (default: agent.yaml or 900)")
    args = parser.parse_args(argv)

    try:
        settings = load_settings(provider=args.provider, dry_run=args.dry_run)
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 1

    # A dry run computes everything and writes nothing, not even to this
    # repo's own data/agent.db - an in-memory store gives every tool the
    # same real code path with nothing landing on disk. See
    # factory/workflows/build-repo.md section 5.
    store = Store(settings, path=":memory:" if settings.dry_run else None)
    store_ext.ensure_schema(store)
    try:
        if args.watch:
            poll_seconds = args.poll_seconds or int(settings.agent_get("poll_seconds", 900))
            while True:
                code, stats = one_pass(settings, store, provider=args.provider,
                                       swaps_only=args.swaps_only)
                print(summary_line(stats, settings.mode))
                if code != 0:
                    return code
                time.sleep(poll_seconds)
        code, stats = one_pass(settings, store, provider=args.provider,
                               swaps_only=args.swaps_only)
        print(summary_line(stats, settings.mode))
        return code
    except AdapterError as exc:
        print(f"integration error: {exc}", file=sys.stderr)
        print("Run `make doctor` to see what is missing and how to fix it.", file=sys.stderr)
        return 1
    except LLMError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    finally:
        store.close()


if __name__ == "__main__":
    raise SystemExit(main())
