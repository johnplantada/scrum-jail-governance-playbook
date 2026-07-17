#!/usr/bin/env python3
"""objective_gate.py — objectives are the Chairman's alone, in code (DESIGN.md invariant 1).

Work intake is a reserved power: the Chairman files an `[OBJECTIVE]`, the org decomposes
it. Until now that was a norm nowhere written and everywhere contradicted — `agents/ceo.md`
positively *mandated* the CEO to "turn the north star into `[OBJECTIVE]` issues", and it
did (org#7/8/9), exactly as told. `pm-gh.sh` already refuses `--type objective`, so the
only path left open was bare `gh issue create --label objective` — which is what this gate
closes, as a PreToolUse hook (.claude/settings.json) on the Bash tool.

WHY A HOOK AND NOT CI: all agents share the Chairman's GitHub identity, so
`github.event.issue.user.login` reads the Chairman's login for a CEO-opened objective exactly as
it does for a Chairman-opened one (runner.py's banner_dept exists for this reason). No
Actions authorship check can tell them apart. The only honest discriminator is the one the
wake itself creates: agent-run.sh exports AGENT_NAME; the Chairman's own shell never has
it. So the gate lives where that env is real — inside the cycle, before the tool runs.

HONEST SCOPE (emoji-gate.md's own warning applies): this is a backstop, not a wall. An
agent holding the token can still reach the API around Bash. It refuses the honest path —
the one a correctly-behaving agent following a stale mandate would take — and that is the
failure this org actually had. The wall is the mandate; this is the guardrail on it.

Fail-open: outside a wake (no AGENT_NAME — the Chairman is never gated) or on ANY error
the call is allowed and the gate says why on stderr; a broken gate must never brick a
cycle. Exit code is always 0; the verdict rides stdout JSON per the hooks contract.
"""
import json
import os
import shlex
import sys

KIND_LABEL = "objective"
KIND_PREFIX = "[OBJECTIVE]"
SEPARATORS = {"&&", "||", ";", "|", "&", "\n"}


# --- pure helpers (unit-tested in test_objective_gate.py; no I/O) -------------------

def split_commands(tokens):
    """Shell tokens → the simple commands within, split on the usual operators.

    `gh issue list && gh issue create --label objective` is two commands; the gate must
    judge each, or a refusal is one `true &&` away from being bypassed by accident.
    """
    out, cur = [], []
    for t in tokens or []:
        if t in SEPARATORS:
            if cur:
                out.append(cur)
                cur = []
        else:
            cur.append(t)
    if cur:
        out.append(cur)
    return out


def flag_values(argv, names):
    """Every value passed to any of `names`, for both `--flag value` and `--flag=value`."""
    vals = []
    for i, t in enumerate(argv):
        for n in names:
            if t == n and i + 1 < len(argv):
                vals.append(argv[i + 1])
            elif t.startswith(n + "="):
                vals.append(t[len(n) + 1:])
    return vals


def _is_objective_label(value):
    """True when a --label value names the objective kind (it may be a comma list)."""
    return any(p.strip().strip("\"'").lower() == KIND_LABEL for p in (value or "").split(","))


def _is_objective_title(value):
    return (value or "").strip().lstrip("\"'").upper().startswith(KIND_PREFIX)


def has_verb(argv, noun, verb):
    """True when `noun verb` appears adjacently — order-insensitive to global flags."""
    return any(argv[i] == noun and argv[i + 1] == verb for i in range(len(argv) - 1))


def carries_objective_kind(argv):
    """Does this argv apply the objective kind — by label or by title prefix?"""
    if any(_is_objective_label(v) for v in flag_values(argv, ("--label", "-l"))):
        return True
    return any(_is_objective_title(v) for v in flag_values(argv, ("--title", "-t")))


def api_carries_objective_kind(argv):
    """The `gh api` path: -f/-F/--field labels[]=objective or title=[OBJECTIVE] …"""
    for v in flag_values(argv, ("-f", "-F", "--field", "--raw-field")):
        key, _, val = v.partition("=")
        key = key.strip()
        if key.startswith("labels") and _is_objective_label(val):
            return True
        if key == "title" and _is_objective_title(val):
            return True
    return False


def is_objective_write(argv):
    """True when this simple command would CREATE or CONVERT-TO an objective.

    Reads are never gated: `gh issue list --label objective`, `gh issue view 7`, and a
    comment whose body merely quotes "[OBJECTIVE]" must all pass — the org reasons about
    objectives constantly and only minting one is reserved.
    """
    if not argv:
        return False
    if os.path.basename(argv[0]) != "gh":
        return False
    if has_verb(argv, "issue", "create"):
        return carries_objective_kind(argv)
    if has_verb(argv, "issue", "edit"):  # relabelling an existing issue INTO an objective
        if any(_is_objective_label(v) for v in flag_values(argv, ("--add-label",))):
            return True
        return any(_is_objective_title(v) for v in flag_values(argv, ("--title", "-t")))
    if len(argv) > 1 and argv[1] == "api" and any("issues" in t for t in argv[2:]):
        method = flag_values(argv, ("-X", "--method"))
        if not method or method[0].upper() in ("POST", "PATCH"):
            return api_carries_objective_kind(argv)
    return False


def decide(command):
    """'deny' when any simple command in `command` would mint an objective."""
    try:
        tokens = shlex.split(command or "", comments=True)
    except ValueError:  # unbalanced quotes — not ours to adjudicate
        return "allow"
    return "deny" if any(is_objective_write(a) for a in split_commands(tokens)) else "allow"


def deny_payload(agent):
    """The hooks-contract JSON for a refusal (both current and legacy key shapes)."""
    reason = (
        f"[OBJECTIVE]s are the Chairman's alone (DESIGN.md invariant 1) — {agent} may not "
        f"open one. Work intake is reserved: the Chairman files the objective, the org "
        f"decomposes it. If a mission pillar has no ticket, say so where it will be read: "
        f"open a [PROPOSAL] issue (.github/ISSUE_TEMPLATE/proposal.yml) naming the gap and "
        f"the objective you'd file, and the Chairman's own filing is the answer. To build "
        f"UNDER an objective he has already filed, use "
        f"`scripts/pm-gh.sh create --type epic|feature|story --parent <N>`."
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

def main():
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return  # no payload, no command to judge
    agent = os.environ.get("AGENT_NAME", "")
    if not agent:
        return  # not a headless wake — the Chairman's own sessions are never gated
    try:
        command = (payload.get("tool_input") or {}).get("command", "")
        if decide(command) == "deny":
            print(json.dumps(deny_payload(agent)))
    except Exception as exc:  # fail-open, loudly — a broken gate must never brick a cycle
        print(f"objective_gate: fail-open ({type(exc).__name__}: {exc})", file=sys.stderr)


if __name__ == "__main__":
    main()
