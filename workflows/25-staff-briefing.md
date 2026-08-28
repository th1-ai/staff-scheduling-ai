# Workflow: Staff Briefing AI ("The Sergeant") - daily per-person briefs

**Off by default.** The Planner is fully useful without this - a published
weekly rota already tells everyone their shifts and hours. Turn it on once
you also want a personal, per-language task brief for each person every
morning.

## Turn it on

```yaml
# config/agent.yaml
subagents:
  staff_briefing:
    enabled: true
    languages: [en, fr, de, es, it, pt]
```

`make doctor` shows the sub-agent's status either way.

## Step by step

**1. Publish a week first.** The Sergeant reads `schedule_shifts`, which is
only populated once a weekly rota has been approved and sent
(`workflows/10-scheduling.md`). Nothing to brief before that.

**This means "see one" against the hotel's own real data is shadow-gated,
the same as sending is** - `schedule_shifts` stays empty until go-live, so
`tools/briefing.py build` keeps answering "no published shifts yet" until
then, whatever `llm.provider` is set to. If the hotel wants to see the
shape of a brief before going live, run `make demo` instead: its Staff
Briefing preview (step 4 of the demo output) always composes from the
bundled example hotel's in-memory plan, sub-agent on or off, and needs no
publish first.

**2. Compose the day's briefs.**

```bash
python3 tools/briefing.py build --day-offset 0        # today
python3 tools/briefing.py build --date 2026-09-01       # a specific day
```

One `prompts/staff-brief.md` call per on-duty person, in their own language
(`fixtures/hotel/staff.json: language` / `data/imports/staff.csv`). Each
brief only contains what that person needs: their rooms or their service,
and any note tied to those rooms (a VIP arrival, a maintenance ticket) or
that service (a group booking, a dietary flag) - see
`tools/briefing.py:relevant_notes`.

**Language is enforced, not assumed.** A staff member's `language` is
checked against `subagents.staff_briefing.languages` above
(`tools/briefing.py:resolve_briefing_language`) before it is used. A
missing/blank language, or a code that is not on that list, falls back to
the hotel's default language (`hotel.languages[0]`) and flags that day's
briefing batch `needs_human` with the exact reason - add the language to
the list above first if a real person needs it, rather than relying on the
fallback long-term.

If `llm.provider` is `interactive`, several prompts may park in
`data/pending/` at once (one per on-duty person). Answer them all, then run
the same command again.

**3. Read a few with the hotel** before the first live send:

```bash
python3 tools/review.py show <id>
```

**4. Approve and send the batch.**

```bash
python3 tools/review.py approve <id>
python3 tools/review.py send
```

Sends every person their own brief, then a manager copy of the whole batch,
and records delivery in `schedule_briefs` (`make report` shows the
delivered count). Blocked in `mode: shadow`, same as everything else.

## Why one item, not one per person

Composing is automatic - no manager drafts a brief. Sending is still a
guarded write, and this template batches the whole day into one review item
instead of asking for fourteen separate approvals before breakfast. See
docs/how-it-works.md "Design decisions" point "9a".

## Schedule it

```yaml
# config/agent.yaml: schedule.staff_briefing
command: "tools/briefing.py build --day-offset 0"
cadence: "0 6 * * *"   # daily 06:00
```

`make schedule ARGS="--all"` prints the ready-to-paste snippet. Dispatch
(`tools/review.py send`) is a manager action, not scheduled - see
`workflows/80-review.md`.
