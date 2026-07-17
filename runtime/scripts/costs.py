#!/usr/bin/env python3
"""costs.py — read the org's unified spend ledger and total + trend ALL Claude spend. Every call
is a row in state/spend.jsonl, written live by agent_cycle.py (full agent wakes, source=cycle) and
spend_offload.py (offloads, source=offload). This reports by source / agent / model / day with
token stats, and can export a headered CSV for a spreadsheet. Pure stdlib.

  scripts/costs.py                   # report from state/spend.jsonl
  scripts/costs.py --csv costs.csv   # also export a headered CSV (spreadsheet / sqlite3 -csv)
  scripts/costs.py --import-logs     # one-time: seed historical CYCLE spend from agent-*.log
                                     #   (tokens unknown for history; refuses once live rows exist)
"""
import argparse
import csv
import glob
import json
import os
import re
import sys
from collections import defaultdict

# Anchor DEFAULT paths at the repo root (regardless of CWD); user-supplied relative --csv/--ledger/
# --logs resolve against the caller's CWD as usual. We deliberately do NOT chdir — that would
# silently relocate a relative path the user passed from elsewhere.
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LEDGER = os.environ.get("SPEND_LEDGER") or os.path.join(REPO_ROOT, "state", "spend.jsonl")
DEFAULT_LOGS = os.path.join(REPO_ROOT, "agent-*.log")
CSV_FIELDS = ["ts", "source", "agent", "model", "wake", "turns",
              "in", "out", "cache_read", "cache_creation", "cost_usd", "status", "via"]

# Historical cycle spend lives in the agent logs as a wake marker + a result line:
#   === 2026-06-28 19:34:26 :: comedy wake \xb7 brain=haiku \xb7 wake=broadcast ===
#   === sdk cycle ok: turns=16 cost=$0.103 ===
WAKE_RE = re.compile(r"=== (\d{4}-\d\d-\d\d \d\d:\d\d:\d\d) :: (\S+) wake \xb7 brain=(\S+) \xb7 wake=(\S+) ===")
COST_RE = re.compile(r"=== sdk cycle (ok|ERROR): turns=(\d+) cost=\$([0-9.]+) ===")


def load(path):
    rows = []
    if not os.path.exists(path):
        return rows
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return rows


def import_logs(path, logs_glob, force):
    """One-time history seed: project the cycle cost already in agent-*.log into the ledger as
    via=log rows (tokens unknown for history). Refuses if live via=sdk rows exist — by then the
    spend is captured going forward, and re-importing could double-count (log ts != sdk ts)."""
    rows = load(path)
    if not force and any(r.get("via") == "sdk" for r in rows):
        print("refusing --import-logs: live (via=sdk) rows already present, so history is captured "
              "going forward. Pass --force to import anyway.", file=sys.stderr)
        return
    # Dedup on the full cycle identity, not just (ts, agent): an agent can have two distinct cycles
    # in the same 1-second ts (e.g. a direct wake right after a broadcast), and we must not drop one.
    seen = {(r.get("ts"), r.get("agent"), r.get("wake"), r.get("turns"), r.get("cost_usd"))
            for r in rows if r.get("via") == "log"}
    added = 0
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "a", encoding="utf-8") as out:
        for lp in sorted(glob.glob(logs_glob)):
            pending = None
            with open(lp, encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    w = WAKE_RE.search(line)
                    if w:
                        pending = w.groups()
                        continue
                    c = COST_RE.search(line)
                    if c and pending:
                        ts, agent, brain, wake = pending
                        pending = None
                        turns, cost = int(c.group(2)), round(float(c.group(3)), 6)
                        key = (ts, agent, wake, turns, cost)
                        if key in seen:
                            continue
                        seen.add(key)
                        out.write(json.dumps({
                            "ts": ts, "source": "cycle", "agent": agent, "model": brain, "wake": wake,
                            "turns": turns, "in": 0, "out": 0, "cache_read": 0, "cache_creation": 0,
                            "cost_usd": cost, "status": "ok" if c.group(1) == "ok" else "error", "via": "log",
                        }) + "\n")
                        added += 1
    print(f"imported {added} historical cycle(s) from {logs_glob} -> {path}", file=sys.stderr)


def export_csv(rows, csv_path):
    with open(csv_path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=CSV_FIELDS, extrasaction="ignore")
        w.writeheader()
        for r in sorted(rows, key=lambda r: r.get("ts", "")):
            w.writerow(r)
    print(f"wrote {len(rows)} rows -> {csv_path}", file=sys.stderr)


def _f(r, k):
    try:
        return float(r.get(k) or 0)
    except (TypeError, ValueError):
        return 0.0


def report(rows):
    if not rows:
        print(f"no spend recorded yet in {LEDGER} (agents write it as they run; "
              "--import-logs seeds history)", file=sys.stderr)
        return
    total = sum(_f(r, "cost_usd") for r in rows)
    tok_in = sum(_f(r, "in") for r in rows)
    tok_out = sum(_f(r, "out") for r in rows)
    groups = {"source": defaultdict(lambda: [0.0, 0]),
              "agent": defaultdict(lambda: [0.0, 0]),
              "model": defaultdict(lambda: [0.0, 0]),
              "day": defaultdict(lambda: [0.0, 0])}
    for r in rows:
        for key, field in (("source", r.get("source", "?")), ("agent", r.get("agent", "?")),
                           ("model", r.get("model", "?")), ("day", (r.get("ts", "") or "?")[:10])):
            groups[key][field][0] += _f(r, "cost_usd")
            groups[key][field][1] += 1
    days = sorted(groups["day"])
    real = [d for d in days if len(d) == 10 and d[:4].isdigit()]
    span = f"{real[0]} -> {real[-1]}" if real else "n/a"
    n = len(rows)
    # A "row" is one ledger line. Since 2026-06-29, a cycle emits one row PER MODEL it touched
    # (agent_cycle.py reads ResultMessage.model_usage), so cycle rows no longer map 1:1 to wakes —
    # the unit here is rows, not wakes. Cost SUMS are unaffected (per-model costUSD partitions the
    # wake total); only the counts mean "model-rows". Offload rows stay one-per-call.
    print(f"=== spend ledger — {n} rows, {span} ===")
    print(f"TOTAL ${total:.2f}   avg ${total / n:.4f}/row   tokens: in {int(tok_in):,} / out {int(tok_out):,}")
    for title in ("source", "agent", "model"):
        print(f"\nby {title}:")
        for k in sorted(groups[title], key=lambda x: groups[title][x][0], reverse=True):
            c, cnt = groups[title][k]
            print(f"  {k:<12} ${c:8.3f}  {cnt:>4} rows  ${c / cnt:.4f}/row")
    print("\ndaily trend:")
    peak = max((v[0] for v in groups["day"].values()), default=1.0) or 1.0
    for d in days:
        c, cnt = groups["day"][d]
        print(f"  {d}  ${c:8.3f}  {cnt:>4} rows  {'#' * int(c / peak * 32)}")


def main():
    ap = argparse.ArgumentParser(description="org spend ledger: total + trend ALL Claude spend")
    ap.add_argument("--ledger", default=LEDGER)
    ap.add_argument("--csv", default="", help="also export a headered CSV to this path")
    ap.add_argument("--import-logs", action="store_true", dest="import_logs",
                    help="one-time, single-process: seed historical cycle spend from agent-*.log")
    ap.add_argument("--logs", default=DEFAULT_LOGS)
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()
    if a.import_logs:
        import_logs(a.ledger, a.logs, a.force)
    rows = load(a.ledger)
    if a.csv:
        export_csv(rows, a.csv)
    report(rows)


if __name__ == "__main__":
    main()
