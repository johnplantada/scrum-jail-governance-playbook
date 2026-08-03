# Authorization gate

The Human Principal exclusively controls objectives, pull-request merges, workflow
dispatches and deployments, spending authorization and execution, repository settings,
and governance activation. These capabilities are absent from the agent protocol.

## Agent path

An agent can request only a named operation through its per-wake Unix socket. The installed
controller binds the socket to a server-side role, policy digest, repositories, budget, and
expiry. It constructs GitHub endpoints and methods itself and re-reads all state needed for
authorization. The request cannot provide an actor, token, URL, HTTP method, command,
repository slug, arbitrary branch, provider, model, tool set, or worker socket.

Issue and pull-request requests use `scripts/pm-gh.sh`. Repository submissions use
`scripts/git-push-branch.sh` and can update only `agent/<role>/<topic>` through GitHub's Git
database API. Direct `gh`, remote Git, workflow dispatch, merge, and deployment operations
are unavailable inside the wake.

## Human path

The Human Principal uses an authenticated operator identity outside every agent wake:

- files, edits, and closes objectives;
- reviews and merges pull requests;
- manually dispatches SHA-bound workflows and deployments;
- authorizes and personally executes spending;
- commissions repository settings and installs or activates governance releases.

CODEOWNERS, branch protection, manual-only workflows, protected environments, and OIDC are
defense in depth. They do not replace capability separation. No Human credential is copied
into a generated repository or provider environment.

## Failure rules

- Incomplete or failed reads are UNKNOWN and authorize no mutation.
- A mutation whose response is lost becomes `verify_pending`/`indeterminate`; it is not
  blindly replayed.
- A declined or absent Human action grants nothing.
- A green test or review is evidence, never merge, deploy, spend, or governance authority.
