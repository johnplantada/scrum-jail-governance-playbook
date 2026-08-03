# Failure patterns and durable countermeasures

## 1. Prompt-only authority

**Failure:** a mandate says “never merge,” while the wake still holds a credential that can
merge. **Countermeasure:** remove the credential and the operation. Human-only capabilities
are absent from the agent protocol.

## 2. Shared identity

**Failure:** agents use the Human Principal's token, making actor checks meaningless.
**Countermeasure:** supervisor-held service credentials never enter provider environments;
the socket binds a server-side role and the Human uses a separate operator identity.

## 3. Broker bypass

**Failure:** alternate `gh`, `git push`, HTTP, SSH, nested CLI, shell-wrapper, or MCP forms
bypass a friendly script. **Countermeasure:** no process network, no ambient credentials,
sealed provider policy, forbidden direct command prefixes, strict MCP configuration, and a
fixed-operation broker that constructs every endpoint and method.

## 4. UNKNOWN becomes absent

**Failure:** a failed page, GraphQL error, or truncated read is interpreted as deletion,
zero children, or an absent branch. **Countermeasure:** independent complete snapshots,
full pagination, tri-state facts, and no checkpoint or mutation on incomplete evidence.

## 5. Lost or duplicate work

**Failure:** one shared cursor advances past a failed source, delayed indexing misses an
event, or an expired process is blindly replayed. **Countermeasure:** per-source checkpoints,
overlap reads, durable version deduplication, event-to-role route records, leases, retry
backoff, dead letters, and `uncertain` for an expired running attempt.

## 6. Ownership theft

**Failure:** a routing action replaces a legitimate owner or a coordinator creates work it
can never finish. **Countermeasure:** routing is triage-only for unowned items; project-created
delivery work must name a canonical delivery owner at creation.

## 7. Closure without evidence

**Failure:** a PR title closes an issue, a stale demo is reused, or an incomplete child list
looks empty. **Countermeasure:** reject closing keywords; require complete children and exact,
current, item-bound evidence before a feature or story can close.

## 8. Worker recursion or scope creep

**Failure:** a named worker inherits the parent socket, spawns peers, selects a stronger model,
or writes undeclared paths. **Countermeasure:** supervisor-selected worker profiles, no broker
socket, denied Agent/Task tools, cumulative and concurrent reservations, disposable scratch,
and declared-path import.

## 9. Budget races

**Failure:** concurrent wakes each see remaining budget and collectively exceed it, or a crash
releases usage that may have occurred. **Countermeasure:** reserve organization and role
buckets in one immediate transaction; settle exact usage, and retain ambiguous reservations.

## 10. Mutable policy shadow

**Failure:** a generated `.codex`, Claude settings file, hook, runner, or monitor quietly
becomes the real authority. **Countermeasure:** generated files are clients and desired state
only. Install a hashed, immutable release outside agent-writable directories and activate it
only through the Human operator path.

## 11. Workflow privilege mixing

**Failure:** untrusted pull-request code runs in a job holding deployment credentials.
**Countermeasure:** manual-only dispatch, Human actor/main/SHA binding, pinned actions,
unprivileged artifact construction, and OIDC only in the protected deploy job.

## 12. Framework as security boundary

**Failure:** LangChain, A2A, or MCP routing is mistaken for authorization. **Countermeasure:**
keep the enforcement core typed and framework-free. Protocol adapters may terminate outside
the broker and can only translate into its closed operation schema.
