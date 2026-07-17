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

if failures:
    print(f"\n{len(failures)} FAILED: {failures}")
    sys.exit(1)
print("\nall spend_log per-model metering checks passed")
