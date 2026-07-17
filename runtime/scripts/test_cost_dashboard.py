#!/usr/bin/env python3
"""test_cost_dashboard.py — stdlib-only checks for the spend-ledger dashboard server (no pytest;
runs in CI via `python3 scripts/test_cost_dashboard.py`). The load-bearing invariants: /data.json
serves exactly the ledger's parseable rows (fresh from disk each request, tolerant of junk lines),
/logs.json tails the watch.sh streams incrementally (cursor = bytes consumed; rotation-aware;
client cursor keys are looked up, never opened), the page ships self-contained (no external
assets to fetch), and the server never writes."""
import json
import os
import sys
import tempfile
import threading
import urllib.parse
import urllib.request
from http.server import ThreadingHTTPServer

import cost_dashboard

failures = []


def check(name, cond):
    if cond:
        print(f"ok   - {name}")
    else:
        print(f"FAIL - {name}")
        failures.append(name)


ROWS = [
    {"ts": "2026-07-11 10:00:00", "source": "cycle", "agent": "it", "model": "sonnet",
     "cost_usd": 1.25, "in": 10, "out": 20, "cache_read": 30, "cache_creation": 5,
     "turns": 3, "status": "ok", "via": "sdk"},
    {"ts": "2026-07-12 11:00:00", "source": "offload", "agent": "ceo", "model": "haiku",
     "cost_usd": 0.01, "in": 1, "out": 2, "cache_read": 0, "cache_creation": 0,
     "turns": 0, "status": "ok", "via": "cli"},
]

with tempfile.TemporaryDirectory() as td:
    ledger = os.path.join(td, "spend.jsonl")
    with open(ledger, "w", encoding="utf-8") as fh:
        for r in ROWS:
            fh.write(json.dumps(r) + "\n")
        fh.write("not json — the parser must skip this line\n")

    cost_dashboard.Handler.ledger = ledger
    srv = ThreadingHTTPServer(("127.0.0.1", 0), cost_dashboard.Handler)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    def get(path):
        with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}") as res:
            return res.status, res.read()

    status, body = get("/")
    page = body.decode("utf-8")
    check("GET / serves the page", status == 200 and "Org spend ledger" in page)
    # the SVG namespace URI is an identifier, not a fetch — it's the one allowed "http"
    stripped = page.replace("http://www.w3.org/2000/svg", "")
    check("page is self-contained (no external assets)",
          "http://" not in stripped and "https://" not in stripped
          and "<link" not in stripped and 'src="' not in stripped)

    status, body = get("/data.json")
    data = json.loads(body)
    check("GET /data.json serves the ledger rows", status == 200 and data["rows"] == ROWS)
    check("data.json names its ledger", data["ledger"] == ledger)

    # live view: an append is visible on the next request, no restart
    with open(ledger, "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"ts": "2026-07-12 12:00:00", "source": "cycle", "agent": "warden",
                             "model": "haiku", "cost_usd": 0.002}) + "\n")
    check("appended rows appear on refetch", len(json.loads(get("/data.json")[1])["rows"]) == 3)

    try:
        get("/etc/passwd")
        check("unknown paths 404", False)
    except urllib.error.HTTPError as e:
        check("unknown paths 404", e.code == 404)

    with open(ledger, encoding="utf-8") as fh:
        check("server never writes the ledger", sum(1 for _ in fh) == 4)

    # --- /logs.json: the watch.sh streams over HTTP -----------------------------------
    logs_dir = os.path.join(td, "logs")
    os.makedirs(logs_dir)
    with open(os.path.join(logs_dir, "runner.log"), "w", encoding="utf-8") as fh:
        fh.write("tick one\ntick two\n")
    with open(os.path.join(logs_dir, "agent-it.log"), "w", encoding="utf-8") as fh:
        fh.write("\x1b[32mcolored cycle line\x1b[0m\n")
    cost_dashboard.Handler.logs_dir = logs_dir

    logs = json.loads(get("/logs.json")[1])
    labels = {s["label"] for s in logs["streams"]}
    check("streams labeled like watch.sh (RUNNER + dept from agent-*.log)",
          labels == {"RUNNER", "IT"}
          and next(s for s in logs["streams"] if s["label"] == "IT")["agent"] == "it")
    check("first poll returns each log's tail, ANSI stripped",
          {(l["s"], l["t"]) for l in logs["lines"]}
          == {("RUNNER", "tick one"), ("RUNNER", "tick two"), ("IT", "colored cycle line")})

    def poll(cursor):
        return json.loads(get("/logs.json?cursor=" + urllib.parse.quote(json.dumps(cursor)))[1])

    check("caught-up poll returns nothing", poll(logs["cursor"])["lines"] == [])
    with open(os.path.join(logs_dir, "runner.log"), "a", encoding="utf-8") as fh:
        fh.write("tick three\npartial with no newline")
    nxt = poll(logs["cursor"])
    check("poll returns only appended complete lines (partial stays unconsumed)",
          [(l["s"], l["t"]) for l in nxt["lines"]] == [("RUNNER", "tick three")])

    with open(os.path.join(logs_dir, "runner.log"), "w", encoding="utf-8") as fh:
        fh.write("fresh after rotation\n")
    check("rotation (offset past EOF) restarts from the top",
          ("RUNNER", "fresh after rotation") in
          {(l["s"], l["t"]) for l in poll(nxt["cursor"])["lines"]})

    evil = poll({"../../../../etc/passwd": 0})
    check("cursor keys are lookups, never paths to open",
          {s["label"] for s in evil["streams"]} == {"RUNNER", "IT"}
          and set(evil["cursor"]) == {"runner.log", "agent-it.log"})

    # --- POST /chat: validation + routing (run_chat patched out — CI never calls a model)
    def post(payload, raw=None):
        req = urllib.request.Request(f"http://127.0.0.1:{port}/chat", method="POST",
                                     data=raw if raw is not None else json.dumps(payload).encode("utf-8"),
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req) as res:
                return res.status, json.loads(res.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    check("chat rejects a non-JSON body", post(None, raw=b"not json")[0] == 400)
    check("chat rejects an empty message", post({"message": "  "})[0] == 400)

    def ask(payload):
        """POST /chat then poll the job to completion — the flow the page uses."""
        status, out = post(payload)
        if status != 200 or not out.get("job"):
            return status, out
        import time as _t
        for _ in range(100):
            p = json.loads(get(f"/chat/poll?job={out['job']}")[1])
            if p["status"] == "done":
                return 200, p["result"]
            _t.sleep(0.02)
        return 200, {"error": "poll never finished"}

    real_run_chat = cost_dashboard.run_chat
    calls = []
    cost_dashboard.run_chat = lambda message, session_id, tier, ledger, logs_dir, progress=None: (
        calls.append((message, session_id, tier, ledger, logs_dir)) or
        {"text": f"echo:{message}", "session_id": "s1", "cost_usd": 0.001,
         "turns": 2, "model": tier, "ms": 5, "error": ""})
    status, out = ask({"message": "hi", "model": "sonnet", "session_id": "prev"})
    check("chat job resolves with run_chat's result (message/session/model routed)",
          status == 200 and out["text"] == "echo:hi" and out["model"] == "sonnet"
          and calls[-1][:3] == ("hi", "prev", "sonnet") and calls[-1][3] == ledger)
    check("an unknown model falls back to haiku",
          ask({"message": "hi", "model": "opus-max"})[1]["model"] == "haiku")
    check("polling an unknown job says so, not a hang",
          json.loads(get("/chat/poll?job=nope")[1])["status"] == "unknown")
    cost_dashboard.run_chat = real_run_chat

    # digest is pure computation over the ledger — usable without the SDK installed
    digest = cost_dashboard.chat_digest(ledger, logs_dir)
    check("chat digest totals the ledger and lists the logs",
          "total $1.26" in digest and "runner.log" in digest and "by agent" in digest)

    # freshest file's lines must land LAST (bottom of the feed), whatever the glob order
    os.utime(os.path.join(logs_dir, "runner.log"), (1000, 1000))
    os.utime(os.path.join(logs_dir, "agent-it.log"), (2000, 2000))
    check("line batches ordered by file mtime, newest content last",
          [l["s"] for l in json.loads(get("/logs.json")[1])["lines"]] == ["RUNNER", "IT"])
    os.utime(os.path.join(logs_dir, "agent-it.log"), (1000, 1000))
    os.utime(os.path.join(logs_dir, "runner.log"), (2000, 2000))
    check("…and it follows the mtimes, not the names",
          [l["s"] for l in json.loads(get("/logs.json")[1])["lines"]] == ["IT", "RUNNER"])

    srv.shutdown()

print(("PASS" if not failures else f"FAIL ({len(failures)})"))
sys.exit(1 if failures else 0)
