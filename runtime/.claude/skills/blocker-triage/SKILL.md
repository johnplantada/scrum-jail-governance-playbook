---
name: blocker-triage
description: Record a human-only blocker once in blockers.yaml and end the cycle silently. Use when work is blocked on something only the Chairman can do — cloud credentials, money, registering an account, a real URL or mailing address, or a 🚀/💰/🏛️/💎 authorization.
---

# Blocker triage — record it once, then go quiet

You hit a wall only the Chairman can clear. The ledger (`blockers.yaml`, repo root) is the
Chairman's queue (DESIGN.md invariant 2) — never a status post. Follow these steps exactly.

## 1. Confirm it is actually a human-only blocker

It qualifies only if the unblock requires one of:
- **external-input** — a credential, account registration, product/storefront URL, mailing
  address, or any real-world artifact an agent cannot create;
- **governance** — a Chairman authorization: a decisions.yaml merge (💰 fund ·
  🏛️ charter · 💎 promote · ⚰️ sunset) or the 🚀 prod-deploy dispatch (the
  Chairman's manual workflow_dispatch).

A peer/tech dependency (waiting on IT's estimate, a failing test) is NOT a ledger entry —
that goes in your `STATUS` `blocked:` line or a thread, as usual.

## 2. Dedupe against the open ledger

Read `blockers.yaml`. If an open entry already covers this blocker (same unlock, even if
your work item is new), **stop here — post nothing, add nothing.** Re-announcing a ledgered
blocker is the exact noise the ledger exists to remove. If your new work is gated by the
same entry, you may append your item to that entry's `blocks:` list, nothing more.

## 3. Append one entry (state: open)

Edit `blockers.yaml` in place (the ledger is live runtime state — no git operations, no
worktree). Match the file's schema:

```yaml
  - id: <kebab-case-slug>
    kind: external-input | governance
    needs: chairman
    summary: >-
      What is blocked and why only the Chairman can clear it. Be specific enough that
      the Chairman can act without asking a follow-up question.
    action: "The single concrete step the Chairman takes to clear it."
    blocks: [<work-items, e.g. deploy, revenue, docket-v1>]
    value: <revenue | signal | infra>   # what clearing it unlocks — orders the Chairman's queue
    effort_minutes: <int>               # honest Chairman-minutes estimate; cheap unlocks print first
    opened: <YYYY-MM-DD>
    state: open
```

`value` and `effort_minutes` are how the queue self-prioritizes (`scripts/blockers.py` prints
best-value-first, cheapest-first within a class): revenue = a conversion path goes live,
signal = market evidence lands, infra = pipeline/deploy capability. Estimate effort honestly —
an inflated estimate buries a quick win.

Never flip an entry to `cleared` — only the Chairman does that, and the clearing is itself
the new inbound that wakes you.

## 4. Park the ticket, then end the cycle silently

- If a board ticket was in flight, **`scripts/pm-gh.sh move --id N --to Blocked`** so the
  board shows it parked (not feigning progress in Doing). This is the ledger entry's board
  reflection — the ledger stays the record; the column just makes the stall visible. Move it
  back to its flow stage the cycle the Chairman clears the blocker. (No board ticket yet? Skip
  this — the ledger alone is enough.)
- Do **not** post a `STATUS` about being blocked.
- Do **not** re-list ledgered blockers in any future `STATUS` — at most a one-line
  `blocked: see ledger (N open)` pointer.
- If the ledger entry gates your whole critical path (`blocks: [deploy]` or `[revenue]`),
  you are now in a **deploy-hold**: follow the deploy-hold rules in `agents/_policy.md`
  (minimum motion, no speculative inventory, no ceremony).
- If the queue you just joined is over `global.unlock_wip_limit` (org-chart.yaml — the
  injected queue opens with a ⚠ warning when it is), the whole org is in queue-overflow:
  do not start further work that terminates in yet another human-only unlock.
