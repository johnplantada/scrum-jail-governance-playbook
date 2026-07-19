#!/usr/bin/env python3
"""metrics_watch.py — the org's sensors pointed at the MARKET (docs/METRICS.md).

The constitution's core loop is hypothesis → action → MEASURE → learn, and until this
watcher the measure leg didn't exist: nothing a stranger did could ever wake an agent.
This is the same deterministic-watcher pattern as deploy-watch.sh, aimed at demand:

  collect   poll every configured source (metrics.yaml) — the product's counters
            endpoint today — and append observations to the store
            (state/metrics.jsonl). Append-on-change plus one sample per day per
            metric, so the store carries both step-changes and a daily trend line.
  announce  diff the store's latest values against the last-announced cursor
            (state/metrics-cursor.json) and print ONE demand digest per sweep; a
            metric whose config declares an `owner` and whose change is a `milestone`
            prints a loud milestone line naming that department. (The chat-era
            version posted these to channels; routing a milestone into a department
            wake is a wake-rules.yaml follow-up.)
  sweep     collect + announce (what the cron/launchd entry runs).

Sources (metrics.yaml at the repo root — committed, it's config not state):
  http_json  GET a JSON endpoint (URL supports ${ENV} expansion), extract dotted
             paths into named metrics. The product website emits its counters this
             way (template: scripts/templates/product-repo/metrics-endpoint.md).
  manual     no polling — a human appends observations with
             `scripts/metrics.py add` (mug/Spring sales, anything dashboard-only).
             The announcer treats them identically, so manual entries wake agents too.

Everything is fail-soft per source: one dead API never loses the other signals, and a
broken sweep never breaks an agent cycle (this runs from cron/launchd, not from wakes).
Pure helpers are unit-tested in test_metrics_watch.py without yaml or network.
"""
import json
import os
import sys
import time
import urllib.error
import urllib.request

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STORE = os.path.join(REPO, "state", "metrics.jsonl")
CURSOR = os.path.join(REPO, "state", "metrics-cursor.json")
CONFIG = os.path.join(REPO, "metrics.yaml")
TS_FMT = "%Y-%m-%d %H:%M:%S"


# --- pure helpers (no I/O, no yaml — unit-tested) -----------------------------------

def expand_env(s, env):
    """Expand ${VAR} in a config string; unresolved vars leave the string unusable —
    return None so the caller can skip the source instead of hitting a bogus URL."""
    out = s
    for k, v in env.items():
        out = out.replace("${%s}" % k, v)
    if "${" in out:
        return None
    return out


def extract_path(obj, path):
    """Dotted-path lookup into parsed JSON; None when any hop is missing/non-numeric."""
    cur = obj
    for hop in path.split("."):
        if not isinstance(cur, dict) or hop not in cur:
            return None
        cur = cur[hop]
    if isinstance(cur, bool) or not isinstance(cur, (int, float)):
        return None
    return cur


def latest_values(rows):
    """Latest observation per 'source.metric' from store rows (append order wins)."""
    out = {}
    for r in rows:
        try:
            key = f"{r['source']}.{r['metric']}"
            out[key] = (r["value"], str(r.get("ts", "")))
        except (KeyError, TypeError):
            continue
    return out


def observations_to_append(latest, fresh, today):
    """Which fresh readings deserve a store row: value changed, or first sample today
    (a flat daily heartbeat keeps the trend line queryable without bloating the store)."""
    out = []
    for key, value in fresh.items():
        prev = latest.get(key)
        if prev is None or prev[0] != value or not prev[1].startswith(today):
            out.append((key, value))
    return out


def is_milestone(kind, prev, new):
    """Does this change warrant waking the owning department (not just the record)?"""
    if kind == "any_change":
        return prev != new
    if kind == "first_nonzero":
        return (prev is None or prev == 0) and new not in (None, 0)
    return False


def diff_for_announce(cursor, latest):
    """(key, old, new) for every metric whose latest value differs from last-announced."""
    out = []
    for key, (value, _ts) in sorted(latest.items()):
        old = cursor.get(key)
        if old != value:
            out.append((key, old, value))
    return out


# --- store / cursor I/O --------------------------------------------------------------

def load_rows(path=STORE):
    rows = []
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    try:
                        rows.append(json.loads(line))
                    except ValueError:
                        continue
    except OSError:
        pass
    return rows


def append_rows(entries, path=STORE):
    """entries: [(key, value, note)] — one JSONL row each, best-effort."""
    if not entries:
        return
    os.makedirs(os.path.dirname(path), exist_ok=True)
    ts = time.strftime(TS_FMT)
    with open(path, "a", encoding="utf-8") as fh:
        for key, value, note in entries:
            source, _, metric = key.partition(".")
            row = {"ts": ts, "source": source, "metric": metric, "value": value}
            if note:
                row["note"] = note
            fh.write(json.dumps(row) + "\n")


def load_cursor():
    try:
        with open(CURSOR, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def save_cursor(cur):
    os.makedirs(os.path.dirname(CURSOR), exist_ok=True)
    tmp = CURSOR + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(cur, fh)
    os.replace(tmp, CURSOR)


# --- config + collection --------------------------------------------------------------

def load_config(path=CONFIG):
    import yaml  # lazy, warden-style — pure helpers stay testable without it
    with open(path, encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def fetch_json(url, headers=None):
    req = urllib.request.Request(url)
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read() or "null")


def collect_source(src, env):
    """Fresh {source.metric: value} readings from one source. Raises on failure —
    the caller catches per-source so one dead API never loses the others."""
    name, kind = src.get("name", "?"), src.get("type")
    fresh = {}
    if kind == "http_json":
        url = expand_env(src.get("url", ""), env)
        if not url:
            raise RuntimeError("URL has unresolved ${ENV} vars — set them in .env")
        body = fetch_json(url)
        for m in src.get("metrics") or []:
            v = extract_path(body, m.get("path", m.get("name", "")))
            if v is not None:
                fresh[f"{name}.{m['name']}"] = v
    elif kind == "manual":
        pass  # humans append via `scripts/metrics.py add`; nothing to poll
    else:
        raise RuntimeError(f"unknown source type {kind!r}")
    return fresh


def metric_policy(cfg, key):
    """(owner, milestone_kind) for a metric key, from its metric- or source-level config."""
    source = key.split(".", 1)[0]
    for src in cfg.get("sources") or []:
        if src.get("name") != source:
            continue
        for m in src.get("metrics") or []:
            if f"{source}.{m.get('name')}" == key:
                return m.get("owner") or src.get("owner"), m.get("milestone") or src.get("milestone") or "none"
        return src.get("owner"), src.get("milestone") or "none"
    return None, "none"


# --- announcing --------------------------------------------------------------------------

def fmt_change(key, old, new):
    return f"`{key}`: {'—' if old is None else old} → **{new}**"


# --- commands ---------------------------------------------------------------------------

def cmd_collect(cfg, env):
    rows = load_rows()
    latest = latest_values(rows)
    today = time.strftime("%Y-%m-%d")
    fresh, failures = {}, []
    for src in cfg.get("sources") or []:
        try:
            fresh.update(collect_source(src, env))
        except Exception as exc:  # fail-soft per source
            failures.append(f"{src.get('name', '?')}: {exc}")
    entries = [(k, v, "") for k, v in observations_to_append(latest, fresh, today)]
    append_rows(entries)
    for f in failures:
        print(f"metrics-watch: source SKIPPED — {f}", file=sys.stderr)
    if entries:
        print(f"metrics-watch: recorded {len(entries)} observation(s)")
    return entries


def cmd_announce(cfg):
    latest = latest_values(load_rows())
    cursor = load_cursor()
    changes = diff_for_announce(cursor, latest)
    if not changes:
        return
    digest = "Demand telemetry — " + "; ".join(fmt_change(k, o, n) for k, o, n in changes)
    print(digest)
    for key, old, new in changes:
        owner, kind = metric_policy(cfg, key)
        if owner and is_milestone(kind, old, new):
            print(f"Demand milestone for {owner} — {fmt_change(key, old, new)}. "
                  f"React to the market, not the process.")
    for key, _old, new in changes:
        cursor[key] = new
    save_cursor(cursor)
    print(f"metrics-watch: announced {len(changes)} change(s)")


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "sweep"
    if not os.path.exists(CONFIG):
        print("metrics-watch: no metrics.yaml — nothing to do")
        return
    cfg = load_config()
    env = dict(os.environ)
    if cmd == "collect":
        cmd_collect(cfg, env)
    elif cmd == "announce":
        cmd_announce(cfg)
    elif cmd == "sweep":
        cmd_collect(cfg, env)
        cmd_announce(cfg)
    else:
        sys.exit("usage: metrics_watch.py <sweep | collect | announce>")


if __name__ == "__main__":
    main()
