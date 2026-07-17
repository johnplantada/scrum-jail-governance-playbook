#!/usr/bin/env python3
"""budget_gate.py — enforce each node's org-chart `envelope.daily_token_budget`.

The envelope budget used to be a prompt-level norm no code read. This gate makes it
real: agent-run.sh consults it before starting a model cycle, and a non-direct wake for
an agent already past its daily budget is skipped — a per-department brownout instead
of the org-wide spend breaker (the runner's SPEND_BREAKER_DAILY_USD hold). DIRECT wakes
(a runner-routed GitHub event: the Chairman's issue, a deploy failure) always run: a
spent budget must never block the Chairman, and the overage stays visible in the
log + ledger.

Budget accounting: sum of `in` + `out` tokens across today's state/spend.jsonl rows for
the agent (cycles AND its offloads — offload.sh attributes spend via AGENT_NAME).
Cache reads/creations are excluded — the budget is a bound on fresh work, and cached
input is exactly the cost the org already optimized away.

Usage:  budget_gate.py <agent>
Output: one line —  "ok used=<n> budget=<b>"  or  "over used=<n> budget=<b>"
Fail-open: ANY error (missing ledger, unreadable chart, no yaml) prints ok — a broken
meter must brown out the metering, never the org. Exit code is always 0; the verdict
rides stdout.
"""
import json
import os
import sys
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LEDGER = os.path.join(REPO, "state", "spend.jsonl")


# --- pure helpers (unit-tested in test_budget_gate.py; no I/O, no yaml) -----------

def tokens_today(rows, agent, today):
    """Sum in+out tokens of the agent's ledger rows stamped with today's date.

    rows: dicts in spend_log.py's schema; malformed rows are skipped, never fatal.
    today: 'YYYY-MM-DD' (rows' ts is 'YYYY-MM-DD HH:MM:SS').
    """
    used = 0
    for r in rows:
        try:
            if r.get("agent") != agent or not str(r.get("ts", "")).startswith(today):
                continue
            used += int(r.get("in", 0) or 0) + int(r.get("out", 0) or 0)
        except (TypeError, ValueError):
            continue
    return used


def decide(used, budget):
    """'over' only when a positive budget is spent; 0/absent budget = unlimited."""
    if budget and budget > 0 and used >= budget:
        return "over"
    return "ok"


def find_budget(depts, agent):
    """daily_token_budget for the named node in a departments tree (0 = none set)."""
    for d in depts or []:
        if d.get("name") == agent:
            return int((d.get("envelope") or {}).get("daily_token_budget") or 0)
        b = find_budget(d.get("teams"), agent)
        if b:
            return b
    return 0


# --- I/O ---------------------------------------------------------------------------

def load_rows(path=LEDGER):
    rows = []
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    try:
                        rows.append(json.loads(line))
                    except ValueError:
                        continue
    except OSError:
        pass
    return rows


def load_budget(agent):
    import yaml  # lazy, like warden.py — the pure helpers stay testable without it
    with open(os.path.join(REPO, "org-chart.yaml"), encoding="utf-8") as fh:
        chart = yaml.safe_load(fh) or {}
    return find_budget(chart.get("departments"), agent)


def main():
    agent = sys.argv[1] if len(sys.argv) > 1 else ""
    if not agent:
        print("ok used=0 budget=0")
        return
    try:
        budget = load_budget(agent)
        used = tokens_today(load_rows(), agent, time.strftime("%Y-%m-%d"))
        print(f"{decide(used, budget)} used={used} budget={budget}")
    except Exception as exc:  # fail-open: a broken meter never blocks the org
        # …but say WHY on stderr (agent-run.sh appends it to the agent log), so a
        # permanently broken meter is visible instead of silently unlimited.
        print(f"budget_gate: fail-open: {exc}", file=sys.stderr)
        print("ok used=? budget=?")


if __name__ == "__main__":
    main()
