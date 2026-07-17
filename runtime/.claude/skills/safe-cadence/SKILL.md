---
name: safe-cadence
description: Walk the output-gated SAFe cadence (DESIGN.md invariant 4) — iteration close, the shipped? binary, PI counters, and whether PI Planning may convene. Use when closing a [REVIEW] issue or when a PI boundary is due. Ceremony is gated on shipped output, not elapsed time.
---

# SAFe cadence — ceremony only runs when the org has shipped

One principle governs every step: **no clock advances process state — only a real
production ship does** (DESIGN.md invariant 4, `playbook/safe.md`). The predicate is
`scripts/last-ship.sh`; check it before any ceremony decision.

## Closing an iteration (the `[REVIEW]` issue's conclusion)

An iteration = one review interval (`global.review_interval` wakes — org-chart.yaml).
When you close a review:

1. **First line is the mandatory binary:** `Shipped to prod this iteration? Y/N (cite the
   deploy)` — the sha/run from `scripts/last-ship.sh`, never from memory.
2. **If N for three or more consecutive iterations:** the conclusion is one line naming
   the single external blocker, then stop — no retro theater over a hold.
3. **During a deploy-hold** (`shipped=no` with an open `blocks: [deploy]`/`[revenue]`
   ledger entry): run the *light* review — a one-line held-conclusion, no synthesis-and-
   discussion round that wakes peers for another lap over a known hold.
4. Run `scripts/pi-tick.sh` and read its flags (next section).

## PI boundaries (`scripts/pi-tick.sh`)

A PI = `global.pi_interval` iterations (org-chart.yaml). The script prints:

- `pi_planning_due` — the cadence boundary was crossed.
- `pi_planning_eligible` — due **and** the org actually shipped in the window.

**Convene PI Planning only when `pi_planning_eligible=yes`.** When due but not eligible,
post the one-line suppression instead:

> PI N closed with zero prod ships; PI Planning suppressed; sole blocker = X; no theme set.

You cannot plan a Program Increment for a program that has incremented nothing.

## PI Planning (when eligible)

1. **Theme from real signal** — the CEO frames a demand theme from the metrics store and
   the closed `[REVIEW]` issues, not from what would sound strategic.
2. **Objectives** — Business posts increment objectives; IT posts build objectives + risks.
3. **Synthesize** into `briefs/pi-NN.md` (via an isolated worktree PR, never the runtime dir).
4. **Close with Inspect & Adapt** — score the previous increment's objectives and commit
   exactly **one** process change.

## What this skill never does

- It never overrides a gate: money, deploys, and org-shape stay Chairman-only, and the
  `[DEMO]` handoff (`agents/_policy.md` §handoffs; `scripts/demo-verify.sh`) still fronts
  every deploy request.
- It never runs ceremony to look busy. If the honest state is "held, nothing shipped,
  nothing new," the correct output is the one-liner — or silence.
