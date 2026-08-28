# Workflow: working the review queue

**Objective.** Nothing The Planner produces reaches anyone - a manager, a
staff member on WhatsApp - without a human looking at it first. This is how
you look at it.

## See what is waiting

```bash
make review                                  # everything waiting, any kind
python3 tools/review.py list --kind weekly_rota
python3 tools/review.py list --kind swap
python3 tools/review.py list --kind staff_briefing
python3 tools/review.py list --status needs_human   # the ones that need the most attention
```

## Read one

```bash
python3 tools/review.py show <id>
```

Prints the full item as JSON: the plan or the swap match, the AI narrative
(if any), and the audit trail. Translate it for the hotel - do not paste
raw JSON at a manager. For a `weekly_rota` item: staff on shift, hours,
cost, and every warning in plain language. For a `swap` item: who asked,
who would cover, and why (or why not).

## Decide

```bash
python3 tools/review.py approve <id> [--note "..."]
python3 tools/review.py edit <id> --note-file better-note.txt [--note "..."]
python3 tools/review.py reject <id> --reason "..."
```

`edit` only rewrites the AI narrative attached to the item (the
duty-manager briefing, or nothing for a `swap`/`staff_briefing` item, which
have no narrative to edit) - the underlying plan or match is deterministic
output of `tools/engine.py`. If the plan itself is wrong, fix
`config/agent.yaml` or the roster/room/covers data and rebuild, do not
hand-edit a shift here.

`reject` is terminal for that item - the agent will not retry it. Rebuild
(`tools/scheduling.py build --week-start ...` for a rota, or
`tools/swaps.py request` again for a swap) once the underlying cause is
fixed.

## Send

```bash
python3 tools/review.py send
```

Claims everything `approved`/`edited` and dispatches each item by its kind
(`weekly_rota` → publish + notify everyone; `swap` → reassign + notify two
people; `staff_briefing` → send the day's batch). In `mode: shadow` this is
blocked for every item and the approval is kept - re-run `send` once you
flip to `live` (`workflows/90-go-live.md`); nothing needs re-approving.

## Retry a failed send

```bash
python3 tools/review.py retry <id>
```

Only for an item that genuinely failed mid-send (a messaging outage, for
example) - queues it back to `approved` for another attempt. A `blocked`
result (shadow mode) is not a failure and does not need `retry`; it needs
`live` mode.
