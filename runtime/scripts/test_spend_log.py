#!/usr/bin/env python3
"""test_spend_log.py — stdlib-only checks for the per-model spend metering (no pytest needed; runs
in CI via `python3 scripts/test_spend_log.py`). The load-bearing invariant: the per-model rows a
cycle emits must PARTITION the wake's total_cost_usd (sum of row costs == total) and its turns
(sum of row turns == num_turns), so splitting by model never double-counts or loses spend."""
import sys

import spend_log

EPS = 1e-9
failures = []


def check(name, cond):
    if cond:
        print(f"ok   - {name}")
    else:
        print(f"FAIL - {name}")
        failures.append(name)


# A real two-model ResultMessage.model_usage sample (a no-fan-out sonnet cycle that also touched a
# helper haiku), captured live from the SDK. costUSD values sum exactly to total_cost_usd.
SAMPLE_2 = {
    "claude-haiku-4-5-20251001": {
        "inputTokens": 508, "outputTokens": 10, "cacheReadInputTokens": 0,
        "cacheCreationInputTokens": 0, "costUSD": 0.000558,
    },
    "claude-sonnet-4-6": {
        "inputTokens": 3, "outputTokens": 4, "cacheReadInputTokens": 13802,
        "cacheCreationInputTokens": 2411, "costUSD": 0.0186756,
    },
}
SAMPLE_2_TOTAL = 0.0192336

# A three-model fan-out: sonnet brain delegates to haiku + opus workers inside one cycle.
SAMPLE_3 = {
    "claude-sonnet-4-6": {"inputTokens": 100, "outputTokens": 50, "cacheReadInputTokens": 0,
                          "cacheCreationInputTokens": 0, "costUSD": 0.02},
    "claude-haiku-4-5-20251001": {"inputTokens": 200, "outputTokens": 80, "cacheReadInputTokens": 0,
                                  "cacheCreationInputTokens": 0, "costUSD": 0.001},
    "claude-opus-4-8": {"inputTokens": 300, "outputTokens": 120, "cacheReadInputTokens": 0,
                        "cacheCreationInputTokens": 0, "costUSD": 0.30},
}
SAMPLE_3_TOTAL = 0.321


# --- normalize_model ---
check("normalize sonnet id", spend_log.normalize_model("claude-sonnet-4-6") == "sonnet")
check("normalize haiku id", spend_log.normalize_model("claude-haiku-4-5-20251001") == "haiku")
check("normalize opus id", spend_log.normalize_model("claude-opus-4-8") == "opus")
check("normalize unknown passes through", spend_log.normalize_model("gpt-x") == "gpt-x")
check("normalize None -> empty", spend_log.normalize_model(None) == "")

# --- empty / non-dict -> [] (caller falls back to a single scalar row) ---
check("None model_usage -> []", spend_log.model_usage_rows(None, primary_model="sonnet") == [])
check("empty model_usage -> []", spend_log.model_usage_rows({}, primary_model="sonnet") == [])
check("non-dict model_usage -> []", spend_log.model_usage_rows("nope", primary_model="sonnet") == [])

# --- two-model sample: partition cost, turns on primary only, tokens mapped from camelCase ---
rows2 = spend_log.model_usage_rows(SAMPLE_2, primary_model="sonnet", num_turns=7)
check("2-model -> 2 rows", len(rows2) == 2)
check("2-model cost partitions total",
      abs(sum(r["cost_usd"] for r in rows2) - SAMPLE_2_TOTAL) < EPS)
check("2-model turns sum == num_turns", sum(r["turns"] for r in rows2) == 7)
by2 = {r["model"]: r for r in rows2}
check("2-model primary (sonnet) carries all turns", by2["sonnet"]["turns"] == 7)
check("2-model helper (haiku) carries 0 turns", by2["haiku"]["turns"] == 0)
check("2-model primary listed first", rows2[0]["model"] == "sonnet")
check("2-model token mapping (sonnet cache_read)", by2["sonnet"]["cache_read"] == 13802)
check("2-model token mapping (sonnet cache_creation)", by2["sonnet"]["cache_creation"] == 2411)
check("2-model token mapping (haiku in_)", by2["haiku"]["in_"] == 508)

# --- three-model fan-out: still a clean partition; turns only on the brain ---
rows3 = spend_log.model_usage_rows(SAMPLE_3, primary_model="sonnet", num_turns=12)
check("3-model -> 3 rows", len(rows3) == 3)
check("3-model cost partitions total",
      abs(sum(r["cost_usd"] for r in rows3) - SAMPLE_3_TOTAL) < EPS)
check("3-model turns sum == num_turns", sum(r["turns"] for r in rows3) == 12)
by3 = {r["model"]: r for r in rows3}
check("3-model turns only on sonnet brain",
      by3["sonnet"]["turns"] == 12 and by3["haiku"]["turns"] == 0 and by3["opus"]["turns"] == 0)

# --- primary brain absent from breakdown (edge): turns land on the highest-cost row ---
rows_edge = spend_log.model_usage_rows(SAMPLE_3, primary_model="haiku-not-present-as-id-xyz",
                                       num_turns=5)
# "haiku-not-present-as-id-xyz" normalizes to haiku (contains 'haiku'); haiku IS present, so it's
# not actually the absent-primary case. Use a truly-absent primary instead:
rows_edge = spend_log.model_usage_rows(
    {"claude-sonnet-4-6": {"costUSD": 0.02}, "claude-opus-4-8": {"costUSD": 0.30}},
    primary_model="gemini", num_turns=5)
check("absent-primary: turns sum still == num_turns", sum(r["turns"] for r in rows_edge) == 5)
check("absent-primary: turns on highest-cost row (opus)",
      rows_edge[0]["model"] == "opus" and rows_edge[0]["turns"] == 5)

# --- identity fields: the exact model id, kept ALONGSIDE the collapsed tier -----------
# `model` is lossy where it matters: rates diverge within a tier (opus-4-5 is 5/25,
# opus-4-1 is 15/75) and one tier row can blend several ids, so a tier row's implied
# $/token is not any model's real rate. Downstream cost auditing needs the exact id.
by2 = {r["model"]: r for r in rows2}
check("model_id carries the exact billed id",
      by2["haiku"]["model_id"] == "claude-haiku-4-5-20251001"
      and by2["sonnet"]["model_id"] == "claude-sonnet-4-6")
check("model tier is unchanged (rollups keep working)",
      sorted(by2) == ["haiku", "sonnet"])

# --- optional row fields are OMITTED when empty ---------------------------------------
# Old rows must stay byte-comparable and every reader uses .get()-style access, so a new
# field may never appear as an empty placeholder.
import json
import os
import tempfile

with tempfile.TemporaryDirectory() as d:
    p = os.path.join(d, "spend.jsonl")
    spend_log.append(source="cycle", agent="ceo", model="sonnet", path=p, wake_id="")
    bare = json.loads(open(p).read().strip())
    check("bare row omits every optional identity field",
          not ({"model_id", "session_id", "duration_ms", "duration_api_ms",
                "api_error_status"} & set(bare)))

    p2 = os.path.join(d, "spend2.jsonl")
    spend_log.append(source="cycle", agent="ceo", model="sonnet", path=p2, wake_id="w-x",
                     model_id="claude-sonnet-5", session_id="sess-abc",
                     duration_ms=1234, duration_api_ms=567, api_error_status=429)
    full = json.loads(open(p2).read().strip())
    check("populated row carries model_id", full.get("model_id") == "claude-sonnet-5")
    check("populated row carries session_id", full.get("session_id") == "sess-abc")
    check("populated row carries durations",
          full.get("duration_ms") == 1234 and full.get("duration_api_ms") == 567)
    check("populated row carries api_error_status", full.get("api_error_status") == 429)
    # 0 is a real duration and 0 a real status — neither may be dropped as falsy.
    p3 = os.path.join(d, "spend3.jsonl")
    spend_log.append(source="cycle", agent="ceo", path=p3, duration_ms=0, api_error_status=0)
    zero = json.loads(open(p3).read().strip())
    check("zero duration/status are kept, not treated as absent",
          zero.get("duration_ms") == 0 and zero.get("api_error_status") == 0)

check("append still never raises on bad input",
      spend_log.append(source="cycle", path="/nonexistent-dir-xyz/\0/spend.jsonl") is None)

if failures:
    print(f"\n{len(failures)} FAILED: {failures}")
    sys.exit(1)
print("\nall spend_log per-model metering checks passed")
