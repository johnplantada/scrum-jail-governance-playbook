#!/usr/bin/env python3
"""Resolve a brain tier (haiku|sonnet|opus) to the pinned API model id.

org-chart.yaml `global.model_ids` is the single source of truth; the FALLBACK map below
only covers a chart that predates the key. Anything already shaped like a full model id
passes through unchanged, so a caller can pin an exact id in an emergency without
touching the chart. Tiers stay the org's vocabulary everywhere else (ledger, banners,
brownouts, 💎) — resolution happens only at the CLI/SDK invocation boundary.

Usage: model_id.py <tier-or-id>    → prints the model id
"""
import sys

FALLBACK = {
    "haiku": "claude-haiku-4-5",
    "sonnet": "claude-sonnet-5",
    "opus": "claude-opus-4-8",
}


def resolve(tier: str) -> str:
    t = (tier or "").strip().lower()
    if t.startswith("claude-"):
        return t  # already a full id — pass through
    ids = {}
    try:
        import yaml
        chart = yaml.safe_load(open("org-chart.yaml")) or {}
        ids = (chart.get("global") or {}).get("model_ids") or {}
    except Exception:
        pass  # missing chart/pyyaml → fall back to the pinned defaults below
    return ids.get(t) or FALLBACK.get(t) or t


if __name__ == "__main__":
    print(resolve(sys.argv[1] if len(sys.argv) > 1 else "sonnet"))
