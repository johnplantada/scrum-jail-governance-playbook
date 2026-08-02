#!/usr/bin/env bash
# Operator client for clearing the supervisor-owned kill switch.
set -euo pipefail
client="${SJG_ADMIN_BIN:-/usr/local/libexec/scrum-jail/current/bin/sjg-admin}"
[ -x "$client" ] || { echo "resume: installed controller unavailable: $client" >&2; exit 69; }
[ -n "${SJG_SUPERVISOR_SOCKET:-}" ] || {
  echo "resume: SJG_SUPERVISOR_SOCKET is not set" >&2
  exit 77
}
exec "$client" runner resume
