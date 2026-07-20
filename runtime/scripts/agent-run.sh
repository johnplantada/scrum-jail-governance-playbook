#!/usr/bin/env bash
# Run ONE org agent as a headless Claude Code cycle: load its definition, act via
# gh / scripts/pm-gh.sh / the ledgers, then exit. Invoked by scripts/runner.py when a
# GitHub event routes to this department (wake-rules.yaml), or by hand for a
# supervised test:  WAKE_NOTE="why" ./scripts/agent-run.sh business
set -euo pipefail
cd "$(dirname "$0")/.."

name="${1:?usage: agent-run.sh <ceo|business|it|...>}"
def="agents/${name}.md"
[ -f "$def" ] || { echo "no agent definition: $def" >&2; exit 1; }

# Respect the kill switch.
if [ -f .halt ]; then echo "halted — skipping $name wake"; exit 0; fi

[ -f .env ] && { set -a; . ./.env; set +a; }

# Python helpers run on the repo venv — deps (pyyaml, claude-agent-sdk) are pinned in
# scripts/requirements.txt. Bare `python3` under launchd resolves to whatever interpreter
# that PATH finds, which is how blocker-ledger injection and per-agent model resolution
# silently died once (ModuleNotFoundError: yaml, swallowed by the 2>/dev/null fallbacks).
py="$(pwd)/.venv/bin/python"; [ -x "$py" ] || py=python3
# Where the local product-repo clone lives (IT works/PRs there; never prod-merges).
# Set in .env; no fallback — a wrong default silently pointed agents at another repo once.
export PRODUCT_REPO="${PRODUCT_REPO:-}"
log="agent-${name}.log"

# --- Single-flight per agent ---------------------------------------------------------------
# Two agent-run.sh processes for the SAME agent must never run concurrently: the runner and a
# manual shell can both dispatch, and a runner restart can overlap an in-flight child. A
# concurrent cycle duplicates work and races the shared product-repo branch / org Project (this
# really happened — two IT wakes built the identical PR at once). mkdir is atomic on the FS, so
# it is a portable lock (macOS has no flock binary). A contended DIRECT wake exits 75
# (EX_TEMPFAIL — "retry me"); the runner re-queues a nonzero dispatch's events for redelivery
# (bounded retries, then state/dead-letter.jsonl — see runner.requeue_failed).
lockdir=".locks/agent-${name}.lock"
lock_ttl=1800   # a wake should never legitimately run this long; past this the lock is stale
                # ONLY if its pid is no longer a live runner (see lock_is_stale) — bounds the
                # wedge from a SIGKILL + a recycled PID without stealing from a slow live cycle.
mkdir -p .locks

# Exit a contended wake: re-triggerable (75) for a direct wake, silent no-op (0) otherwise.
skip_contended() { # $1 = log reason
  echo "=== $(date '+%F %T') :: $name wake skipped — $1 ===" >> "$log"
  [ "${WAKE_REASON:-}" = direct ] && exit 75
  exit 0
}

# acquire_lock: atomic; on success sets the EXIT trap and records our pid. Returns non-zero if held.
acquire_lock() {
  mkdir "$lockdir" 2>/dev/null || return 1
  trap 'rm -rf "$lockdir"' EXIT
  echo "$$" > "$lockdir/pid" 2>/dev/null || true
  return 0
}

# lock_is_stale: holder gone (empty pid = SIGKILL before the pid write, or a dead pid), or the
# lock is older than lock_ttl AND the pid is not actually a runner anymore. Age alone used to be
# enough ("regardless of pid liveness") — but that let a long-but-ALIVE cycle have its lock
# stolen and a second concurrent cycle of the same agent started (a CEO cycle once ran 2h08m,
# well past the ttl, still holding the lock). The command check keeps what the age rule was
# really defending against — a SIGKILLed runner whose PID got recycled by an unrelated process
# would otherwise look alive forever — without ever stealing from a live cycle. The recorded
# pid is agent-run.sh's own $$ (the cycle runs as its child), so match either script name.
lock_is_stale() {
  local h mtime
  h="$(cat "$lockdir/pid" 2>/dev/null || true)"
  [ -z "$h" ] && return 0
  kill -0 "$h" 2>/dev/null || return 0
  mtime="$(stat -f %m "$lockdir" 2>/dev/null || echo '')"
  if [ -n "$mtime" ] && [ "$(( $(date +%s) - mtime ))" -gt "$lock_ttl" ]; then
    ps -p "$h" -o command= 2>/dev/null | grep -qE 'agent[-_](run\.sh|cycle)' || return 0
  fi
  return 1
}

if ! acquire_lock; then
  if lock_is_stale; then
    # Reclaim ATOMICALLY: rename the stale dir aside (rename is atomic per-name, so two racers
    # can't both win) then re-acquire. A loser's mv (source already gone) falls through to skip.
    stale="${lockdir}.stale.$$"
    if mv "$lockdir" "$stale" 2>/dev/null; then
      rm -rf "$stale" 2>/dev/null || true
      if acquire_lock; then
        echo "=== $(date '+%F %T') :: $name reclaimed a stale lock ===" >> "$log"
      else
        skip_contended "lost the stale-lock reclaim race"
      fi
    else
      skip_contended "already running"
    fi
  else
    skip_contended "already running (pid $(cat "$lockdir/pid" 2>/dev/null || echo '?'))"
  fi
fi

# Per-agent model (token efficiency): CEO/Business/IT=sonnet, sub-teams=haiku.
# Policy lives in org-chart.yaml (`model:` per node); resolver applies the defaults. The
# agent BRAIN must be a Claude tier.
model="$("$py" scripts/agent_model.py "$name" 2>>"$log" || echo sonnet)"
case "$model" in opus|sonnet|haiku) ;; *) model="sonnet" ;; esac

# Why we woke: the runner passes WAKE_REASON=direct and WAKE_NOTE="github: <event>"; a bare
# manual invocation gets reason=manual. There are no scheduled wake floors and no broadcast
# fan-out — the runner only fires on a routed GitHub event, so every wake has a cause
# (DESIGN.md §3: no event, no wake, no spend).
wake_reason="${WAKE_REASON:-manual}"
wake_note="${WAKE_NOTE:-}"
# Wake-correlation id: every artifact of THIS wake — spend rows, offload/subagent log
# lines — carries the same id, so a grep can join "what did this wake do" to "what did
# it cost" across every ledger.
WAKE_ID="w-${name}-$(date +%Y%m%d%H%M%S)-$$"
export AGENT_NAME="$name" AGENT_BRAIN="$model" WAKE_REASON="$wake_reason" WAKE_ID

# --- Envelope budget gate: enforce org-chart daily_token_budget (DESIGN invariant 3) --------
# Sums today's in+out tokens for this agent from state/spend.jsonl (cycles + offloads) against
# its envelope. Over budget → non-direct wakes skip (a per-department brownout, logged and
# measurable); DIRECT wakes always run — a spent budget must never block a runner-routed
# GitHub event (the Chairman's issue, a deploy failure). Fail-open: a broken meter prints ok
# and the wake runs. The org-wide $/day breaker lives in the runner (SPEND_BREAKER_DAILY_USD).
budget_verdict="$("$py" scripts/budget_gate.py "$name" 2>>"$log" || echo "ok")"
case "$budget_verdict" in
  over*)
    if [ "$wake_reason" = direct ]; then
      echo "=== $(date '+%F %T') :: $name over daily token budget ($budget_verdict) — direct wake runs anyway ===" >> "$log"
    else
      echo "=== $(date '+%F %T') :: $name BUDGET BROWNOUT ($budget_verdict) — $wake_reason wake skipped until the ledger rolls over ===" >> "$log"
      exit 0
    fi
    ;;
esac
# ------------------------------------------------------------------------------------------

# --- Review cadence (DESIGN invariant 4 plumbing) -------------------------------------------
# Tick this agent's wake counter (scripts/cycle-tick.sh, N = org-chart global.review_interval).
# Deterministic and in the wake path — the same lesson as the warden brief: the counter ticks
# in code, not by hoping a brain remembers to run it (33 wakes passed without .cycles/ ever
# being created). Only the CEO's mandate consumes the boundary today (agents/ceo.md "Periodic
# reviews"); other agents' counters tick with no consumer yet. At a CEO boundary, run the
# output predicate + PI counters read-only and inject the results, so ceremony stays
# output-gated (playbook/safe.md: while shipped=no the review closes with one held line) and the cycle
# starts at judgment, not orientation. Fail-open to "tick": a broken counter must never turn
# every wake into a review.
cadence="$(scripts/cycle-tick.sh "$name" 2>>"$log" || echo tick)"
cadence_brief=""
if [ "$cadence" = update ] && [ "$name" = ceo ]; then
  cadence_brief="$( { scripts/last-ship.sh 2>/dev/null | grep '^shipped=' || echo 'shipped=no'; \
                      scripts/pi-tick.sh 2>/dev/null || true; } )"
  echo "=== $(date '+%F %T') :: $name review-interval boundary (cycle-tick.sh → update) ===" >> "$log"
fi
# ------------------------------------------------------------------------------------------

# The agentic wake runs through the Claude Agent SDK (scripts/agent_cycle.py), not a bare
# `claude -p`. The tool allowlist (Bash + gh/git/pm-gh.sh, Edit/Write, WebSearch/WebFetch,
# Skill, Agent/Task) and the headless permission model live in that runner. Catastrophic
# commands stay denied via .claude/settings.json (loaded by the SDK, enforced before the
# permission mode). The runner authenticates on the logged-in Claude subscription — no API key.
sdk_python="$(pwd)/.venv/bin/python"   # the SDK cycle REQUIRES the venv (claude-agent-sdk)
# Ensure the SDK can find the `claude`/`node` runtime it drives, even under launchd/the runner
# (which don't inherit an interactive PATH).
export PATH="/opt/homebrew/bin:$PATH"

# Snapshot the delegation logs so we can report THIS cycle's delegation in the agent log
# itself (not just the separate offload/subagents logs) — visible where you look.
# Ensure the delegation logs exist: on a fresh org no offload/subagent has run yet, so a
# bare `< offload.log` redirect fails and leaks a "No such file" line into the agent log
# (2>/dev/null can't suppress a redirect that fails before it is applied).
touch offload.log subagents.log
offl_before=$(wc -l < offload.log 2>/dev/null || echo 0)
sub_before=$(wc -l < subagents.log 2>/dev/null || echo 0)

policy="agents/_policy.md"
# Surface the open human-blocker ledger so the agent reasons against the real queue instead
# of re-deriving it (and re-posting it) every cycle. Per _policy.md it must NOT restate these.
blockers_open="$("$py" scripts/blockers.py open 2>>"$log" || true)"
# Warden: run the deterministic engine BEFORE the cycle and inject its output, so the
# wake starts at judgment, not orientation (commissioning lesson: a haiku brain told to
# run its own script hit a bare-python error that read like "wrong repo" and wandered
# the filesystem for ~10 turns). Errors are injected too — the brain reports them
# instead of re-deriving state by hand. Sync is idempotent; a pre-wake run is safe.
warden_brief=""
if [ "$name" = warden ]; then
  warden_brief="$(PYTHONPATH=scripts "$py" scripts/warden.py sync 2>&1 || true)"
fi
prompt="You are the '$name' agent for the Scrum Jail autonomous org. Your standing
instructions are below. This is a single wake — do ONE cycle (read the GitHub state the
wake note points you at, act within your mandate, record your results on the issue/PR/
ledger where they belong), then stop. Never spend money or deploy to prod without the
Chairman's authorization; propose and wait.
$( [ -n "$wake_note" ] && printf '\n--- why you woke (from the runner) ---\n%s\n' "$wake_note" )
$( [ -f "$policy" ] && { printf '\n--- shared response policy (applies to ALL agents) ---\n'; cat "$policy"; } )
$( [ -n "$blockers_open" ] && printf '\n--- open human-blockers (blockers.yaml — do NOT re-post these as status) ---\n%s\n' "$blockers_open" )
$( [ -n "$warden_brief" ] && printf '\n--- your engine ALREADY RAN this wake (scripts/warden.py sync output — do NOT run it again, do NOT re-verify it) ---\n%s\n' "$warden_brief" )
$( [ -n "$cadence_brief" ] && printf '\n--- review-interval boundary reached (cycle-tick.sh ALREADY RAN this wake — do NOT run it again) ---\nThis wake crosses the review interval (org-chart.yaml global.review_interval): run the periodic review per your mandate (the safe-cadence skill). Ceremony is output-gated (DESIGN.md invariant 4) — the predicate below already ran for you; while shipped=no the review closes with the one held line and PI Planning stays suppressed.\n%s\n' "$cadence_brief" )

--- agents/${name}.md ---
$(cat "$def")"

{ echo "=== $(date '+%F %T') :: $name wake · brain=$model · wake=$wake_reason · id=$WAKE_ID ==="; } >> "$log"

# Run the agentic cycle through the SDK runner. It streams the agent's text to stdout
# (appended to the agent log) and writes each Agent/Task subagent fan-out to subagents.log
# in-process — no jq, no fragile event scraping. The prompt arrives on stdin.
# `set -o pipefail` (above) makes a failed runner propagate through the printf pipe.
# Preflight: confirm the spend hook is importable BEFORE the cycle, so a broken meter is visible
# on this wake instead of silently dropping its cost. Non-fatal — the wake still runs.
"$sdk_python" -c 'import sys; sys.path.insert(0, "scripts"); import spend_log' 2>/dev/null \
  || echo "=== $(date '+%F %T') :: $name WARN spend_log not importable — this wake will be UNMETERED ===" >> "$log"

cycle_rc=0
printf '%s' "$prompt" | "$sdk_python" scripts/agent_cycle.py "$name" "$model" >>"$log" 2>>"$log" || cycle_rc=$?

# Make an aborted cycle loud AND propagate it: this script exits with cycle_rc (bottom), and
# the runner re-queues a failed wake's events for redelivery — bounded retries through the
# deferred-event spool, then state/dead-letter.jsonl — recording the attempt in
# state/spend.jsonl + state/wake-filter.jsonl (runner.requeue_failed).
if [ "$cycle_rc" -ne 0 ]; then
  echo "=== $(date '+%F %T') :: $name cycle ABORTED (rc=$cycle_rc) — exiting nonzero so the runner re-queues this wake's events ===" >> "$log"
  # A review boundary consumed by a crashed cycle would silently skip a whole interval:
  # re-arm the counter so the NEXT wake crosses the boundary again. Same org-chart read as
  # cycle-tick.sh (pi-tick.sh set the grep precedent).
  if [ "${cadence:-tick}" = update ]; then
    n="$(grep -m1 'review_interval:' org-chart.yaml | awk '{print $2}' | tr -d '[:space:]' || true)"
    case "$n" in ''|*[!0-9]*) n=5 ;; esac
    if [ "$n" -gt 1 ]; then echo $((n - 1)) > ".cycles/$name"; fi
  fi
fi

{
  # Delegation summary: this caller's offloads (by tier) + subagents added during the cycle.
  offl_after=$(wc -l < offload.log 2>/dev/null || echo 0)
  mine=$(sed -n "$((offl_before + 1)),${offl_after}p" offload.log 2>/dev/null | grep "caller=${name}(" || true)
  n_off=$(printf '%s' "$mine" | grep -c "caller=" || true)
  sub_after=$(wc -l < subagents.log 2>/dev/null || echo 0)
  n_sub=$(sed -n "$((sub_before + 1)),${sub_after}p" subagents.log 2>/dev/null | grep -c "caller=${name} " || true)
  parts=""
  if [ "${n_off:-0}" -gt 0 ]; then
    tiers=$(printf '%s\n' "$mine" | grep -oE "tier=[a-z]+" | sed 's/tier=//' | sort | uniq -c | awk '{printf "%s×%s ", $1, $2}')
    parts="${n_off} offload(s) [${tiers}]"
  fi
  if [ "${n_sub:-0}" -gt 0 ]; then
    [ -n "$parts" ] && parts="${parts}+ "
    parts="${parts}${n_sub} subagent(s)"
  fi
  if [ -n "$parts" ]; then
    echo "=== $(date '+%F %T') :: $name delegation: ${parts} ==="
  else
    echo "=== $(date '+%F %T') :: $name delegation: none this cycle (did work inline) ==="
  fi
  echo "=== $(date '+%F %T') :: $name done ==="
} >> "$log" 2>&1

# The exit code IS the delivery receipt: nonzero tells the runner this wake failed and its
# events need re-queueing (a manual caller sees the same signal).
exit "$cycle_rc"
