#!/usr/bin/env python3
"""handoff_check.py — validate typed handoff payloads
([AGREEMENT] / [DEMO] / [CODEREVIEW] / [CLOSE]).

DESIGN.md §4's "planned Actions validator", now real — the successor to the chat-era
Go validator (services/common/protocol/handoff.go, demolished 2026-07-05). Counter-
ratchet: this replaces that validator and the Warden's malformed-payload citation,
nothing else. The handoff-validator workflow runs this against every issue/PR comment
that leads a line with a handoff marker; a malformed payload fails the check run and
gets a reply naming the missing keys, so schema drift is caught at post time.

REQUIRED below is the authoritative schema. agents/_policy.md §handoffs documents it
for the mandates, and test_handoff_check.py asserts the two never drift apart.

A marker only counts when it LEADS A PARAGRAPH — the body's first line, or a line
right after a blank one. Handoff posts lead with their tag (after the identity banner
and its blank line), so every compliant handoff qualifies; prose that merely mentions
"[DEMO]" is ignored — including when a hard line-wrap happens to land the marker at
the start of a mid-paragraph line, the false positive that cost business#129 a cycle
(any-line-start was the original rule; wrapped agent prose made it fire on mentions).

Usage: handoff_check.py            # comment/review body on stdin
Exit:  0 = no handoff marker, or every marker's payload is valid
       1 = a marker is present but its payload is missing/malformed (problems on stdout)
"""
import re
import sys

REQUIRED = {
    "AGREEMENT": ("plan", "owners", "acceptance", "tickets"),
    "DEMO": ("pr", "evidence_run", "acceptance", "ci"),
    "CODEREVIEW": ("pr", "head_sha", "verdict", "findings", "review_url", "evidence_run"),
    # The work-item tree's upward path (scripts/workitems.py): evidence is a mapping
    # whose right contents depend on kind — that's the facts layer's call, not shape's.
    "CLOSE": ("item", "kind", "evidence"),
}

MARKER = re.compile(r"^\s*\[(AGREEMENT|DEMO|CODEREVIEW|CLOSE)\]")
FENCE = re.compile(r"```ya?ml\s*\n(.*?)```", re.DOTALL)


# --- pure helpers (unit-tested in test_handoff_check.py; yaml only touches main) ---

def markers(body):
    """Distinct handoff types whose marker leads a paragraph (first line of the body,
    or the previous line is blank), sorted for stable output. Mid-paragraph line
    starts don't count — that's where hard-wrapped prose mentions land."""
    lines = (body or "").split("\n")
    found = set()
    for i, line in enumerate(lines):
        m = MARKER.match(line)
        if m and (i == 0 or not lines[i - 1].strip()):
            found.add(m.group(1))
    return sorted(found)


def fenced_blocks(body):
    """Raw text of every ```yaml fenced block in the body."""
    return FENCE.findall(body or "")


def validate(types, payloads):
    """Problem strings for the given marker types against parsed payload mappings.

    payloads: list of dicts (already yaml-parsed; non-mappings filtered by caller).
    A type is satisfied if ANY payload carries all its required keys; otherwise the
    closest payload's missing keys are reported. Empty problem list = valid.
    """
    problems = []
    for t in types:
        keys = REQUIRED[t]
        if not payloads:
            problems.append(f"[{t}]: no fenced yaml payload found "
                            f"(required keys: {', '.join(keys)})")
            continue
        best_missing = None
        for p in payloads:
            missing = [k for k in keys if k not in p]
            if not missing:
                best_missing = None
                break
            if best_missing is None or len(missing) < len(best_missing):
                best_missing = missing
        if best_missing:
            problems.append(f"[{t}]: payload missing required keys: "
                            f"{', '.join(best_missing)}")
    return problems


# --- I/O ---------------------------------------------------------------------------

def main():
    body = sys.stdin.read()
    types = markers(body)
    if not types:
        return 0
    import yaml
    payloads = []
    for raw in fenced_blocks(body):
        try:
            data = yaml.safe_load(raw)
        except yaml.YAMLError as exc:
            print(f"unparseable yaml payload: {exc}")
            return 1
        if isinstance(data, dict):
            payloads.append(data)
    problems = validate(types, payloads)
    for p in problems:
        print(p)
    if problems:
        print(f"schema: scripts/handoff_check.py REQUIRED "
              f"(documented in agents/_policy.md §handoffs)")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
