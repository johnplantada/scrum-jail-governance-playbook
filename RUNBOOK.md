# Scrum Jail 1.0 commissioning runbook

All installation, activation, credential, repository-setting, and hosted-workflow steps in
this runbook are Human Principal/operator actions. Never execute them inside an agent wake.

## 1. Verify the golden

Run `make verify`. It covers the control package, generator boundary tests, a fresh dirty
development stamp and its complete generated test suite, Python compilation, ShellCheck,
workflow policy, provenance, and whitespace checks.

A publishable release additionally requires a clean working tree. `orggen` and the
installer refuse dirty release output unless `--allow-dirty` is explicit; such output is
permanently marked `releaseQualified: false`.

## 2. Stamp the organization

Run `bin/orggen init` with the Human Principal login and both repository identities. Review:

- `organization.yaml` for authority, repositories, budgets, canonical agents, worker caps,
  and the required control-release digest;
- `org-chart.yaml`, `agents/`, `wake-rules.yaml`, issue forms, CODEOWNERS, and workflows;
- `playbook/SOURCE.json` with `scripts/verify-provenance.py`.
- For release verification, set the protected repository variable
  `SJG_SOURCE_MANIFEST_SHA256` to the SHA-256 of the Human-approved
  `playbook/SOURCE.json`; release verification fails closed when it is absent or stale.

The target must be new and outside the golden. Generation stages into a sibling directory,
validates all structured output, then renames atomically.

## 3. Install and activate the controller

Install under an administrator-owned prefix, normally `/usr/local/libexec/scrum-jail`:

```sh
sudo bin/sjg-install --prefix /usr/local/libexec/scrum-jail
sudo bin/sjg-activate \
  --prefix /usr/local/libexec/scrum-jail \
  --version 1.0.0 \
  --organization /absolute/path/to/org/organization.yaml
```

Installation copies no credentials. Activation verifies every release file and requires
the organization’s version and source digest to match before atomically changing `current`.
Dirty activation requires an explicit development override and must never be production.

Keep mutable service state, sockets, provider homes, and GitHub App credentials outside the
release and organization repositories. The service identity may read the active release but
must not write it. Agent processes may write only commissioned workspaces and their own
scratch paths.

## 4. Commission provider sessions

Choose Codex CLI or Claude CLI in supervisor-owned configuration and use absolute executable
paths. Log the service identity into the selected first-party subscription/OAuth session.
Do not set provider API keys, alternate base URLs, Bedrock, Vertex, or Foundry flags.
Use a fixed administrator-reviewed `PATH` and shell; never inherit either from a wake or
workspace. For Codex, place the validated installed policy at the managed home's `.codex`
config/rules location so the same home supplies both sealed policy and first-party login.
Before enabling wakes, place a harmless sentinel beside the provider auth state and prove a
sandboxed tool subprocess cannot read it. Treat a readable sentinel as a commissioning
failure even when subscription status is healthy.

Run the read-only supervisor authentication check. Unknown status output or an unexpected
CLI version fails closed. Codex uses an installed config/rules layer with no process network,
no project trust, no multi-agent tools, and forbidden direct network/GitHub/provider command
forms. Claude uses validated settings plus redundant sealed command-line restrictions;
sandbox unavailability, excluded commands, unsandboxed retry, bypass mode, auto mode,
project hooks, MCP expansion, remote control, and arbitrary Unix sockets are denied.

## 5. Commission GitHub

Use a controller-only GitHub App installation or equivalently scoped service credential.
Do not share the Human Principal token and do not expose `GH_TOKEN`, `GITHUB_TOKEN`, or the
GitHub configuration directory to agents.

As the Human Principal:

1. Create both repositories and the organization Project.
2. Install the labels and Project fields using `scripts/github-pm-setup.sh` from an operator
   shell. This is the only generated direct-`gh` commissioning utility.
3. Protect `main`: PR required, Human/Code Owner review required, force pushes and deletion
   disabled, and workflows unable to satisfy the Human review.
4. Set repository variable `HUMAN_PRINCIPAL_LOGIN` to the exact principal login.
5. Keep all hosted workflows manual-only. Configure the `preview` environment with the
   Human Principal as required reviewer and a prefix-scoped OIDC role.
6. Create and pin the Human Principal action queue and Project number.

Do not run live GitHub mutation or spend/deploy smoke tests during ordinary verification.

## 6. Start in observation mode

The playbook ships the enforcement core and clients, not a turnkey privileged service
daemon. Before this step, implement the host integration contract in
`docs/CONTROL-PLANE.md` under a separate OS identity; provide its own reviewed service
entrypoint, source adapters, GitHub App credential loader, provider launcher, and admin/wake
socket lifecycle. Do not point the generated clients at an ad-hoc wrapper around `gh` or a
provider CLI.

Start that commissioned supervisor with source polling in preview mode. Each of the five sources
has an independent checkpoint: org issues, org comments, product issues, product comments,
and product workflow runs. Confirm complete pagination, routing, duplicate suppression,
leases, retry waits, budget holds, and dead-letter visibility before enabling wakes.

Then run the deterministic governance audit in dry-run mode. Incomplete observations must
produce no planned mutation. Enable reconciliation writes only after stable source markers,
preconditions, idempotency keys, and a separately reviewed, operation-specific marker verifier
are visible. The reference core records ambiguous results as `verify_pending` but does not ship
that host verifier; until it exists, keep reconciliation writes disabled.

## 7. Inject work

The Human Principal creates the first objective. The Project Orchestrator may create an epic
only under that objective; downstream roles create features and stories under typed parents.
Agents may propose governance or spending, but only Human merge/activation and Human payment
execution have effect.

## 8. Operate and recover

- Halt with `make halt`; this requests the supervisor-owned pause state. The host terminates
  every parent/worker process group before revoking active wake sessions. Resume with
  `make resume` through the authenticated operator socket.
- On an incomplete source read, retain its checkpoint and process no absence-based action.
- On a retryable read, back off without consuming delivery.
- On an ambiguous mutation, mark verification pending. Never retry until the commissioned
  operation-specific verifier proves the write absent.
- On budget exhaustion, retain queued work in `budget_hold` until a later eligible epoch.
- On a dead letter, preserve event, route, attempts, and error; require explicit Human replay.
- Upgrades are install-new, verify, Human activate, and restart. Never self-fetch authority
  code from a generated organization.
