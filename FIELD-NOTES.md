# Field notes — lessons carried into Scrum Jail 1.0

This is an operational record, not an alternate specification. The enforceable design is
`organization.yaml`, the immutable release under `control/`, and the contracts in
`docs/CONTROL-PLANE.md` and `docs/ARCHITECTURE.md`. Retired runner, hook, direct-CLI, chat,
and workspace-policy mechanisms are intentionally absent from 1.0.

## Incomplete reads must never look like absence

Earlier monitors could turn a failed or partial GitHub read into a close, move, or duplicate
create. In 1.0 every source has an independent staged snapshot. Checkpoints advance only
after complete pagination; staged observations are not routeable. Governance reconciliation
requires a complete fixed-scope snapshot, and UNKNOWN produces no mutation.

Operational consequence: a failed source is a visible no-op for that source. It does not
erase prior knowledge or block complete sources from advancing.

## Agent-writable controls are not controls

Prompt hooks, repository scripts, project-local provider settings, and command-prefix filters
were useful diagnostics but could not carry authority. The 1.0 boundary is an
administrator-owned installed release. Generated repositories contain policy data and thin
clients only. A wake receives a role-bound Unix socket, never a credential, raw transport,
provider choice, model choice, or Human-only operation.

Operational consequence: changing a workspace cannot widen the broker vocabulary, role
matrix, provider sandbox, worker roster, release identity, or activation decision.

## Delivery needs a durable handoff

At-most-once polling lost work when a process stopped between observing and launching. The
1.0 store stages observations, records routes, batches pending routes by role, leases durable
deliveries, and records success, retry wait, budget hold, uncertainty, or dead letter.
Pending routes are rediscovered after a crash, so the routing-to-delivery boundary has no
in-memory-only gap. One role receives at most one batch per supervisor tick.

Operational consequence: a retry may deliver the same event again, so broker mutations are
idempotent and ambiguous outcomes require verification.

## An expired lease does not kill an OS process

Database expiry alone once allowed replacement work while an old provider could still be
running. The supervisor now records the PID, process group, and birth identity before marking
a wake running. Pause, shutdown, and restart recovery terminate the recorded groups first.
A changed birth identity is quarantined rather than signalled, and its budget remains
uncertain for Human inspection.

Operational consequence: replacement capacity is released only after host-level process
death is proved. A provider timeout with uncertain process identity is not reported as a
clean termination.

## Govern tokens, not wake counts

Frequency is only a proxy for spend. Each wake reserves both organization and role token
buckets transactionally before launch. Machine-readable parent and worker usage settles the
reservation. Missing usage, termination after start, or an over-reservation result remains
uncertain; it never becomes free capacity. Budget holds do not consume the delivery retry
ceiling.

Operational consequence: first-party subscription authentication is allowed, but provider
API keys, alternate backends, and caller-selected models are not.

## Workers must not inherit parent authority

Native provider delegation and recursive agent tools made worker depth an authority escape.
The only 1.0 workers are the controller-owned named Luna types: `researcher`, `drafter`, and
`implementer`. Their model, tools, timeout, cumulative cap, concurrent cap, and scratch path
come from policy. Workers receive no wake socket; implementers may return only declared file
changes and never receive a shell.

Operational consequence: the parent remains accountable for tests, broker requests, branch
submission, and every durable effect.

## Subscription auth and tool authority are different boundaries

A CLI must read its subscription session while its model-generated tools must not read that
auth storage. Commissioning installs sealed provider policy and a harmless random sentinel,
then proves three facts: the agent OS identity can read the sentinel, the sandbox is healthy,
and the sandboxed tool cannot read it. Codex uses a sealed named permission profile; Claude
uses path-scoped native settings and is supported for 1.0 only on macOS, where its Unix-socket
allowlist can remain exact.

Operational consequence: an unknown version, auth shape, sandbox result, or token-usage shape
fails closed. Commissioning does not invoke a spend-bearing Codex model; Claude's tool probe
is explicit because its native sandbox has no equivalent deterministic command probe.

## Ambiguous writes are evidence problems

Repeating a timed-out mutation can duplicate an issue, comment, review, branch update, or
governance queue item. The broker and governance monitor journal intent before the first
write. An operation-specific verifier later classifies the effect as PRESENT, ABSENT,
CONFLICT, or UNKNOWN from exact markers and complete remote state. UNKNOWN remains pending;
it never triggers a blind retry.

Operational consequence: absence or conflict after an ambiguous write requires a new Human-
reviewed request or repair. The system prefers a visible hold to an unproved duplicate.

## Human Principal boundaries remain intentionally manual

The controller cannot create or redefine the organization objective, merge or auto-merge a
pull request, activate a governance release, dispatch hosted workflows, deploy, spend money,
delete or force-update branches, change repository settings, or manage credentials. The
governance monitor maintains only marker-owned children of the commissioned Human queue for
open blockers, proposals, and clean merge-ready pull requests. It does not infer Project
stage moves or synthesize cross-PR coordination issues without a specified evidence model.

Operational consequence: install, credential setup, repository protection, shadow-to-live
enablement, activation, merge, workflow dispatch, deployment, and payment remain explicit
Human/operator actions in `RUNBOOK.md`.

## Release evidence is broader than a green unit suite

The 1.0 gate includes control and generator tests, a fresh generated organization and its
complete tests, Python compilation, ShellCheck, whitespace checks, Codex execution-policy
checks, provider settings validation, real Unix-socket peer authentication, and the Codex
sandbox sentinel denial probe. Ordinary verification performs no live GitHub mutation,
workflow dispatch, deployment, or spend-bearing provider run.

That evidence demonstrates the reviewed implementation; it does not replace Human review,
release activation, or production commissioning.
