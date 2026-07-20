#!/usr/bin/env python3
"""warden.py — the deterministic engine behind the warden department: keep the
Chairman action queue (org-chart `global.chairman_queue_issue`) true to ground truth,
and audit GitHub hygiene across the org.

Philosophy (docs/plans/token-efficiency.md): rules enforced in code are paid for once,
at authoring time — so everything derivable lives HERE, token-free. The warden AGENT
(agents/warden.md, haiku) only handles the judgment residue this script flags.

The queue is a desired-state sync. Ground truth, each source mapped to one child
sub-issue under the queue epic, keyed by a `warden-source:` marker in the child body:

  blocker:<id>        every OPEN blockers.yaml entry (the human-task ledger)
  pr:<repo>#<n>       every open PR that is Chairman-ready: not draft, mergeStateStatus
                      CLEAN or BEHIND (checks green; BEHIND just means main moved past
                      it, still open+mergeable), review not CHANGES_REQUESTED — the
                      merge is the Chairman's click (constitution, invariant 1)
  proposal:org#<n>    every open [PROPOSAL] issue awaiting a human verdict

Sync = create children for new sources, close children whose source cleared (via
pm-gh.sh done — the one closing path), leave everything else alone. Children are
labeled dept:warden (NOT the epic's dept) so queue churn wakes the warden, never the
CEO — queue upkeep must not create the wake noise it exists to reduce. Open children
without a marker are never touched, only reported as unmanaged.

The BOARD RECONCILER makes the project board reorganize itself from code truth: every
open org issue is joined to the PRs that reference it (`org#N` in PR title/body, both
repos), and the board's Status single-select is moved FORWARD-only where the code has
outrun the board — an issue with an open non-draft PR belongs in Awaiting Merge (the
Chairman's merge queue); one whose PR merged belongs in Demo. Backward inconsistencies
(Awaiting Merge/Demo/Awaiting Deploy with no PR anywhere) are reported, never
auto-moved — a human or the owning dept decides those. Two hygiene reconciles ride
along: an open epic whose child work has started is lifted off Todo (epics are what the
Chairman scans, and a frozen Todo epic reads as untouched), and a CLOSED issue stuck at
a live column is settled to its terminal truth (NOT_PLANNED → Dropped, else Done).

The CONFLICT CONVENER turns in-flight friction into a discussion instead of a
surprise: deterministic detections — a product PR gone DIRTY (merge-conflicts with
main), two open product PRs touching the same files, a PR whose body declares
`depends on #N` while #N is still open — each maintain ONE `[SYNC]` issue labeled
dept:it + dept:business, so the runner wakes BOTH sides into the thread (the designed
two-party fan-out) to re-order or re-scope, ideally landing an [AGREEMENT]. Threads
are marker-managed like queue children: the warden opens them when the fact appears
and closes them when the fact clears; it never arbitrates the discussion itself.

The hygiene audit reports (never nags per-issue): unlabeled open issues (unroutable —
the runner can't wake anyone), kind-label/title-prefix mismatches, product PRs with no
org#N work-item link, and recently-closed typed items missing their [CLOSE] evidence
comment. The report lands as ONE warden-bannered comment on the queue epic, edited in
place (marker <!-- warden-report -->) and only when its content changed — an unchanged
report must not bump the issue and wake anyone.

  scripts/warden.py audit             # read-only: print the report + queue drift
  scripts/warden.py sync [--dry-run]  # converge the queue + update the report comment

Fail-soft everywhere: no gh / offline / missing files degrade to empty sections, never
a crash — this runs unattended (launchd/cron: `python3 scripts/warden.py sync`, ~4h).
Pure helpers up top (unit-tested in test_warden.py); I/O at the bottom.
"""
import argparse
import datetime
import json
import os
import re
import subprocess
import sys

REPO_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# No identity fallbacks: unset env degrades every gh call to None, and the engine's
# fail-soft design (propose nothing, touch nothing) already handles that honestly —
# better than silently auditing someone else's repo. The wrappers source .env first.
ORG_REPO = os.environ.get("ORG_GH_REPO", "")
PRODUCT_REPO = os.environ.get("PRODUCT_GH_REPO", "")

# Self-heal the interpreter. This runs unattended (launchd) and from agent personas —
# under a bare system python3, `import yaml` fails and every yaml-backed lookup would
# degrade. Commissioning lesson: that degradation surfaced as "chairman_queue_issue is
# not set", which reads like WRONG REPO and sent a haiku brain wandering the filesystem
# for its own script. So: if yaml is missing and the repo venv exists, re-exec onto it.
try:
    import yaml as _yaml_probe  # noqa: F401
except ImportError:  # pragma: no cover — environment-dependent by nature
    _venv = os.path.join(REPO_DIR, ".venv", "bin", "python")
    if os.path.exists(_venv) and os.path.realpath(sys.executable) != os.path.realpath(_venv):
        os.execv(_venv, [_venv] + sys.argv)

MARKER = "warden-source:"
REPORT_MARKER = "<!-- warden-report -->"
BANNER = "**Warden —**"
MAX_CREATE = 15   # runaway brake: no single sync files more than this many children
MAX_MOVES = 10    # …or board moves
MAX_SYNC = 3      # …or newly-convened [SYNC] discussions (each wakes two departments)
KINDS = ("epic", "feature", "story")


def _chart_list(key, default):
    """A `global.<key>: [a, b, …]` flow list straight off org-chart.yaml — the canon
    lives THERE (the chart is explicit that no tool may restate it); the default only
    covers a missing/unreadable chart so this engine can never crash on its config."""
    try:
        with open(os.path.join(REPO_DIR, "org-chart.yaml"), encoding="utf-8") as fh:
            text = fh.read()
    except OSError:
        return list(default)
    m = re.search(rf"^\s*{re.escape(key)}:\s*\[([^\]]+)\]", text, re.MULTILINE)
    return [s.strip().strip("'\"") for s in m.group(1).split(",")] if m else list(default)


STAGES = _chart_list("pm_stages",
                     ["Todo", "In Progress", "Awaiting Merge", "Demo", "Awaiting Deploy", "Done"])
HOLDING_STAGES = tuple(_chart_list("pm_holding_stages", ["Blocked", "On Hold"]))
TERMINAL_STAGES = tuple(_chart_list("pm_terminal_stages", ["Dropped"]))
# Generated files whose overlap between PRs is churn, not a real collision.
LOCKFILES = ("package-lock.json", "yarn.lock", "go.sum", "go.mod")


# --- pure helpers (unit-tested in test_warden.py; no I/O) ---------------------------------

def parse_source(body):
    """The child's `warden-source: <key>` marker, or None (unmanaged child)."""
    m = re.search(rf"{MARKER}\s*(\S+)", body or "")
    return m.group(1) if m else None


def parse_chart_pin(text, key):
    """Read a numeric `global.<key>` pin straight off org-chart.yaml text — the
    no-dependency fallback so a missing yaml module can never turn 'right repo, thin
    interpreter' into an error that looks like 'wrong repo'."""
    m = re.search(rf"^\s*{re.escape(key)}:\s*(\d+)", text or "", re.MULTILINE)
    return int(m.group(1)) if m else None


def pr_ready(pr):
    """Chairman-ready: not draft, checks green (CLEAN or BEHIND), review not
    CHANGES_REQUESTED. BEHIND still means an open, mergeable, unmerged PR — only the base
    branch has moved past it, a state this repo's PRs hit constantly under fast main-line
    churn — so it stays a real action item (merging it is still the Chairman's move; at
    worst GitHub's UI asks for an "Update branch" click first). UNKNOWN/BLOCKED/DIRTY/etc.
    are NOT ready — fail toward an emptier queue, the report still shows the PR next tick
    once GitHub settles. (Incident 2026-07-12: BEHIND being treated as not-ready made
    plan_sync close the "Chairman: merge PR #N" tracker for a still-open, still-unmerged
    PR, silently dropping the action item — see org#254 / scrum-jail-business PR #253.)"""
    if not isinstance(pr, dict) or pr.get("isDraft"):
        return False
    if str(pr.get("mergeStateStatus", "")).upper() not in ("CLEAN", "BEHIND"):
        return False
    return str(pr.get("reviewDecision", "")).upper() != "CHANGES_REQUESTED"


def _trim(text, n=90):
    text = " ".join(str(text or "").split())
    return text if len(text) <= n else text[: n - 1] + "…"


def desired_queue(blockers_data, prs_by_repo, proposals):
    """Ground truth → {key: {title, body}} of children the queue SHOULD hold."""
    desired = {}
    for b in (blockers_data or {}).get("blockers") or []:
        if (b.get("state") or "open").strip() != "open":
            continue
        bid = str(b.get("id") or "").strip()
        if not bid:
            continue
        key = f"blocker:{bid}"
        action = " ".join((b.get("action") or b.get("summary") or "").split())
        tags = ", ".join(str(t) for t in (b.get("value"), b.get("effort_minutes") and
                         f"~{b.get('effort_minutes')}min") if t)
        blocks = ", ".join(str(x) for x in b.get("blocks") or [])
        mc = bool(b.get("gates_market_contact"))
        desired[key] = {
            "title": _trim(f"{'[MARKET-CONTACT] ' if mc else ''}Chairman: {action or bid}"),
            "body": (f"{action}\n\n"
                     + ("MARKET-CONTACT — the product is live but a checkout and/or an "
                        "audience is gated on you. This does NOT go quiet (DESIGN.md "
                        "invariant 2); it resurfaces every wake until cleared.\n\n" if mc else "")
                     + (f"({tags})\n" if tags else "")
                     + (f"unblocks: {blocks}\n" if blocks else "")
                     + f"ledger: blockers.yaml `{bid}`\n\n{MARKER} {key}\n"
                     f"_(managed by scripts/warden.py — keep the marker line intact)_"),
        }
    for repo_key, prs in (prs_by_repo or {}).items():
        for pr in prs or []:
            if not pr_ready(pr):
                continue
            n = pr.get("number")
            key = f"pr:{repo_key}#{n}"
            desired[key] = {
                "title": _trim(f"Chairman: merge {repo_key} PR #{n} — {pr.get('title', '')}"),
                "body": (f"All checks green; awaiting your merge (invariant 1 — only the "
                         f"Chairman merges).\n{pr.get('url', '')}\n\n{MARKER} {key}\n"
                         f"_(managed by scripts/warden.py — keep the marker line intact)_"),
            }
    for issue in proposals or []:
        n = issue.get("number")
        key = f"proposal:org#{n}"
        desired[key] = {
            "title": _trim(f"Chairman: answer {issue.get('title', f'[PROPOSAL] org#{n}')}"),
            "body": (f"Open proposal awaiting a human verdict (an unanswered proposal "
                     f"is a no, but say so).\n{issue.get('url', '')}\n\n{MARKER} {key}\n"
                     f"_(managed by scripts/warden.py — keep the marker line intact)_"),
        }
    return desired


def plan_sync(desired, children):
    """Diff desired state against the queue's existing sub-issues.

    Returns dict(create=[(key, spec)], close=[(number, key)], unmanaged=[numbers],
    kept=[keys]). Only OPEN children participate; a closed child whose source
    re-opened gets a fresh child (history stays intact). Unmanaged = open children
    without a marker — never touched, only surfaced."""
    managed, unmanaged, dupes = {}, [], []
    for c in children or []:
        if str(c.get("state", "")).lower() not in ("open", ""):
            continue
        key = parse_source(c.get("body"))
        if key:
            # Two open children with one key = a concurrent-sync race (launchd sweep vs
            # a wake-time run). Keep the first, close the extras — self-healing dedupe.
            if key in managed:
                dupes.append((c.get("number"), f"{key} (duplicate of #{managed[key]})"))
            else:
                managed[key] = c.get("number")
        else:
            unmanaged.append(c.get("number"))
    create = [(k, spec) for k, spec in sorted(desired.items()) if k not in managed]
    close = [(n, k) for k, n in sorted(managed.items()) if k not in desired] + sorted(dupes)
    kept = sorted(set(managed) & set(desired))
    return dict(create=create, close=close, unmanaged=sorted(unmanaged), kept=kept)


def kind_mismatch(title, labels):
    """A typed kind label must match the [KIND] title prefix, and vice versa."""
    title = str(title or "")
    labeled = [k for k in KINDS if k in (labels or [])]
    prefixed = [k for k in KINDS if title.upper().startswith(f"[{k.upper()}]")]
    return sorted(labeled) != sorted(prefixed)


def linked_refs(text):
    """Every org#N work-item reference in a PR's title/body → set of ints."""
    return {int(n) for n in re.findall(r"org#(\d+)", str(text or ""))}


def expected_stage(current, has_merged_pr, has_open_pr):
    """The FORWARD-only board move code truth demands, or None. Never proposes a
    backward move — regressions are findings for humans/depts, not auto-moves. A parked
    item (org-chart pm_holding_stages: Blocked/On Hold) is left alone — someone put it
    there on purpose, and having a PR must not auto-yank it back into the flow; a
    Dropped item is dead, not behind. An open non-draft PR titled (org#N) IS the
    awaiting-a-merge fact; a merged one IS the awaiting-demo/acceptance fact (playbook/safe.md's
    column definitions) — Awaiting Deploy and Done are gate outcomes (demo acceptance,
    pm-gh.sh done), never derivable from PR state alone."""
    if current in HOLDING_STAGES or current in TERMINAL_STAGES:
        return None
    cur = current if current in STAGES else STAGES[0]
    if has_merged_pr and STAGES.index(cur) < STAGES.index("Demo"):
        return "Demo"
    if has_open_pr and STAGES.index(cur) < STAGES.index("Awaiting Merge"):
        return "Awaiting Merge"
    return None


def plan_moves(open_issues, stages, links):
    """Board reconcile plan: [(number, from_stage, to_stage, reason)] plus backward
    anomalies as finding strings. `stages` maps issue# → Stage (board truth); `links`
    maps issue# → {"open"/"merged": [title-link refs], "open_weak"/"merged_weak":
    [body-mention refs]}.

    Precision asymmetry, learned from the first live audit: MOVES (mutations) trust
    only TITLE links — "(org#N)" in a PR title is the implements-this convention,
    while a body mention can be incidental (a report PR listing an issue moved it to
    In Progress). ANOMALIES (report-only) are suppressed by ANY reference, weak included —
    better to miss a nag than to nag a linked item. Epics and objectives are skipped
    entirely: they track by child rollup, not by PR."""
    moves, anomalies = [], []
    for i in open_issues or []:
        labels = [l.get("name", "") if isinstance(l, dict) else str(l)
                  for l in i.get("labels") or []]
        if "epic" in labels or "objective" in labels:
            continue
        n = i.get("number")
        cur = stages.get(n)
        l = links.get(n) or {}
        to = expected_stage(cur, bool(l.get("merged")), bool(l.get("open")))
        if to:
            why = (f"PR {', '.join(l['merged'][:3])} merged" if l.get("merged")
                   else f"open PR {', '.join(l['open'][:3])}")
            moves.append((n, cur or "(off board)", to, why))
        elif cur in ("Awaiting Merge", "Demo", "Awaiting Deploy") and not any(
                l.get(k) for k in ("merged", "open", "merged_weak", "open_weak")):
            anomalies.append(f"org#{n} sits in {cur} with no PR referencing it anywhere "
                             f"— the status claims more than the code shows: "
                             f"{_trim(i.get('title'), 60)}")
    return moves, anomalies


def plan_epic_rollup(open_epics, stages, children):
    """Epics track by child rollup, so plan_moves skips them — which left every epic
    frozen at Todo while its children moved, reading as untouched work on exactly the
    rows the Chairman scans. One forward-only lift: an open epic still at Todo (or off
    the board) whose child work has visibly started — a child already closed, or past
    Todo on the board — belongs in In Progress. Nothing deeper is derived (Done is the
    rollup close's job), and a parked epic stays parked. `children`: epic# → child dicts
    (number/state)."""
    moves = []
    first, active = STAGES[0], STAGES[1]
    for e in open_epics or []:
        n = e.get("number")
        cur = stages.get(n)
        if cur is not None and cur != first:
            continue
        kids = children.get(n) or []
        if any((k.get("state") or "").lower() == "closed"
               or (stages.get(k.get("number")) or first) != first for k in kids):
            moves.append((n, cur or "(off board)", active, "child work has started"))
    return moves


def plan_closed_reconcile(closed_issues, stages):
    """A CLOSED issue frozen at a live column lies to every board reader (the stale
    To-Dos that prompted the status overhaul were exactly this). Settle each to its
    terminal truth: closed as NOT_PLANNED → Dropped, otherwise Done. Only items already
    ON the board move — a closed issue nobody tracked doesn't earn a card posthumously."""
    moves = []
    terminal = {STAGES[-1], *TERMINAL_STAGES}
    for i in closed_issues or []:
        n, cur = i.get("number"), stages.get(i.get("number"))
        if n is None or cur is None or cur in terminal:
            continue
        to = ("Dropped" if str(i.get("stateReason") or "").upper() == "NOT_PLANNED"
              else STAGES[-1])
        moves.append((n, cur, to, "issue is closed"))
    return moves


def detect_conflicts(open_product_prs):
    """Deterministic in-flight friction → {key: {title, facts}}. Inputs are open
    product-repo PRs with number/title/body/mergeStateStatus and (optionally) files."""
    conflicts = {}
    prs = [p for p in open_product_prs or [] if isinstance(p, dict)]
    open_nums = {p.get("number") for p in prs}
    for p in prs:
        n = p.get("number")
        if str(p.get("mergeStateStatus", "")).upper() == "DIRTY":
            conflicts[f"conflict:dirty:product#{n}"] = {
                "title": f"[SYNC] merge conflict: product PR #{n}",
                "facts": f"PR #{n} ({_trim(p.get('title'), 60)}) no longer merges "
                         f"cleanly into main — something landed under it.",
            }
        for dep in re.findall(r"(?:depends on|blocked by)\s+(?:PR\s*)?#(\d+)",
                              str(p.get("body") or ""), re.IGNORECASE):
            if int(dep) in open_nums:
                conflicts[f"conflict:dep:product#{n}->product#{dep}"] = {
                    "title": f"[SYNC] dependency: product PR #{n} waits on #{dep}",
                    "facts": f"PR #{n} declares it depends on PR #{dep}, which is "
                             f"still open — merge order needs an owner.",
                }
    for a in prs:
        for b in prs:
            if not (a.get("number") and b.get("number")) or a["number"] >= b["number"]:
                continue
            shared = sorted((set(a.get("files") or []) & set(b.get("files") or []))
                            - set(LOCKFILES))
            if shared:
                key = f"conflict:overlap:product#{a['number']}+product#{b['number']}"
                conflicts[key] = {
                    "title": f"[SYNC] overlapping files: product PR #{a['number']} "
                             f"↔ #{b['number']}",
                    "facts": f"Both PRs modify: {', '.join(shared[:5])}"
                             + (f" (+{len(shared) - 5} more)" if len(shared) > 5 else "")
                             + " — whichever merges second inherits the conflict.",
                }
    return conflicts


def desired_sync_threads(conflicts):
    """Conflict detections → the [SYNC] issues that should exist, marker-keyed so
    plan_sync() can converge them exactly like queue children."""
    desired = {}
    for key, c in (conflicts or {}).items():
        desired[key] = {
            "title": _trim(c["title"], 110),
            "body": (f"{c['facts']}\n\n"
                     f"IT & Business: this is in-flight friction the board can't "
                     f"resolve by itself — re-order, re-scope, or split ownership in "
                     f"this thread (an [AGREEMENT] on the affected tickets is the "
                     f"ideal close). The warden opened this on a detected fact and "
                     f"will close it automatically when the fact clears; it will not "
                     f"arbitrate.\n\n{MARKER} {key}\n"
                     f"_(managed by scripts/warden.py — keep the marker line intact)_"),
        }
    return desired


def render_report(plan, findings, now_str, queue_issue, moves=None, sync_plan=None):
    """The single edited-in-place report comment. Deterministic for given inputs so an
    unchanged org produces an IDENTICAL comment body → no edit → no wake ripple."""
    lines = [REPORT_MARKER,
             f"{BANNER} Chairman-queue, board & hygiene report",
             "",
             f"Queue (org#{queue_issue}): {len(plan.get('kept', []))} current, "
             f"{len(plan.get('create', []))} to add, {len(plan.get('close', []))} cleared.",
             ]
    if plan.get("unmanaged"):
        lines.append("Unmanaged children (no `warden-source` marker — adopt or close by "
                     "hand): " + ", ".join(f"#{n}" for n in plan["unmanaged"]))
    if moves:
        lines += ["", "Board reconcile (code truth → Status):"]
        lines += [f"- org#{n}: {frm} → {to} ({why})" for n, frm, to, why in moves]
    if sync_plan and (sync_plan.get("kept") or sync_plan.get("create")
                      or sync_plan.get("close")):
        active = len(sync_plan.get("kept", [])) + len(sync_plan.get("create", []))
        lines += ["", f"In-flight conflicts: {active} active [SYNC] discussion(s) "
                      f"(IT ⇄ Business), {len(sync_plan.get('close', []))} resolved."]
    if findings:
        lines += ["", "Hygiene findings:"]
        lines += [f"- {f}" for f in findings]
    else:
        lines += ["", "Hygiene: clean."]
    lines += ["", f"_as of {now_str} · scripts/warden.py_"]
    return "\n".join(lines)


# --- I/O ----------------------------------------------------------------------------------

def gh_json(args, default=None):
    """gh → parsed JSON; `default` on any failure. Never raises."""
    try:
        out = subprocess.run(["gh"] + args, capture_output=True, timeout=60)
        if out.returncode != 0:
            return default
        return json.loads(out.stdout or b"null")
    except (OSError, ValueError):
        return default


def sh(args):
    """Run a repo script; (rc, output). Never raises."""
    try:
        out = subprocess.run(args, capture_output=True, text=True, timeout=120, cwd=REPO_DIR)
        return out.returncode, (out.stdout + out.stderr).strip()
    except OSError as exc:
        return 1, str(exc)


def _chart_pin(key):
    """A numeric global.<key> from org-chart.yaml — yaml when available, regex text
    fallback otherwise (never let a thin interpreter masquerade as a missing pin)."""
    path = os.path.join(REPO_DIR, "org-chart.yaml")
    try:
        import yaml
        chart = yaml.safe_load(open(path, encoding="utf-8"))
        return int((chart.get("global") or {}).get(key))
    except Exception:
        try:
            return parse_chart_pin(open(path, encoding="utf-8").read(), key)
        except OSError:
            return None


def queue_issue_number():
    """org-chart global.chairman_queue_issue — the ONE place the queue epic is pinned."""
    return _chart_pin("chairman_queue_issue")


def chairman_github():
    """org-chart chairman.github — the human every queue child is assigned to.

    The `Chairman:` title prefix says who a child is FOR; a real GitHub assignee is what
    makes the queue reachable from his "Assigned to me" filter instead of only by opening
    this repo's board. Agents can never be assignees (one shared identity), so this is the
    only assignee the org ever sets. None → children are still created, just unassigned:
    a missing pin must not cost the Chairman his queue.
    """
    path = os.path.join(REPO_DIR, "org-chart.yaml")
    try:
        import yaml
        chart = yaml.safe_load(open(path, encoding="utf-8")) or {}
        return ((chart.get("chairman") or {}).get("github") or "").strip() or None
    except Exception:
        try:
            m = re.search(r'^\s*github:\s*"?([A-Za-z0-9-]+)"?', open(path, encoding="utf-8").read(), re.M)
            return m.group(1) if m else None
        except OSError:
            return None


def load_blockers():
    try:
        import yaml
        return yaml.safe_load(open(os.path.join(REPO_DIR, "blockers.yaml"), encoding="utf-8")) or {}
    except Exception:
        return {}


def fetch_children(queue):
    got = gh_json(["api", f"repos/{ORG_REPO}/issues/{queue}/sub_issues?per_page=100"], [])
    return [{"number": c.get("number"), "state": c.get("state"),
             "title": c.get("title"), "body": c.get("body")} for c in got or []]


def fetch_prs(repo):
    return gh_json(["pr", "list", "--repo", repo, "--state", "open", "--limit", "50",
                    "--json",
                    "number,title,url,isDraft,mergeStateStatus,reviewDecision,body"], [])


def fetch_merged_prs(repo, limit=30):
    return gh_json(["pr", "list", "--repo", repo, "--state", "merged", "--limit",
                    str(limit), "--json", "number,title,body"], []) or []


def fetch_pr_files(repo, number):
    got = gh_json(["pr", "view", str(number), "--repo", repo, "--json", "files",
                   "--jq", "[.files[].path]"], [])
    return got or []


def fetch_open_issues():
    return gh_json(["issue", "list", "--repo", ORG_REPO, "--state", "open", "--limit", "200",
                    "--json", "number,title,url,labels,body"], []) or []


def fetch_board_stages():
    """{issue_number: Status} from the org Project (gh flattens the built-in Status
    single-select to `.status` on each item). {} when the project is unreachable —
    reconcile then proposes nothing (fail-soft toward inaction). The board number comes
    from the org-chart pin; the by-title lookup is only the un-pinned fallback."""
    pn = _chart_pin("pm_project_number") or gh_json(
        ["project", "list", "--owner", ORG_REPO.split("/")[0], "--format", "json",
         "--jq", '[.projects[] | select(.title == "'
         + os.environ.get("PM_PROJECT_TITLE", "Org Project")
         + '") | .number][0]'], None)
    if not pn:
        return {}
    items = gh_json(["project", "item-list", str(pn), "--owner", ORG_REPO.split("/")[0],
                     "--limit", "500", "--format", "json",
                     "--jq", "[.items[] | {n: .content.number, stage: .status}]"], [])
    return {i["n"]: i.get("stage") for i in items or [] if i.get("n")}


def fetch_closed_issues(limit=30):
    """Recently closed issues with their close reason — the closed-board reconcile's
    input. Bounded: the sweep converges over a few runs, it doesn't need all history."""
    return gh_json(["issue", "list", "--repo", ORG_REPO, "--state", "closed",
                    "--limit", str(limit), "--json", "number,stateReason"], []) or []


def build_links(open_org_issues, prs_by_repo, merged_by_repo):
    """issue# → PR references across both repos, split by evidence strength: "open"/
    "merged" hold TITLE links (the "(org#N)" implements-this convention — safe to act
    on), "open_weak"/"merged_weak" hold body-only mentions (context, not claims).
    Pure over already-fetched PR lists."""
    links = {}
    numbers = {i.get("number") for i in open_org_issues or []}

    def note(refs, repo_key, pr_number, bucket):
        for ref in refs & numbers:
            links.setdefault(ref, {"open": [], "merged": [],
                                   "open_weak": [], "merged_weak": []})[bucket] \
                .append(f"{repo_key}#{pr_number}")

    for repo_key in ("org", "product"):
        for pr in (prs_by_repo or {}).get(repo_key) or []:
            if pr.get("isDraft"):
                continue
            strong = linked_refs(pr.get("title"))
            note(strong, repo_key, pr.get("number"), "open")
            note(linked_refs(pr.get("body")) - strong, repo_key, pr.get("number"),
                 "open_weak")
        for pr in (merged_by_repo or {}).get(repo_key) or []:
            strong = linked_refs(pr.get("title"))
            note(strong, repo_key, pr.get("number"), "merged")
            note(linked_refs(pr.get("body")) - strong, repo_key, pr.get("number"),
                 "merged_weak")
    return links


def hygiene_findings(open_issues, product_prs):
    """Deterministic hygiene sweep → list of finding strings. Each section fail-soft."""
    findings = []
    for i in open_issues:
        labels = [l.get("name", "") for l in i.get("labels") or []]
        if not any(l.startswith("dept:") for l in labels):
            findings.append(f"org#{i['number']} has no dept:* label — the runner cannot "
                            f"route it (unroutable): {_trim(i.get('title'), 60)}")
        if kind_mismatch(i.get("title"), labels):
            findings.append(f"org#{i['number']} kind label/title-prefix mismatch: "
                            f"{_trim(i.get('title'), 60)}")
    # Product PRs must link the work item they implement ("MRs linked properly") —
    # the same link the board reconciler steers by, so an unlinked PR is invisible work.
    org_slug = ORG_REPO.split("/")[-1]
    for pr in product_prs or []:
        text = f"{pr.get('title', '')} {pr.get('body', '')}"
        if "org#" not in text and (not org_slug or org_slug not in text):
            findings.append(f"{PRODUCT_REPO.split('/')[-1]} PR #{pr['number']} has no org#N "
                            f"work-item link: {_trim(pr.get('title'), 60)}")
    # Recently closed typed items must carry their [CLOSE] evidence (bounded: 8 lookups).
    # NOT_PLANNED closes are exempt — a drop's record is its [DROP] comment, and no
    # [CLOSE] evidence is owed for work the org decided not to do.
    closed = gh_json(["issue", "list", "--repo", ORG_REPO, "--state", "closed", "--limit", "8",
                      "--json", "number,title,labels,stateReason"], []) or []
    for i in closed:
        if str(i.get("stateReason") or "").upper() == "NOT_PLANNED":
            continue
        labels = [l.get("name", "") for l in i.get("labels") or []]
        if not any(k in labels for k in KINDS):
            continue
        comments = gh_json(["issue", "view", str(i["number"]), "--repo", ORG_REPO,
                            "--json", "comments", "--jq", "[.comments[].body]"], []) or []
        if not any("[CLOSE]" in (c or "") for c in comments):
            findings.append(f"org#{i['number']} closed without a [CLOSE] evidence comment "
                            f"(bypassed pm-gh.sh done): {_trim(i.get('title'), 60)}")
    return findings


def apply_plan(plan, queue):
    """Converge the queue: create via pm-gh.sh (board + sub-issue link + dept:warden
    routing), close via pm-gh.sh done (the one closing path). Prints every action."""
    created = 0
    chairman = chairman_github()
    for key, spec in plan["create"]:
        if created >= MAX_CREATE:
            print(f"warden: MAX_CREATE={MAX_CREATE} reached — remaining creations next run")
            break
        args = ["scripts/pm-gh.sh", "create", "--project", "warden",
                "--parent", str(queue), "--title", spec["title"],
                "--desc", spec["body"]]
        if chairman:  # his "Assigned to me" filter, not just this repo's board
            args += ["--assignee", chairman]
        rc, out = sh(args)
        print(f"warden: create {key}: {'ok — ' + out.splitlines()[-1] if rc == 0 else 'FAILED — ' + _trim(out, 200)}")
        created += rc == 0
    for number, key in plan["close"]:
        sh(["scripts/pm-gh.sh", "comment", "--id", str(number),
            "--body", f"{BANNER} source cleared (`{key}`) — closing."])
        rc, out = sh(["scripts/pm-gh.sh", "done", "--id", str(number)])
        print(f"warden: close #{number} ({key}): {'ok' if rc == 0 else 'FAILED — ' + _trim(out, 200)}")


def upsert_report(queue, report):
    """Create or edit-in-place the single report comment — and skip the write entirely
    when the body is unchanged, so a clean org never bumps the epic."""
    comments = gh_json(["api", f"repos/{ORG_REPO}/issues/{queue}/comments?per_page=100"], [])
    existing = next((c for c in comments or [] if REPORT_MARKER in (c.get("body") or "")), None)
    if existing and (existing.get("body") or "").strip() == report.strip():
        print("warden: report unchanged — not touching the epic")
        return
    if existing:
        gh_json(["api", "-X", "PATCH", f"repos/{ORG_REPO}/issues/comments/{existing['id']}",
                 "-f", f"body={report}"], {})
        print("warden: report comment updated")
    else:
        gh_json(["api", "-X", "POST", f"repos/{ORG_REPO}/issues/{queue}/comments",
                 "-f", f"body={report}"], {})
        print("warden: report comment created")


def apply_moves(moves):
    done = []
    for n, frm, to, why in moves[:MAX_MOVES]:
        rc, out = sh(["scripts/pm-gh.sh", "move", "--id", str(n), "--to", to])
        print(f"warden: move org#{n} {frm} → {to} ({why}): "
              f"{'ok' if rc == 0 else 'FAILED — ' + _trim(out, 160)}")
        if rc == 0:
            done.append((n, frm, to, why))
    if len(moves) > MAX_MOVES:
        print(f"warden: MAX_MOVES={MAX_MOVES} reached — remaining moves next run")
    return done


def apply_sync_threads(sync_plan):
    """Open new [SYNC] discussions (labeled dept:it + dept:business so the runner
    fans the thread out to BOTH sides) and close resolved ones."""
    created = 0
    for key, spec in sync_plan["create"]:
        if created >= MAX_SYNC:
            print(f"warden: MAX_SYNC={MAX_SYNC} reached — remaining conflicts next run")
            break
        rc, out = sh(["scripts/pm-gh.sh", "create", "--project", "it",
                      "--assigned", "business", "--title", spec["title"],
                      "--desc", spec["body"]])
        print(f"warden: convene {key}: "
              f"{'ok — ' + out.splitlines()[-1] if rc == 0 else 'FAILED — ' + _trim(out, 160)}")
        created += rc == 0
    for number, key in sync_plan["close"]:
        sh(["scripts/pm-gh.sh", "comment", "--id", str(number),
            "--body", f"{BANNER} the underlying fact cleared (`{key}`) — resolved, closing."])
        rc, out = sh(["scripts/pm-gh.sh", "done", "--id", str(number)])
        print(f"warden: resolve #{number} ({key}): "
              f"{'ok' if rc == 0 else 'FAILED — ' + _trim(out, 160)}")


def sync_issue_shapes(open_issues):
    """Open [SYNC] issues in the child-shape plan_sync() expects."""
    return [{"number": i.get("number"), "state": "open", "title": i.get("title"),
             "body": i.get("body")}
            for i in open_issues or []
            if str(i.get("title", "")).startswith("[SYNC]")]


def gather_all(queue):
    """One pass over GitHub → every plan the warden acts on. Fetches each source once."""
    open_issues = fetch_open_issues()
    prs = {"org": fetch_prs(ORG_REPO) or [], "product": fetch_prs(PRODUCT_REPO) or []}
    merged = {"org": fetch_merged_prs(ORG_REPO), "product": fetch_merged_prs(PRODUCT_REPO)}
    for p in prs["product"]:   # file lists power the overlap detector (bounded: open PRs)
        p["files"] = fetch_pr_files(PRODUCT_REPO, p["number"])
    queue_plan = plan_sync(
        desired_queue(load_blockers(), prs,
                      [i for i in open_issues
                       if str(i.get("title", "")).startswith("[PROPOSAL]")]),
        fetch_children(queue))
    links = build_links(open_issues, prs, merged)
    stages = fetch_board_stages()
    moves, anomalies = plan_moves(open_issues, stages, links)
    epics = [i for i in open_issues
             if "epic" in [l.get("name", "") for l in i.get("labels") or []]
             and "objective" not in [l.get("name", "") for l in i.get("labels") or []]]
    moves += plan_epic_rollup(epics, stages,
                              {e["number"]: fetch_children(e["number"]) for e in epics})
    moves += plan_closed_reconcile(fetch_closed_issues(), stages)
    sync_plan = plan_sync(desired_sync_threads(detect_conflicts(prs["product"])),
                          sync_issue_shapes(open_issues))
    findings = hygiene_findings(open_issues, prs["product"]) + anomalies
    return queue_plan, moves, sync_plan, findings


def main():
    ap = argparse.ArgumentParser(description="Chairman-queue sync + board reconcile + hygiene")
    ap.add_argument("cmd", nargs="?", default="audit", choices=["audit", "sync"])
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    queue = queue_issue_number()
    if not queue:
        # Precise, self-locating error — never one that reads like "wrong repo".
        sys.exit(f"warden: could not resolve global.chairman_queue_issue from "
                 f"{os.path.join(REPO_DIR, 'org-chart.yaml')} — the repo location is "
                 f"correct (this script anchors to it); check that the pin exists")
    pin = _chart_pin("pm_project_number")
    if pin:  # pm-gh.sh subprocesses inherit this and skip their by-title board lookup
        os.environ.setdefault("PM_PROJECT_NUMBER", str(pin))
    queue_plan, moves, sync_plan, findings = gather_all(queue)
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    if a.cmd == "audit" or a.dry_run:
        print(render_report(queue_plan, findings, now, queue, moves, sync_plan))
        print()
        for key, spec in queue_plan["create"]:
            print(f"WOULD-CREATE {key}: {spec['title']}")
        for number, key in queue_plan["close"]:
            print(f"WOULD-CLOSE  #{number} ({key})")
        for n, frm, to, why in moves:
            print(f"WOULD-MOVE   org#{n}: {frm} → {to} ({why})")
        for key, spec in sync_plan["create"]:
            print(f"WOULD-CONVENE {key}: {spec['title']}")
        for number, key in sync_plan["close"]:
            print(f"WOULD-RESOLVE #{number} ({key})")
        if not any([queue_plan["create"], queue_plan["close"], moves,
                    sync_plan["create"], sync_plan["close"]]):
            print("no drift anywhere — board matches code, queue matches ground truth")
        return
    apply_plan(queue_plan, queue)
    applied = apply_moves(moves)
    apply_sync_threads(sync_plan)
    # Re-derive so the posted report reflects what the org now IS (moves render as
    # what was applied this run — they're already done, not pending).
    queue_plan, _, sync_plan, findings = gather_all(queue)
    upsert_report(queue, render_report(queue_plan, findings, now, queue,
                                       applied, sync_plan))


if __name__ == "__main__":
    main()
