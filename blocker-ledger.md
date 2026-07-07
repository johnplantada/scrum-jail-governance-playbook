# The Blocker Ledger, Capability Boundary & Wake Backpressure

The single highest-leverage thing you can add to a multi-agent org after the authorization
gates. Together these three primitives stop the org's most expensive failure mode — agents
waking and re-narrating the same blockers forever ([patterns.md](patterns.md) Patterns 9–11).
They cost a few lines each and pay for themselves in tokens immediately.

---

## 1. The capability boundary

Be explicit about what **no agent can do** — the actions that require a human in the physical
world or in the owner's account, not just an approval:

- cloud / infrastructure credentials (e.g. setting a deploy role or repo secret)
- money (a card, a payment account, a paid signup)
- registering an account or a domain
- providing a real-world value: a public URL, a mailing address, a phone number
- the authorization acts themselves: merging a `decisions.yaml` PR past CODEOWNERS,
  approving the `production` environment, changing repo Settings
  *(v1: these were chat reactions — 💰/🚀/🏛️/💎; retired 2026-07-05 with the chat stack. The
  emoji survive as `decisions.yaml` type mnemonics, not reactions.)*

Write this list into the shared policy every agent loads (`agents/_policy.md`). An agent that
hits one of these has exactly one correct move, below — **not** "try harder," and **not**
"announce it again."

## 2. The ledger (`blockers.yaml`)

A durable file at the repo root — the **operator's queue**. It, not the issue/comment stream,
is where "what does a human need to do" lives. One entry per human-only blocker:

```yaml
blockers:
  - id: deploy-aws-credentials
    kind: external-input          # external-input | governance
    needs: chairman
    summary: >-
      Prod can't deploy until the deploy IAM role + region are set. One-time bootstrap;
      requires the operator's cloud admin creds. Steps: DEPLOY-RUNBOOK.md.
    action: "Follow DEPLOY-RUNBOOK.md (bootstrap → set 2 repo vars → deploy)."
    blocks: [deploy, revenue]     # what stays gated until this clears
    value: infra                  # revenue | signal | infra — what clearing it unlocks
    effort_minutes: 30            # honest estimate of the Chairman-minutes to clear it
    opened: 2026-06-26
    state: open                   # open | cleared
```

**The contract:**
- Agents **read** the ledger every wake (the open queue is injected into the agent's prompt)
  and may **append** a new `open` entry for a blocker they hit. They never re-post it as
  STATUS.
- Agents **never** flip an entry to `cleared` — only the operator does. Clearing it (and the
  issue/comment activity that does so) is what wakes the org again.
- The helper (`scripts/blockers.py open`) prints the open queue **EV-sorted** (each line
  tagged kind, value, effort, age), prefixed by the WIP banner when it applies — for the
  prompt and a `make blockers` view. The whole point: the operator scans **one short,
  ordered list**, not 70 status posts.

### EV ordering & the unlock WIP limit

The operator is single-threaded and high-latency, so the queue must be worth reading
top-down:

- **Expected-value sort.** Every entry carries `value:` and `effort_minutes:`. The helper
  orders open entries by value class (`revenue > signal > infra > unclassified`), then
  cheapest effort within a class, then oldest first (`VALUE_RANK` / `sort_key` in
  `blockers.py`). A batching sit-down starts at the top and works down — the Chairman always
  sees the highest-leverage unlock first. Unknown effort sorts last within its class: an
  unestimated unlock is not a quick win until someone says so.
- **The WIP limit.** Past `global.unlock_wip_limit` (org-chart.yaml) open entries, the helper
  prefixes the queue with a WIP banner: agents must **not start new work whose critical path
  ends in another human-only unlock** — swarm what is already unblocked (DESIGN.md
  invariant 2). Enforcement is prompt-level: the banner rides into every wake prompt with the
  queue; nothing hard-blocks the work.

## 3. Wake backpressure

The behavioral half — now structural. Distinguish *why* a department wakes:

| Wake reason | Rule |
|---|---|
| **direct** — the Chairman files or labels an issue at the department (`dept:*`) | always runs a full cycle |
| **routed event** — `wake-rules.yaml` maps issue labels, comments, and workflow conclusions to the owning department; the runner batches **one wake per department per tick**, however many events triggered it | the wake carries its triggering events; the agent's own lock, budget, and no-op gates still apply |

*(v1: two more reasons — **broadcast** shared-channel posts and the **scheduled** cron floor
with its peek-the-channels-first no-op; retired 2026-07-05 with the chat stack.)*

The saver moved upstream of the model entirely: **no event, no wake, no spend** (DESIGN.md
§3). There are no scheduled wake floors — an idle org costs **zero** model tokens because
nothing wakes it. And it is safe: an agent can never get stuck silent, because any unblock —
you clearing a ledger entry, a deploy workflow completing — is itself a GitHub event the
runner routes into a wake.

Pair it with **state-change-only STATUS**: an agent posts only when something changed, so a
no-change cycle emits no comment — and no comment event to echo-wake peers (the self-wake
storm, Pattern 11).

## 4. Reliability corollaries

Two properties that make backpressure safe to lean on:

- **Coalesce suppressed wakes, don't drop them.** The runner's `batch_wakes` folds every
  triggering event into one wake per department per tick — five comments are one cycle
  carrying five events, not five cycles and not four dropped triggers. (v1: a rate-limited
  wake got one delayed retry instead of a discard.)
- **Don't advance the read cursor until the cycle succeeds.** A crashed cycle whose events
  were already marked seen loses them; hold the watermark back on failure so the next tick
  re-delivers (at-least-once).

  **Honesty note:** the live runner does not have this yet. A failed *poll* is safe (the
  cursor never advances past what was fetched), but once a tick dispatches, the cursor
  advances even if the dispatched cycle crashes — delivery is currently **at-most-once**.
  The cursor hold-back is explicitly on the build list (GITHUB-NATIVE-PLAN.md, item 2
  follow-up). Until it lands, a crashed wake needs a re-poke — any new comment on the issue
  re-wakes its owner.

---

## Why this matters

In the live Scrum Jail org, before these primitives: **~25% of all wake cycles produced no
work product**, agent-triggered wakes outnumbered the operator's, and the real blockers
(three tiny human inputs) sat buried inside dozens of restated STATUS posts the operator had
stopped reading. The fix is not a smarter prompt — it is teaching the loop the difference
between "blocked," "done," and "nothing to do."
