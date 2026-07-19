#!/usr/bin/env python3
"""runner.py — the one poller (GITHUB-NATIVE-PLAN.md Phase 3).

Each tick: read the cursor → ask GitHub what changed since (issues + PRs + comments on
both repos, workflow runs on the product repo; PRs ride the issues poll, split out as
kind: pr by normalize_issue) → normalize to events → route through
wake-rules.yaml → wake the owning departments via agent-run.sh → advance the cursor.
GitHub is the durable queue: a closed laptop just means the next tick drains a longer
backlog, oldest first. This one loop is what retires the watcher fleet and the
registrar's wake routing at Phase 3 exit.

Polling is CONDITIONAL: each endpoint's ETag rides in the cursor and is replayed as
If-None-Match, so a quiet tick's polls (and repeat issue-label lookups) revalidate as
304s, which GitHub does not bill against the rate limit — the steady-state poll costs
~0 quota. GH_NO_ETAG=1 disables (plain 200s every tick, as before).

Modes (RUNNER_MODE in .env — the strangler switch):
  shadow (default)  poll + route + LOG what would wake; fire nothing. Runs beside the
                    legacy wake system so the two can be compared for the parallel run.
  live              actually wake departments (agent-run.sh, WAKE_REASON=direct — the
                    agent's own lock, budget, and no-op gates still apply).

Guards, both modes: the kill switch (.halt) stops the tick; the GitHub rate-limit
hold (GH_RATE_FLOOR, default 500; 0 disables) skips the whole tick — no poll, no wakes —
while core or graphql remaining sits below the floor, leaving the cursor untouched so
GitHub itself queues the backlog until the window resets; the spend cap
(SPEND_BREAKER_DAILY_USD, same ledger the spend-breaker reads) stops LIVE wakes; the
wake filter (scripts/wake_filter.py, WAKE_FILTER_MODE=shadow|live|off) defers
provably-pointless wakes — closed-thread echoes, noop-streak storms, and issue-event
self-echoes (an agent's own comment bumping its issue's updatedAt back into the poll) —
with a deferred-event spool + dead-man catch-up so nothing is ever silently dropped.

Failed-wake handling (live mode): delivery used to be at-most-once — the cursor
advances per tick, so a wake that crashed (missing agent definition, aborted/timed-out
cycle, contended lock exiting 75) consumed its events forever (org#12's creation event
died exactly this way on 2026-07-16). Now a nonzero dispatch RE-QUEUES the wake's
events through the same deferred-event spool (redelivered on the dept's next fired
wake, or by the catch-up sweep after WAKE_RETRY_MIN); after WAKE_MAX_RETRIES failed
attempts an event dead-letters to state/dead-letter.jsonl instead of retrying forever.
Every failed attempt is recorded — a zero-cost status=error row in state/spend.jsonl
(source=wake) and an audit row in state/wake-filter.jsonl — so a dropped dispatch is
visible in the ledgers, never silent.

Usage: runner.py tick   (scheduled — runner-watch.sh)  |  runner.py preview  (one dry tick)
"""
import json
import os
import re
import subprocess
import sys
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CURSOR = os.path.join(REPO, "state", "runner-cursor.json")
RULES = os.path.join(REPO, "wake-rules.yaml")
HALT = os.path.join(REPO, ".halt")
DEAD_LETTER = os.path.join(REPO, "state", "dead-letter.jsonl")
MAX_SEEN = 500  # dedup ring per source — far beyond one tick's realistic event count
# Failed-wake requeue ceiling: attempts before an event dead-letters. With the catch-up
# sweep retrying every ~WAKE_RETRY_MIN, the default rides out one full lock-TTL-length
# cycle of the same agent (the legitimate transient) without looping a hard failure
# (a missing agents/<dept>.md) forever.
MAX_WAKE_RETRIES = int(os.environ.get("WAKE_MAX_RETRIES", "4"))


def require_env(name):
    """Identity env vars have no fallback: a runner silently polling someone else's repo
    is worse than one that refuses to start (the wrappers source .env before us)."""
    val = os.environ.get(name)
    if not val:
        sys.exit(f"runner: {name} not set — cp .env.example .env and fill it in")
    return val


# --- pure helpers (unit-tested in test_runner.py; no I/O) --------------------------------

def normalize_issue(item, repo_key):
    """One GitHub issue/PR (REST shape) → a routable event. The issues endpoints return
    PRs too (a `pull_request` sub-object marks them, carrying merged_at) — those become
    kind: pr with a pr_state of open|merged|closed, so the rules table can route PR
    traffic (org#13: the Chairman's charter merge woke nobody). number + issue_state
    feed attribute_issue_bumps / wake_filter's self-echo rule."""
    pr = item.get("pull_request")
    kind = "pr" if pr else "issue"
    state = item.get("state") or ""
    ev = {
        "id": f"{repo_key}-{kind}-{item.get('number')}-{item.get('updated_at', '')}",
        "kind": kind,
        "repo": repo_key,
        "at": item.get("updated_at") or "",
        "number": item.get("number"),
        "issue_state": state,
        "title": item.get("title") or "",
        "url": item.get("html_url") or "",
        "labels": sorted(l.get("name", "") for l in item.get("labels") or []),
    }
    if pr:
        ev["pr_state"] = "merged" if pr.get("merged_at") else state
    return ev


def normalize_comment(item, repo_key):
    """One issue comment (REST shape) → a routable event. The comment API carries no
    labels; route() resolves from-label via the issue URL the caller maps in. `banner`
    is the author signal read from the FULL body (the title's first line can be a
    machine marker like '<!-- warden-report -->', hiding the banner beneath it)."""
    return {
        "id": f"{repo_key}-comment-{item.get('id')}",
        "kind": "comment",
        "repo": repo_key,
        "at": item.get("updated_at") or item.get("created_at") or "",
        "title": (item.get("body") or "").splitlines()[0][:120] if item.get("body") else "",
        "url": item.get("html_url") or "",
        "issue_url": item.get("issue_url") or "",
        "banner": body_banner(item.get("body")),
        "labels": [],
    }


def normalize_run(item, repo_key):
    """One workflow run (REST shape) → a routable event. Only completed runs route —
    an in-progress run would wake someone with nothing to act on yet."""
    return {
        "id": f"{repo_key}-run-{item.get('id')}",
        "kind": "run",
        "repo": repo_key,
        "at": item.get("updated_at") or item.get("created_at") or "",
        "title": f"{item.get('name') or item.get('path') or '?'}: {item.get('conclusion') or item.get('status')}",
        "url": item.get("html_url") or "",
        "labels": [],
        "workflow": os.path.basename(item.get("path") or ""),
        "conclusion": item.get("conclusion") or "",
        "status": item.get("status") or "",
    }


def rule_matches(match, event):
    """Every given field must match; label means 'present on the event'."""
    for key, want in (match or {}).items():
        if key == "label":
            if want not in event.get("labels", []):
                return False
        elif str(event.get(key, "")) != str(want):
            return False
    return True


def depts_from_labels(labels):
    """Every dept:* label → department names. Multi-label fan-out is what keeps a
    two-party thread alive: a Business⇄IT ticket carries both labels, so each side's
    comment wakes the other."""
    return [str(lab).split(":", 1)[1] for lab in labels or []
            if str(lab).startswith("dept:")]


BANNER_RE = re.compile(r"\*\*\s*(\w+)\s*—", re.UNICODE)


def banner_dept(text):
    """The identity banner's department ('**IT —** …' → 'it'). All agents share one
    GitHub identity, so the mandated banner is the only author signal — it lets the
    runner skip echo-waking the department that wrote the comment. None when unsigned
    (a human comment, or an agent forgetting the banner — then everyone labeled wakes,
    which fails safe: a wasted wake beats a stalled thread)."""
    m = BANNER_RE.match((text or "").strip())
    return m.group(1).lower() if m else None


_MARKER_LINE = re.compile(r"^\s*<!--.*-->\s*$")


def body_banner(body):
    """banner_dept over a comment BODY: agent comments open with the banner, but a
    machine marker may sit above it (warden's report opens '<!-- warden-report -->'),
    so skip leading blank and full-line HTML-comment lines and read the first
    substantive line. None = human/unsigned, same fail-safe as banner_dept."""
    for line in (body or "").splitlines():
        if not line.strip() or _MARKER_LINE.match(line):
            continue
        return banner_dept(line)
    return None


def issue_number_from_url(url):
    """Trailing issue number of an api/html issue URL ('…/issues/138'); None when absent."""
    m = re.search(r"/issues/(\d+)$", str(url or "").rstrip("/"))
    return int(m.group(1)) if m else None


def attribute_issue_bumps(events):
    """Stamp bump_dept on each issue/PR event whose updatedAt bump was caused by an org
    agent's own bannered comment — the self-echo wake_filter v2 defers. GitHub sets the
    parent issue's updated_at to the causing comment's updated_at (to the second,
    verified on org#138), so an exact timestamp match against a same-poll comment on
    the same issue IS the cause. PR conversation comments are issue comments (their
    issue_url carries the PR number), so PR bumps attribute identically. Must run on
    the PRE-dedup batch: an EDITED comment
    keeps its id (already in the seen ring) and survives only long enough to attribute
    the bump — precisely the warden report-refresh echo this exists to catch. Anything
    unmatched (human edit, label change, close, a poll gap) stays unattributed and
    wake_filter fires it exactly as before."""
    causes = {}
    for ev in events:
        if ev.get("kind") == "comment" and ev.get("banner"):
            num = issue_number_from_url(ev.get("issue_url"))
            if num is not None and ev.get("at"):
                causes[(ev.get("repo"), num, ev.get("at"))] = ev["banner"]
    for ev in events:
        if ev.get("kind") in ("issue", "pr"):
            dept = causes.get((ev.get("repo"), ev.get("number"), ev.get("at")))
            if dept:
                ev["bump_dept"] = dept
    return events


def route(events, rules):
    """(wakes, unrouted): wakes are (dept, event) pairs, first matching rule wins,
    oldest event first — catch-up order is event order, per the plan. `from-label`
    fans out to EVERY dept:* label on the item, minus the comment's own banner author
    (no self-echo). A rule-matched event whose fan-out is empty (an agent talking on
    its own single-label ticket) is handled, not unrouted."""
    wakes, unrouted = [], []
    for ev in sorted(events, key=lambda e: e.get("at") or ""):
        matched = False
        for rule in rules or []:
            if rule_matches(rule.get("match"), ev):
                matched = True
                dept = rule.get("wake")
                if dept == "from-label":
                    author = banner_dept(ev.get("title")) if ev.get("kind") == "comment" else None
                    for d in depts_from_labels(ev.get("labels")):
                        if d != author:
                            wakes.append((d, ev))
                else:
                    wakes.append((dept, ev))
                break
        if not matched:
            unrouted.append(ev)
    return wakes, unrouted


def dedup(events, seen_ids):
    """Drop events whose id is in the seen ring (a re-poll overlap, not new inbound)."""
    seen = set(seen_ids or [])
    return [e for e in events if e.get("id") not in seen]


def next_cursor(cursor, events, now_iso):
    """Advance since-timestamps to the newest event seen (never past now), and roll the
    seen-id ring forward, keeping the newest MAX_SEEN."""
    out = dict(cursor or {})
    newest = max((e.get("at") or "" for e in events), default="")
    if newest:
        out["since"] = min(newest, now_iso)
    ids = list(out.get("seen", [])) + [e["id"] for e in events]
    out["seen"] = ids[-MAX_SEEN:]
    return out


def batch_wakes(wakes):
    """One wake per department per tick, carrying every triggering event — waking a dept
    five times for five comments is the noise the old architecture taught us to suppress."""
    by_dept = {}
    for dept, ev in wakes:
        by_dept.setdefault(dept, []).append(ev)
    return by_dept


def parse_gh_response(raw):
    """(status, etag, body) from `gh api -i` output — headers end at the first blank
    line (gh emits CRLF), status is the int in 'HTTP/2.0 304 Not Modified', etag is
    the Etag header verbatim. (0, None, b'') when unparseable — the caller degrades
    to a plain failed-poll no-op."""
    for sep in (b"\r\n\r\n", b"\n\n"):
        if sep in raw:
            head, body = raw.split(sep, 1)
            break
    else:
        head, body = raw, b""
    lines = head.replace(b"\r\n", b"\n").split(b"\n")
    m = re.match(rb"HTTP/[\d.]+\s+(\d{3})", lines[0] if lines else b"")
    if not m:
        return 0, None, b""
    etag = None
    for ln in lines[1:]:
        if ln.lower().startswith(b"etag:"):
            etag = ln.split(b":", 1)[1].strip().decode("ascii", "replace")
    return int(m.group(1)), etag, body


ISSUE_CACHE_MAX = 200  # persisted issue-lookup entries — a tick's comment traffic is ~10s


def prune_issue_cache(cache, keep=ISSUE_CACHE_MAX):
    """Newest `keep` entries by last-touched ts — bounds cursor growth. An evicted
    issue just re-pays one billed 200 on its next comment."""
    items = sorted((cache or {}).items(), key=lambda kv: kv[1].get("ts", ""),
                   reverse=True)
    return dict(items[:keep])


def rate_hold(resources, floor, now_epoch):
    """(hold, reason) from a gh `rate_limit` response's resources block. Hold when core
    or graphql remaining sits below the floor and the window hasn't reset — both buckets
    matter: the runner's own poll is REST (core), but agent cycles lean on `gh pr list`/
    `gh issue list`/`gh project`, which spend graphql. A throttled tick must hold BEFORE
    polling: gh_json returns [] on a 403, so a rate-limited poll is indistinguishable
    from a quiet org, and every wake it did fire would boot an agent whose own gh calls
    are about to fail the same way. Fail-open on missing/malformed data — an unreadable
    meter must never stop the org."""
    worst = None
    for name in ("core", "graphql"):
        res = (resources or {}).get(name) or {}
        try:
            remaining, reset = int(res["remaining"]), int(res["reset"])
        except (KeyError, TypeError, ValueError):
            continue
        if remaining < floor and reset > now_epoch and (worst is None or remaining < worst[1]):
            worst = (name, remaining, reset)
    if worst is None:
        return False, ""
    name, remaining, reset = worst
    mins = int((reset - now_epoch) // 60) + 1
    return True, f"{name} {remaining} remaining < floor {floor}, resets in ~{mins}m"


def _retries(event):
    """The event's failed-delivery count; garbage (a hand-edited spool) reads as 0."""
    try:
        return int(event.get("retries", 0) or 0)
    except (TypeError, ValueError):
        return 0


def split_retryable(events, max_retries):
    """(retry, dead) for the events of a FAILED wake, each with its retries count
    incremented. Events still at or under max_retries go back to the spool; the rest
    dead-letter — bounded retries, so a hard failure can't crash-loop forever, and
    nothing is ever silently dropped. Input events are not mutated."""
    retry, dead = [], []
    for ev in events or []:
        stamped = dict(ev, retries=_retries(ev) + 1)
        (retry if stamped["retries"] <= max_retries else dead).append(stamped)
    return retry, dead


def spend_today(rows, today):
    """Metered $ so far today from ledger rows (ts 'YYYY-MM-DD …')."""
    total = 0.0
    for r in rows:
        try:
            if str(r.get("ts", "")).startswith(today):
                total += float(r.get("cost_usd", 0) or 0)
        except (TypeError, ValueError):
            continue
    return round(total, 2)


# --- I/O ----------------------------------------------------------------------------------

def gh_json(path):
    """gh api → parsed JSON; [] on any failure (offline, auth) — a failed poll is a
    no-op tick, never a crash, and the cursor does not advance past what we saw."""
    try:
        out = subprocess.run(["gh", "api", path], capture_output=True, timeout=60)
        if out.returncode != 0:
            return []
        got = json.loads(out.stdout or b"null")
        return got if isinstance(got, list) else got or []
    except (OSError, ValueError):
        return []


def rate_limit_resources():
    """gh api rate_limit → the `resources` block; {} on failure. This endpoint is
    exempt from the quota, so the check costs nothing even when fully throttled."""
    got = gh_json("rate_limit")
    return (got.get("resources") or {}) if isinstance(got, dict) else {}


def gh_json_etag(path, etag=None):
    """Conditional gh api GET → (data, etag, unchanged). Sends If-None-Match when we
    hold an ETag from a prior poll; a 304 reply costs ZERO rate-limit quota (verified:
    X-Ratelimit-Remaining does not move) and means the body is byte-identical to what
    the last saved tick already processed — so 'no new events' is exact, not a guess,
    even after `since` advances (an identical body would dedup away anyway). gh exits
    nonzero on 304, so status comes from the -i headers, not the exit code. Any
    failure or parse anomaly degrades to ([], None, False) — the same no-op-tick
    semantics as gh_json. GH_NO_ETAG=1 is the escape hatch (callers just never pass
    an etag)."""
    args = ["gh", "api", "-i", path]
    if etag:
        args += ["-H", f"If-None-Match: {etag}"]
    try:
        out = subprocess.run(args, capture_output=True, timeout=60)
        status, new_etag, body = parse_gh_response(out.stdout or b"")
        if status == 304:
            return [], etag, True
        if status != 200:
            return [], None, False
        got = json.loads(body or b"null")
        return (got if isinstance(got, list) else got or []), new_etag, False
    except (OSError, ValueError):
        return [], None, False


def fetch_events(cursor):
    """→ (events, etags). Each of the 5 poll endpoints keeps its last ETag in the
    cursor; a quiet tick revalidates all of them as free 304s instead of billed 200s
    (the steady-state poll drops from ~60 billed calls/hour to ~0). The issues
    endpoints return PRs too — normalize_issue splits those into kind: pr, so PR
    traffic routes with zero extra polling."""
    org = require_env("ORG_GH_REPO")
    product = require_env("PRODUCT_GH_REPO")
    since = cursor.get("since") or time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                                 time.gmtime(time.time() - 86400))
    etags = dict(cursor.get("etags") or {})
    use_etags = not os.environ.get("GH_NO_ETAG")

    def poll(key, path):
        data, etag, unchanged = gh_json_etag(path, etags.get(key) if use_etags else None)
        if unchanged:
            return []
        if etag:
            etags[key] = etag
        return data

    events = []
    for repo, key in ((org, "org"), (product, "product")):
        for it in poll(f"{key}-issues",
                       f"repos/{repo}/issues?state=all&sort=updated&direction=asc"
                       f"&since={since}&per_page=100"):
            events.append(normalize_issue(it, key))
        for c in poll(f"{key}-comments",
                      f"repos/{repo}/issues/comments?sort=updated&direction=asc"
                      f"&since={since}&per_page=100"):
            events.append(normalize_comment(c, key))
    runs = poll("product-runs", f"repos/{product}/actions/runs?created=%3E{since}&per_page=50")
    for r in (runs.get("workflow_runs", []) if isinstance(runs, dict) else runs):
        ev = normalize_run(r, "product")
        if ev["status"] == "completed":
            events.append(ev)
    return events, etags


def resolve_comment_labels(events, issue_cache=None, now_iso=""):
    """→ (events, cache). from-label routing for comments needs the parent issue's
    labels — one lookup per distinct issue, only for comments (bounded by the tick's
    comment traffic). The same lookup stashes the issue's open/closed state on the
    event for free — wake_filter's closed-thread-echo rule reads it with no additional
    API call. The cache persists in the cursor WITH each issue's ETag, so a repeat
    lookup on an unchanged issue revalidates as a free 304 and reuses the cached
    labels/state — still exactly current (304 ⇒ the issue is byte-identical), never
    stale-cache routing."""
    cache = dict(issue_cache or {})
    fresh = {}  # paths verified THIS tick — at most one request per issue per tick
    for ev in events:
        if ev["kind"] != "comment" or not ev.get("issue_url"):
            continue
        path = ev["issue_url"].split("github.com/", 1)[-1]
        path = path if path.startswith("repos/") else "repos/" + path.split("api.github.com/repos/")[-1]
        if path not in fresh:
            held = cache.get(path) or {}
            use_etag = held.get("etag") if not os.environ.get("GH_NO_ETAG") else None
            data, etag, unchanged = gh_json_etag(path, use_etag)
            if unchanged:
                entry = dict(held, ts=now_iso)
            else:
                item = data if isinstance(data, dict) else {}
                entry = {
                    "etag": etag,
                    "labels": sorted(l.get("name", "") for l in item.get("labels", [])),
                    "state": item.get("state", ""),
                    "ts": now_iso,
                }
            fresh[path] = entry
            cache[path] = entry
        ev["labels"] = fresh[path].get("labels", [])
        ev["issue_state"] = fresh[path].get("state", "")
    return events, cache


def load_rules():
    import yaml
    with open(RULES, encoding="utf-8") as fh:
        return (yaml.safe_load(fh) or {}).get("rules") or []


def load_cursor():
    try:
        with open(CURSOR, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def save_cursor(cursor):
    os.makedirs(os.path.dirname(CURSOR), exist_ok=True)
    tmp = CURSOR + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(cursor, fh)
    os.replace(tmp, CURSOR)


def fire_wake(dept, evs, carried=None):
    """Dispatch one wake; the returncode is the delivery verdict the caller re-queues
    on. Never raises: a dispatch that times out or can't exec is a FAILED delivery
    (nonzero), not a crashed tick — the other departments' wakes must still fire and
    the cursor must still advance (their events were delivered)."""
    note = "; ".join(f"{e['title']} <{e['url']}>" for e in evs[:5])
    if carried:  # deferred ≠ dropped: spooled events ride the next fired wake's note
        note += " | deferred earlier: " + "; ".join(
            f"{e.get('title', '?')} <{e.get('url', '?')}>" for e in carried[:5])
    env = dict(os.environ, WAKE_REASON="direct", WAKE_NOTE=f"github: {note}")
    try:
        return subprocess.run(["scripts/agent-run.sh", dept], cwd=REPO, env=env,
                              timeout=1800).returncode
    except subprocess.TimeoutExpired:
        return 124
    except OSError:
        return 127


def dead_letter(dept, entries, rc, now_iso):
    """Append exhausted events to state/dead-letter.jsonl — the terminal record for an
    event the org could not deliver. Loud on any write failure: a dead-letter that
    can't land must at least land in the runner log."""
    try:
        os.makedirs(os.path.dirname(DEAD_LETTER), exist_ok=True)
        with open(DEAD_LETTER, "a", encoding="utf-8") as fh:
            for e in entries:
                fh.write(json.dumps(dict(e, dept=dept, rc=rc,
                                         dead_lettered_at=now_iso)) + "\n")
        return True
    except OSError as exc:
        print(f"runner: dead-letter WRITE FAILED for {dept} ({exc}) — "
              f"{len(entries)} event(s) recorded only in this log")
        return False


def requeue_failed(dept, evs, rc, fmode, now_iso):
    """A fired wake exited nonzero — its events were consumed by the cursor but never
    acted on. Re-queue them (at-most-once → bounded at-least-once): under
    MAX_WAKE_RETRIES they respool for redelivery (the dept's next fired wake, or the
    catch-up sweep after WAKE_RETRY_MIN); past it they dead-letter. The attempt is
    recorded in state — an audit row per verdict, plus a zero-cost status=error row in
    the spend ledger so the failed dispatch shows up next to what wakes cost."""
    import spend_log
    import wake_filter
    retry, dead = split_retryable(evs, MAX_WAKE_RETRIES)
    if retry and not wake_filter.spool(dept, retry, now_iso):
        dead, retry = dead + retry, []  # spool unwritable — dead-letter beats a silent drop
    if retry:
        wake_filter.audit(dept, retry, "requeue", f"wake-failed-rc-{rc}", fmode)
        print(f"runner: REQUEUE {dept} — wake failed (rc={rc}); "
              f"{len(retry)} event(s) respooled for redelivery")
    if dead:
        dead_letter(dept, dead, rc, now_iso)
        wake_filter.audit(dept, dead, "dead-letter", f"wake-failed-rc-{rc}", fmode)
        print(f"runner: DEAD-LETTER {dept} — {len(dead)} event(s) failed "
              f"{MAX_WAKE_RETRIES}+ deliveries (rc={rc}) → state/dead-letter.jsonl")
    spend_log.append(source="wake", agent=dept, wake="direct", via="runner",
                     status="error", cost_usd=0.0)


def cmd_tick(preview=False):
    if os.path.exists(HALT):
        print("runner: .halt engaged — tick skipped")
        return
    floor = int(os.environ.get("GH_RATE_FLOOR", "500"))
    resources = rate_limit_resources()
    limited, why = rate_hold(resources, floor, time.time())
    if limited:
        print(f"runner: RATE-LIMIT HOLD — {why}; tick skipped (no poll, no wakes; "
              f"cursor untouched, GitHub queues the backlog)")
        return
    mode = os.environ.get("RUNNER_MODE", "shadow")
    cursor = load_cursor()
    # Attribution BEFORE dedup: an edited comment keeps its id (already seen), so
    # post-dedup the issue bump it caused would look causeless and always fire.
    events, etags = fetch_events(cursor)
    events = attribute_issue_bumps(events)
    events = dedup(events, cursor.get("seen"))
    events, issue_cache = resolve_comment_labels(
        events, cursor.get("issues"), time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
    wakes, unrouted = route(events, load_rules())
    for ev in unrouted:
        print(f"runner: unrouted {ev['kind']} {ev['url']}")
    batched = batch_wakes(wakes)
    cap = float(os.environ.get("SPEND_BREAKER_DAILY_USD", "25"))
    import budget_gate
    import wake_filter
    ledger_rows = budget_gate.load_rows()
    spent = spend_today(ledger_rows, time.strftime("%Y-%m-%d"))
    # Wake filter (docs/plans/token-efficiency.md Phase 1): deterministic defer verdicts
    # BEFORE any spend. shadow (default) only logs WOULD-DEFER; live actually defers.
    fmode = wake_filter.mode()
    hold = wake_filter.deploy_hold_now() if fmode != "off" else False
    now_local = time.strftime("%Y-%m-%d %H:%M:%S")
    now_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    deferring = fmode == "live" and mode == "live" and not preview
    fired = set()
    for dept, evs in batched.items():
        head = f"runner: wake {dept} ({len(evs)} event{'s' if len(evs) != 1 else ''}: " \
               f"{evs[0]['title'][:80]}…)" if evs else f"runner: wake {dept}"
        if fmode != "off":
            cooldown = wake_filter.cooldown_active(ledger_rows, dept, now_local, hold)
            action, reason = wake_filter.batch_verdict(evs, cooldown, hold)
            if not preview:
                wake_filter.audit(dept, evs, action, reason, fmode)
            if action == "defer":
                if deferring:
                    print(f"DEFER  {head} — {reason}")
                    wake_filter.spool(dept, evs, now_iso)
                    continue
                print(f"WOULD-DEFER {head} — {reason} (wake-filter {fmode})")
        if preview or mode != "live":
            print(f"SHADOW {head}")
        elif spent >= cap:
            print(f"HELD   {head} — ${spent} ≥ ${cap}/day cap")
        else:
            print(f"LIVE   {head}")
            # Drain only on an actual fire, so a held/capped wake never eats the spool.
            # Drained in EVERY filter mode: besides filter deferrals (live-only), the
            # spool holds failed-wake requeues, which exist regardless of filter mode.
            carried = wake_filter.drain(dept)
            rc = fire_wake(dept, evs, carried=carried)
            fired.add(dept)
            if rc != 0:  # failed delivery — requeue evs AND the carried entries it ate
                requeue_failed(dept, evs + carried, rc, fmode, now_iso)
    # Catch-up sweep: a spooled event past its bound (dead-man for filter deferrals,
    # the shorter retry bound for failed-wake requeues) forces a wake even when nothing
    # new routed to that dept this tick. Respects the same $/day breaker.
    if mode == "live" and not preview:
        for dept in wake_filter.deadman_departments(now_iso):
            if dept in fired:
                continue
            if spent >= cap:
                print(f"HELD   runner: catch-up wake {dept} — ${spent} ≥ ${cap}/day cap")
                continue
            entries = wake_filter.drain(dept)
            if entries:
                print(f"LIVE   runner: catch-up wake {dept} "
                      f"({len(entries)} deferred event{'s' if len(entries) != 1 else ''})")
                wake_filter.audit(dept, entries, "deadman-fire", "deferred-past-bound", fmode)
                rc = fire_wake(dept, entries)
                if rc != 0:
                    requeue_failed(dept, entries, rc, fmode, now_iso)
    # Save on events OR etag/cache motion: a 200 that yielded zero routable events
    # still carries a fresh ETag — persisting it is what makes the NEXT poll free.
    cursor_moved = (events or etags != (cursor.get("etags") or {})
                    or issue_cache != (cursor.get("issues") or {}))
    if not preview and cursor_moved:
        nxt = next_cursor(cursor, events, time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                                        time.gmtime()))
        nxt["etags"] = etags
        nxt["issues"] = prune_issue_cache(issue_cache)
        save_cursor(nxt)
    core_left = (resources.get("core") or {}).get("remaining")
    print(f"runner: {len(events)} events → {len(batched)} wakes "
          f"({mode}{', preview' if preview else ''}"
          f"{f', api core {core_left} left' if core_left is not None else ''})")


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "tick"
    if cmd == "tick":
        cmd_tick()
    elif cmd == "preview":
        cmd_tick(preview=True)
    else:
        sys.exit("usage: runner.py <tick | preview>")


if __name__ == "__main__":
    main()
