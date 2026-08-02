# Hosted workflow budget policy

Hosted workflows are Human Principal-dispatched and manual-only. No workflow may trigger on
push, pull request, issue, comment, schedule, repository dispatch, workflow run, or an
untrusted reusable caller.

Before commissioning a workflow, the Human Principal records its expected runs, jobs per
run, runner class, artifact/storage use, and monthly ceiling. Provider pricing and included
allowances must be checked against current first-party documentation at commissioning time;
this repository intentionally carries no hard-coded price or quota claims.

Workflow rules:

- pin every external action to a full commit SHA;
- require the configured Human actor and `refs/heads/main`;
- bind requested PR numbers to their live head SHA before evidence work;
- keep build/review jobs unprivileged;
- put OIDC and deployment permission only in the protected deploy job;
- never run untrusted artifact code after credentials become available;
- treat local hooks as convenience checks, not an authorization or branch-protection gate.

Only the Human Principal may dispatch, deploy, or authorize associated spend. Exhausted
hosted-runner allowance is an operational hold, never permission to bypass verification.
