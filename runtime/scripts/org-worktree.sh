#!/usr/bin/env bash
# org-worktree.sh — an ISOLATED git worktree for committing org-repo changes.
#
# The runtime checkout (where the runner reads org-chart.yaml and agents read their
# briefs/scripts) must NEVER be branch-switched or committed to by an agent — doing so
# collides with other agents and with infra work. So any org-repo change an agent wants to
# land goes through a throwaway worktree on its own branch, leaving the runtime checkout
# untouched.
#
#   # 1. get an isolated checkout from the locally commissioned main ref:
#   wt=$(scripts/org-worktree.sh new design-s9-amend)     # prints the worktree PATH
#
#   # 2. make ALL your edits UNDER $wt (absolute paths), e.g. edit "$wt/DESIGN.md".
#
#   # 3. commit, submit through the controller, and request the PR through pm-gh.sh:
#   ( cd "$wt" && git add -A && git commit -m "docs: amend DESIGN.md §9" \
#       && scripts/git-push-branch.sh "$wt" )
#   scripts/pm-gh.sh pr-create --repo org --topic <topic> --title "Describe the change"
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
    agent="${AGENT_NAME:-}"
    case "$agent" in
      project-orchestrator|commercial-planner|software-implementer|governance-reviewer|policy-reviewer) ;;
      *) echo "org-worktree: AGENT_NAME must be a canonical executing role" >&2; exit 2 ;;
    esac
    safe="$(printf '%s' "$suffix" | tr -c 'a-zA-Z0-9._-' '-' | sed 's/^-*//;s/-*$//')"
    branch="agent/${agent}/${safe}"
    path="$WT_BASE/${agent}-${safe}"
    mkdir -p "$WT_BASE"
    [ ! -e "$path" ] || { echo "org-worktree: path already exists: $path" >&2; exit 73; }
    ! git show-ref --verify --quiet "refs/heads/$branch" || {
      echo "org-worktree: branch already exists: $branch" >&2; exit 73;
    }
    git show-ref --verify --quiet refs/heads/main || {
      echo "org-worktree: commissioned local main ref is missing" >&2; exit 69;
    }
    git worktree add -q -b "$branch" "$path" refs/heads/main >&2
    echo "  ✓ isolated worktree on $branch (off local main) — edit under it, then submit through the controller" >&2
    printf '%s\n' "$path"
    ;;
  done)
    path="${2:-}"; [ -n "$path" ] || usage
    base="$(cd "$WT_BASE" 2>/dev/null && pwd -P)" || { echo "org-worktree: worktree base missing" >&2; exit 69; }
    resolved="$(cd "$(dirname "$path")" 2>/dev/null && printf '%s/%s\n' "$(pwd -P)" "$(basename "$path")")" || exit 2
    case "$resolved" in "$base"/*) ;; *) echo "org-worktree: refusing path outside $base" >&2; exit 2;; esac
    git worktree remove "$resolved"
    git worktree prune
    echo "  ✓ removed worktree $resolved" >&2
    ;;
  list)
    git worktree list
    ;;
  *) usage ;;
esac
