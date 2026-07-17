#!/usr/bin/env python3
"""efficiency.py — the experiment's denominator: what a unit of shipped output costs.

The org meters every Claude call into state/spend.jsonl (the numerator — see
scripts/costs.py), but the build-in-public experiment is "what does multi-agent
coordination actually COST", and cost is only meaningful per unit of output. This joins
the spend ledger to the product repo's objective output records on GitHub:

  merged product PRs   gh pr list  --state merged            (code that landed)
  accepted demos       gh run list --workflow=demo-evidence.yml, success
                       (the [DEMO] gate's evidence predicate — scripts/demo-verify.sh)
  prod ships           gh run list --workflow=deploy.yml, success, main
                       (the same predicate as scripts/last-ship.sh)

  scripts/efficiency.py report [--days N]   # table, default trailing 30 days
  scripts/efficiency.py line   [--days N]   # one-line summary (embedded by digest.py)

Read-only and fail-soft: no gh, offline, or an empty ledger degrades to n/a values,
never an exception — the weekly digest embeds this, and a broken denominator must not
sink the digest. Pure stdlib.
"""
import argparse
import json
import os
import subprocess
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_DAYS = 30


# --- pure helpers (unit-tested in test_efficiency.py; no I/O) ---------------------------

def window_cost(rows, since_ts):
    """Total cost_usd over ledger rows with ts >= since_ts (both 'YYYY-MM-DD …' strings,
    so lexicographic compare is chronological — same convention as digest.spend_summary)."""
    total = 0.0
    for r in rows:
        try:
            if str(r.get("ts", "")) >= since_ts:
                total += float(r.get("cost_usd", 0) or 0)
        except (TypeError, ValueError):
            continue
    return round(total, 2)


def count_since(iso_timestamps, since_date):
    """How many ISO-8601 timestamps ('2026-07-05T19:22:20Z') fall on/after since_date
    ('YYYY-MM-DD'). Ignores empty/None entries."""
    return sum(1 for ts in iso_timestamps if ts and str(ts)[:10] >= since_date)


def per_unit(total_usd, count):
    """'$X.XX' per unit, or 'n/a' when nothing shipped — an honest denominator prints
    n/a rather than pretending division by zero is free output."""
    if not count:
        return "n/a"
    return f"${total_usd / count:.2f}"


def render_line(total, days, prs, demos, ships):
    """One digest-embeddable line. Counts of None mean the source was unavailable."""
    def part(label, count):
        if count is None:
            return f"{label} n/a"
        return f"{per_unit(total, count)}/{label} ({count})"
    return (f"${total} spent over {days}d — "
            + ", ".join([part("merged PR", prs), part("accepted demo", demos),
                         part("prod ship", ships)]))


# --- I/O --------------------------------------------------------------------------------

def gh_json(args):
    """Run gh and parse its --json output; None on any failure (no gh, offline, auth)."""
    try:
        out = subprocess.run(["gh"] + args, capture_output=True, timeout=30)
        if out.returncode != 0:
            return None
        return json.loads(out.stdout or b"null")
    except (OSError, ValueError):
        return None


def output_counts(repo, since_date):
    """(merged_prs, accepted_demos, prod_ships) in the window; None per unavailable source."""
    prs = gh_json(["pr", "list", "--repo", repo, "--state", "merged",
                   "--limit", "200", "--json", "mergedAt"])
    demos = gh_json(["run", "list", "--repo", repo, "--workflow=demo-evidence.yml",
                     "--status=success", "--limit", "200", "--json", "createdAt"])
    ships = gh_json(["run", "list", "--repo", repo, "--workflow=deploy.yml",
                     "--status=success", "--branch", "main",
                     "--limit", "200", "--json", "createdAt"])
    return (
        None if prs is None else count_since((p.get("mergedAt") for p in prs), since_date),
        None if demos is None else count_since((r.get("createdAt") for r in demos), since_date),
        None if ships is None else count_since((r.get("createdAt") for r in ships), since_date),
    )


def gather(days):
    """(total_usd, prs, demos, ships) for the trailing window."""
    import budget_gate
    since = time.strftime("%Y-%m-%d", time.localtime(time.time() - days * 86400))
    total = window_cost(budget_gate.load_rows(), since)
    repo = os.environ.get("PRODUCT_GH_REPO", "")
    prs, demos, ships = output_counts(repo, since)
    return total, prs, demos, ships


def yield_line(days):
    """The wake-yield line for the window (Phase 0, docs/plans/token-efficiency.md):
    % of wakes that mutated the record, from outcome-tagged ledger rows. The steering
    KPI — $/day rewards cheaper idling; yield rewards fewer pointless wakes."""
    import budget_gate
    import wake_outcome
    since = time.strftime("%Y-%m-%d", time.localtime(time.time() - days * 86400))
    return wake_outcome.render_yield(wake_outcome.wake_yield(budget_gate.load_rows(), since))


def main():
    ap = argparse.ArgumentParser(description="cost per unit of shipped output")
    ap.add_argument("cmd", nargs="?", default="report", choices=["report", "line"])
    ap.add_argument("--days", type=int, default=DEFAULT_DAYS)
    a = ap.parse_args()
    total, prs, demos, ships = gather(a.days)
    if a.cmd == "line":
        print(render_line(total, a.days, prs, demos, ships) + " — " + yield_line(a.days))
        return
    print(f"spend, trailing {a.days}d:   ${total}")
    for label, count in (("merged product PRs", prs), ("accepted demos", demos),
                         ("prod ships", ships)):
        shown = "n/a (source unavailable)" if count is None else count
        print(f"{label + ':':<24}{shown:>6}    cost/unit: {per_unit(total, count or 0)}")
    print(yield_line(a.days))


if __name__ == "__main__":
    main()
