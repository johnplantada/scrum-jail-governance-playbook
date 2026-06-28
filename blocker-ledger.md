# The Blocker Ledger, Capability Boundary & Wake Backpressure

The single highest-leverage thing you can add to a multi-agent org after the emoji gates.
Together these three primitives stop the org's most expensive failure mode: agents waking on a
schedule and re-narrating the same blockers forever (see [patterns.md](patterns.md) Patterns
9–11). They cost a few lines each and pay for themselves in tokens immediately.

---

## 1. The capability boundary

Be explicit about what **no agent can do** — the actions that require a human in the physical
world, not just an approval in chat:

- cloud / infrastructure credentials (e.g. setting a deploy role or repo secret)
- money (a card, a payment account, a paid signup)
- registering an account or a domain
- providing a real-world value: a public URL, a mailing address, a phone number
- the governance reactions themselves (💰 / 🚀 / 🏛️ / 💎)

Write this list into the shared policy every agent loads (`agents/_policy.md`). An agent that
hits one of these has exactly one correct move, below — **not** "try harder," and **not**
"announce it again."

## 2. The ledger (`blockers.yaml`)

A durable file at the repo root — the **operator's queue**. It, not the chat stream, is where
"what does a human need to do" lives. One entry per human-only blocker:

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
    opened: 2026-06-26
    state: open                   # open | cleared
```

**The contract:**
- Agents **read** the ledger every wake (inject the open entries into the agent's prompt) and
  may **append** a new `open` entry for a blocker they hit. They never re-post it as STATUS.
- Agents **never** flip an entry to `cleared` — only the operator does. Clearing it (and the
  post/reaction that does so) is what wakes the org again.
- A tiny helper (`blockers.py open`) prints the open queue for the prompt and for a
  `make blockers` view. The whole point: the operator scans **one short list**, not 70 status
  posts.

## 3. Wake backpressure

The behavioral half. Distinguish *why* an agent woke and gate the expensive part accordingly:

| Wake reason | Rule |
|---|---|
| **direct** — an owner-channel task, the operator, a voice call | always runs a full cycle |
| **broadcast** — a post in a shared discussion channel | runs only if the agent has something to add (a cheap classifier pre-gate); never on the most expensive model |
| **scheduled** — the periodic floor (cron/launchd) | **peek the channels first; if nothing is new since the agent last read, the wake is a no-op — the model never starts** |

The no-op is the saver. A blocked org on a quiet day costs **zero** model tokens instead of one
expensive cycle per agent per wake. And it is safe: the agent can never get stuck silent,
because any unblock — you clearing a ledger entry, a deploy notifier posting — is itself new
inbound that produces a `direct`/new-activity wake.

Pair it with **state-change-only STATUS**: an agent posts only when something changed. A
no-change cycle ends with no post — which also removes the heartbeats that were waking peers
(the self-wake storm, Pattern 11).

## 4. Reliability corollaries

Two cheap fixes that make backpressure safe to lean on:

- **Coalesce suppressed wakes, don't drop them.** If a rate-limiter denies an agent-initiated
  wake, schedule one delayed retry instead of discarding the trigger — otherwise back-pressure
  silently eats real messages.
- **Don't advance the read cursor until the cycle succeeds.** A crashed cycle that already
  marked messages "read" loses them. Snapshot the agent's read watermarks at the start of a
  cycle and roll them back on a non-zero exit (at-least-once delivery).

---

## Why this matters

In the live Scrum Jail org, before these primitives: **~25% of all wake cycles produced no work
product**, agent-triggered wakes outnumbered the operator's, and the real blockers (three tiny
human inputs) sat buried inside dozens of restated STATUS posts the operator had stopped
reading. The fix is not a smarter prompt — it is teaching the loop the difference between
"blocked," "done," and "nothing to do."
