# Workflow: build and publish the weekly rota

**Objective.** Build next week's rota from the team roster, the room board
and the restaurant book, get it approved, and publish it - staff notified,
nothing left for the manager to re-key.

**Inputs.** `fixtures/hotel/staff.json` or `data/imports/staff.csv` (the
team); `room_status`/`restaurant_covers` the same way; `config/agent.yaml`
(`rules:`, `working_time:`, `housekeeping:`, `fnb:`).

**Tools.** `tools/scheduling.py`, `tools/engine.py` (the decision layer -
read `docs/how-it-works.md` before changing anything in it),
`tools/review.py`.

## Step by step

**1. Build the week.**

```bash
python3 tools/scheduling.py build
python3 tools/scheduling.py build --week-start 2026-09-07   # a specific Monday
```

Idempotent per ISO week - running it twice for the same week returns the
existing item, it does not rebuild. Prints every day's headcount, hours,
cost and warning count, then the item id and status.

If `llm.provider` is `interactive`, this parks the duty-manager narrative
prompt in `data/pending/` and exits 3. Read it, write your answer as JSON
to the matching `.answer.json`, and run the same command again.

**2. Read the plan with the hotel.**

```bash
python3 tools/review.py show <id>
```

Walk them through it day by day: staff on shift, hours, cost, and every
warning in plain language - "Friday's dinner needs four servers but only
two are available" is what a warning means, not "servers: 2/4". Read the
AI briefing out loud; it is written for exactly this moment.

**3. A rule looks wrong?** Change it in `config/agent.yaml: rules` or
`working_time`, then rebuild - a rota already built for a week is not
touched by a later rule change; delete the item (or pick a different
`--week-start` while testing) to see the new rule take effect. This is the
same "toggle a rule, watch the plan change" mechanic `make demo` shows
(step 2 of the demo output).

**4. Approve, edit or reject.**

```bash
python3 tools/review.py approve <id>
python3 tools/review.py edit <id> --note-file better-note.txt   # rewrite the AI briefing text
python3 tools/review.py reject <id> --reason "too many warnings, rebuild after hiring"
```

Editing only rewrites the duty-manager narrative - the shifts and their
costs are deterministic output of `tools/engine.py` and are not meant to be
hand-edited here (see docs/how-it-works.md for why). If the plan itself is
wrong, the fix belongs in `config/agent.yaml` or `fixtures`/`data/imports`,
not in a one-off edit.

**5. Publish.**

```bash
python3 tools/review.py send
```

Only runs once the item is approved or edited. Seeds `schedule_shifts`,
exports the week to a sheet, and sends every rostered person their shifts
for the week. In `mode: shadow` this is blocked and the approval is kept
for later - see docs/safety.md.

## What runs when

See docs/how-it-works.md "What runs when" for the full table. In short:
`tools/run.py --once` builds the week (a no-op most days once it is already
built) and resolves any new swap/sick request in the same pass; publishing
is always a deliberate `tools/review.py send`, never automatic.
