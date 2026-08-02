# Production workflow requirements

Production deployment is Human Principal-only. A commissioned deploy workflow must:

- expose only `workflow_dispatch` (no push, pull-request, schedule, repository-dispatch,
  workflow-run, or reusable untrusted caller trigger);
- require `github.actor == vars.HUMAN_PRINCIPAL_LOGIN` and `refs/heads/main`;
- bind the requested deployment to an immutable reviewed SHA;
- use a protected `production` environment with the Human Principal as required reviewer;
- place OIDC/deployment permissions only on the deploy job;
- use an environment-specific, least-privilege role and no long-lived cloud keys;
- pin every external action to a full commit SHA;
- never execute untrusted artifact code after deployment credentials are available;
- record SHA, environment, workflow run, actor, and rollback reference in the run summary.

Workflow editing is a protected repository change and cannot be submitted through the
agent branch broker. The Human Principal commissions it separately after review.
