# Measuring the benefit

## The business case

**−15% overstaffed hours (labour).** That is the roster's own number for
this agent. It comes from matching the housekeeping team size and the
restaurant floor to what the day actually needs (forecast occupancy, the
real room-turn workload, the covers you have booked) instead of a rota that
gets copy-pasted from last week and adjusted by feel.

**What actually drives that number:**

- The team-size math (`tools/engine.py:build_housekeeping` /
  `build_fnb`) is workload-driven, not headcount-driven - a light Tuesday
  gets fewer floor attendants than a Friday with three departures and a
  wedding party, and the plan shows exactly why (`thinking_log` on every
  item).
- `cost-optimise` (`config/agent.yaml: rules`) shows what the week would
  have cost against the average rate of everyone available that day, and
  only surfaces a saving once it clears `cost_savings_floor` - so the
  number you see is real, not a rounding artefact.
- Swap and sick-day cover (`tools/swaps.py`) is resolved without a manager
  spending the morning on the phone - see "Design decisions" in
  docs/how-it-works.md for exactly what "automatic" means here.

**The Staff Briefing sub-agent's own number, if you turn it on:**
"Saves a manager ~30-45 min every morning; gets guest notes to staff with
~100% reliability." `schedule_briefs` (`tools/store_ext.py`) records every
brief sent, which is what makes "~100% reliability" something you can
actually check rather than take on trust.

## What to measure

```bash
make report
```

Shows, straight from `data/agent.db`:

- Weekly rotas queued, and how many needed a human before they could be
  published.
- Swap/sick-day requests resolved, and how many had no eligible cover (a
  real staffing gap, not a software problem - worth tracking on its own).
- Published weeks and the total shift rows behind them.
- Staff briefs delivered (only meaningful once the sub-agent is on).
- LLM spend, always zero on `mock`/`interactive`.

**Week over week, watch:**

- **Warning count per week** (`plan.warning_count` in the draft, also in
  the duty-manager narrative). A falling trend means your rules and your
  roster size are converging on what the week actually needs; a rising one
  is a hiring or a rota-rule conversation, not a software bug.
- **Cost saved vs baseline**, when `cost-optimise` is on - the gap between
  "everyone at the average rate" and the actual plan.
- **Edit rate on the weekly rota's narrative.** If a manager keeps
  rewriting the duty-manager briefing before publishing, that is worth
  reading - `core.review.edit` records the before/after so you can see
  what keeps changing.
- **Swap requests with no eligible cover.** A rising trend here is a real
  staffing signal (not enough depth in one role) that the software cannot
  fix by matching harder.

## Honest caveats

- **−15% is the roster's promise for a property that adopts workload-based
  staffing; it is not a guarantee for yours.** The size of the saving
  depends entirely on how far your current rota is from matching actual
  demand today.
- **`hours_worked_mtd` is not decremented by this agent.** It reads it for
  quota headroom but does not write it back - see docs/how-it-works.md
  "Design decisions" point 3. Feed it from payroll or your PMS's clock-in
  data for the quota math to stay accurate through the month.
- **The −15% figure is a labour-hours claim, not a guest-experience
  claim.** Nothing here measures service quality; a warning that a floor is
  short-staffed is the software telling you where quality risk sits, not a
  substitute for checking.
