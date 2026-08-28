# Connecting your systems

Every connector in this repo is one of three things, and the table says which.
We will not tell you an integration exists when it does not.

| Badge | Means |
|---|---|
| **built** | Written against the real API and tested against it. |
| **universal** | Works with any system through a common protocol: IMAP/SMTP, CSV, a webhook. |
| **stub** | Interface only. Calling it raises a clear error with a recipe for adding it. |

Check what is actually working on your machine at any time:

```bash
make doctor
```

**This agent uses two of the four adapter families: Messaging and Sheets.**
`make doctor` also pings the PMS and Email adapters (every repo in this
family does, for consistency), but The Planner's own tools never call
either - see "Your staff, rooms and covers data" below for why.

## Status

### Messaging - `systems.messaging.adapter`

Used for: notifying every rostered person when a week is published
(`tools/scheduling.py:dispatch_weekly_rota`), notifying both people on a
swap or sick-day reassignment (`tools/swaps.py:dispatch_swap`), and - only
while the Staff Briefing sub-agent is on - sending each person's daily
brief (`tools/briefing.py:dispatch_daily_briefing`).

| Adapter | Status | Needs | Notes |
|---|---|---|---|
| `mock` | universal | nothing | Logs to `data/exports/sent_messages.jsonl`. What `make demo` uses. |
| `unipile` | built | your own UniPile account | WhatsApp on your own number. **Start here for a real deployment.** |
| `webhook` | universal | any URL | POST to Zapier, Make, n8n, or your own endpoint - useful if staff messaging already goes through SMS or Slack. |

**`unipile`.** You create the account, you connect your number by QR code, you
own the credentials: `UNIPILE_DSN`, `UNIPILE_API_KEY`, `UNIPILE_ACCOUNT_ID`.
Each staff member's `id` in `fixtures/hotel/staff.json` (or
`data/imports/staff.csv`) is what gets used as the WhatsApp chat id today -
if your UniPile contacts are keyed differently (a phone number, say), ask
your Claude session to map `staff.phone` to the chat id in
`tools/scheduling.py`, `tools/swaps.py` and `tools/briefing.py` before you
switch this on live.

**`webhook`.** Set `MESSAGING_WEBHOOK_URL` and the agent POSTs
`{chat_id, text, kind, hotel, sent_at}` for every notification. Your
automation tool delivers it however you like - an SMS gateway, a Slack
channel per department, anything that can receive a webhook.

### Sheets - `systems.sheets.adapter`

Used for: exporting the published week's shift ledger for the manager's
records (`tools/scheduling.py:dispatch_weekly_rota`).

| Adapter | Status | Needs | Notes |
|---|---|---|---|
| `csv` | universal | nothing | Writes `data/exports/weekly-rota-<date>.csv`. What `make demo` would use if it dispatched (it never does - see docs/safety.md). |
| `google` | built | service account JSON | A live shared spreadsheet the whole team can open. |

For `google`: enable the Sheets API, create a service account and a JSON key,
save it as `service_account.json`, and share your spreadsheet with the service
account's email address as an Editor. Set `systems.sheets.spreadsheet_id` to the
long id from the sheet's URL.

## Your staff, rooms and covers data

The Planner's scheduling data - who is on the team, today's room board, the
restaurant's covers - is not a reservation and does not fit the generic
`PMS` interface (`core/adapters/base.py`'s `Reservation`/`Guest` shape is
built for bookings, not shifts). Instead `tools/store_ext.py` reads it
directly, CSV import first, the bundled fixtures as a fallback - the same
"CSV always works" principle as the family's PMS adapters, just applied to
this agent's own data shapes:

| File | What it holds | Columns |
|---|---|---|
| `data/imports/staff.csv` | Your team | `id, name, email, phone, department (housekeeping\|fnb), role, years_experience, hourly_cost, contract (FTE\|PTE), monthly_quota_hours, hours_worked_mtd, days_unavailable (semicolon-separated, e.g. Wed;Sat), weekend_rule (any\|no_weekends\|weekends_only), language, tags (semicolon-separated, e.g. vip_cleared;allergy_trained), notes` |
| `data/imports/room_status.csv` | Today's (and the coming week's) room board | `day_offset (0=Monday), room_number, floor, room_type, status (checkout\|stayover\|arrival\|vacant), vip, note` |
| `data/imports/restaurant_covers.csv` | The restaurant's booked covers | `day_offset, service (breakfast\|lunch\|dinner), covers, dietary (semicolon-separated), is_group, occasion, notes` |

Drop these three files in and `tools/run.py` / `tools/swaps.py check` read
them automatically (`source="auto"` - CSV import first, `fixtures/hotel/*.json`
fallback). `tools/demo.py` always reads the bundled fixtures only
(`source="fixtures"`), never your own imports - that is what keeps `make
demo` identical on every machine (SIMULATION.md-style contract, see
docs/how-it-works.md).

### Swap and sick-day requests - `data/imports/swap_requests.csv`

| File | What it holds | Columns |
|---|---|---|
| `data/imports/swap_requests.csv` | Swap and sick-day requests | `id, staff_id, date, reason (swap\|sick), note` |

This one is **not** like the three above: on a real run (`source="auto"` -
`make run`, `tools/swaps.py check`, and the scheduled `schedule.swaps`
job) it is the *only* source `tools/swaps.py:load_requests` reads. There is
no fixture fallback here - with no file connected, a real run processes
zero requests and prints "no swap requests file connected" rather than
resolving the bundled example hotel's `fixtures/inbound/swap-*.json`
against your real roster. Those bundled requests are for `make demo` and
the test suite only (`source="fixtures"`, what `tools/demo.py` always
passes and what `--source fixtures` asks for explicitly) - see
`workflows/20-swaps.md`. `make doctor` shows whether the file is connected.

**`fixtures/hotel/guest_notes.json`** (arrivals, departures, VIP notes,
maintenance tickets - what the Staff Briefing sub-agent reads) has no CSV
path yet: most PMS/maintenance systems do not export this shape today. If
yours does, ask your Claude session to add a `data/imports/guest_notes.csv`
loader to `tools/store_ext.py:load_guest_notes`, following the pattern the
other three loaders already use.

**Room status and covers by hand today, PMS tomorrow.** If you already run
one of the PMS adapters below for another agent in this family
(`front-desk-ai`, `housekeeping-maintenance-ai`), your Claude session can
wire `tools/store_ext.py:load_room_status` to call `pms.list_housekeeping()`
/ `pms.get_availability()` instead of reading a CSV - the room/cover
dataclasses in `tools/engine.py` (`RoomStatus`, `CoverService`) are
intentionally small and easy to build from a live PMS response.

## Implement your own

<a id="implement-your-own"></a>

Only Messaging and Sheets have real adapters to swap in this repo. The
five-step recipe (copy the closest adapter, implement `ping()` and
`capabilities()` first, implement the reads, guard every write with
`@guarded_write`, register it in `core/adapters/__init__.py`) is the same
one every repo in this family uses - open `claude` in this folder and
paste:

> Read `docs/integrations.md#implement-your-own` and
> `core/adapters/messaging_webhook.py`. I need a messaging adapter for
> **<your system>**. Its API docs are at **<url>** and I have credentials in
> `.env` as `<VAR names>`. Copy `messaging_webhook.py` as the shape,
> implement `ping`, `capabilities`, `send` and `notify_staff`, register it
> in `core/adapters/__init__.py`, and stop so I can check it with
> `make doctor`.

### Rules that matter

- **`ping()` never raises.** It returns `HealthCheck(ok=False, ...)` with a
  hint. A broken adapter must still produce a readable doctor table.
- **Every write is decorated with `@guarded_write`.** No exceptions -
  without it, an adapter can send while The Planner is in shadow mode,
  which defeats the entire safety model.
- **Never log a credential.** `core/log.py` masks anything whose key looks
  like a secret, but do not rely on it.

### `core/` is shared

`core/` is identical in all 28 agents in this family. If you change
something in `core/`, keep it generic - an agent-specific tweak belongs in
`tools/` or in your own adapter file, not in the shared runtime.
