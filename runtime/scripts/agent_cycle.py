#!/usr/bin/env python3
"""Run ONE org agent cycle via the Claude Agent SDK — the engine behind a headless
wake. Replaces the old `claude -p ... --output-format stream-json | jq` pipeline.

Invoked by scripts/agent-run.sh (which keeps the wake-reason backpressure, pre-gates,
prompt assembly, and watermark rollback). This script owns only the agentic cycle:

  argv:  <name> <model>           e.g.  agent_cycle.py it sonnet
  stdin: the fully assembled prompt (policy + open blockers + agents/<name>.md)
  out:   the agent's top-level text -> stdout (agent-run.sh appends it to agent-<name>.log)
  side:  each Agent/Task subagent invocation -> subagents.log (same line format the jq
         pipeline produced, so the delegation summary keeps working)
  exit:  0 on success; nonzero on any failure, so agent-run.sh rewinds the watermark
         (at-least-once delivery — a crashed cycle never eats unread messages)

Auth: the logged-in Claude subscription (apiKeySource=none; no ANTHROPIC_API_KEY).
Guardrails: .claude/settings.json deny rules load via setting_sources and are enforced
even under bypassPermissions (verified — deny is evaluated before permission_mode).
Skills: the canonical blocker-triage / board-proposals / safe-cadence skills are version-
controlled in the repo at .claude/skills/ and load via setting_sources=project (cwd is
the repo root), so the Skill tool can invoke them as the mandates expect. Personal
~/.claude/skills still load via setting_sources=user — don't keep stale copies of the
canonical three there, or they can shadow/duplicate the repo versions.
"""
import datetime
import os
import sys

try:
    import spend_log  # scripts/spend_log.py — records per-cycle spend; best-effort, never fatal
except Exception as _spend_exc:  # pragma: no cover
    spend_log = None
    # Make a metering outage LOUD. A silently-unmetered wake is exactly the blind spot that let
    # historical spend go unrecorded; this line lands in agent-<name>.log (stderr is teed there),
    # so a broken hook is visible on the first cycle instead of after a day of lost data.
    print(f"=== spend_log UNAVAILABLE — this wake is UNMETERED: {_spend_exc} ===", file=sys.stderr)

import anyio
import wake_outcome  # Phase-0 yield instrumentation — classify what this wake DID
from claude_agent_sdk import (
    AgentDefinition,
    AssistantMessage,
    ClaudeAgentOptions,
    ResultMessage,
    TextBlock,
    ToolUseBlock,
    query,
)

from model_id import resolve as resolve_model_id  # tier → pinned API model id (org-chart global.model_ids)
from worker_policy import WORKER_SPECS  # scripts/worker_policy.py — declarative worker roster

# Mirrors the old CLAUDE_FLAGS allowlist. "Task" is the pre-2.1.63 name for "Agent";
# both are accepted so subagent fan-out keeps working across CLI/SDK versions.
ALLOWED_TOOLS = ["Bash", "Edit", "Write", "Read", "WebSearch", "WebFetch", "Skill", "Task", "Agent"]
SUBAGENT_TOOLS = {"Agent", "Task"}

# Declarative worker subagents for parallel decomposition. The roster + tool/model scoping live as
# plain data in worker_policy.WORKER_SPECS (stdlib-only, so CI can assert the governance invariants
# without the SDK). The load-bearing rule: NO worker gets Bash, so none can git-push, comment via
# gh, run offload.sh, spend (💰), or deploy (🚀) at depth — those stay on the parent cycle, which
# holds all authority and synthesizes/commits the output. test_agent_workers.py asserts no-Bash in
# CI so a future edit can't silently hand a worker shell power.
#   researcher  read-only, Haiku  — parallel research/exploration (returns findings)
#   drafter     read-only, Haiku  — parallel text drafting (returns a draft)
#   implementer code-authoring, Sonnet — Edit/Write in parallel, but NO shell (parent does git/PR)
# Worker specs speak in tiers (worker_policy stays stdlib-pure for the CI invariants);
# tier→pinned-id resolution happens here, at the SDK boundary, same as build_options.
WORKERS = {
    name: AgentDefinition(**{**spec, "model": resolve_model_id(spec["model"])})
    for name, spec in WORKER_SPECS.items()
}


def build_options(model: str) -> ClaudeAgentOptions:
    return ClaudeAgentOptions(
        # argv carries the TIER (banners + ledger stay tier-keyed); the SDK gets the
        # pinned id so "sonnet" can't silently drift with the CLI's alias resolution.
        model=resolve_model_id(model),
        allowed_tools=ALLOWED_TOOLS,
        # Turn bound, belt to the wall-clock timeout's braces (see run()). Normal cycles run
        # 11–27 turns; a cycle past 60 is looping, not working, and the SDK cutting it off
        # ends it cleanly (with a ResultMessage, so it still gets metered) instead of letting
        # it burn until the clock kills it.
        max_turns=int(os.environ.get("CYCLE_MAX_TURNS", "60")),
        # Headless: never block on a permission prompt. The .claude/settings.json deny
        # rules are evaluated BEFORE permission_mode, so the catastrophic-command and
        # .env-read guardrails still bite (verified empirically).
        permission_mode="bypassPermissions",
        # Load the repo's .claude/skills (canonical, via "project") + any personal
        # ~/.claude/skills (via "user") for the Skill tool, and .claude/settings.json
        # (deny rules).
        setting_sources=["user", "project", "local"],
        skills="all",
        cwd=os.getcwd(),
        # Named, tool-scoped worker subagents. The built-in general-purpose subagent stays
        # available; these add governance-safe options the orchestrator picks by description
        # when a persona fans out a research / draft / code strand.
        agents=WORKERS,
    )


def log_subagent(name: str, block: ToolUseBlock) -> None:
    inp = block.input or {}
    stype = inp.get("subagent_type") or "?"
    desc = " ".join(str(inp.get("description") or inp.get("prompt") or "").split())[:90]
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open("subagents.log", "a", encoding="utf-8") as fh:
        fh.write(f"{ts}  caller={name} wake={os.environ.get('WAKE_ID', '?')} subagent={stype} :: {desc}\n")


async def run(name: str, model: str, prompt: str) -> int:
    rc = 0
    result = None  # the terminal ResultMessage; metered exactly once after the stream (below)
    # Wall-clock bound on the whole agentic stream. A CEO cycle once hung 2h08m inside this
    # loop, holding the single-flight lock the entire time — nothing above the SDK bounds a
    # wake's duration, so a wedged transport wedges the agent. The default 1500s stays under
    # agent-run.sh's lock_ttl=1800s: a timed-out cycle exits (nonzero → watermark rewind,
    # lock released by the EXIT trap) before its lock could ever be judged stale.
    timeout_s = int(os.environ.get("CYCLE_TIMEOUT_S", "1500"))
    # Yield classification (docs/plans/token-efficiency.md Phase 0): fold every tool call
    # the cycle makes — including worker-subagent edits — into ship | post | noop. Pure
    # observation of the stream we already iterate; classify_tool_use never raises.
    outcome = "noop"
    with anyio.move_on_after(timeout_s) as scope:
        async for msg in query(prompt=prompt, options=build_options(model)):
            if isinstance(msg, AssistantMessage):
                is_sub = msg.parent_tool_use_id is not None
                for block in msg.content:
                    if isinstance(block, TextBlock):
                        if block.text.strip():
                            print(("  [subagent] " if is_sub else "") + block.text, flush=True)
                    elif isinstance(block, ToolUseBlock):
                        outcome = wake_outcome.worst_case(
                            outcome, wake_outcome.classify_tool_use(block.name, block.input))
                        if block.name in SUBAGENT_TOOLS:
                            log_subagent(name, block)
            elif isinstance(msg, ResultMessage):
                # When a cycle fans out, the SDK emits an INTERMEDIATE ResultMessage per subagent, all
                # sharing one session_id with IDENTICAL cumulative model_usage; only the final one is
                # authoritative (its total_cost_usd == sum(model_usage costUSD)). Metering each would
                # double-count the whole cycle. So just keep the latest and meter once after the stream.
                result = msg

    if scope.cancelled_caught:
        print(f"=== cycle TIMEOUT after {timeout_s}s — killed ===", flush=True)
        # Same zero-cost error marker as the abort path in main(): the authoritative
        # ResultMessage never arrived, so the tokens billed before the kill aren't knowable
        # here — but the failed wake must still be visible in the ledger.
        if spend_log is not None:
            spend_log.append(source="cycle", agent=name, model=model,
                             wake=os.environ.get("WAKE_REASON", ""), via="sdk",
                             status="error", cost_usd=0.0, turns=0)
        return 1

    if result is not None:
        cost = result.total_cost_usd
        tag = "ERROR" if result.is_error else "ok"
        print(f"=== sdk cycle {tag}: turns={result.num_turns} cost=${cost} ===", flush=True)
        if spend_log is not None:
            status = "error" if result.is_error else "ok"
            wake = os.environ.get("WAKE_REASON", "")
            # Per-model metering: ResultMessage.model_usage breaks the session cost down by model.
            # Even a no-fan-out cycle touches a helper Haiku alongside the brain, and a fan-out can
            # pull in another tier — folding it all into one model=<brain> row mis-attributes spend
            # and blinds the `by model` rollup the testing phase relies on. The per-model costUSD
            # values partition total_cost_usd (sum == total), so one row per model is exact.
            rows = spend_log.model_usage_rows(
                result.model_usage, primary_model=model, num_turns=result.num_turns)
            if rows:
                # The outcome is cycle-level; it rides the primary-brain row only
                # (rows[0] — model_usage_rows puts the turns-bearing row first) so a
                # per-wake yield count never double-counts the per-model siblings.
                for i, r in enumerate(rows):
                    spend_log.append(source="cycle", agent=name, wake=wake, via="sdk",
                                     status=status, outcome=(outcome if i == 0 else ""),
                                     **r)
            else:
                # Older CLI with no model_usage breakdown: one scalar row, as before.
                spend_log.append(
                    source="cycle", agent=name, model=model, wake=wake, via="sdk",
                    status=status, cost_usd=cost or 0.0, turns=result.num_turns,
                    outcome=outcome, **spend_log.usage_tokens(result.usage),
                )
        if result.is_error:
            rc = 1
            if result.result:
                print(f"=== result: {result.result} ===", flush=True)
            if result.errors:
                print(f"=== errors: {result.errors} ===", flush=True)
    return rc


def main() -> None:
    name = sys.argv[1] if len(sys.argv) > 1 else "agent"
    model = sys.argv[2] if len(sys.argv) > 2 else "sonnet"
    prompt = sys.stdin.read()
    if not prompt.strip():
        print("agent_cycle: empty prompt on stdin", file=sys.stderr)
        sys.exit(2)
    try:
        rc = anyio.run(run, name, model, prompt)
    except Exception as exc:  # SDK/transport/auth failure -> nonzero so the watermark rewinds
        print(f"=== cycle aborted: {type(exc).__name__}: {exc} ===", file=sys.stderr)
        # Make the failed wake visible in the spend ledger. Tokens billed before the abort aren't
        # knowable here (the SDK reports cost only at ResultMessage), so this is a zero-cost error
        # marker; the wake re-runs next cycle (watermark rewind) and that run is costed.
        if spend_log is not None:
            spend_log.append(source="cycle", agent=name, model=model,
                             wake=os.environ.get("WAKE_REASON", ""), via="sdk",
                             status="error", cost_usd=0.0, turns=0)
        sys.exit(1)
    sys.exit(rc)


if __name__ == "__main__":
    main()
