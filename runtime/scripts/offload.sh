#!/usr/bin/env bash
# offload.sh — run a cheap, self-contained subtask on a smaller/cheaper Claude model and
# print the result to stdout. This is how an agent (CEO/Business/IT or any sub-team) keeps
# its OWN reasoning on its assigned tier but pushes high-volume, low-stakes text work —
# summarize a page, classify feedback, extract fields, draft boilerplate — onto a cheaper
# brain. The caller stays in control; offload is a tool it calls, not a handoff.
#
#   ./scripts/offload.sh <tier> "<prompt>"        # prompt as an argument
#   ./scripts/offload.sh <tier> - <<'EOF'         # prompt on stdin (heredoc, multi-line)
#   ... prompt ...
#   EOF
#   echo "text" | ./scripts/offload.sh haiku -    # or pipe it
#
# Tiers:
#   haiku | sonnet | opus   → Claude via `claude -p --model` (no tools — pure text task)
#
# Pick the CHEAPEST tier that does the job: haiku for bulk/cheap/light-reasoning text,
# sonnet/opus only when the subtask genuinely needs it. The whole point is token
# efficiency — don't offload to a tier above the one you're already running on.
#
# (A `local` Ollama tier used to live here; it was removed — local 7-8B output needed
# Sonnet rework often enough that haiku was cheaper overall. Ollama is reserved for
# embeddings, not generation.)
set -euo pipefail
cd "$(dirname "$0")/.."

tier="${1:?usage: offload.sh <haiku|sonnet|opus> <prompt | ->}"
shift

# Gather the prompt: explicit "-" (or no further args) reads stdin; else join the args.
if [ "$#" -eq 0 ] || [ "${1:-}" = "-" ]; then
  prompt="$(cat)"
else
  prompt="$*"
fi
[ -n "${prompt//[[:space:]]/}" ] || { echo "offload: empty prompt" >&2; exit 2; }

# --- Route DOWN only ----------------------------------------------------------------------
# Offload exists to push work to a CHEAPER brain than the caller's own. Routing UP (e.g. a
# Sonnet agent offloading to Opus) is anti-economical — it costs MORE than just doing the work
# on the caller's own brain — and was the single largest offload waste in the cost audit (7/7
# opus offloads were up-routes). Refuse an up-route unless the caller deliberately justifies it
# with OFFLOAD_ESCALATE="<why>", which is then recorded in offload.log so escalations are auditable.
rank() { case "$1" in haiku) echo 0 ;; sonnet) echo 1 ;; opus) echo 2 ;; *) echo 1 ;; esac; }  # unknown→sonnet
caller_brain="${AGENT_BRAIN:-sonnet}"; [ "$caller_brain" = "?" ] && caller_brain=sonnet
if [ "$(rank "$tier")" -gt "$(rank "$caller_brain")" ] && [ -z "${OFFLOAD_ESCALATE:-}" ]; then
  echo "offload: REFUSING route-up ${caller_brain}→${tier} — offload to a cheaper tier, or set OFFLOAD_ESCALATE=\"<why ${tier} is genuinely needed>\" to escalate deliberately." >&2
  exit 3
fi

# Record every delegation centrally so we can verify the token-efficiency story: who
# offloaded, to which tier/model, and how much text. One grep-able line in offload.log;
# also echoed to stderr so the calling agent sees it.
log_offload() { # $1 = resolved model id (e.g. haiku)
  local caller="${AGENT_NAME:-?}" brain="${AGENT_BRAIN:-?}" chars="${#prompt}"
  local preview; preview="$(printf '%s' "$prompt" | tr '\n' ' ' | cut -c1-80)"
  # Sanitize the (agent-supplied) escalate reason so a newline or quote can't forge/break an
  # offload.log audit line — same single-line treatment as the prompt preview below.
  local esc=""
  [ -n "${OFFLOAD_ESCALATE:-}" ] && esc=" escalate=\"$(printf '%s' "$OFFLOAD_ESCALATE" | tr '\n\r"' '   ' | cut -c1-200)\""
  local line; line="$(date '+%F %T')  caller=${caller}(brain=${brain}) wake=${WAKE_ID:-?} tier=${tier} model=${1} chars=${chars}${esc} :: ${preview}"
  printf '%s\n' "$line" >> offload.log 2>/dev/null || true
  printf 'offload» %s\n' "$line" >&2
}

case "$tier" in
  haiku|sonnet|opus)
    # Pure text task: no tools, no agentic loop — just one model turn. Far cheaper than a full
    # agent wake, and it can't touch the repo or the bus. We run it as JSON so we can capture its
    # cost + token stats; spend_offload.py prints the text result to stdout (the contract callers
    # depend on) and appends a spend row. The shim always exits 0 — an in-band `is_error` result is
    # still usable text and must NOT abort a caller; only a genuine claude *process* failure exits
    # nonzero, which `set -o pipefail` propagates (callers that must survive it guard with `|| true`).
    log_offload "$tier"
    # Resolve the tier to the chart-pinned model id at the invocation boundary (TIER in
    # the spend row stays the tier). Venv python: model_id.py wants pyyaml under launchd
    # — the same trap that once crash-looped blocker-watch (see agent-run.sh's resolver).
    py="$(pwd)/.venv/bin/python"; [ -x "$py" ] || py=python3
    model="$("$py" scripts/model_id.py "$tier" 2>/dev/null || echo "$tier")"
    claude -p "$prompt" --model "$model" --output-format json \
      | AGENT="${AGENT_NAME:-?}" TIER="$tier" python3 scripts/spend_offload.py
    ;;
  *)
    echo "offload: unknown tier '$tier' (use haiku|sonnet|opus)" >&2
    exit 2
    ;;
esac
