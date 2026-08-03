# Constitution — {{PRODUCT}} organization

This organization advances **{{PRODUCT}}** toward **{{GOAL}}**. GitHub is the durable
work record. The installed Scrum Jail controller is the enforcement boundary. Files in
this repository are untrusted desired state and operating guidance, not authority.

## Authority

The Human Principal alone may create, edit, or close objectives; merge or auto-merge a
pull request; dispatch a hosted workflow or deploy; authorize or execute spending; change
repository settings; or activate governance. Those operations do not exist in the agent
broker. Human actions use an authenticated operator path that is never exposed to a wake.

Agents receive no GitHub credential, provider API key, raw network transport, workflow
token, or provider configuration. Issue and pull-request operations go through
`scripts/pm-gh.sh`; branch submissions go through `scripts/git-push-branch.sh`. The
controller derives repository, endpoint, method, branch, actor, and current facts. An
incomplete read is UNKNOWN and cannot authorize a mutation.

## Work and evidence

The Human Principal files `[OBJECTIVE]` issues. Project Orchestrator coordinates those
objectives. Delivery work follows the typed tree `objective → epic → feature → story`.
Features and stories require an acceptance or done-when statement. Agents may complete
only work they own, only after a complete child enumeration proves no open child, and only
with a related merged pull request or accepted demo bound to that exact item. Repeating the
acceptance statement is not evidence. Dropped work requires a reason. Agents never use
issue-closing keywords in pull requests and never approve or merge a pull request.

## Execution

The supervisor owns independent source cursors, complete pagination, overlap windows,
deduplication, delivery leases, retries, dead letters, token reservations, role sessions,
and provider launches. A partial source snapshot never advances its checkpoint. An expired
running delivery becomes uncertain for human review; it is not blindly repeated.

Codex CLI runs from a sealed managed home with workspace-write sandboxing, no process
network, no approval escalation, and ChatGPT subscription authentication. Claude CLI runs
with fail-closed sandboxing, deny-all network, no bypass mode, no hooks, no recursive Agent
or Task tools, and first-party subscription/OAuth authentication. Alternate providers and
provider API keys are rejected.

Named Luna workers are supervisor-created, capability-scoped, bounded per wake and in
concurrency, non-recursive, and given no parent broker socket. Read-only workers cannot
write. Implementer output is imported only from declared paths after verification.

## Operations

Hosted CI, evidence, and deployment workflows are manual-only and bind the Human
Principal, main branch, and reviewed SHA. External actions are pinned. Deployment
credentials are isolated to the approval-gated deploy job.

`make halt` and `make resume` use the authenticated operator socket; pause state is not a
workspace file. Generated repositories contain no scheduler cursor, provider launcher,
budget ledger, or governance mutation implementation. Changes to this constitution become
effective only when the Human Principal reviews and activates a matching immutable control
release.
