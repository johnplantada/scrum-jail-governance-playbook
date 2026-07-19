---
name: org-worktree
description: Land an org-repo change (a doc, brief, roadmap, or approved constitution edit) via an isolated git worktree + PR. Use whenever you need to commit to the org repo — the runtime checkout is shared, read-only state and must NEVER be committed in.
---

# Org-repo changes go through an isolated worktree — never the runtime dir

The directory you wake in is **shared, read-only runtime state**: the runner and every
agent read `org-chart.yaml`, briefs, and scripts from it live. Running `git commit`,
`git checkout`, or `git branch` there collides with other agents (and the pre-commit hook
refuses it). The one permitted in-place edit is appending to `blockers.yaml` (the ledger
contract).

## The procedure

```bash
wt=$(scripts/org-worktree.sh new <short-branch>)   # fresh checkout off main; prints its path
# make ALL your edits UNDER $wt (e.g. edit "$wt/DESIGN.md" — never the runtime copy), then:
( cd "$wt" && git add -A && git commit -m "<msg>" \
    && git push -u origin "$(git -C "$wt" branch --show-current)" && gh pr create --fill --base main )
scripts/org-worktree.sh done "$wt"                 # always clean up, even if the PR failed
```

## Rules

- **Branch names:** short and purposeful (e.g. `safe-mandates`, `pi-12-brief`).
- **One concern per PR** — a reviewable unit, not a grab-bag of unrelated edits.
- **Constitution edits (`DESIGN.md`) merge only after the Board's charter approval** on the
  matching PROPOSAL — the worktree PR is the vehicle, not the approval.
- **Product code is a different repo** (`$PRODUCT_REPO`) with its own branch/PR flow —
  never landed through the org worktree.
- CI runs the constitution linter on your PR; if it flags a hardcoded cadence number, a
  non-canonical stage, or an unknown CLI flag, fix the doc to reference the source of
  truth instead of restating its value.
