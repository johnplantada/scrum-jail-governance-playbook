#!/usr/bin/env python3
"""spend_log.py — append ONE spend event to the unified ledger (state/spend.jsonl). Every Claude
call the org makes records a row here:
  - a full agent wake  → written by scripts/agent_cycle.py  (source=cycle, via=sdk)
  - every offload       → written by scripts/spend_offload.py (source=offload, via=cli)
so scripts/costs.py can total and trend ALL spend, not just the main cycles. Subagents spawned
via the Agent/Task tool roll INTO their parent cycle's cost (one SDK session), so they are not
double-counted; offloads are separate `claude -p` processes, so they ARE counted on their own.

Append-only JSONL: a single-line append is atomic across concurrent agents, self-describing (no
header race), and trivially parsed. Writes are best-effort — a logging failure never raises, so it
can never break an agent cycle.

Used as a library (`from spend_log import append`) and as a CLI (--source … --cost … flags)."""
import argparse
import datetime
import json
import os
import sys

# Anchor the default ledger at the repo root so metering never depends on the caller's CWD.
# agent-run.sh cd's to root today, but an offload (or any future caller) invoked from elsewhere
# must still record to the one canonical ledger. An explicit $SPEND_LEDGER always wins.
LEDGER = os.environ.get("SPEND_LEDGER") or os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "state", "spend.jsonl")


def append(*, source, agent="", model="", cost_usd=0.0, status="ok", wake="", turns=0,
           in_=0, out=0, cache_read=0, cache_creation=0, via="", ts=None, path=None,
           wake_id=None, outcome=""):
    """Append one spend event. Best-effort: never raises."""
    try:
        # Wake correlation: every artifact of one wake carries the same id (agent-run.sh
        # exports WAKE_ID), so `wake-trace.sh <id>` can join spend to logs to bus posts.
        if wake_id is None:
            wake_id = os.environ.get("WAKE_ID", "")
        row = {
            "ts": ts or datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "source": source,
            "agent": agent or "",
            "model": model or "",
            "wake": wake or "",
            "turns": int(turns or 0),
            "in": int(in_ or 0),
            "out": int(out or 0),
            "cache_read": int(cache_read or 0),
            "cache_creation": int(cache_creation or 0),
            "cost_usd": round(float(cost_usd or 0.0), 6),
            "status": status or "ok",
            "via": via or "",
        }
        if wake_id:
            row["wake_id"] = wake_id
        # Phase-0 yield instrumentation (docs/plans/token-efficiency.md): what the wake
        # DID — ship | post | noop, classified by wake_outcome.py. Only cycle rows carry
        # it (on the primary-brain row), so absence keeps old rows and offloads unchanged.
        if outcome:
            row["outcome"] = outcome
        p = path or LEDGER
        d = os.path.dirname(p)
        if d:
            os.makedirs(d, exist_ok=True)
        with open(p, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row) + "\n")
        return row
    except Exception as exc:  # logging must never break a caller
        print(f"spend_log: append failed: {exc}", file=sys.stderr)
        return None


def usage_tokens(u):
    """Pull the four token counts out of a usage dict (same shape from the SDK and the CLI json).
    Coerces any non-dict input (None or unexpected) to empty so it can never raise into a caller."""
    if not isinstance(u, dict):
        u = {}
    return dict(
        in_=u.get("input_tokens", 0),
        out=u.get("output_tokens", 0),
        cache_read=u.get("cache_read_input_tokens", 0),
        cache_creation=u.get("cache_creation_input_tokens", 0),
    )


def normalize_model(model_id):
    """Map a full model id (claude-sonnet-4-6, claude-haiku-4-5-20251001, claude-opus-4-8) to the
    short tier the ledger uses everywhere else (the cycle's argv model + the offload TIER), so the
    `by model` rollup stays consistent across cycle, offload, and historical rows. Unknown ids pass
    through unchanged rather than being lost to a placeholder."""
    m = (model_id or "").lower()
    for tier in ("haiku", "sonnet", "opus"):
        if tier in m:
            return tier
    return model_id or ""


def _model_usage_tokens(u):
    """Token counts out of ONE ResultMessage.model_usage entry. NOTE: model_usage uses camelCase
    keys (inputTokens, …), unlike the snake_case `usage` dict that usage_tokens() reads."""
    if not isinstance(u, dict):
        u = {}
    return dict(
        in_=u.get("inputTokens", 0),
        out=u.get("outputTokens", 0),
        cache_read=u.get("cacheReadInputTokens", 0),
        cache_creation=u.get("cacheCreationInputTokens", 0),
    )


def model_usage_rows(model_usage, *, primary_model="", num_turns=0):
    """Expand a ResultMessage.model_usage dict into one per-model row payload each.

    Returns a list of dicts (model, cost_usd, turns, in_, out, cache_read, cache_creation) — the
    per-call fields for append(); the caller adds source/agent/wake/via/status. The per-model
    costUSD values PARTITION the cycle's total_cost_usd, so summing the rows reproduces the cycle
    total exactly (no double-counting). `turns` is a cycle-level scalar, so it is attributed to the
    primary-brain row only (0 elsewhere) — sum(turns) == num_turns.

    Returns [] when model_usage is empty / not a dict, so the caller can fall back to a single
    scalar row (older CLIs without a breakdown)."""
    if not isinstance(model_usage, dict) or not model_usage:
        return []
    primary = normalize_model(primary_model)
    # Deterministic order: primary brain first, then remaining models by cost descending.
    items = sorted(
        model_usage.items(),
        key=lambda kv: (normalize_model(kv[0]) != primary,
                        -float((kv[1] or {}).get("costUSD", 0) or 0)),
    )
    rows = []
    turns_assigned = False
    for model_id, u in items:
        tier = normalize_model(model_id)
        u = u if isinstance(u, dict) else {}
        give_turns = 0
        if not turns_assigned and tier == primary:
            give_turns = int(num_turns or 0)
            turns_assigned = True
        rows.append(dict(model=tier, cost_usd=float(u.get("costUSD", 0) or 0),
                         turns=give_turns, **_model_usage_tokens(u)))
    # Primary brain absent from the breakdown (edge): attribute turns to the highest-cost row.
    if not turns_assigned and rows:
        rows[0]["turns"] = int(num_turns or 0)
    return rows


def main():
    ap = argparse.ArgumentParser(description="append one spend event to state/spend.jsonl")
    ap.add_argument("--source", required=True)
    ap.add_argument("--agent", default="")
    ap.add_argument("--model", default="")
    ap.add_argument("--wake", default="")
    ap.add_argument("--via", default="")
    ap.add_argument("--status", default="ok")
    ap.add_argument("--cost", type=float, default=0.0)
    ap.add_argument("--turns", type=int, default=0)
    ap.add_argument("--usage-json", default="", help="a usage dict from claude --output-format json")
    ap.add_argument("--in", dest="in_", type=int, default=0)
    ap.add_argument("--out", type=int, default=0)
    ap.add_argument("--cache-read", type=int, default=0)
    ap.add_argument("--cache-creation", type=int, default=0)
    a = ap.parse_args()
    toks = dict(in_=a.in_, out=a.out, cache_read=a.cache_read, cache_creation=a.cache_creation)
    if a.usage_json:
        try:
            toks = usage_tokens(json.loads(a.usage_json))
        except Exception:
            pass
    append(source=a.source, agent=a.agent, model=a.model, wake=a.wake, via=a.via,
           status=a.status, cost_usd=a.cost, turns=a.turns, **toks)


if __name__ == "__main__":
    main()
