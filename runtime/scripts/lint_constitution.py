#!/usr/bin/env python3
"""Constitution linter — fails CI when the org's prose drifts from its single sources
of truth. The org's rules live in one place each (org-chart.yaml for parameters,
.claude/skills/ for ritual procedure); the constitution (DESIGN.md) and the agent
mandates (agents/*.md) must REFERENCE those sources, not restate their values. Every
check below encodes a drift class that actually happened:

  1. cadence literals   — "every 5 wakes" / "5-cycle review" hardcoded in seven files
                          while org-chart.yaml said review_interval: 20
  2. stage canon        — the constitution and agents/it.md disagreed on the kanban
                          stage list and order; the canon is org-chart.yaml
                          global.pm_stages
  3. skill references   — mandates invoked skills that weren't version-controlled

(The chat-era linter also cross-checked documented CLI flags and handoff schemas
against the Go services; those retired with the services. The typed-handoff schema now
lives in scripts/handoff_check.py, enforced by the handoff-validator workflow;
test_handoff_check.py keeps the _policy.md §handoffs documentation in sync.)

Scope: DESIGN.md, README.md, agents/*.md, .claude/skills/*/SKILL.md, docs/*.md.
Deliberately NOT scanned: playbook/ (vendored from the governance golden — fix it there
and `make sync-playbook`), content/ (marketing copy tells stories about past
configurations), scripts/ (code comments).

Usage: python3 scripts/lint_constitution.py   (exit 0 clean, 1 with findings on stderr)
"""
import glob
import os
import re
import sys

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

# --- check 1: hardcoded cadence numbers -------------------------------------------

CADENCE_PATTERNS = [
    (re.compile(r"(?i)\bevery\s+\d+\s+wakes?\b"),
     "hardcoded review cadence — write 'every review interval' and reference "
     "global.review_interval (org-chart.yaml)"),
    (re.compile(r"(?i)\b\d+-cycle(?:-review)?s?\b"),
     "hardcoded review cadence ('N-cycle') — the interval lives in "
     "org-chart.yaml global.review_interval only"),
    (re.compile(r"(?i)\bPI\s*=\s*\d+\s+iterations\b"),
     "hardcoded PI length — reference global.pi_interval (org-chart.yaml)"),
    (re.compile(r"(?i)~\s*\d+\s+wakes\b"),
     "hardcoded wake math — derive from review_interval × pi_interval, don't restate it"),
    (re.compile(r"(?i)\b\d+\s+wakes?\s+per\s+PI\b"),
     "hardcoded wake math — derive from review_interval × pi_interval, don't restate it"),
]


def check_cadence(path, text):
    findings = []
    for lineno, line in enumerate(text.splitlines(), 1):
        for pat, msg in CADENCE_PATTERNS:
            m = pat.search(line)
            if m:
                findings.append(f"{path}:{lineno}: cadence literal {m.group(0)!r} — {msg}")
    return findings


# --- check 2: kanban stage canon ---------------------------------------------------

def _flow_list(chart_text, key):
    m = re.search(rf"^\s*{re.escape(key)}:\s*\[([^\]]+)\]", chart_text, re.M)
    if not m:
        return []
    return [s.strip().strip("'\"") for s in m.group(1).split(",")]


def canonical_stages(chart_text):
    """Extract global.pm_stages from org-chart.yaml (the ordered flow list)."""
    return _flow_list(chart_text, "pm_stages")


def holding_stages(chart_text):
    """Extract global.pm_holding_stages — valid Status board columns that are NOT part
    of the ordered flow (Blocked/On Hold). Recognized so prose may name them without
    the ordered-flow check treating them as non-canonical."""
    return _flow_list(chart_text, "pm_holding_stages")


def terminal_stages(chart_text):
    """Extract global.pm_terminal_stages — the won't-do outcomes (Dropped). Like holding
    columns: valid names with no flow position."""
    return _flow_list(chart_text, "pm_terminal_stages")


# A chain element is at most TWO words — the widest canonical name ("In Progress",
# "Awaiting Merge") — so an element can't swallow the surrounding prose wholesale.
ARROW_CHAIN = re.compile(
    r"[\w`*\"-]+(?:[ \t][\w`*\"-]+)?(?:\s*→\s*[\w`*\"-]+(?:[ \t][\w`*\"-]+)?)+")


def _canon_token(tok, valid_lower):
    """Normalize one chain element. Two-word elements mean the regex can absorb one word
    of surrounding prose ("moves Todo → …" → element "moves Todo"; "→ Done` on" →
    "Done on"); when the element isn't a valid name but a word-boundary suffix or prefix
    of it is, judge that — the prose word belongs to the sentence, not the chain."""
    t = tok.strip("`*\"' ")
    if t.lower() in valid_lower:
        return t
    words = [w.strip("`*\"'") for w in t.split()]
    if len(words) > 1:
        for cand in (" ".join(words[-2:]), words[-1], " ".join(words[:2]), words[0]):
            if cand.lower() in valid_lower:
                return cand
    return t


def check_stages(path, text, stages, holding=(), terminal=()):
    """Arrow chains naming ≥2 canonical flow stages must use only VALID status names, in
    canonical flow order. Holding columns (pm_holding_stages: Blocked/On Hold) and
    terminal-alternates (pm_terminal_stages: Dropped) are valid names but carry no flow
    position, so they're allowed in a chain yet skipped by the order check. A line
    claiming to list 'workflow stages' must name them all (or point at pm_stages) —
    checked against the line plus its continuation line, since prose wraps."""
    findings = []
    canon_lower = [s.lower() for s in stages]
    valid_lower = canon_lower + [h.lower() for h in holding] + [t.lower() for t in terminal]
    lines = text.splitlines()
    for lineno, line in enumerate(lines, 1):
        for chain in ARROW_CHAIN.findall(line):
            tokens = [_canon_token(t, valid_lower) for t in chain.split("→")]
            hits = [t for t in tokens if t.lower() in canon_lower]
            if len(hits) < 2:
                continue  # not a stage chain (e.g. poll → route → wake)
            bad = [t for t in tokens if t.lower() not in valid_lower]
            if bad:
                findings.append(
                    f"{path}:{lineno}: stage chain names non-canonical stage(s) {bad} — "
                    f"canon is org-chart.yaml pm_stages: {stages}")
                continue
            # Order is only defined over flow stages; holding columns carry no position.
            idx = [canon_lower.index(t.lower()) for t in tokens if t.lower() in canon_lower]
            if idx != sorted(idx):
                findings.append(
                    f"{path}:{lineno}: stage chain out of canonical order {tokens} — "
                    f"canon is org-chart.yaml pm_stages: {stages}")
        if re.search(r"(?i)workflow stages", line):
            window = line + " " + (lines[lineno] if lineno < len(lines) else "")
            if "pm_stages" not in window and not all(
                    re.search(re.escape(s), window, re.I) for s in stages):
                findings.append(
                    f"{path}:{lineno}: 'workflow stages' claim doesn't list the full canon "
                    f"{stages} or reference pm_stages (org-chart.yaml)")
    return findings


# --- check 3: referenced skills are version-controlled -----------------------------

# A skill reference is a backticked or bolded name followed by "skill" (optionally
# "domain skill"). The bold form requires a hyphenated name so prose like "**every**
# skill" can't false-positive — all real skill names are kebab-case. The bold+"domain
# skill" form is the exact shape that dangled undetected: two mandates named their
# authoritative tool in bold while no .claude/skills/ copy existed, and CI stayed green.
SKILL_REF = re.compile(
    r"(?:`([a-z][a-z0-9-]*)`|\*\*([a-z][a-z0-9]*(?:-[a-z0-9]+)+)\*\*)\s+(?:domain\s+)?skill")


def check_skills(path, text, root):
    findings = []
    for lineno, line in enumerate(text.splitlines(), 1):
        for m in SKILL_REF.finditer(line):
            name = m.group(1) or m.group(2)
            candidates = [
                os.path.join(root, ".claude", "skills", name, "SKILL.md"),
                os.path.join(root, "agents", "skills", f"{name}.md"),
            ]
            if not any(os.path.isfile(c) for c in candidates):
                findings.append(
                    f"{path}:{lineno}: references the `{name}` skill but no "
                    f".claude/skills/{name}/SKILL.md (or agents/skills/{name}.md) exists")
    return findings


# --- driver -------------------------------------------------------------------------

def scanned_files(root):
    files = [os.path.join(root, "DESIGN.md"), os.path.join(root, "README.md")]
    files += sorted(glob.glob(os.path.join(root, "agents", "*.md")))
    files += sorted(glob.glob(os.path.join(root, ".claude", "skills", "*", "SKILL.md")))
    files += sorted(glob.glob(os.path.join(root, "docs", "*.md")))
    return [f for f in files if os.path.isfile(f)]


def run(root=ROOT):
    with open(os.path.join(root, "org-chart.yaml"), encoding="utf-8") as fh:
        chart = fh.read()
    stages = canonical_stages(chart)
    holding = holding_stages(chart)
    terminal = terminal_stages(chart)
    if not stages:
        return ["org-chart.yaml: global.pm_stages missing — the stage canon must live there"]
    findings = []
    for f in scanned_files(root):
        with open(f, encoding="utf-8") as fh:
            text = fh.read()
        rel = os.path.relpath(f, root)
        findings += check_cadence(rel, text)
        findings += check_stages(rel, text, stages, holding, terminal)
        findings += check_skills(rel, text, root)
    return findings


def main():
    findings = run()
    if findings:
        print("constitution lint: FAIL", file=sys.stderr)
        for f in findings:
            print(f"  {f}", file=sys.stderr)
        print(f"  {len(findings)} finding(s) — prose drifted from its source of truth; "
              "fix the doc to reference the source (or fix the source)", file=sys.stderr)
        return 1
    print("constitution lint: clean")
    return 0


if __name__ == "__main__":
    sys.exit(main())
