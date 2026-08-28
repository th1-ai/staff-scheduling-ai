# Workflow: troubleshooting

Read the whole error before doing anything - every tool in this repo is
written to say what broke and what to do about it. `make doctor` is always
the first command to run.

## "make demo" fails

This means something is wrong with the clone itself, not a hotel's setup -
`make demo` never reads real config or credentials. Run
`python3 tools/demo.py` directly to see the full traceback, and check
`fixtures/hotel/*.json` and `fixtures/inbound/*.json` are all present and
valid JSON.

## "make doctor" shows a FAIL

Every `FAIL` line has a `->` fix hint underneath it - follow it. Common
ones for this agent:

- **`hotel identity`** - `config/hotel.yaml` still has the placeholder name
  "Hotel Aurora". Fill in the real property.
- **`scheduling rules`** - `config/agent.yaml: rules` is missing one of the
  ten keys. Copy the block fresh from `config/agent.example.yaml`.
- **`working-time limits`** - same, for `config/agent.yaml: working_time`.
- **messaging/sheets adapter** - see docs/integrations.md for exactly what
  each adapter needs in `.env`.

## "tools/scheduling.py build" says the week is already built

That is not a bug - a weekly rota is idempotent per ISO Monday. Either
review the existing item (`python3 tools/review.py show <id>`) or pick a
different week with `--week-start`.

## A swap request comes back "NO ELIGIBLE COVER" every time for one role

Check how many people actually hold that role in `fixtures/hotel/staff.json`
/ `data/imports/staff.csv`. If it is genuinely one person (a single
sommelier, a single night auditor), that is an honest staffing gap, not a
matching bug - `tools/engine.py:resolve_swap` cannot cover a role nobody
else holds.

## "tools/briefing.py build" says nothing is published

The Staff Briefing sub-agent reads `schedule_shifts`, which only exists
once a weekly rota has been approved and sent
(`workflows/10-scheduling.md`). Publish a week first.

## A command exits 3

That is not an error - `llm.provider: interactive` parked a prompt in
`data/pending/` and is waiting for an answer. Read the `.prompt.md` file,
write your answer as JSON to the matching `.answer.json` (matching the
schema in the `.schema.json` file next to it, no prose, no code fence), and
run the same command again.

## "blocked ... (approval kept)" when sending

Expected in `mode: shadow` - every write is blocked, approved or not. The
approval is not lost; it will send once `mode: live` is set
(`workflows/90-go-live.md`) and you run `tools/review.py send` again.

## A weekly rota's numbers look wrong

Read the item's `thinking_log` (`python3 tools/review.py show <id>`, under
`draft.plan.days[N].thinking_log`) - every number is computed from a line
you can trace: room minutes, covers-per-server, quota headroom. If a
number is wrong, the input data is wrong (a bad room status, a
mis-typed `hourly_cost`) far more often than the engine is - check
`fixtures/hotel/*.json` or `data/imports/*.csv` first.

## Tests fail after you changed `config/agent.yaml` or a fixture

`make test` never reads this working copy's own `config/hotel.yaml` /
`config/agent.yaml`, or your `data/imports/*.csv` - `tests/conftest.py`
isolates every test to a fresh copy of the `.example.yaml` files and the
bundled `fixtures/`. If a test fails after an edit, it is a real
regression in `tools/engine.py` or the fixtures themselves, not your local
config bleeding through.

## Still stuck

`data/logs/*.jsonl` has every decision, in order, with a run id. Read the
lines around when things went wrong before asking for help.
