---
name: board-proposals
description: Propose a Human Principal governance change without activating it.
---

# Governance proposals

The clean-break 1.0 roster is fixed. An agent may explain a proposed mandate, worker-cap,
budget, profile, or future schema change in a `[PROPOSAL]` issue. It may also submit a
reviewable `decisions.yaml` entry on its scoped agent branch. Neither a PR nor its merge
changes active authority: only the Human Principal may install and activate a matching
control release and organization policy.

Use functional canonical ids only. Worker policy uses `allowed`, `max_per_wake`, and
`max_concurrent`; the two limits are distinct. Use provider-neutral `fast`, `standard`, or
`deep` profiles, never a provider model id. State rationale, measurable benefit, risks,
rollback, source/version changes required, and all roster-derived surfaces affected.

Do not add a custom role to a 1.0 organization: the schema and policy compiler reject it.
Propose a future versioned schema/control change instead.
