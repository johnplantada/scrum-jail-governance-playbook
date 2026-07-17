#!/usr/bin/env python3
"""Read blockers.yaml — the human-task ledger — and print it, best-value-first.

  scripts/blockers.py open    # only state: open entries (compact; injected into agent prompts)
  scripts/blockers.py         # same as 'open'
  scripts/blockers.py all     # every entry with its state, in file order
  scripts/blockers.py lint    # CI gate: exit 1 if any open blocker lacks a runbook (`action:`)

The Chairman is single-threaded and high-latency, so the open queue prints in EV order:
market-contact first (existential — a live product with no checkout/audience), then value
class (revenue > signal > infra > unclassified), cheapest `effort_minutes` within a class,
oldest first on ties — a batching sit-down starts at the top and works down. Each line
carries the entry's age so staleness is visible at a glance.

Market-contact entries (`gates_market_contact: true`) also get a loud banner printed FIRST
that re-enters every agent wake — the one blocker class that does not go quiet (invariant 2).

When the open count exceeds `global.unlock_wip_limit` (org-chart.yaml), the queue is
prefixed with a WIP warning. The queue is injected into every agent's wake prompt, so
the warning is the enforcement channel — prompt-level, because "critical path ends in a
human-only unlock" takes judgment no cheap check can make (unlike the subagent cap, which
subagent_gate.py now enforces in code): agents must not START new work whose critical
path ends in another human-only unlock (DESIGN.md, invariant 2).

Prints nothing (exit 0) when there are no matching blockers, so callers can `$(...)` it
safely. Falls back silently if the file is missing or unparseable.
"""
import datetime
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATH = os.path.join(REPO, "blockers.yaml")
CHART = os.path.join(REPO, "org-chart.yaml")

VALUE_RANK = {"revenue": 0, "signal": 1, "infra": 2}

# A runbook shorter than this (whitespace-normalized `action:`) is not actionable — the
# Chairman can't clear a one-liner. `blockers.py lint` fails on open entries below it.
RUNBOOK_MIN_CHARS = 40


# --- pure helpers (unit-tested in test_blockers.py; no I/O) ----------------------------

def age_days(opened, today):
    """Whole days since the entry's `opened` date (YYYY-MM-DD); None if unparseable."""
    try:
        return max(0, (today - datetime.date.fromisoformat(str(opened))).days)
    except (TypeError, ValueError):
        return None


def is_market_contact(b):
    """True for a blocker on the critical path to ANY market contact — a live checkout
    or a real audience. This class is the one exception to record-once-and-go-quiet
    (DESIGN.md invariant 2): while a product is live with no way to pay it and no one
    told it exists, the ledger surfaces it loudly every wake until the Chairman clears
    it. Flagged in blockers.yaml with `gates_market_contact: true`."""
    return bool(b.get("gates_market_contact"))


def sort_key(b):
    """EV order: market-contact first (existential — never routine), then value class,
    then Chairman-effort ascending, then oldest first. Unclassified entries sink below
    every classified one; unknown effort sorts last within its class (an unestimated
    unlock is not a quick win until someone says so)."""
    rank = VALUE_RANK.get(str(b.get("value") or "").strip(), len(VALUE_RANK))
    try:
        effort = float(b.get("effort_minutes"))
    except (TypeError, ValueError):
        effort = float("inf")
    return (0 if is_market_contact(b) else 1, rank, effort,
            str(b.get("opened") or "9999-99-99"))


def needs_runbook(b):
    """An OPEN blocker the Chairman can't act on: its `action:` is missing or too thin
    to be a runbook and it carries no `runbook:` file pointer. Cleared entries are exempt.
    Enforced by `blockers.py lint` (CI) so every human-only subtask ships with a runbook."""
    if (b.get("state") or "open").strip() != "open":
        return False
    action = " ".join((b.get("action") or "").split())
    return len(action) < RUNBOOK_MIN_CHARS and not str(b.get("runbook") or "").strip()


def open_entries(data):
    """The open queue, EV-sorted."""
    return sorted((b for b in (data.get("blockers") or [])
                   if (b.get("state") or "open").strip() == "open"), key=sort_key)


def format_line(b, today, show_state=False):
    """One compact queue line: id, (kind, value, effort, age), summary → action.
    Market-contact entries lead their tag list with 🔴 so they stand out in the queue."""
    tags = ["🔴 market-contact"] if is_market_contact(b) else []
    tags.append(b.get("kind", ""))
    value = str(b.get("value") or "").strip()
    if value:
        tags.append(value)
    try:
        tags.append(f"~{int(b.get('effort_minutes'))}min")
    except (TypeError, ValueError):
        pass
    age = age_days(b.get("opened"), today)
    if age is not None:
        tags.append(f"{age}d old")
    state = f" [{(b.get('state') or 'open').strip()}]" if show_state else ""
    line = (f"- [{b.get('id', '?')}] ({', '.join(t for t in tags if t)}){state} "
            f"{' '.join((b.get('summary') or '').split())}")
    action = " ".join((b.get("action") or "").split())
    if action:
        line += f"  → {action}"
    return line


def wip_warning(n_open, limit):
    """The queue-overflow banner, or None while under the limit (or with no limit set)."""
    if not limit or n_open <= limit:
        return None
    return (f"⚠ UNLOCK WIP LIMIT EXCEEDED — {n_open} open human-only blockers "
            f"(global.unlock_wip_limit is {limit}). Do NOT start new work whose critical "
            f"path ends in another human-only unlock; swarm what is already unblocked "
            f"(DESIGN.md, invariant 2).")


def market_contact_alert(entries, today):
    """The one banner that does NOT go quiet. When any open entry is flagged
    `gates_market_contact`, name each one loudly — a live product with no checkout and/or
    no audience is the single blocker class the org must never let fade into the ledger.
    This prints first in `blockers.py open`, so it re-enters every agent's wake prompt
    each cycle until the Chairman clears it (the deliberate inverse of invariant 2's
    record-once-and-go-quiet). None when nothing is flagged. Incident 2026-07-11: the org
    sat org-quiet over a live product with no way to pay it and no one told it existed."""
    flagged = [b for b in (entries or []) if is_market_contact(b)]
    if not flagged:
        return None

    def one(b):
        age = age_days(b.get("opened"), today)
        agestr = f", {age}d open" if age is not None else ""
        summary = " ".join((b.get("summary") or "").split())
        return f"  • [{b.get('id', '?')}{agestr}] {summary}"

    return ("🔴 MARKET-CONTACT BLOCKED — the product is live but a checkout and/or an "
            "audience is gated on the Chairman. This class does NOT go quiet; until it "
            "clears, the org ships into a void:\n" + "\n".join(one(b) for b in flagged))


# --- I/O --------------------------------------------------------------------------------

def unlock_wip_limit():
    """global.unlock_wip_limit from org-chart.yaml; None if absent/unreadable."""
    try:
        import yaml
        chart = yaml.safe_load(open(CHART, encoding="utf-8")) or {}
        return int((chart.get("global") or {}).get("unlock_wip_limit"))
    except Exception:
        return None


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "open"
    try:
        import yaml
        data = yaml.safe_load(open(PATH, encoding="utf-8")) or {}
    except Exception:
        return
    today = datetime.date.today()
    if mode == "lint":
        missing = [b for b in (data.get("blockers") or []) if needs_runbook(b)]
        for b in missing:
            print(f"blockers.py lint: open blocker '{b.get('id', '?')}' has no runbook "
                  f"(`action:` < {RUNBOOK_MIN_CHARS} chars and no `runbook:` pointer) — "
                  f"every human-only subtask must ship with one", file=sys.stderr)
        if missing:
            sys.exit(f"blockers.py lint: FAIL — {len(missing)} open blocker(s) missing a runbook")
        print("blockers.py lint: every open blocker carries a runbook")
        return
    if mode == "all":
        for b in data.get("blockers") or []:
            print(format_line(b, today, show_state=True))
        return
    queue = open_entries(data)
    alert = market_contact_alert(queue, today)
    if alert:
        print(alert)
    warn = wip_warning(len(queue), unlock_wip_limit())
    if warn:
        print(warn)
    for b in queue:
        print(format_line(b, today))


if __name__ == "__main__":
    try:
        main()
    except BrokenPipeError:  # a closed pipe (| head) is a fine way to stop reading a queue
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        sys.exit(0)
