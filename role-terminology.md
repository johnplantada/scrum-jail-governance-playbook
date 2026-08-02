# Canonical 1.0 Role Terminology

Governance names are capability claims. Policy, grants, logs, labels, and manifests use
functional canonical ids; a UI may show a friendly title, but that title grants no authority.

| Canonical id | Kind | Scope | Truthful function |
|---|---|---|---|
| `human-principal` | human | portfolio | Owns consequences and ratifies hard-gate actions |
| `portfolio-orchestrator` | agent | portfolio | Prioritizes and allocates inside ratified charters and envelopes |
| `project-orchestrator` | agent | project | Coordinates one project's assigned objectives |
| `commercial-planner` | agent | project | Produces commercial plans and evidence |
| `software-implementer` | agent | project | Produces software changes and delivery evidence |
| `policy-reviewer` | agent | project | Reviews artifacts against a named policy corpus; never certifies |
| `governance-monitor` | deterministic service | project | Reconciles ledgers, routing, queue state, and mechanical drift |
| `governance-reviewer` | agent | project | Resolves only ambiguous findings emitted by the monitor |

## Naming rules

1. Name an agent for the artifact or judgment it produces, not a human office it imitates.
2. Declare `kind`, `scope`, `lifetime`, authority, prohibitions, write scope, network scope,
   delegation, and output contract in its manifest.
3. A deterministic component is a service, never an agent.
4. `reviewer` may advise or withhold a review input; it does not certify, deploy, merge, or spend.
5. `orchestrator` may dispatch only within its declared scope and ratified envelope.
6. Authorization evaluates canonical ids and grants; display wording never grants authority.

## Version policy

Version 1.0 has no legacy runtime aliases. Generated organizations emit canonical role ids,
`role:*` wake labels, `human_principal`, `principal_queue_issue`, and `roles:` only. Existing
portfolio organizations are regenerated or migrated once; the golden does not carry permanent
branches for retired schemas. Historical incident reports may retain old terminology only when
quoting the system that actually failed.
