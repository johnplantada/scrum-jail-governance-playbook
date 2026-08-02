# Trusted control-plane contract

The package under `control/sjg_control` is the reference enforcement core. It intentionally
uses standard-library protocols and SQLite rather than an agent framework.

## Deterministic guarantees

- `policy.py` compiles only the canonical clean-break roles and binds a digest to the source
  release, repositories, budgets, and worker capabilities.
- `authz.py` is a closed role-operation matrix. Human-only operations are absent, not
  prompt-denied. Ownership, typed hierarchy, evidence, PR head role, protected path, and
  current-state checks are deterministic.
- `store.py` reserves requests, workers, budgets, snapshots, deliveries, and reconciliation
  operations in immediate transactions with WAL and full synchronization.
- `github_backend.py` constructs every endpoint, method, repository, branch, label, and
  mutation body. It never accepts raw transport authority.
- `delivery.py` never advances a source checkpoint on a partial failure.
- `reconcile.py` maps incomplete facts to no mutation.
- `provider.py` validates subscription auth, strips ambient credentials/config overrides,
  validates Claude settings, and constructs sealed launches.
- `workers.py` imports only declared changes from a disposable worker scratch directory and
  never grants recursive authority.

## Host integration contract

No privileged supervisor daemon is included in this release. The modules below are a
reference enforcement core, and the generated clients deliberately fail closed until a
separately reviewed host integration supplies the service lifecycle described here.

The host supervisor owns process lifecycle, OS users, immutable release mounts, provider
homes, GitHub App credentials, source adapters, wake sockets, and mutable state directories.
Generated repositories do not contain a service credential or a provider launch path.

Before creating a wake, host integration must reserve both organization and role token
buckets, persist the delivery lease, create a server-side session, and create its socket with
an authenticated peer UID. It must revoke the session and settle or mark the reservation
uncertain on every exit path.

An expired database row is not proof that its OS process has stopped. Active wake and worker
rows continue to consume capacity until the host terminates the entire process group. It must
then call the expired-session reaper, which fails active workers, retains any unknown budget
reservation as uncertain, and only then permits a replacement wake.

Before activation, verify the installed `MANIFEST.json` and match both `version` and
`sourceTreeSha256` to `organization.yaml`. Before starting a provider, verify exact supported
CLI versions, parse machine-readable auth status, validate settings, use an absolute binary,
and construct an allowlisted environment with a supervisor-owned home and fixed trusted
`PATH`/`SHELL`. For Codex, the validated `config.toml` and `rules/` must be installed as the
root-protected `.codex` layer of that exact managed home; do not rely on the invoking user's
home or an ambient `CODEX_HOME`. Keep mutable subscription state inaccessible to agent
workspaces while preserving the CLI's first-party login. Commissioning must prove with a
sentinel read test that model tool subprocesses cannot read provider auth storage; a valid
login-status response alone is insufficient.

## Protocol evolution

Add operations by extending the closed operation set, facts model, authorization decision,
fixed backend, audit record, failure semantics, and adversarial tests together. Never add a
generic command, URL, HTTP method, GraphQL document, Git ref, provider/model override, or
arbitrary MCP passthrough.
