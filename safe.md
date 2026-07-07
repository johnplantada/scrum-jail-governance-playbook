# Scaled Agile for an Agent Org — without the theater

If you run more than a couple of agents, you'll be tempted to give them a process: sprints,
demos, planning. Do it — a little structure helps. But agent orgs fail at this in a specific,
expensive way (see [patterns.md](patterns.md) Pattern 10): the **ceremony runs on a clock, not
on delivery**, so it keeps running over an org that has shipped nothing. We ran *PI Planning for
a product that had never deployed once.* This is how to get the value without the theater.

## The one principle

> **Ceremony is gated on shipped output, not on elapsed time.**

No clock advances process state. Only a real production ship does. Everything below hangs off a
single predicate.

## The output predicate (the keystone)

Define "did we actually ship?" once, as a script every ritual checks first:

```bash
# last-ship.sh → shipped=<yes|no>, plus the sha/time of the last real deploy.
# A "real ship" = a SUCCESSFUL deploy of production (e.g. a green deploy workflow on main).
```

One subtlety worth stealing: scope it to the **current** pipeline on your release branch, so a
stale or pre-org green run doesn't read as `shipped=yes`. While the predicate is `no`, the whole
process layer is **dormant by design** — you cannot demo, accept, or plan an increment for a
program that has incremented nothing.

## Iterations

An iteration = N wakes (the live org started at 5 and now runs 20 — by Chairman directive, once
reviews every 5 wakes proved too frequent; tune N to your org's tempo). It opens with
objectives and closes with a review. Make the
review's **first line a binary**: *"Shipped to prod this iteration? Y/N (cite the deploy)."* If
the answer is N for three iterations running, the review degenerates to one line naming the
single external blocker — no retro over a hold. This kills the "executed cleanly within our
constraints" report that lets a stuck org feel productive.

## The `[CODEREVIEW]` gate — correctness, before the demo

The `[DEMO]` proves a change *reaches a user*; it does not prove the *code* is correct or safe.
Add one gate in front of it for product-surface work: an independent review — structurally
separate from whoever authored the code (**author ≠ reviewer**) — gives every product-surface
PR a `PASS` / `CHANGES-REQUESTED` verdict before it can be demoed or deployed. In the live org
that reviewer is a `claude-code-action` check running *on the PR itself*, with its workflow
runs routed to IT by `wake-rules.yaml` so a department owns acting on the verdict. (Lineage:
v1 chartered an independent Reviewer *department* for this; retired 2026-07-05 with the chat
stack, replaced by the CI check.) Three rules keep it honest:

- **Anchor the verdict to evidence, not vibes.** Bind it to the PR's current **head SHA** —
  a new push invalidates a stale PASS. The review itself
  happens *on the PR* (inline `file:line` comments), where the code is.
- **A demo may cite only a PASS.** The `[DEMO]` carries the passing review's id. HONESTY NOTE:
  today that citation is prompt-enforced — the typed-handoff schema lives in
  `agents/_policy.md` §handoffs, and the Actions validator that would mechanically catch a
  handoff skipping it is planned, not built (the chat-era Warden that used to police this
  retired with the demolition).
- **Ship it dormant.** The gate is dormant until the review check is installed on the product
  repo — no check, no citation required — then binding. Until then, don't pretend to have it.

This is the "author ≠ reviewer" separation the emoji gate already applies to spend and deploy,
extended to code correctness — a CI check on the PR, not a department and not a parser.

## The `[DEMO]` gate — the one gate worth adding

A `[DEMO]` is a worked demonstration that a change meets agreed **acceptance criteria**. Make it
the prerequisite for a deploy request: no product-surface PR reaches a 🚀 without a
demand-side-**accepted** `[DEMO]` (which cites its passing `[CODEREVIEW]`), and the approval
relay cites the accepted demo's id. Two rules keep it honest:

- **A demo proves a feature reaches a user, so it's produced at deploy time, for deployable work
  only.** While prod is dark, an accepted PR *merges* (green CI) and **queues** — it does not
  demo. You cannot demo a feature that cannot reach a user; this is what stops the "demo gate
  demoing itself."
- **Acceptance criteria scale with the work.** ACs (≤3 observable-outcome bullets) are required
  only for product-surface work. Process, manual, or one-shot tasks get a one-line "done-when,"
  not a Definition-of-Done. (Over-documenting a one-shot task is the overhead agents rightly
  resent — and a tell that the process is running ahead of the substance.)

A `[DEMO]` needs no special tooling — it's a message type, a convention. Don't build a parser.
Do steal the live org's one predicate behind it, though: `scripts/demo-verify.sh` answers "is
there a green `demo-evidence.yml` run on this PR's **current head SHA**?" — same pattern as the
output predicate, evidence in code, not prose. A stale run (evidence generated, then more
commits pushed) does not verify, and the run URL goes in the `[DEMO]` so the acceptor opens
artifacts, not adjectives.

**Let the tracker hold the queue.** Give the board a fixed, ordered set of columns so a
change can't silently skip the gate. The live org runs **To-Do → Doing → Staged → Demo → Done**:
`Staged` = PR open, awaiting merge; `Demo` = merged to main, awaiting the first prod deploy (or
awaiting demand-side acceptance of the `[DEMO]`); `Done` = shipped (or, for non-product tasks,
completed). Define the stage list in **one place** and have your docs and lint reference it —
the column *is* the queue, so a merged-but-undeployed PR visibly waits in `Demo` instead of
being quietly treated as shipped.

## Program Increments — gate the planning, not just the cadence

A PI bundles a few iterations. A counter (`pi-tick`) tells you the cadence boundary — the live
org's `scripts/pi-tick.sh` counts closed `[REVIEW]` issues as completed iterations, with
`iters_per_pi` read from the org chart's `pi_interval`, so the cadence is derived from the
record, not a clock — but emit a
**second** flag: `pi_planning_eligible = (due AND shipped-in-window)`. **PI Planning convenes
only when eligible.** Otherwise the lead posts a one-line conclusion: *"PI N closed with zero
prod ships; planning suppressed; sole blocker = X; no theme set."* You cannot plan an increment
that didn't increment. When it *is* eligible, set the theme from real signal and close with
Inspect & Adapt (score the increment, commit one process change).

## What to build vs. skip

| Build | Skip |
|---|---|
| `last-ship.sh` output predicate | a separate `#demos` channel (reuse the board) |
| `pi-tick` counter + `pi_planning_eligible` flag | a custom `[DEMO]` parser (it's just a tag) |
| The `[DEMO]`-before-🚀 rule in the constitution + mandates | per-task Definitions-of-Done |
| ACs only for product-surface work | a sprint tool / burndown charts |
| Output-gating on every ceremony | ceremony that runs on a clock |

## Adopt it deliberately

The process layer is a constitution change — gate its adoption behind an operator 🏛️, and ship
the capabilities (the counter, the demo column) **dormant**. The gate becomes binding the moment
your first deploy goes green and the output predicate flips to `yes`. Until then, the most honest
thing your org can do is stay quiet and point at the one blocker in [blocker-ledger.md](blocker-ledger.md).
