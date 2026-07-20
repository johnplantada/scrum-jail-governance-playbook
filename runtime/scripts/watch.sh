#!/usr/bin/env bash
#
# watch.sh — one-terminal live view of the GitHub-native org.
#
# Merges every local log stream into a single colored feed so you don't juggle `tail` windows:
#
#   • RUNNER    — the poller's decisions (poll → route → wake; SHADOW / LIVE / HELD), runner.log
#   • <DEPT>    — each department cycle's narration (ceo / business / it / any chartered dept),
#                 picked up live as new agent-*.log files appear
#   • OFFLOAD   — every delegation to a cheaper/local model (offload.log)
#   • SUBAGENT  — every Task/Agent fan-out — which agent spawned what (subagents.log)
#   • <OPS>     — every other root *.log (any ops/infra stream a cron job or script
#                 grows later) so no stream is silently dropped
#
# The old chat + registrar feed retired with the chat stack (the GitHub-native migration; DESIGN.md is the standing spec). The org's
# conversation now lives in GitHub Issues/PRs — watch those on the Project board or with `gh`
# (e.g. `gh issue list`, `gh pr list`), not here.
#
# Usage:
#   scripts/watch.sh              # follow only new lines on every log (default)
#   scripts/watch.sh --history    # include each log's full existing history first
#   scripts/watch.sh --lines N    # start each log from its last N lines (default 0 = only new)
#   make logs                     # same thing
#
# Ctrl-C stops every tailer cleanly.

set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

# --- flags ------------------------------------------------------------------
# FROM is passed to `tail -n`: 0 = only new lines; +1 = whole file; N = last N lines.
FROM=0
while [ $# -gt 0 ]; do
  case "$1" in
    --history)  FROM="+1" ;;
    --lines)    FROM="${2:?--lines needs a number}"; shift ;;
    -h|--help)  sed -n '2,25p' "$0"; exit 0 ;;
    *) echo "unknown flag: $1 (try --help)" >&2; exit 2 ;;
  esac
  shift
done

# --- colors -----------------------------------------------------------------
if [ -t 1 ]; then
  R=$'\033[0m'
  C_RUN=$'\033[33m'      # runner     — yellow (the nervous system)
  C_CEO=$'\033[35m'      # ceo        — magenta
  C_BUS=$'\033[36m'      # business   — cyan
  C_IT=$'\033[32m'       # it         — green
  C_OTHER=$'\033[34m'    # other dept — blue
  C_OFF=$'\033[1;33m'    # offload to a cheaper/local model — bright yellow
  C_SUB=$'\033[1;35m'    # subagent (Task/Agent) fan-out     — bright magenta
  C_SYS=$'\033[90m'      # ops/infra logs                    — grey
else
  R=""; C_RUN=""; C_CEO=""; C_BUS=""; C_IT=""; C_OTHER=""; C_OFF=""; C_SUB=""; C_SYS=""
fi

color_for() { # $1 = department name
  case "$1" in
    ceo)      printf '%s' "$C_CEO" ;;
    business) printf '%s' "$C_BUS" ;;
    it)       printf '%s' "$C_IT" ;;
    *)        printf '%s' "$C_OTHER" ;;
  esac
}

PIDS=()
cleanup() { trap - INT TERM EXIT; [ ${#PIDS[@]} -gt 0 ] && kill "${PIDS[@]}" 2>/dev/null; }
trap cleanup INT TERM EXIT

# --- log tailers ------------------------------------------------------------
# One `tail -F` per log, prefixed with a fixed-width colored label. awk does the prefixing
# (line-buffered via fflush) because BSD/macOS sed has no -u. TAILED is a space-delimited set
# of already-tailed paths (bash 3.2 has no associative arrays).
TAILED=" "
start_tailer() { # $1 = path  $2 = label  $3 = color  $4 = tail-from (-n value)
  local f="$1" label="$2" col="$3" from="${4:-0}"
  tail -n "$from" -F "$f" 2>/dev/null \
    | awk -v p="${col}$(printf '%-9s' "$label")${R} " '{print p $0; fflush()}' &
  PIDS+=($!)
  TAILED="$TAILED$f "
}

# $1 = tail-from: "0" at startup (skip history on long-lived logs) or the requested FROM;
# "+1" on rescan (replay a freshly-chartered dept's first cycle from the top).
start_log_tailers() {
  local from="${1:-0}" f name
  # The nervous system: the runner's poll → route → wake decisions.
  case "$TAILED" in *" runner.log "*) ;; *) [ -f runner.log ] && start_tailer runner.log "RUNNER" "$C_RUN" "$from" ;; esac
  # Central delegation feeds.
  case "$TAILED" in *" offload.log "*) ;; *) [ -f offload.log ] && start_tailer offload.log "OFFLOAD" "$C_OFF" "$from" ;; esac
  case "$TAILED" in *" subagents.log "*) ;; *) [ -f subagents.log ] && start_tailer subagents.log "SUBAGENT" "$C_SUB" "$from" ;; esac
  # Per-department cycle narration.
  for f in agent-*.log; do
    [ -f "$f" ] || continue
    case "$TAILED" in *" $f "*) continue ;; esac
    name="${f#agent-}"; name="${name%.log}"
    start_tailer "$f" "$(printf '%s' "$name" | tr '[:lower:]' '[:upper:]')" "$(color_for "$name")" "$from"
  done
  # Catch-all: every remaining root-level *.log (any ops/infra stream) so the feed
  # never silently drops a stream. Already-tailed logs are skipped.
  for f in *.log; do
    [ -f "$f" ] || continue
    case "$TAILED" in *" $f "*) continue ;; esac
    name="${f%.log}"
    start_tailer "$f" "$(printf '%s' "$name" | tr '[:lower:]' '[:upper:]')" "$C_SYS" "$from"
  done
}

start_log_tailers "$FROM"
[ ${#PIDS[@]} -gt 0 ] || { echo "no logs to watch yet (nothing has run) — try again after a runner tick" >&2; exit 0; }
echo "${C_RUN}── watching $(( ${#PIDS[@]} )) log streams · Ctrl-C to stop ──${R}"

# Rescan for newly-created agent logs (e.g. a freshly chartered department), replaying each
# new one from the top so its first cycle isn't missed.
while :; do sleep 4; start_log_tailers "+1"; done
