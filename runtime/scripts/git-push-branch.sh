#!/usr/bin/env bash
# Unprivileged branch-submission client. No network Git command appears here. The
# installed controller reconstructs the commit against its own base and creates only
# agent/<server-bound-role>/<topic> with a non-force update.
set -euo pipefail

[ "$#" -le 1 ] || { echo "usage: git-push-branch.sh [repo-or-worktree|--product]" >&2; exit 2; }
repo="${1:-.}"
repository=org
if [ "$repo" = "--product" ]; then
  repo=.
  repository=product
fi
root="$(git -C "$repo" rev-parse --show-toplevel 2>/dev/null)" || {
  echo "git-push-branch: not a git checkout: $repo" >&2
  exit 1
}
root="$(cd "$root" && pwd -P)"
branch="$(git -C "$root" branch --show-current)"
case "$branch" in
  agent/*/*)
    branch_role="${branch#agent/}"
    branch_role="${branch_role%%/*}"
    topic="${branch#agent/*/}"
    [ -n "${AGENT_NAME:-}" ] && [ "$branch_role" = "$AGENT_NAME" ] || {
      echo "git-push-branch: local branch role does not match the governed wake" >&2
      exit 77
    }
    ;;
  *)
    echo "git-push-branch: refusing branch '$branch' — agent submissions require agent/<role>/<topic>" >&2
    exit 1
    ;;
esac

script_root="$(cd "$(dirname "$0")/.." && pwd -P)"
if [ "$repository" = org ]; then
  org_common="$(git -C "$script_root" rev-parse --git-common-dir 2>/dev/null || true)"
  root_common="$(git -C "$root" rev-parse --git-common-dir 2>/dev/null || true)"
  [ -n "$org_common" ] && org_common="$(cd "$script_root" && cd "$org_common" && pwd -P)"
  [ -n "$root_common" ] && root_common="$(cd "$root" && cd "$root_common" && pwd -P)"
  [ -n "$org_common" ] && [ "$org_common" = "$root_common" ] || {
    echo "git-push-branch: refusing checkout outside the org/product scope: $root" >&2
    exit 1
  }
fi

base=main
upstream="$(git -C "$root" rev-parse --abbrev-ref --symbolic-full-name '@{upstream}' 2>/dev/null || true)"
if [ -n "$upstream" ]; then
  base="$upstream"
elif ! git -C "$root" rev-parse --verify main >/dev/null 2>&1; then
  base=HEAD~1
fi
client="${SJGCTL_BIN:-/usr/local/libexec/scrum-jail/current/bin/sjgctl}"
[ -x "$client" ] || { echo "git-push-branch: installed control client not found: $client" >&2; exit 69; }
[ -n "${SJG_WAKE_SOCKET:-}" ] || { echo "git-push-branch: SJG_WAKE_SOCKET is not set" >&2; exit 77; }

exec "$client" repo submit --repository "$repository" --topic "$topic" --workspace "$root" --base "$base"
