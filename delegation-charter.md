# Portfolio Delegation Charter — 1.0

This charter establishes one `human-principal`, one `portfolio-orchestrator`, and any number
of independently governed project cells. It is optimized for agent work: authority follows
artifacts and explicit grants, not simulated executive rank.

## Authority boundary

The Human Principal owns consequences and exclusively ratifies:

- new spend commitments and changes to spending ceilings;
- durable changes to roles, authority, or this charter;
- production deployment and public/external action;
- account, credential, and repository-security changes;
- clearing the portfolio halt.

The Portfolio Orchestrator may prioritize, research, plan, allocate already-ratified capacity,
dispatch project objectives, and recommend a gate action. It cannot merge its own authority
change, spend, deploy, publish, or perform a human-only action.

## Agent-native operating shape

Project cells are independent loops, not departments in a hierarchy. Each has a
`project-orchestrator`, role-owned work queues, deterministic governance services, its own
budget envelope, and a local halt. The Portfolio Orchestrator communicates with a project cell
through two typed artifacts:

1. an objective dispatch containing scope, acceptance evidence, budget, and priority;
2. a result report containing delivered evidence, remaining risk, usage, and gate requests.

This isolates failures: a stalled or over-budget project cell cannot consume another cell's
queue, credentials, or model allowance.

## Portfolio-level task workers

The Portfolio Orchestrator may directly create bounded, ephemeral task workers for independent
portfolio work. Default worker capabilities are:

| Worker | Output | Mutation authority |
|---|---|---|
| `portfolio-researcher` | sourced findings across projects or markets | read-only |
| `portfolio-planner` | ranked options, dependency maps, and allocation proposals | read-only |
| `portfolio-auditor` | cross-project policy, budget, and drift findings | read-only |
| `migration-coordinator` | an explicit migration plan and verification record | only named files in an approved migration task |

Workers report only to the Portfolio Orchestrator, are created for one task, receive a time and
token bound, and terminate after returning their artifact. They cannot dispatch project work,
create objectives, spend, deploy, publish, or recursively delegate unless the charter explicitly
grants that capability. The Portfolio Orchestrator synthesizes their results and remains
accountable for the portfolio decision.

## Shared services

Cross-project polling, authentication checks, budget accounting, health summaries, and schema
validation are deterministic services. They are shared infrastructure, not agent roles. They
emit facts; ambiguous residue routes to a named reviewer. This keeps model loops for judgment
and uses ordinary code for reconciliation.

## Human Principal queue

Every human-only request enters one portfolio queue with expected value, exact action,
estimated `principal_minutes`, deadline if real, and the project it unblocks. Duplicate asks are
reconciled mechanically. Silence never means approval.

## Ratification and amendment

The Human Principal's reviewed merge of this charter is ratification. Amendments use the same
path. There are no alternate titles or compatibility identities in schema 1.0.
