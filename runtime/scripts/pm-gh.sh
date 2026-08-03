#!/usr/bin/env bash
# Unprivileged client for every agent issue/Project mutation. The installed controller
# derives role, repository, ownership, allowed transitions, and evidence from the wake;
# this workspace script has no GitHub credential and contains no authority logic.
set -euo pipefail

client="${SJGCTL_BIN:-/usr/local/libexec/scrum-jail/current/bin/sjgctl}"
[ -x "$client" ] || {
  echo "pm-gh: installed Scrum Jail control client not found: $client" >&2
  exit 69
}
[ -n "${SJG_WAKE_SOCKET:-}" ] || {
  echo "pm-gh: SJG_WAKE_SOCKET is not set; issue mutations require a governed wake" >&2
  exit 77
}

exec "$client" pm "$@"
