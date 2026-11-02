# Trusted control-plane contract

The package under `control/sjg_control` is the reference enforcement core. It intentionally
uses standard-library protocols and SQLite rather than an agent framework.

## Deterministic guarantees

- `policy.py` compiles only the canonical clean-break roles and binds a digest to the source
  release, repositories, budgets, and worker capabilities.
- `authz.py` is a closed role-operation matrix. Human-only operations are absent, not
  prompt-denied. Ownership, typed hierarchy, evidence, PR head role, protected path, and
  current-state checks are deterministic. `control_backend.py` refreshes mutable GitHub facts
  and repeats authorization immediately before the fixed backend's first write.
- `store.py` reserves requests, workers, budgets, snapshots, deliveries, and reconciliation
  operations in immediate transactions with WAL and full synchronization.
- `github_backend.py` constructs every endpoint, method, repository, branch, label, and
  mutation body. It never accepts raw transport authority.
- `delivery.py` never advances a source checkpoint on a partial failure.
- `reconcile.py` maps incomplete facts to no mutation.
- `provider.py` validates subscription auth, strips ambient credentials/config overrides,
  validates Claude settings and the exact Codex execution-policy digest, and constructs sealed
  launches.
- `workers.py` imports only declared changes from a disposable worker scratch directory and
  never grants recursive authority. Import is rejected if any changed parent file no longer
  matches the worker's starting snapshot.

## Host integration contract

`sjg-supervisor` is the 1.0 single-host integration. It owns process lifecycle, OS-user
separation, immutable release binding, provider homes, the GitHub service credential, five
complete source adapters, wake/admin sockets, mutable state, shadow/live execution, and
operation-specific ambiguous-write verification. Generated repositories do not contain a
service credential or a provider launch path.

The supervisor configuration is an exact-field JSON document. Every path is absolute,
organization and wake rules are digest-bound, each role has only its policy-declared
workspaces and model, provider versions are an explicit allowlist, and operator/agent UIDs
must differ. Authority, credentials, state, sockets, scratch, and provider homes must remain
outside agent workspaces. The shipped `share/supervisor.example.json` and service templates
are reviewed starting points, not self-installing authority.

Before creating a wake, the supervisor reserves both organization and role token buckets,
persists the delivery lease, creates a server-side session, and creates its socket with an
authenticated peer UID/GID. It revokes the session and settles or marks the reservation
uncertain on every exit path. Parent wakes receive only their own unguessable socket path;
workers receive no broker socket.

An expired database row is not proof that its OS process has stopped. The supervisor records
PID, process group, and a PID-reuse-resistant birth identity before marking a wake running.
Recovery terminates the recorded process group, fails active workers, retains unknown token
usage as uncertain, and only then permits replacement capacity. A changed process identity is
quarantined for Human inspection rather than killed.

Before activation, verify the installed `MANIFEST.json` and match both `version` and
`sourceTreeSha256` to `organization.yaml`. Before starting a provider, verify exact supported
CLI versions, parse machine-readable auth status, validate settings, use an absolute binary,
and construct an allowlisted environment with a supervisor-owned home and fixed trusted
`PATH`/`SHELL`. For Codex, `commission-provider` renders the sealed named-permissions template
with the exact managed-home path and installs its config/rules as that home's `.codex` layer.
The parent tool filesystem profile writes only the commissioned workspace, reads only the
minimal OS surface plus the immutable control release needed by its client, denies reads of
`.codex`, denies network, and adds only the current wake socket at launch. Workers do not
receive that socket. Claude uses sealed native filesystem/network profiles and is supported
for 1.0 only on macOS because Linux cannot express the required exact Unix-socket allowance.
Claude launches with user/project/local setting sources and auto-memory disabled; managed
host policy may still tighten the boundary. Do not rely on an invoking user's home or ambient
config.

Commissioning creates a random harmless sentinel in provider auth storage. `check` first
proves the agent OS identity can read it without a sandbox, proves the provider sandbox is
healthy, then requires the same read to be denied inside the provider tool boundary. Codex
uses its deterministic sandbox command; Claude requires an explicit denied `Read` tool result.
Readable contents, an unavailable sandbox, an unattempted probe, unknown output, or an
unsupported CLI version all fail closed. Service startup repeats this commissioning gate.

## Runtime entrypoints

- `sjg-supervisor commission-provider` installs sealed provider policy and rotates the harmless
  sentinel without modifying subscription auth.
- `sjg-supervisor check` validates release binding, configuration, provider auth/version,
  sandbox isolation, and state visibility.
- `sjg-supervisor preview` polls and routes against a SQLite backup; live checkpoints,
  deliveries, budgets, sessions, and GitHub state are untouched. Shadow mode also rejects
  manual wakes and live governance sync.
- `sjg-supervisor serve` recovers durable state before the provider gate, opens the
  UID-authenticated admin socket, and schedules polling. Shutdown terminates/reconciles active
  process groups before exit. `tick`, `recover`, and `status` are bounded operator diagnostics.
- `sjg-admin` exposes the closed operator protocol. `sjgctl` remains the role-bound agent
  protocol and has no operator operations.

Ambiguous mutations remain `verify_pending` until the fixed verifier proves the exact
idempotency marker or complete remote state PRESENT, ABSENT, or CONFLICT. UNKNOWN never
becomes a blind retry. `monitor sync` resolves only proved records.

## Protocol evolution

Add operations by extending the closed operation set, facts model, authorization decision,
fixed backend, audit record, failure semantics, and adversarial tests together. Never add a
generic command, URL, HTTP method, GraphQL document, Git ref, provider/model override, or
arbitrary MCP passthrough.

### Completion evidence trust

A demo comment is evidence only when its idempotency key resolves to a succeeded
`pm.issue.comment` in the installed controller's durable journal, created by the
Commercial Planner in the same organization and policy digest. The target issue,
remote comment ID, original body, and recorded GitHub author ID must all match.
Text banners, markers, and `author_association` alone confer no authority. Missing
journal access, legacy receipts without author identity, and policy changes fail
closed; the Commercial Planner must independently review and post fresh acceptance.
No trusted controller username is inferred from the Human Principal login.

Nonempty current acceptance criteria and a unique matching acceptance digest are
required. Features require `--demo`; merged PR linkage alone cannot complete them.
The `--pr` route for internal stories proves only a related merge and a matching
acceptance digest in the PR body. It does not certify successful tests or a demo.
Product work must use the demo route even when represented as a story.

Product demos require a PR number, its current exact head SHA, and a successful,
completed `demo-evidence.yml` run in the configured product repository. Both the
original and triggering actors must be the configured Human Principal; the run
must originate from the configured product default branch through workflow dispatch.
The run must contain one unexpired artifact named
`demo-evidence-<PR number>-<current PR head SHA>` with complete artifact enumeration.
The standard workflow runs on main and checks out the requested PR SHA, so its
`run.head_sha` identifies main, not necessarily the PR head. The protected workflow's
fixed artifact name binds the requested SHA; no claim is made that artifact metadata
alone independently proves the evidence contents. The Planner still reviews those
contents against acceptance. Adapting that protected workflow requires Human review.

Unknown workflow/run/artifact reads never establish evidence. A missing principal,
missing identity fields, more than one artifact page, or unsupported workflow layout
blocks completion until commissioning or a separately reviewed pagination extension.
These checks do not grant workflow dispatch, merge, deployment, or spending authority.
