#!/usr/bin/env python3
"""subagent_gate.py — enforce each node's org-chart `envelope.max_subagents` in code.

The chat-era Registrar refused over-cap sub-teams; that enforcement retired with the
demolition and the cap fell back to a prompt-level norm. This gate makes it code
again, as a PreToolUse hook (.claude/settings.json) on the Agent/Task tools:
agent_cycle.py loads the repo settings via setting_sources, and hooks — like the deny
rules — are evaluated even under bypassPermissions. Counter-ratchet: this replaces the
Registrar's max_subagents check, nothing else.

Mechanics: agent-run.sh exports AGENT_NAME + WAKE_ID per wake. Each allowed Agent/Task
call bumps state/subagents-<WAKE_ID>.count; a call that would exceed the agent's
`envelope.max_subagents` (0 = no delegation — the documented default) is DENIED with a
reason the model reads in-cycle: finish the work yourself, or propose a cap change via
a decisions.yaml [CHARTER] PR. `global_max_agents` stays a configuration invariant:
test_subagent_gate.py asserts the roster arithmetic (every brain + its full fan-out)
fits under it, so the chart can't quietly promise more concurrency than the ceiling.

Fail-open: outside a wake (no AGENT_NAME/WAKE_ID — the Chairman's own sessions are
never capped) or on ANY error (unreadable chart, missing yaml) the call is allowed
and the gate says why on stderr — a broken gate must never brick a cycle. Exit code
is always 0; the verdict rides stdout JSON per the hooks contract.
"""
import json
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
COUNT_DIR = os.path.join(REPO, "state")


# --- pure helpers (unit-tested in test_subagent_gate.py; no yaml, no I/O) ----------

def find_cap(depts, agent):
    """envelope.max_subagents for the named node; None = node not in the chart.

    Unlike budget_gate.find_budget (where 0 means "no budget set"), 0 here is a real
    verdict — "no delegation" — so absent-node and zero must stay distinguishable.
    A node present without the key gets the documented default, 0.
    """
    for d in depts or []:
        if d.get("name") == agent:
            return int((d.get("envelope") or {}).get("max_subagents") or 0)
        cap = find_cap(d.get("teams"), agent)
        if cap is not None:
            return cap
    return None


def decide(count, cap):
    """'deny' when this spawn would exceed the cap; count is spawns already made."""
    return "deny" if count >= cap else "allow"


def deny_payload(agent, count, cap):
    """The hooks-contract JSON for a refusal (both current and legacy key shapes)."""
    reason = (
        f"max_subagents cap: {agent} already spawned {count}/{cap} subagents this wake "
        f"(org-chart.yaml envelope.max_subagents). Do the remaining work in this cycle "
        f"yourself, or propose a cap change as a decisions.yaml [CHARTER] PR — the "
        f"Chairman's merge is the approval."
    )
    return {
        "decision": "block",
        "reason": reason,
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        },
    }


# --- I/O ---------------------------------------------------------------------------

def read_count(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return int(fh.read().strip() or 0)
    except FileNotFoundError:
        return 0


def write_count(path, count):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(str(count))


def main():
    try:
        json.load(sys.stdin)  # hook payload — read so the pipe drains; env is authoritative
    except Exception:
        pass
    agent = os.environ.get("AGENT_NAME", "")
    wake = os.environ.get("WAKE_ID", "")
    if not agent or not wake:
        return  # not a headless wake — humans aren't capped
    try:
        import yaml
        with open(os.path.join(REPO, "org-chart.yaml"), encoding="utf-8") as fh:
            chart = yaml.safe_load(fh) or {}
        cap = find_cap(chart.get("departments"), agent)
        if cap is None:
            print(f"subagent_gate: {agent} not in org-chart — allowing", file=sys.stderr)
            return
        path = os.path.join(COUNT_DIR, f"subagents-{wake}.count")
        count = read_count(path)
        if decide(count, cap) == "deny":
            print(json.dumps(deny_payload(agent, count, cap)))
            return
        write_count(path, count + 1)
    except Exception as exc:  # fail-open, loudly — a broken gate must never brick a cycle
        print(f"subagent_gate: fail-open ({type(exc).__name__}: {exc})", file=sys.stderr)


if __name__ == "__main__":
    main()
