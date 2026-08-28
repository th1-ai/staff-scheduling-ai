# Workflow: first-run setup

Read this before anything else. Objective: get The Planner running against
this hotel's own details, prove it with `make demo`, and leave the hotel
ready to build a real week.

## 1. Environment

```bash
make setup
```

Creates `.venv`, installs `requirements.txt`, copies `.env.example` to
`.env` and every `config/*.example.yaml` to its real name if they are not
there already. Safe to run more than once.

## 2. Prove it works with zero credentials

```bash
make demo
```

Expect `DEMO OK — 5 items processed, 5 drafted, 0 sent (shadow)` at the
end. If this fails, something is wrong with the clone itself (not the
hotel's setup) - read `workflows/99-troubleshooting.md`.

## 3. The hotel's own details

Open `config/hotel.yaml` and fill in:

- `hotel:` name, timezone, currency, languages, address, phone, email.
- `contacts:` who gets escalations.
- `mode:` leave as `shadow` - this is not something to change today.

Ask the hotel directly, in plain language: *"What is your legal hotel
name? What timezone are you in? What currency do rota costs show in?"*

## 4. The property's own scheduling data

Copy the templates and fill them in, or point `data/imports/` at real
exports (see docs/integrations.md "Your staff, rooms and covers data"):

```bash
cp knowledge/property.example.md knowledge/property.md
cp knowledge/faq.example.md knowledge/faq.md
cp knowledge/staffing-policy.example.md knowledge/staffing-policy.md
```

Then either:

- **Fastest path:** export your team roster as a CSV and save it as
  `data/imports/staff.csv` (columns in docs/integrations.md). Room status
  and restaurant covers can follow the same pattern once you have them.
- **No export yet:** ask the hotel the numbers directly and write them
  into a copy of `fixtures/hotel/staff.json` at
  `data/imports/staff.csv` - or just run on the bundled fixtures while you
  get the real data together. Nothing breaks either way; `tools/doctor.py`
  will tell you which source is in use.
- **Swap and sick-day requests are different: `data/imports/swap_requests.csv`
  has no fixture fallback on a real run.** Unlike the three files above,
  `make run` never substitutes the bundled example hotel's requests for a
  missing swap-requests file - with none connected it processes zero
  requests and says so. Create the file (columns in docs/integrations.md)
  whenever the hotel is ready to log swaps this way, or log them directly
  with `python3 tools/swaps.py request ...` - see `workflows/20-swaps.md`.

## 5. Ten rules, and the working-time limits

Open `config/agent.yaml`. The ten toggles under `rules:` are on by default -
read `knowledge/staffing-policy.md` (or the `.example.md` if not copied
yet) with the hotel and turn off anything that does not apply to their
operation (a property with no restaurant can leave the `fnb-*` rules on;
they simply never fire). The `working_time:` block (max consecutive days,
minimum rest, quota headroom floor) is not a toggle - confirm the defaults
match local labour law and the hotel's own contracts before going further.

## 6. Health check

```bash
make doctor
```

Fix every `FAIL` line. `WARN` lines are fine to leave for now (an empty
`.env`, the Staff Briefing sub-agent being off, `mode: shadow`).

## 7. Choose how the agent thinks

`config/hotel.yaml: llm.provider`. Start with `interactive` - it costs
nothing extra (uses this Claude Code session) and it is the best way for
the hotel to see how the agent reasons. Read CLAUDE.md "The interactive
provider" for how that works. Move to `claude-code` or `anthropic` once the
hotel is ready to run this unattended - see docs/safety.md "Subscription or
API: an honest note".

## Next

`workflows/10-scheduling.md` for the main loop, then
`workflows/80-review.md` for working the queue.
