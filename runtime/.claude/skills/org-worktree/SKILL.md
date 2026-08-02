---
name: org-worktree
description: Prepare an org-repository change in an isolated local worktree and submit it through the controller.
---

# Isolated org worktree

The shared runtime checkout must not be branch-switched or committed by an agent.

```bash
wt=$(scripts/org-worktree.sh new <topic>)
# edit only beneath "$wt"
( cd "$wt" && git add -A && git commit -m "<message>" )
scripts/git-push-branch.sh "$wt"
scripts/pm-gh.sh pr-create --repo org --topic <topic> --title "<title>" --body "<body>"
scripts/org-worktree.sh done "$wt"
```

The worktree starts from the commissioned local `main`; it never fetches. Remote submission
and PR creation are separate typed broker requests. One concern per PR. A PR is a proposal,
not governance activation or merge authorization.
