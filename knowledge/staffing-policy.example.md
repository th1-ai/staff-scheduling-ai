# Staffing policy - Hotel Aurora

<!--
Copy this to knowledge/staffing-policy.md and rewrite it for your own
property. This is what the ten rule toggles in config/agent.yaml actually
mean in plain language - keep it in sync when you change a threshold.
-->

## Hard constraints - never a toggle

These are always enforced, whatever the rules below say, because the
roster's own promise is that they are hard constraints:

- **Contracted hours.** Nobody is scheduled past their `monthly_quota_hours`
  headroom for the month (`working_time.quota_headroom_floor_hours` in
  `config/agent.yaml` - the floor below which someone is excluded, not just
  warned about).
- **Rest between shifts.** At least `working_time.min_rest_hours` (default
  11) between the end of one shift and the start of the next. In practice
  this means someone who closes dinner service is never put on the next
  morning's breakfast.
- **Consecutive days.** Nobody works more than `working_time.max_consecutive_days`
  (default 5) days in a row without a day off.

## The ten rules (`config/agent.yaml: rules`)

| Key | In plain language |
|---|---|
| `personal-rules` | Respect each person's fixed days off and their weekend preference (always available, never weekends, weekends only). |
| `quota-hard-cap` | A soft preference: when someone still eligible (above the contracted-hours floor above) is close to using up their monthly hours, offer the shift to someone else first if there is a choice. This never refuses a shift to someone below the floor - that is the always-on hard constraint above, not this toggle. |
| `fairness-quota` | When two people could take the same shift, offer it to whoever has the most hours left this month, not whoever is cheapest or first on the list. |
| `cost-optimise` | When fairness does not decide it, prefer the lower hourly rate, and show what the week would have cost without this rule. |
| `hk-team-mix` | Every housekeeping floor gets an experienced lead over the newer staff, not a room lottery. |
| `hk-vip-floor` | A floor with a VIP guest gets a lead who is specifically cleared for VIP service. |
| `hk-supervisor-span` | Supervisors split the floors between them and personally check every checkout room in their block. |
| `fnb-ratios` | Size the restaurant floor to the covers you actually expect, not a flat headcount. |
| `fnb-group-senior` | A group booking or a dietary flag gets an experienced or allergy-trained server, not whoever is free. |
| `fnb-sommelier` | Pull a sommelier for a busy or wine-themed dinner service. |

## Why a shift comes back short-staffed

The agent never hides a gap. If a floor or a service comes back under the
number it needed, or a scarce role (a VIP-cleared senior, the one
sommelier) has nobody free, that is a warning on the plan, not a silent
guess. Read `docs/how-it-works.md` "Team assembly, in order" for exactly
how each number is computed.

## Swap and sick-day cover

A swap or sick-day request is matched automatically against the same
person's role and department, respecting every rule above for that day.
`config/agent.yaml: swaps.rank_by` decides the tiebreak when more than one
person could cover (`fairness` = most headroom first, `cost` = cheapest
first). If nobody qualifies, the request is queued as `needs_human` -
nobody is ever double-booked or pulled off a day they are not available for
just to fill a gap.
