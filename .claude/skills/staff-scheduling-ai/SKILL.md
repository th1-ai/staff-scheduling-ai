---
name: staff-scheduling-ai
description: Run Staff Scheduling AI ("The Planner") — Builds each rota from forecast occupancy, the real room-turn workload, the restaurant book, and your house rules — team composition, legal and contract limits, fairness, cost.. Use when the user asks to run the agent, check what is waiting for review, approve or reject a draft, or asks how the agent is doing. Trigger phrases: "run The Planner", "/staff-scheduling-ai", "check the queue", "what is waiting for me", "approve that draft".
---

# Staff Scheduling AI

Runs Staff Scheduling AI (weekly rota + swap/sick-day cover, plus the
folded Staff Briefing sub-agent if it is on) and works its review queue.
Everything happens from the repo root; every command below exists and
works.

## Before anything else

Read `README.md` if you have not this session, and `workflows/10-scheduling.md`
for the main loop. If the user has never run this agent, start at
`workflows/00-setup.md` instead and walk them through it.

## The loop

**1. Check the agent is healthy.**

```bash
make doctor
```

Any `FAIL` line has a fix hint. Fix it before going further. `WARN` lines
are worth mentioning but do not stop the run.

**2. Run one pass.**

```bash
make run                            # build the week (if not built yet) + resolve swap requests
make run ARGS="--dry-run"           # compute everything, write nothing
make run ARGS="--swaps-only"        # just swap/sick requests, skip the weekly build
python3 tools/scheduling.py build   # build (or show) next week's rota directly
python3 tools/swaps.py request --staff-id <id> --date <iso> --reason swap --note "..."
python3 tools/briefing.py build --day-offset 0   # only useful once the sub-agent is on
```

If `llm.provider` is `interactive`, a run will stop with exit code 3 and
park prompts in `data/pending/`. That is expected. Read each `*.prompt.md`,
write your answer as JSON to the matching `*.answer.json` following the
schema exactly, then run the same command again.

**3. Show what is waiting.**

```bash
make review
python3 tools/review.py show <id>
```

Summarise it for the user in plain language: a weekly rota's staff count,
hours, cost and warnings; a swap's requester and proposed cover; a
briefing batch's headcount. Do not paste raw JSON at them.

**4. Act on their decision.**

```bash
python3 tools/review.py approve <id>
python3 tools/review.py edit <id> --note-file <path>
python3 tools/review.py reject <id> --reason "<why>"
```

Read the draft back to them before approving. `edit` only rewrites the AI
narrative attached to the item - the plan or the match underneath it is
deterministic; if it is wrong, the fix is `config/agent.yaml` or the
roster/room/covers data, then rebuild.

**5. Publish.**

```bash
python3 tools/review.py send
```

**6. Report.**

```bash
make report
```

## Rules

- **Never send in shadow mode**, and never work around a blocked write. The
  error message says what to do.
- **Going live is the hotel's decision.** Only raise it after
  `workflows/90-go-live.md` has been worked through.
- **Confirm before anything irreversible** - publishing a week, reassigning
  a shift, sending a staff briefing batch - even when it is approved.
- **Never print or paste a credential**, and treat `fixtures/hotel/staff.json`
  / `data/imports/staff.csv` (pay rates, contracted hours) with the same
  care as any HR export.
- If a run fails, read the whole error, fix the cause, re-run, and note
  what you learned in `workflows/99-troubleshooting.md`.
