#!/usr/bin/env python3
"""workitems.py — the work-item tree adapter (OBJECTIVE → EPIC → FEATURE → STORY).

The decomposition hierarchy lives in native GitHub sub-issues:

    [OBJECTIVE] → [PROPOSAL]*            competing means; epics descend from the ACCEPTED one
    [OBJECTIVE] → [EPIC] → [FEATURE] → [STORY]     the delivery tree

A story's plan is a `## Plan` section in the story body — deliberately not a fifth
issue level. Kind is carried by the plain labels the org already uses (objective,
proposal, epic, feature, story); issue types aren't available on a personal account.

Decomposing downward is cheap for an agent and looks like progress; this module owns
the UPWARD path — the closure rules that keep the tree from becoming decomposition
theater (playbook/safe.md):

  - nothing closes over open children, whatever its kind;
  - a STORY closes only with evidence: a merged repo-qualified PR, or a done_when
    line for internal one-shots;
  - a FEATURE closes only citing its accepted [DEMO] comment (or done_when when it
    has no product surface — same bar as the demo gate itself);
  - EPIC / OBJECTIVE close by rollup: every child closed. A PROPOSAL closes freely
    (rejected proposals must be cheap to bury).

The [CLOSE] payload's *shape* is handoff_check.py's job; THIS is the facts layer —
child state, PR merge state, and the demo marker are re-derived live from GitHub
(the demo-verify.sh pattern: predicate in code, not prose).

Routing note: children inherit the parent's dept:* labels at creation (pm-gh.sh
create --parent), so runner.py routes them with no tree walk — zero added poll cost.

Usage:
  workitems.py link <parent#> <child#>      add child as sub-issue (kind order enforced)
  workitems.py check-edge <parent#> <kind>  pre-flight a link before the child exists
  workitems.py tree <number>                render the subtree (● open / ✓ closed)
  workitems.py can-close <number> [--pr <owner/repo#N|url>] [--done-when <text>]
                                  [--demo <comment-url>]
      facts gate for pm-gh.sh done — prints key=value lines (closable=, kind=,
      reason=…), exit 0 only when closable now.

Read-only except `link`. Exit: 0 ok · 1 refused (reasons on stdout) · 2 usage.
"""
import json
import os
import re
import subprocess
import sys

KINDS = ("objective", "proposal", "epic", "feature", "story")

# Taxonomy edges — what may nest under what. An untyped side is allowed anywhere:
# pre-hierarchy tickets keep working, and the Chairman can hand-assemble freely.
CHILD_KINDS = {
    "objective": {"proposal", "epic"},
    "epic": {"feature"},
    "feature": {"story"},
    "proposal": set(),
    "story": set(),
}

MAX_DEPTH = 6  # objective→epic→feature→story is 4; headroom, not an invitation

DEMO_MARKER = re.compile(r"^\s*\[DEMO\]", re.MULTILINE)


# --- pure helpers (unit-tested in test_workitems.py; no I/O) -----------------------------

def kind_of(labels):
    """The work-item kind a label set carries, outermost first (an issue mislabeled
    with two kinds reads as the higher level, which fails toward stricter closure)."""
    labels = set(labels or [])
    for k in KINDS:
        if k in labels:
            return k
    return None


def link_problems(parent_kind, child_kind):
    """Why parent→child would violate the taxonomy ([] = allowed)."""
    if parent_kind is None or child_kind is None:
        return []
    allowed = CHILD_KINDS.get(parent_kind, set())
    if child_kind in allowed:
        return []
    want = ", ".join(sorted(allowed)) or "nothing"
    return [f"a {child_kind} cannot nest under a {parent_kind} "
            f"(a {parent_kind} decomposes into: {want})"]


def open_children(children):
    return [c for c in children or [] if (c.get("state") or "").lower() != "closed"]


def parse_pr_ref(ref):
    """'owner/repo#12' or a GitHub PR URL → ('owner/repo', 12); None if unqualified.
    Bare numbers are rejected on purpose — same repo-qualification bar as [DEMO] pr."""
    m = re.match(r"^https?://github\.com/([^/]+/[^/]+)/pull/(\d+)", str(ref or ""))
    if m:
        return m.group(1), int(m.group(2))
    m = re.match(r"^([\w.-]+/[\w.-]+)#(\d+)$", str(ref or ""))
    if m:
        return m.group(1), int(m.group(2))
    return None


def parse_comment_ref(url):
    """Issue-comment URL (html or api) → REST path for the comment; None if not one."""
    m = re.match(r"^https?://github\.com/([^/]+/[^/]+)/(?:issues|pull)/\d+"
                 r"#issuecomment-(\d+)$", str(url or ""))
    if m:
        return f"repos/{m.group(1)}/issues/comments/{m.group(2)}"
    m = re.match(r"^https?://api\.github\.com/(repos/[^/]+/[^/]+/issues/comments/\d+)$",
                 str(url or ""))
    return m.group(1) if m else None


def close_problems(kind, children, evidence):
    """Why this item may not close now ([] = closable). `evidence` carries both the
    cited refs and the live facts the I/O layer resolved for them:
      pr, pr_ref, pr_merged · done_when · demo, demo_ref, demo_is_demo
    """
    ev = evidence or {}
    problems = [f"child org#{c.get('number')} is still open: {c.get('title', '')}"
                for c in open_children(children)]
    # A cited --pr is evidence regardless of kind — an untyped ticket (no story/feature
    # gate below) must not be closable on an unmerged PR just because its kind carries
    # no dedicated check. Verified once here; the kind-specific blocks below only need
    # to handle the "no PR cited" fallback.
    if ev.get("pr"):
        if ev.get("pr_ref") is None:
            problems.append(f"pr '{ev['pr']}' must be repo-qualified "
                            f"(owner/repo#N or the PR URL)")
        elif ev.get("pr_merged") is not True:
            problems.append(f"cited PR {ev['pr']} is not merged — "
                            f"PR evidence must be a merged PR")
    if kind == "story":
        if not (ev.get("done_when") or ev.get("pr")):
            problems.append("a [STORY] closes only with evidence: "
                            "--pr <merged PR> or --done-when <one-line>")
    elif kind == "feature":
        if ev.get("done_when"):
            pass
        elif ev.get("demo"):
            if ev.get("demo_ref") is None:
                problems.append(f"demo '{ev['demo']}' is not an issue-comment URL")
            elif ev.get("demo_is_demo") is not True:
                problems.append(f"cited comment carries no line-leading [DEMO] marker: "
                                f"{ev['demo']}")
        else:
            problems.append("a [FEATURE] closes only citing its accepted [DEMO] "
                            "(--demo <comment-url>), or --done-when for work "
                            "with no product surface")
    # epic / objective / proposal / untyped: rollup only — the children check above.
    return problems


def render_tree(node):
    """Subtree → display lines. ● open · ✓ closed; titles carry their own [KIND] prefix."""
    glyph = "✓" if (node.get("state") or "").lower() == "closed" else "●"
    lines = [f"{glyph} org#{node.get('number')}  {node.get('title', '')}"]
    kids = node.get("children") or []
    for i, kid in enumerate(kids):
        last = i == len(kids) - 1
        sub = render_tree(kid)
        lines.append(("└─ " if last else "├─ ") + sub[0])
        lines.extend((("   " if last else "│  ") + s) for s in sub[1:])
    return lines


# --- I/O ----------------------------------------------------------------------------------

def org_repo():
    # No identity fallback: empty → every gh lookup fails → can-close refuses (fail-safe).
    return os.environ.get("ORG_GH_REPO", "")


def gh_json(*args):
    """gh api → parsed JSON; None on any failure (offline, auth, 404) — callers treat
    None as 'could not verify', which always fails toward refusing the close."""
    try:
        out = subprocess.run(["gh", "api", *args], capture_output=True, timeout=60)
        if out.returncode != 0:
            return None
        return json.loads(out.stdout or b"null")
    except (OSError, ValueError):
        return None


def fetch_issue(number):
    return gh_json(f"repos/{org_repo()}/issues/{number}")


def fetch_children(number):
    got = gh_json(f"repos/{org_repo()}/issues/{number}/sub_issues?per_page=100")
    return got if isinstance(got, list) else []


def fetch_subtree(number, depth=MAX_DEPTH, seen=None):
    seen = seen if seen is not None else set()
    issue = fetch_issue(number)
    if not isinstance(issue, dict):
        return None
    node = {"number": number, "title": issue.get("title") or "",
            "state": issue.get("state") or "", "children": []}
    seen.add(number)
    if depth > 0:
        for c in fetch_children(number):
            n = c.get("number")
            if n in seen:
                continue
            child = fetch_subtree(n, depth - 1, seen)
            if child:
                node["children"].append(child)
    return node


def issue_kind(issue):
    return kind_of(l.get("name", "") for l in (issue or {}).get("labels") or [])


def cmd_link(parent, child):
    p, c = fetch_issue(parent), fetch_issue(child)
    if not p or not c:
        print(f"workitems: cannot fetch org#{parent} / org#{child}")
        return 1
    problems = link_problems(issue_kind(p), issue_kind(c))
    if problems:
        for pr in problems:
            print(pr)
        return 1
    got = gh_json("-X", "POST", f"repos/{org_repo()}/issues/{parent}/sub_issues",
                  "-F", f"sub_issue_id={c.get('id')}")
    if got is None:
        print(f"workitems: linking org#{child} under org#{parent} failed "
              f"(gh api sub_issues POST)")
        return 1
    print(f"org#{child} → sub-issue of org#{parent}")
    return 0


def cmd_check_edge(parent, child_kind):
    p = fetch_issue(parent)
    if not p:
        print(f"workitems: cannot fetch org#{parent}")
        return 1
    kind = child_kind if child_kind in KINDS else None
    problems = link_problems(issue_kind(p), kind)
    for pr in problems:
        print(pr)
    return 1 if problems else 0


def cmd_tree(number):
    tree = fetch_subtree(number)
    if tree is None:
        print(f"workitems: cannot fetch org#{number}")
        return 1
    print("\n".join(render_tree(tree)))
    return 0


def resolve_evidence(flags):
    """Cited refs → refs + live facts (merge state, demo marker), for close_problems."""
    ev = dict(flags)
    if ev.get("pr"):
        ev["pr_ref"] = parse_pr_ref(ev["pr"])
        if ev["pr_ref"]:
            repo, num = ev["pr_ref"]
            got = gh_json(f"repos/{repo}/pulls/{num}")
            ev["pr_merged"] = bool(got.get("merged")) if isinstance(got, dict) else None
    if ev.get("demo"):
        ev["demo_ref"] = parse_comment_ref(ev["demo"])
        if ev["demo_ref"]:
            got = gh_json(ev["demo_ref"])
            body = got.get("body") if isinstance(got, dict) else None
            ev["demo_is_demo"] = bool(DEMO_MARKER.search(body)) if body else None
    return ev


def cmd_can_close(number, flags):
    issue = fetch_issue(number)
    if not issue:
        print("closable=no")
        print(f"reason=cannot fetch org#{number}")
        return 1
    kind = issue_kind(issue)
    problems = close_problems(kind, fetch_children(number), resolve_evidence(flags))
    print(f"closable={'no' if problems else 'yes'}")
    print(f"kind={kind or ''}")
    for p in problems:
        print(f"reason={p}")
    return 1 if problems else 0


def main(argv):
    usage = "usage: workitems.py <link P C | check-edge P KIND | tree N | " \
            "can-close N [--pr R] [--done-when T] [--demo U]>"
    if len(argv) < 2:
        sys.exit(usage)
    cmd, rest = argv[1], argv[2:]
    if cmd == "link" and len(rest) == 2:
        return cmd_link(*rest)
    if cmd == "check-edge" and len(rest) == 2:
        return cmd_check_edge(*rest)
    if cmd == "tree" and len(rest) == 1:
        return cmd_tree(rest[0])
    if cmd == "can-close" and rest:
        flags, i = {}, 1
        names = {"--pr": "pr", "--done-when": "done_when", "--demo": "demo"}
        while i < len(rest):
            if rest[i] in names and i + 1 < len(rest):
                flags[names[rest[i]]] = rest[i + 1]
                i += 2
            else:
                sys.exit(usage)
        return cmd_can_close(rest[0], flags)
    sys.exit(usage)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
