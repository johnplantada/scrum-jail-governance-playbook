# Scrum Jail Governance Playbook 1.0

A clean-break generator and trusted control-plane reference for bounded, subscription-
authenticated Codex CLI and Claude CLI organizations.

The central security rule is simple: the generated organization is untrusted data and
workspace code. It contains mandates, thin clients, issue forms, workflow templates, and
tests—but no GitHub credential, provider policy, provider launcher, scheduler cursor,
budget authority, or governance mutation logic. Those live in an installed, immutable
control release outside every agent-writable product directory.

## Canonical roles

`human-principal`, external `portfolio-orchestrator`, `project-orchestrator`,
`commercial-planner`, `software-implementer`, `governance-reviewer`, and optional
`policy-reviewer`. The generator rejects aliases, missing required roles, duplicates, and
custom compatibility roles.

Only the Human Principal controls objectives, PR merges, workflow dispatch/deployment,
spending authorization/execution, repository settings, and governance activation.

## Repository layout

- `control/` — trusted policy compiler, authorization broker, GitHub adapter, durable
  delivery/reconciliation state, provider policy, and bounded named-worker supervisor.
- `bin/orggen` — atomic organization generator with source/payload provenance.
- `bin/sjg-install` — Human/operator installation into an immutable version directory.
- `bin/sjg-activate` — Human-only, organization-bound atomic release activation.
- `runtime/` and `_init/` — generated workspace data and unprivileged client shims.
- `schemas/organization.schema.json` — portable desired-state contract.

## Generate and verify

```sh
bin/orggen init ../example-org \
  --org example-org \
  --product Example \
  --goal "validated outcome" \
  --principal-github your-login \
  --product-repo your-login/example-product

make test
make verify
```

Use `--allow-dirty` only for a development stamp. Dirty stamps record exact source and
payload digests but are not release-qualified.

## Enforcement boundary

Agents request issue/Project/PR operations through `scripts/pm-gh.sh`, remote branch
submission through `scripts/git-push-branch.sh`, and named Luna workers through
`scripts/worker-run`. The per-wake Unix socket is server-bound to one role and policy
digest. Caller-supplied roles, tokens, URLs, methods, raw commands, provider/model choices,
and protected paths are rejected.

Incomplete external reads are UNKNOWN. They never become deletion, closure, movement, or
absence. Source cursors advance only after complete pagination, delivery is at least once,
budgets and worker caps reserve transactionally, and ambiguous writes enter verification
instead of being repeated blindly.

See [docs/CONTROL-PLANE.md](docs/CONTROL-PLANE.md) and [RUNBOOK.md](RUNBOOK.md).
