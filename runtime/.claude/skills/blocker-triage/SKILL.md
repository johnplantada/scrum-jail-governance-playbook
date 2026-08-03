---
name: blocker-triage
description: Record a human-only blocker once in blockers.yaml and end the cycle silently. Use when only the Human Principal can supply an external input or authorize a governed action.
---

# Blocker triage — record it once, then go quiet

## 1. Confirm the capability boundary

A ledger entry is valid only when the unblock requires:

- **external-input** — credentials, account registration, money, a real URL/address,
  or another artifact an agent cannot create; or
- **governance** — a Human-only objective, merge, workflow dispatch/deployment, spend,
  repository-setting, or governance-activation action.

A peer or technical dependency belongs on the work item, not in this ledger.

## 2. Dedupe

Read `blockers.yaml`. If an open entry already names the same unlock, add the new item
to its `blocks:` list if needed, then stop. Do not post another status message.

## 3. Propose one open entry on your scoped branch

```yaml
  - id: <kebab-case-slug>
    kind: external-input | governance
    needs: human-principal
    summary: >-
      What is blocked and why only the Human Principal can clear it.
    action: "The single concrete action the Human Principal takes."
    blocks: [<work-items or outcomes>]
    value: <revenue | signal | infra>
    effort_minutes: <int>
    opened: <YYYY-MM-DD>
    state: open
```

Estimate `principal_minutes`-equivalent effort honestly. Never flip an entry to
`cleared`; only the Human Principal does that.

## 4. Park and stop

If a board item you own is in flight, run
`scripts/pm-gh.sh move --id N --to Blocked`. Move it back when the Human Principal's
clearing event arrives. Do not post a separate blocked status. The Human-facing WIP warning
is prioritization guidance; it never widens or narrows your compiled capabilities.
