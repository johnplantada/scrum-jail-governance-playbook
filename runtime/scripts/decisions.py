#!/usr/bin/env python3
"""decisions.py — read and validate the decisions ledger (GITHUB-NATIVE-PLAN.md Phase 4).

  scripts/decisions.py check   # exit 1 with problems if the ledger is malformed (CI runs this)
  scripts/decisions.py list    # print the ledger newest-first

The ledger's integrity IS the decision record's integrity, so CI refuses a merge that
would corrupt it — unique ids forever, required fields, known types. Validation is a
pure function (unit-tested in test_decisions.py); yaml only touches main().
"""
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATH = os.path.join(REPO, "decisions.yaml")

TYPES = ("spend", "charter", "promote", "sunset")
REQUIRED = ("id", "type", "dept", "what", "why", "cost_usd", "chairman_minutes",
            "reversibility", "unblocks", "proposed")


# --- pure helpers (unit-tested in test_decisions.py; no I/O) -----------------------------

def validate(data):
    """Problem strings for a malformed ledger; empty list = valid."""
    problems = []
    entries = (data or {}).get("decisions")
    if entries is None:
        return ["missing top-level 'decisions' key"]
    if not isinstance(entries, list):
        return ["'decisions' must be a list"]
    seen = set()
    for i, d in enumerate(entries):
        tag = f"decisions[{i}]"
        if not isinstance(d, dict):
            problems.append(f"{tag}: not a mapping")
            continue
        did = d.get("id")
        tag = f"decisions[{i}] ({did or '?'})"
        if did in seen:
            problems.append(f"{tag}: duplicate id — ids are unique forever")
        seen.add(did)
        for field in REQUIRED:
            if field not in d or d.get(field) in (None, ""):
                problems.append(f"{tag}: missing required field '{field}'")
        if d.get("type") not in TYPES:
            problems.append(f"{tag}: type must be one of {'|'.join(TYPES)}")
        try:
            float(d.get("cost_usd", 0))
        except (TypeError, ValueError):
            problems.append(f"{tag}: cost_usd must be a number")
        if d.get("type") in ("charter", "promote", "sunset") and not d.get("payload"):
            problems.append(f"{tag}: {d.get('type')} needs the org-shape payload")
    return problems


def render(entries):
    """One line per decision, newest-first by proposed date."""
    out = []
    for d in sorted(entries or [], key=lambda x: str(x.get("proposed") or ""), reverse=True):
        out.append(f"- {d.get('proposed', '?')}  [{d.get('type', '?')}] {d.get('id', '?')} "
                   f"(${d.get('cost_usd', '?')}, {d.get('dept', '?')}) — "
                   f"{' '.join(str(d.get('what') or '').split())[:120]}")
    return "\n".join(out)


# --- I/O ----------------------------------------------------------------------------------

def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "check"
    import yaml
    try:
        with open(PATH, encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
    except OSError as exc:
        sys.exit(f"decisions: cannot read {PATH}: {exc}")
    except yaml.YAMLError as exc:
        sys.exit(f"decisions: {PATH} is not valid yaml: {exc}")
    if cmd == "check":
        problems = validate(data)
        for p in problems:
            print(f"decisions: {p}", file=sys.stderr)
        if problems:
            sys.exit(1)
        print(f"decisions ledger: valid ({len(data.get('decisions') or [])} entries)")
    elif cmd == "list":
        print(render(data.get("decisions")) or "(no decisions yet)")
    else:
        sys.exit("usage: decisions.py <check | list>")


if __name__ == "__main__":
    main()
