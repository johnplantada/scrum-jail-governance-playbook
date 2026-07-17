---
name: board-proposals
description: Compose a Board-gated org-shape proposal — a [CHARTER] for a new department (🏛️) or a [PROMOTE] for a model-tier change (💎) — as a decisions.yaml PR whose payload is the exact org-chart.yaml change. Use when proposing a department or relaying/requesting a tier change; these are rare, deliberate acts.
---

# Board proposals — CHARTER and PROMOTE payloads

Both are **proposals**: nothing exists or changes until the Chairman authorizes. Never
assume approval.

## The path — a decisions.yaml PR (GITHUB-NATIVE-PLAN.md)

Board decisions (spend / charter / promote / sunset) are reviewed merges: append ONE
entry to `decisions.yaml` (schema in the file header — id, type, dept, what, why,
cost_usd, chairman_minutes, reversibility, unblocks, proposed, and the executable
`payload` for org-shape types) via the `org-worktree` skill and open the PR. CODEOWNERS
routes it to the Chairman; **his merge is the authorization** — the diff is exactly what
was approved, and `git log decisions.yaml` is the decision history. CI runs
`scripts/decisions.py check`, so a malformed entry cannot merge. Never assume approval;
an unmerged proposal is a no.

## `[CHARTER]` — a new top-level department (🏛️)

Use when the work needs a capability no existing department covers. Sub-teams within an
envelope need NO charter — the parent just announces them (DESIGN §5).

````
[CHARTER] Product Management
```yaml
name: product
role: product-management
reports_to: ceo
envelope:
  max_subagents: 3
  daily_token_budget: 400000
  can_spend: false
  can_deploy: false
```
Rationale: <why this department, what it owns, what success looks like>
````

Set the `model` by role when chartering: a department = `sonnet`, a narrow execution
team = `haiku`. A chartered department also needs its mandate file
(`agents/<name>.md`, from `agents/department.tmpl.md`) and, if the runner should route
a `dept:<name>` label to it, a `wake-rules.yaml` rule — include both in the same PR so
the approval covers the whole shape.

## `[PROMOTE]` — raise (or lower) a node's model tier (💎)

Use for a sustained tier change on a business case — a discrete hard question goes
through `OFFLOAD_ESCALATE` instead (see `_policy.md`). To dial back down later, send the
same payload with the lower tier.

````
[PROMOTE] IT → Opus for the checkout refactor
```yaml
name: it
model: opus
```
Rationale: <which task, why the current tier falls short, expected payoff>
````

The payload is the org-chart.yaml edit itself: the same PR that appends the decision
entry carries the chart's `model:` change, so the Chairman's merge applies the tier in
the same act that authorizes it.
