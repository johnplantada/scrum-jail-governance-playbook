#!/usr/bin/env python3
"""wake_outcome.py — classify what a wake actually DID, from its tool-use stream.

The efficiency work (docs/plans/token-efficiency.md, Phase 0) steers on YIELD — the
fraction of wakes that mutate the system of record — not on $/day, because a $/day cap
rewards cheaper idling. That needs every cycle row in state/spend.jsonl to carry an
`outcome`. Classification happens in-process in agent_cycle.py (it already iterates
every message), from the commands the cycle ran — deterministic, zero extra tokens,
no self-reporting by the model.

Classes, ranked (a cycle gets its highest-ranked observation):
  ship  — durable output landed: git push, PR created/merged, release cut
  post  — the record was mutated: issue/PR comment, ticket create/move/close,
          board mutation, blockers/decisions ledger edit
  noop  — a read-only cycle: nothing observed but reads/greps/status checks

Pure stdlib, no I/O — unit-tested in test_wake_outcome.py. wake_yield() is the
aggregation used by efficiency.py's report.
"""
import re

# Rank order: higher wins when a cycle does several kinds of work.
RANK = {"noop": 0, "post": 1, "ship": 2}

# Bash command patterns, matched anywhere in the command string (commands are often
# chained with && or ;). Order within each list doesn't matter — rank does.
_SHIP_RE = [
    r"\bgit\s+push\b",
    r"\bgh\s+pr\s+(create|merge)\b",
    r"\bgh\s+release\b",
]
_POST_RE = [
    r"\bgh\s+issue\s+(comment|create|close|reopen|edit|transfer)\b",
    r"\bgh\s+pr\s+(comment|review|edit|close|ready)\b",
    r"\bgh\s+api\b.*(-X|--method)\s+(POST|PATCH|PUT|DELETE)\b",
    r"\bgh\s+project\s+item-(add|edit|archive|delete)\b",
    r"\bpm-gh\.sh\s+(create|move|comment|done)\b",
    r"\bgit\s+commit\b",           # committed work: recorded even if the push comes later
    r"\bworkitems\.py\s+link\b",
]
_SHIP = [re.compile(p) for p in _SHIP_RE]
_POST = [re.compile(p) for p in _POST_RE]

# Edit/Write targets that ARE the record (ledgers), not code-in-progress. Editing
# product/org source only counts once it lands (git commit/push above).
_LEDGER_FILE_RE = re.compile(r"(blockers|decisions|metrics)\.ya?ml$")


def classify_tool_use(tool_name, tool_input):
    """One tool call → 'ship' | 'post' | None (contributes nothing).

    Never raises: an unparseable block is just not evidence of a mutation."""
    try:
        name = str(tool_name or "")
        inp = tool_input if isinstance(tool_input, dict) else {}
        if name == "Bash":
            cmd = str(inp.get("command") or "")
            if any(p.search(cmd) for p in _SHIP):
                return "ship"
            if any(p.search(cmd) for p in _POST):
                return "post"
            return None
        if name in ("Edit", "Write", "NotebookEdit"):
            path = str(inp.get("file_path") or inp.get("path") or "")
            if _LEDGER_FILE_RE.search(path):
                return "post"
            return None
    except Exception:
        pass
    return None


def worst_case(current, observed):
    """Fold one observation into the cycle's running outcome (highest rank wins)."""
    if observed is None:
        return current
    if RANK.get(observed, 0) > RANK.get(current or "noop", 0):
        return observed
    return current


def wake_yield(rows, since_ts=""):
    """Aggregate outcome-tagged CYCLE rows into a yield summary.

    Counts one outcome per wake: rows are keyed by wake_id when present (a per-model
    breakdown writes several rows per cycle; outcome rides the primary row only, so
    keying prevents the tagless sibling rows from inflating `untagged`). Rows with
    ts < since_ts are ignored ('YYYY-MM-DD …' strings compare chronologically), and so
    are status!=ok rows — a crashed wake is a failure, not a yield statement.

    Returns {"ship": n, "post": n, "noop": n, "tagged": n, "untagged": n}."""
    counts = {"ship": 0, "post": 0, "noop": 0}
    tagged_wakes = set()
    untagged_wakes = set()
    untagged_anon = 0
    for r in rows:
        try:
            if r.get("source") != "cycle" or str(r.get("ts", "")) < since_ts:
                continue
            if str(r.get("status", "ok")) != "ok":
                continue
            key = r.get("wake_id") or ""
            outcome = str(r.get("outcome") or "")
            if outcome in counts:
                if key in tagged_wakes:
                    continue
                counts[outcome] += 1
                if key:
                    tagged_wakes.add(key)
                    untagged_wakes.discard(key)
            elif key:
                if key not in tagged_wakes:
                    untagged_wakes.add(key)
            else:
                untagged_anon += 1
        except (TypeError, ValueError, AttributeError):
            continue
    tagged = sum(counts.values())
    return dict(counts, tagged=tagged, untagged=len(untagged_wakes) + untagged_anon)


def render_yield(y):
    """One report line from a wake_yield() dict. Honest denominator: only tagged rows
    are a yield claim; untagged (pre-Phase-0 history) is shown, not guessed at."""
    if not y or not y.get("tagged"):
        return "wake yield: n/a (no outcome-tagged cycles yet)"
    t = y["tagged"]
    pct = lambda n: f"{100.0 * n / t:.0f}%"
    line = (f"wake yield: {pct(y['ship'] + y['post'])} productive over {t} wakes "
            f"(ship {y['ship']}, post {y['post']}, noop {y['noop']})")
    if y.get("untagged"):
        line += f" — {y['untagged']} older wakes untagged"
    return line
