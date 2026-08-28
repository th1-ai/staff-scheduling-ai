# Workflow: swap requests and sick-day cover

**Objective.** Turn "Dev wants Wednesday off" or "Marco just called in
sick" into a matched, approved, notified reassignment - without the
manager working the phones.

**Inputs.** The published week's shifts (`schedule_shifts` - a request
against an unpublished week still resolves, just with a smaller "already
assigned" picture, see docs/how-it-works.md); `data/imports/swap_requests.csv`
or a request logged directly.

**Tools.** `tools/swaps.py`, `tools/engine.py:resolve_swap`.

## Step by step

**1. Log the request.** Three ways in:

```bash
# a. directly, on the spot
python3 tools/swaps.py request --staff-id hk-06 --date 2026-09-02 \
    --reason swap --note "wants Wednesday off"

# b. a manager-maintained sheet
#    data/imports/swap_requests.csv - columns: id, staff_id, date, reason, note
python3 tools/swaps.py check

# c. the bundled fixtures (fixtures/inbound/swap-*.json), for demo/tests only
python3 tools/swaps.py check --source fixtures
```

`tools/run.py --once` also calls `tools/swaps.py:load_requests` every pass
- a real property does not need to remember to run this separately once it
is scheduled (`config/agent.yaml: schedule.swaps`, every 15 minutes).

**On a real run (no `--source`), there is no fixture fallback.** With no
`data/imports/swap_requests.csv`, `tools/swaps.py:load_requests` processes
zero requests and prints "no swap requests file connected" - it never
substitutes the bundled example hotel's `fixtures/inbound/swap-*.json`
into your real review queue against your real roster. `make doctor` shows
the same as a WARN. See README "Run it" / docs/integrations.md.

**2. The match is automatic.** `tools/engine.py:resolve_swap` applies the
same rules the weekly rota used - personal-rules, quota headroom, and the
working-time limits - for that person's role, department and day. Nobody
who was excluded from the original plan is ever offered as cover.

**3. Two outcomes:**

- **A candidate was found** → `pending_review`. Show the hotel:
  *"Priya can cover Dev's Wednesday shift - she has the most hours left
  this month."*
- **Nobody qualifies** → `needs_human`. This is a real staffing gap, not a
  software problem - say so plainly: *"No one else is a Server and free
  that day. You need to either call someone in on a day off or run
  short-staffed."*

**4. Approve and send.**

```bash
python3 tools/review.py list --kind swap
python3 tools/review.py show <id>
python3 tools/review.py approve <id>
python3 tools/review.py send
```

Sending reassigns the shift in `schedule_shifts` and notifies both the
original person and their cover. Blocked in `mode: shadow`, same as
everything else - see docs/safety.md.

## Why this still goes through review

The AI's matching is automatic; the notification is not skipped past a
human. See docs/how-it-works.md "Design decisions" point 1 for the full
reasoning - in short, "handled automatically" means nobody re-juggles the
roster by hand, not that a message goes out to staff with no one having
looked at it.
