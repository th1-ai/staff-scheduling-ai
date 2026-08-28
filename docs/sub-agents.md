# Sub-agents in this repo

Staff Scheduling AI folds in one sub-agent. It shares this repo's `core/`,
`data/agent.db` and review queue - there is nothing extra to install. It is
off by default; see `config/agent.yaml`'s `subagents` block.

## Staff Briefing AI - "The Sergeant"

**Does.** Every morning it pulls the day's arrivals, departures, and guest
notes from the PMS plus the cleaning schedule, reasons over them, and sends
each on-duty staff member a personalised WhatsApp brief of their tasks,
automatically.

**Won't.** Doesn't assign new tasks beyond the PMS/schedule; reads the
roster, doesn't manage it.

**Why.** Replaces the manager's daily scramble and makes sure VIP notes and
special requests actually reach the person on the floor.

**Output.** Saves a manager ~30-45 min every morning; gets guest notes to
staff with ~100% reliability.

**Off by default.** The Planner is fully useful without it - a published
weekly rota already tells everyone their shifts and hours. Turn it on
(`config/agent.yaml: subagents.staff_briefing.enabled: true`) once you also
want a personal, per-language daily task brief for each person; see
`workflows/25-staff-briefing.md`.

**How it fits together.** The Planner decides who works (the weekly rota);
the Sergeant tells them what matters today (arrivals, departures, VIP notes,
the group booking at table 4). It only has anything to send once a week has
actually been published - `tools/store_ext.py:list_shifts_for_date` reads
`schedule_shifts`, which `tools/scheduling.py:dispatch_weekly_rota` only
writes after a human approves the week. `tools/briefing.py build` says
plainly when there is nothing published yet to brief. That means "turn it
on and see one" against your own real data is shadow-gated the same way
sending is: `schedule_shifts` stays empty until go-live, so
`python3 tools/briefing.py build` keeps saying "no published shifts" until
then. To see the shape of a brief sooner, run `make demo` - its Staff
Briefing preview (step 4) always composes from the bundled example hotel's
in-memory plan, sub-agent on or off, and needs no publish first.

**Language is an allowlist, not a suggestion.** Every brief's language is
checked against `subagents.staff_briefing.languages`
(`tools/briefing.py:resolve_briefing_language`) before it is used. A staff
member whose `language` is blank, or set to a code that is not on that
list, gets `hotel.languages[0]` instead, and that day's briefing batch is
flagged `needs_human` with the exact reason - never a literal placeholder
and never an unvetted string handed straight to the model.

**What is different from the source demo.** The demo this template was
built from has one LLM call addressed to the duty manager and a simulated
bulk SMS toast, not a per-person brief - `specs/staff-briefing-ai.md`
section 11 documents the gap in full. This template builds the real thing:
one `prompts/staff-brief.md` call per on-duty person, in their own language
(`fixtures/hotel/staff.json: language` - a field this template adds), with
only the notes tied to their own rooms or their own service
(`tools/briefing.py:relevant_notes`). Composing every brief is automatic;
sending the day's batch still goes through the same review queue as
everything else in this family, once a day rather than once a person - see
docs/how-it-works.md "Design decisions" point "9a".

**Delivery is recorded**, not just claimed - `schedule_briefs`
(`tools/store_ext.py`) logs one row per person per day with a `delivered`
flag, which is what makes the roster's "~100% reliability" line something
`make report` can actually show you.
