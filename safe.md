# Evidence-gated delivery cadence

Cadence is derived from durable work events, not a clock that wakes agents to restate status.
No routed event means no wake and no provider usage.

## Work hierarchy

The Human Principal creates objectives. Project Orchestrator creates and assigns the minimum
epics needed to coordinate an objective. Delivery owners create features and stories just in
time. The controller enforces `objective → epic → feature → story`, typed parents, ownership,
and complete child enumeration.

Features and stories carry acceptance or done-when text. Completion requires evidence bound
to the exact issue:

- a merged product pull request that relates to the item;
- an accepted `[DEMO]` comment containing the item reference and current acceptance digest.

Repeating an acceptance or done-when statement is never completion evidence.

Closing keywords in agent pull requests are rejected. Objective and epic closure remains a
Human decision or deterministic rollup commissioned on the Human path.

## Flow

`Todo → In Progress → Awaiting Merge → Demo → Awaiting Deploy → Done` is the ordered flow.
`Blocked` and `On Hold` are holding states, not progress. `Dropped` is terminal and requires a
reason. Agents may move only their own work forward; backward movement and terminal changes
outside the feature/story evidence gate require Human intervention.

## Reviews and deployments

Review and demo workflows are manual-only and bind a Human-supplied pull-request number to
its live head SHA. A review result is evidence, not approval or merge authority. The preview
workflow builds an unprivileged sealed artifact first; only the approval-gated deploy job
receives OIDC and deployment permissions.

The Human Principal reads the bound evidence, merges the pull request, and separately
dispatches deployment. A merge never implies a deployment.

## Failure behavior

Partial pagination advances no cursor. Delayed events are recovered through a bounded
overlap window and deduplicated by external identity and version. Expired pre-start leases
may retry; an expired running lease becomes uncertain. Budget-held work remains queued.
Dead letters and uncertain mutations require explicit Human review.
