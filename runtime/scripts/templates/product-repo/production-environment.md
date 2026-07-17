# TEMPLATE — the deploy gate: `workflow_dispatch`-only workflows (GITHUB-NATIVE-PLAN.md)

> Filename kept for link stability. This template originally described a `production`
> environment with the Chairman as required reviewer — that gate was **superseded 2026-07**
> (ledgered `github-production-environment`): required reviewers on environments are a
> Team/Enterprise feature GitHub Free does not enforce, confirmed live in product PRs
> #96/#97. The gate that actually holds is below.

Deploy authority is the **trigger itself**: every workflow that touches prod runs on
manual `workflow_dispatch` only. A merge to `main` builds and verifies but deploys
nothing; the Chairman's dispatch — platform-enforced, SHA-visible, permanently audited in
the Actions run history — *is* the authorization. This is the only deploy gate in the org
(DESIGN.md invariant 1).

## 1. IT: gate every deploy job on the dispatch trigger

In `.github/workflows/deploy.yml` (and `infra.yml`, and anything else that touches prod):

```yaml
on:
  push:
    branches: [main]      # verify only — build + test, no AWS credentials
  workflow_dispatch:      # ← the gate: deploy steps run ONLY from a manual dispatch

jobs:
  verify:
    runs-on: ubuntu-latest
    steps: ...            # build, test — safe on every push
  deploy:
    if: github.event_name == 'workflow_dispatch'
    runs-on: ubuntu-latest
    steps: ...            # the steps that actually mutate prod
```

No repo Settings are involved — the gate is code, reviewable and branch-protected like
everything else. (Product PR #109 / org#217 is the reference implementation.)

## 2. IT: surface the demo evidence where the Chairman will see it

The Chairman dispatches from the workflow's Actions page — put the `[DEMO]` evidence in
the PR thread and the run summary so the dispatch decision is informed without a chat
lookup. Early in the deploy job:

```yaml
      - name: Demo evidence for the dispatcher
        run: |
          {
            echo "## Deploy ask"
            echo "- sha: \`${{ github.sha }}\`"
            echo "- demo evidence: $(gh run list --workflow=demo-evidence.yml \
                  --commit ${{ github.sha }} --status=success -L 1 \
                  --json url --jq '.[0].url // "none for this sha"')"
          } >> "$GITHUB_STEP_SUMMARY"
        env:
          GH_TOKEN: ${{ github.token }}
```

## Chairman: how to deploy

Actions → the deploy workflow → **Run workflow** → branch `main` → Run. The dispatch is
the approval; the run log is the audit trail.

## Rollback of the gate

Add a `push`-triggered condition back to the deploy job. Don't — the dispatch-only
trigger is the constitution's invariant-1 enforcement; loosening it needs a
`decisions.yaml` decision, not a workflow edit.
