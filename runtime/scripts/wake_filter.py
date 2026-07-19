#!/usr/bin/env python3
"""wake_filter.py — deterministic wake suppression at the routing layer (Phase 1 of
docs/plans/token-efficiency.md).

The finding behind this module: the org enforces its quietness policies with model
tokens — an agent boots a full sonnet cycle to conclude "already handled, ending
silently" (~$0.5–0.7 each; 60–75% of wakes mutate nothing). Every rule here moves one
of those conclusions into code, where obeying it costs zero tokens.

Deliberately narrow — only verdicts that are deterministic AND fail-safe:

  closed-thread echo   an agent-bannered comment on an issue that is already CLOSED
                       defers (post-close bookkeeping echo; the CEO log shows 75 wakes
                       re-verifying work a prior cycle closed).
  noop-streak cooldown N consecutive outcome=noop cycles for a dept (3, or 2 during a
                       deploy-hold) → agent-bannered comment batches defer for a
                       cooldown window. The storm brake the $/day cap never was.
                       During a deploy-hold the cooldown also covers ATTRIBUTED issue
                       events the state-scoped rules below don't (unknown issue_state).
  issue self-echo (v2) an issue/PR event whose updatedAt bump is ATTRIBUTED to an org
                       agent's own bannered comment defers — open issue: the org#138
                       loop (warden edits its report comment each cycle; the bump mints
                       a fresh issue event, id embeds updatedAt, re-waking warden + IT
                       while the edited comment itself dedups away); closed issue: the
                       issue-side of the closed-thread echo, which used to defeat the
                       comment-side deferral. PRs echo the same way (their conversation
                       comments ARE issue comments), so kind: pr rides the same rules.
                       Attribution is the runner's job
                       (attribute_issue_bumps stamps bump_dept when a same-poll comment
                       on the same issue matches the bump timestamp EXACTLY — GitHub
                       sets the parent's updatedAt to the causing comment's).

Everything else fires. Human/unsigned comments, UNATTRIBUTED issue/PR events (human
edits, label changes, closes, a Chairman's merge, poll gaps — anything we can't prove
an agent caused), and workflow runs pierce every rule unconditionally — "a wasted wake
beats a stalled thread" stays the design's tie-breaker; we only suppress where the
waste is provable.

Fail-safes (non-negotiable):
  deferred ≠ dropped   deferred events spool to state/deferred/<dept>.jsonl and ride
                       along in the dept's next fired wake note.
  dead-man switch      a spooled event older than WAKE_DEADMAN_MIN (default 360) forces
                       a catch-up wake even if nothing else fires.
  audit everything     every verdict appends to state/wake-filter.jsonl, shadow or live.

The spool is shared plumbing: besides filter deferrals, the runner re-queues the events
of a FAILED wake here (runner.requeue_failed — a crashed/aborted agent-run.sh dispatch).
Those entries carry a `retries` count and come due on the much shorter WAKE_RETRY_MIN
(default 15) instead of the dead-man bound — a failed delivery retries promptly, in any
filter mode; only filter deferrals wait out the full dead-man window.

Modes (WAKE_FILTER_MODE): shadow (default) — log WOULD-DEFER, fire anyway; live —
actually defer; off — do nothing. Flip to live only after a shadow audit shows the
would-defer set matches the no-op wake set and never touches a human/CI event.

Pure helpers up top (unit-tested in test_wake_filter.py, no I/O); I/O at the bottom.
"""
import datetime
import json
import os
import re

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BLOCKERS = os.path.join(REPO, "blockers.yaml")
AUDIT = os.path.join(REPO, "state", "wake-filter.jsonl")
DEFER_DIR = os.path.join(REPO, "state", "deferred")

NOOP_STREAK = 3        # consecutive noop cycles before cooldown …
NOOP_STREAK_HOLD = 2   # … tighter while the org is in a deploy-hold
COOLDOWN_MIN = int(os.environ.get("WAKE_COOLDOWN_MIN", "120"))
DEADMAN_MIN = int(os.environ.get("WAKE_DEADMAN_MIN", "360"))
RETRY_MIN = int(os.environ.get("WAKE_RETRY_MIN", "15"))  # failed-wake requeue retry bound

# Same author signal the runner routes on (runner.banner_dept) — duplicated rather than
# imported so this module never depends on runner (runner imports US inside cmd_tick).
_BANNER_RE = re.compile(r"\*\*\s*(\w+)\s*—", re.UNICODE)
_HOLD_WORD = re.compile(r"(^|[^a-z0-9])(deploy|revenue)([^a-z0-9]|$)")


# --- pure helpers (unit-tested; no I/O) ---------------------------------------------------

def banner_dept(text):
    m = _BANNER_RE.match((text or "").strip())
    return m.group(1).lower() if m else None


def deploy_hold(blockers_data):
    """True when any OPEN blocker blocks deploy/revenue (word match, so entries named
    like 'ugc-c1-deploy' or 'deploy-authority' count — the ledger's actual shape)."""
    for b in (blockers_data or {}).get("blockers") or []:
        if (b.get("state") or "open").strip() != "open":
            continue
        for item in b.get("blocks") or []:
            if _HOLD_WORD.search(str(item).lower()):
                return True
    return False


def event_verdict(ev, cooldown, hold=False):
    """('fire' | 'defer', reason) for ONE event. Two event classes can defer:
    agent-bannered comments (v1) and issue/PR events whose updatedAt bump the runner
    attributed to an org agent's own bannered comment (v2 self-echo). Runs and
    everything human/unsigned/unattributed always fire."""
    if ev.get("kind") in ("issue", "pr"):
        return _issue_verdict(ev, cooldown, hold)
    if ev.get("kind") != "comment":
        return "fire", ev.get("kind", "?")
    author = ev.get("banner") or banner_dept(ev.get("title"))
    if author is None:
        return "fire", "human-or-unsigned"
    if str(ev.get("issue_state", "")).lower() == "closed":
        return "defer", "closed-thread-echo"
    if cooldown:
        return "defer", "noop-streak-cooldown"
    return "fire", "agent-comment-open-thread"


def _issue_verdict(ev, cooldown, hold):
    """Issue/PR events defer ONLY when bump_dept proves an agent comment caused the
    bump. A bannered issue TITLE is not attribution — a human relabeling an agent-titled
    ticket must still wake someone, and a Chairman's PR merge is never attributed, so it
    always fires. Unknown issue_state fails safe to fire, except under the deploy-hold
    noop-streak cooldown (the bump is still provably agent-caused; state only picks the
    reason label)."""
    kind = ev.get("kind", "issue")
    if not ev.get("bump_dept"):
        return "fire", kind  # human/CI/unknown cause — unconditional, as in v1
    state = str(ev.get("issue_state", "")).lower()
    if state == "open":
        return "defer", f"{kind}-self-echo"
    if state == "closed":
        return "defer", f"{kind}-closed-thread-echo"
    if cooldown and hold:
        return "defer", "noop-streak-cooldown"
    return "fire", f"{kind}-agent-bump-unknown-state"


def batch_verdict(evs, cooldown, hold=False):
    """One verdict for a dept's whole batch: FIRE if ANY event fires (defer-worthy
    events just ride along — the wake is already paid for). Defer only a batch that is
    deferrable in its entirety."""
    reasons = []
    for ev in evs or []:
        action, reason = event_verdict(ev, cooldown, hold)
        if action == "fire":
            return "fire", reason
        reasons.append(reason)
    return "defer", "+".join(sorted(set(reasons))) or "empty"


def noop_streak(rows, dept):
    """Consecutive most-recent outcome=noop cycles for a dept, from ledger rows.
    Only rows that CARRY an outcome participate (pre-Phase-0 rows and per-model sibling
    rows are invisible) — before tagging exists the streak is 0 and nothing defers."""
    streak = 0
    for r in reversed(list(rows or [])):
        try:
            if (r.get("source") != "cycle" or r.get("agent") != dept
                    or "outcome" not in r or str(r.get("status", "ok")) != "ok"):
                continue
            if r.get("outcome") == "noop":
                streak += 1
            else:
                break
        except AttributeError:
            continue
    return streak


def _minutes_between(ts_old, ts_new):
    try:
        fmt = "%Y-%m-%d %H:%M:%S"
        a = datetime.datetime.strptime(str(ts_old)[:19], fmt)
        b = datetime.datetime.strptime(str(ts_new)[:19], fmt)
        return (b - a).total_seconds() / 60.0
    except (TypeError, ValueError):
        return None


def last_cycle_ts(rows, dept):
    """ts of the dept's newest outcome-tagged cycle row, or ''. """
    for r in reversed(list(rows or [])):
        try:
            if (r.get("source") == "cycle" and r.get("agent") == dept
                    and "outcome" in r and str(r.get("status", "ok")) == "ok"):
                return str(r.get("ts", ""))
        except AttributeError:
            continue
    return ""


def cooldown_active(rows, dept, now_ts, hold, cooldown_min=COOLDOWN_MIN):
    """True when the dept just ran a noop streak (3, or 2 on deploy-hold) AND its last
    cycle is inside the cooldown window. Human/CI events pierce this upstream — it only
    ever gates agent-comment batches."""
    needed = NOOP_STREAK_HOLD if hold else NOOP_STREAK
    if noop_streak(rows, dept) < needed:
        return False
    mins = _minutes_between(last_cycle_ts(rows, dept), now_ts)
    return mins is not None and 0 <= mins < cooldown_min


def deadman_due(entries, now_iso, deadman_min=DEADMAN_MIN, retry_min=None):
    """True when any spooled entry has waited past its bound. Entries carry
    deferred_at as UTC ISO-8601 (same clock as event `at`). The bound is per-entry:
    a failed-wake requeue (carries `retries`) comes due after retry_min — a failed
    delivery retries promptly — while a filter deferral waits the full dead-man
    window."""
    if retry_min is None:
        retry_min = RETRY_MIN
    for e in entries or []:
        bound = retry_min if e.get("retries") else deadman_min
        mins = _minutes_between(str(e.get("deferred_at", "")).replace("T", " ")[:19],
                                str(now_iso).replace("T", " ")[:19])
        if mins is not None and mins >= bound:
            return True
    return False


# --- I/O ----------------------------------------------------------------------------------

def mode():
    m = os.environ.get("WAKE_FILTER_MODE", "shadow").strip().lower()
    return m if m in ("shadow", "live", "off") else "shadow"


def deploy_hold_now():
    """Read blockers.yaml → deploy_hold(). Fail-open (False → higher streak threshold →
    more wakes fire): a broken ledger must make us noisier, never quieter."""
    try:
        import yaml
        return deploy_hold(yaml.safe_load(open(BLOCKERS, encoding="utf-8")) or {})
    except Exception:
        return False


def audit(dept, evs, action, reason, filter_mode):
    """Append one verdict to state/wake-filter.jsonl — the shadow-week review reads
    this. Best-effort: auditing must never break a tick."""
    try:
        os.makedirs(os.path.dirname(AUDIT), exist_ok=True)
        row = {"ts": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
               "dept": dept, "action": action, "reason": reason, "mode": filter_mode,
               "events": [dict({"id": e.get("id"), "url": e.get("url")},
                               **({"bump": e["bump_dept"]} if e.get("bump_dept") else {}))
                          for e in (evs or [])[:10]],
               "n_events": len(evs or [])}
        with open(AUDIT, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row) + "\n")
    except Exception:
        pass


def _spool_path(dept):
    return os.path.join(DEFER_DIR, f"{re.sub(r'[^a-z0-9_-]', '', str(dept))}.jsonl")


def spool(dept, evs, now_iso):
    """Park deferred events for carry-forward. Best-effort, but the caller only spools
    in live mode AFTER deciding to defer — a spool failure is logged loud by the caller
    printing the DEFER line either way (the audit row also records it)."""
    try:
        os.makedirs(DEFER_DIR, exist_ok=True)
        with open(_spool_path(dept), "a", encoding="utf-8") as fh:
            for e in evs or []:
                fh.write(json.dumps(dict(e, deferred_at=now_iso)) + "\n")
        return True
    except Exception:
        return False


def spooled(dept):
    """Read (without draining) the dept's deferred entries; [] when none/unreadable."""
    try:
        with open(_spool_path(dept), encoding="utf-8") as fh:
            return [json.loads(l) for l in fh if l.strip()]
    except (OSError, ValueError):
        return []


def drain(dept):
    """Return AND clear the dept's deferred entries — call only when a wake is actually
    firing, so a held/capped wake never eats the spool."""
    entries = spooled(dept)
    if entries:
        try:
            os.remove(_spool_path(dept))
        except OSError:
            pass
    return entries


def deadman_departments(now_iso):
    """Departments whose spool has an entry past the dead-man bound."""
    try:
        depts = [f[:-6] for f in os.listdir(DEFER_DIR) if f.endswith(".jsonl")]
    except OSError:
        return []
    return [d for d in depts if deadman_due(spooled(d), now_iso)]
