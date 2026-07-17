#!/usr/bin/env python3
"""spend_offload.py — the offload cost shim. Reads `claude -p --output-format json` on stdin,
prints the text result to stdout (preserving the contract offload callers depend on), and appends
a spend row (cost + token stats) to the unified ledger. Invoked from offload.sh:

    claude -p "$prompt" --model "$tier" --output-format json \
      | AGENT="$AGENT_NAME" TIER="$tier" python3 scripts/spend_offload.py

IMPORTANT — exit code: this shim ALWAYS exits 0. A claude result with an in-band `"is_error": true`
(refusal, max-turns, overload) is still a usable text result and is recorded with status=error; it
must NOT become a nonzero exit, or `set -e` in a caller (e.g. agent-run.sh's broadcast pre-gate,
which captures `$(offload.sh …)`) would abort the whole wake instead of failing open. A genuine
claude *process* failure exits nonzero on its own and propagates via offload.sh's `pipefail` — the
caller guards that with `|| true`."""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import spend_log  # noqa: E402

raw = sys.stdin.read()

try:
    d = json.loads(raw)
except Exception:
    # Not JSON (e.g. an auth/transport error wrote text, or empty stdin). Pass whatever we got to
    # stdout so the caller still has something, and leave a zero-cost error marker in the ledger so
    # the failed offload is visible rather than vanishing. (A real process failure already surfaced
    # via claude's own nonzero exit + offload.sh pipefail.)
    sys.stdout.write(raw)
    spend_log.append(source="offload", agent=os.environ.get("AGENT", ""),
                     model=os.environ.get("TIER", ""), via="cli", status="error", cost_usd=0.0)
    sys.exit(0)

sys.stdout.write(d.get("result") or "")
spend_log.append(
    source="offload",
    agent=os.environ.get("AGENT", ""),
    model=os.environ.get("TIER", ""),
    via="cli",
    status="error" if d.get("is_error") else "ok",
    cost_usd=d.get("total_cost_usd") or 0.0,
    turns=d.get("num_turns") or 0,
    **spend_log.usage_tokens(d.get("usage")),
)
sys.exit(0)
