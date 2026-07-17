#!/usr/bin/env bash
# org-worktree.sh — an ISOLATED git worktree for committing org-repo changes.
#
# The runtime checkout (where the runner reads org-chart.yaml and agents read their
# briefs/scripts) must NEVER be branch-switched or committed to by an agent — doing so
# collides with other agents and with infra work. So any org-repo change an agent wants to
# land goes through a throwaway worktree on its own branch, leaving the runtime checkout
# untouched.
#
#   # 1. get a fresh isolated checkout (off the latest origin/main), on your own branch:
#   wt=$(scripts/org-worktree.sh new design-s9-amend)     # prints the worktree PATH
#
#   # 2. make ALL your edits UNDER $wt (absolute paths), e.g. edit "$wt/DESIGN.md".
#
#   # 3. commit + push + open the PR from inside it:
#   ( cd "$wt" && git add -A && git commit -m "docs: amend DESIGN.md §9" \
#       && git push -u origin "$(git -C "$wt" branch --show-current)" \
#       && gh pr create --fill --base main )
#
#   # 4. clean up:
#   scripts/org-worktree.sh done "$wt"
#
# Never `git commit`/`git checkout -b` in the runtime dir itself — only in a worktree.
set -euo pipefail
cd "$(dirname "$0")/.."
REPO_ROOT="$(pwd)"
WT_BASE="${ORG_WORKTREE_DIR:-$REPO_ROOT/../.org-worktrees}"

usage() { echo "usage: org-worktree.sh <new <branch-suffix> | done <path> | list>" >&2; exit 2; }

cmd="${1:-}"; [ -n "$cmd" ] || usage
case "$cmd" in
  new)
    suffix="${2:-}"; [ -n "$suffix" ] || usage
    agent="${AGENT_NAME:-manual}"
    safe="$(printf '%s' "$suffix" | tr -c 'a-zA-Z0-9._-' '-' | sed 's/^-*//;s/-*$//')"
    branch="agent/${agent}/${safe}"
    path="$WT_BASE/${agent}-${safe}"
    mkdir -p "$WT_BASE"
    # Diagnostics to stderr; the PATH is the only thing on stdout (so callers can capture it).
    git fetch -q origin main >&2
    # Clear any stale worktree/branch at this slot so re-runs are clean.
    if [ -e "$path" ]; then git worktree remove --force "$path" >&2 2>&1 || true; fi
    git worktree prune >&2 2>&1 || true
    git branch -D "$branch" >/dev/null 2>&1 || true
    git worktree add -q -b "$branch" "$path" origin/main >&2
    echo "  ✓ isolated worktree on $branch (off origin/main) — edit under it, then commit/push/PR" >&2
    printf '%s\n' "$path"
    ;;
  done)
    path="${2:-}"; [ -n "$path" ] || usage
    git worktree remove --force "$path" 2>/dev/null || true
    git worktree prune
    echo "  ✓ removed worktree $path" >&2
    ;;
  list)
    git worktree list
    ;;
  *) usage ;;
esac
