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

Copy `current/share/supervisor.example.json` to an administrator-owned configuration path.
Replace every example path, UID/GID, model/version, token reservation, Project identity, and
the all-zero wake-rules digest. The workspaces and model maps must contain every and only the
roles/repositories in `organization.yaml`. Create distinct operator and agent OS identities;
the supervisor runs as root and drops every provider process to the configured agent UID/GID.
Review the matching service template under `current/share/service/` against those exact paths.

## 4. Commission provider sessions

Choose Codex CLI or Claude CLI in the root-owned supervisor configuration and use absolute
executable paths. The provider managed home must be owned by the configured agent UID. Log
that agent identity into the selected first-party subscription/OAuth session.
Claude is supported for 1.0 only on macOS: its native sandbox can allow one exact Unix-socket
path there. Linux commissioning rejects Claude rather than enabling arbitrary Unix sockets;
use Codex on Linux.
Do not set provider API keys, alternate base URLs, Bedrock, Vertex, or Foundry flags.
Use a fixed administrator-reviewed `PATH` and shell; never inherit either from a wake or
workspace. Install the sealed settings and harmless random sentinel without changing auth:

```sh
sudo /usr/local/libexec/scrum-jail/current/bin/sjg-supervisor \
  --config /etc/scrum-jail/supervisor.json commission-provider
```

For Codex this renders the exact managed-home read denial and immutable control-release read
path into `.codex/config.toml`; for Claude it installs the three exact settings profiles
outside the workspace.

Run the full commissioning check:

```sh
sudo /usr/local/libexec/scrum-jail/current/bin/sjg-supervisor \
  --config /etc/scrum-jail/supervisor.json check
```

It verifies release/config binding, exact subscription status/version, proves the sentinel is
readable by the unsandboxed agent identity, proves the sandbox is operational, then requires
the sandboxed read to fail. Any unknown output fails closed. Codex uses an installed
config/rules layer with no process network,
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

Keep `mode` set to `shadow`. Run an explicit preview before loading the reviewed systemd or
launchd service:

```sh
sudo /usr/local/libexec/scrum-jail/current/bin/sjg-supervisor \
  --config /etc/scrum-jail/supervisor.json preview
```

Preview polls and routes against a SQLite backup; it does not advance live checkpoints,
create live deliveries/sessions/budget holds, launch a provider, or mutate GitHub.
While the root-owned configuration remains in `shadow`, manual wakes and non-dry-run
governance sync are rejected as well.

Start that commissioned supervisor with source polling in preview mode. Each of the five sources
has an independent checkpoint: org issues, org comments, product issues, product comments,
and product workflow runs. Confirm complete pagination, routing, duplicate suppression,
leases, retry waits, budget holds, and dead-letter visibility before enabling wakes.

Then run `sjg-admin monitor audit --dry-run` through the authenticated operator socket.
Incomplete observations must produce no planned mutation. The shipped verifier resolves
ambiguous writes only from complete operation-specific markers or exact remote state;
UNKNOWN remains pending and is never retried blindly.

After the Human Principal reviews preview output and service status, stop the service, change
the root-owned config to `"mode": "live"`, rerun `check`, and restart. This explicit edit is
the live-enablement boundary; no agent operation can perform it.

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
- Service startup recovers orphaned process groups before checking provider login, so expired
  auth cannot block containment. Shutdown performs the same process-first recovery before the
  supervisor exits.
