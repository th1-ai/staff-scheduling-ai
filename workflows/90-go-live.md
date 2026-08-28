# Workflow: shadow to live

**Do not raise this with the hotel until every box below is genuinely
checked.** Going live means real messages reach real staff.

## The checklist

- [ ] At least one real weekly rota has been built (not the demo) and
      reviewed end to end with the hotel - `python3 tools/review.py show <id>`
      walked through, every warning understood.
- [ ] `config/hotel.yaml` has the hotel's real name, timezone and currency
      - `make doctor`'s "hotel identity" check is a `PASS`, not a `FAIL`.
- [ ] `data/imports/staff.csv` (or the fixtures, deliberately, for a small
      test) reflects the real team - contracted hours, quotas and personal
      rules are accurate. A wrong `monthly_quota_hours` here is a real
      labour-law risk, not a cosmetic bug.
- [ ] `config/agent.yaml: working_time` matches local law and the hotel's
      own contracts - confirmed with the hotel, not assumed from the
      default.
- [ ] `make doctor` is entirely `PASS`/`WARN`, no `FAIL`.
- [ ] The messaging adapter is configured and `make doctor` shows it
      reachable (`unipile` or `webhook` - see docs/integrations.md).
- [ ] The sheets adapter is configured, if the hotel wants a live export
      rather than the CSV default.
- [ ] `knowledge/signature.md` exists if you want a disclosure line on
      staff-facing messages (see docs/safety.md - this agent's messages are
      internal, not guest-facing, but many hotels still want staff to know
      an AI wrote the brief).
- [ ] The hotel has said, in their own words, that they understand: once
      live, an **approved** weekly rota, swap, or staff-briefing batch will
      really send. Shadow drafts do not carry over as approvals that will
      surprise anyone - the next step clears them.

## Clear the shadow-era queue

```bash
python3 tools/review.py stale
```

Marks every un-sent item (`pending_review`, `needs_human`, `approved`,
`edited`) as `stale`. Nothing built or approved while testing in shadow
will go out just because the mode flipped. If something genuinely still
matters, a human moves it back out of `stale` deliberately
(`python3 tools/review.py approve <id>` after checking it is still
current).

## Flip it

```yaml
# config/hotel.yaml
mode: live
```

`config/agent.yaml` may be stricter than this, never looser - if it also
sets `mode: shadow`, it wins; remove that line too if you mean it.

## What changes

- `tools/review.py send` on an approved item now really dispatches:
  `weekly_rota` publishes and notifies every rostered person; `swap`
  reassigns and notifies two people; `staff_briefing` sends the day's
  batch.
- `review.require_approval_for` in `config/hotel.yaml` still decides what
  needs an approval even in `live` mode - the defaults
  (`send_email, send_message, pms_write, payment, publish`) cover
  everything this agent does. Do not shorten that list for this agent
  without discussing it with the hotel first.
- `--dry-run` still writes nothing, in either mode - keep using it whenever
  you change a rule or a prompt.

## Going back

```yaml
mode: shadow
```

Stops every outbound action on the next pass, mid-schedule, no other
change needed.
