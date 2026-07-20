# Field-Tested Mechanisms — what the live org runs that the docs alone won't give you

The rest of this playbook is the governance layer: gates, envelopes, ledgers. This file
is the other half — the mechanisms the live Scrum Jail org grew *after* going live, each
built in response to a real failure that cost real tokens (or nearly shipped a real
mistake). None is speculative; every mechanism in Part I is running today. Part II is
the graveyard: on **2026-07-05** the org demolished its entire chat-era runtime
(Mattermost, the bus/pm/registrar/memory/vox services, the watcher fleet, chat
approvals) and went GitHub-native — retiring about half of this file's previous edition
in one afternoon. What replaced each mechanism (or deliberately didn't) is a field note
in itself. For each entry: what it is, the failure it exists to prevent, and how the
real implementation works — so you can build the same thing into your runtime without
paying the tuition twice.

---

# Part I — Running today

## 1. The event loop — GitHub as the durable queue, wakes scarce by construction

**The failure:** the entire chat-era wake economy — when every channel post is a wake
source, you spend your engineering effort *suppressing* wakes (Part II §R1/§R2). The
rebuild inverted the premise: don't rate-limit an abundant wake supply, make wakes
scarce by construction.

**How it works:** one poller (`scripts/runner.py`, the org's only scheduled process
*that spends tokens* — the deterministic maintainers beside it, plain-cron log/metrics
jobs and the warden engine of §11, never call a model; cron'd via `runner-watch.sh`
every ~5 minutes). Each tick: read a cursor → ask GitHub
what changed since (issues + comments on both repos, completed product-repo workflow
runs) → normalize to events → route through `wake-rules.yaml` → wake the owning
departments via `agent-run.sh` → advance the cursor.

- **The runner self-deploys.** Each tick, `runner-watch.sh` fast-forwards the runtime
  checkout to `origin/main` before running, so a merged org-repo PR goes live on its own
  — no manual pull-and-restart after a merge. Fail-soft by construction: only on `main`,
  only a clean fast-forward (never a merge, rebase, or dirty tree), the `.halt` switch
  wins, and a pull that can't fast-forward logs it and runs the code already on disk —
  it never aborts the tick. Counter-ratchet-clean: one existing watcher gained one step,
  not a new watcher.

- **There are no scheduled agent wakes at all.** No heartbeats, no standups, no wake
  floors — DESIGN.md §3 states it as an operating principle: **no event, no wake, no
  spend.** A blocked org on a quiet day costs zero model tokens — the old watermark
  no-op's guarantee, now free because there's nothing to no-op.
- **GitHub is the durable queue.** The runner lives on a laptop; a closed lid is a clean
  pause, not an outage. The next tick drains a longer backlog, **oldest event first**
  (routing sorts by event timestamp), so catch-up order is event order.
- **Routing is a committed, PR-reviewed file.** `wake-rules.yaml` replaced the wake
  logic scattered across the watcher fleet — first matching rule wins. An issue matching
  no `dept:*` rule falls through to the warden for triage (patterns.md Pattern 16 — a
  web-form filing that skips the department dropdown must not be invisible); any other
  event matching nothing wakes nobody (logged as unrouted).
- **A failed poll is a no-op tick, never a crash** — `gh api` errors return empty and
  the cursor doesn't advance past what was seen. **Shadow mode** (`RUNNER_MODE=shadow`,
  the default) polls and logs what *would* wake without firing anything — how the loop
  was proven before it was trusted.

**The residual backpressure** — three amplifiers survive even scarce wakes, and the
runner handles all three in ~30 lines where the chat era needed a dispatcher subsystem:

1. **The dedup ring.** Every event carries a stable id (issue number + updated-at,
   comment id, run id); the cursor keeps the newest **500** seen ids, and a re-polled
   event whose id is in the ring is dropped as overlap, not new inbound.
2. **One wake per department per tick.** `batch_wakes()` coalesces a tick's routed
   events by department, the wake note carrying every triggering event. Waking a dept
   five times for five comments is the noise the old architecture taught the org to
   suppress — now structurally impossible.
3. **The echo-skip.** All agents share one GitHub identity, so the mandated identity
   banner (`**IT —** …`) is the only author signal. The runner parses it off each
   comment (`banner_dept()`) and excludes the author's own department from the fan-out —
   an agent never echo-wakes itself. **It fails safe:** an unsigned comment (a human, or
   an agent forgetting its banner) wakes every labeled department, because a wasted wake
   beats a stalled thread.

The honest caveat that used to sit here — at-most-once delivery, "re-wake by hand if
needed" — got fixed instead of documented around. A dispatched wake that exits nonzero
(`agent-run.sh` exits nonzero on an aborted cycle precisely so the runner can see it)
now has its events **re-queued** through the same deferred-event spool the wake filter
uses: redelivered on the department's next fired wake, or by the catch-up sweep after
`WAKE_RETRY_MIN`. Delivery is **at-least-once up to a retry ceiling** — past
`WAKE_MAX_RETRIES` failed attempts an event **dead-letters** to
`state/dead-letter.jsonl` instead of looping a hard failure forever, and every failed
attempt leaves a zero-cost error row in `state/spend.jsonl` and an audit row in
`state/wake-filter.jsonl`, so a failed dispatch is visible in the ledgers, never silent.

**Conditional polling makes the quiet tick free, not just scarce.** Wakes being scarce
still leaves the *poll itself* metered — every tick calls 5 endpoints whether or not
anything changed. The fix: the cursor keeps an ETag per poll endpoint and replays it as
`If-None-Match`; an unchanged endpoint answers `304 Not Modified`, which GitHub does not
count against the rate limit. Verified live: `core.used` was identical (57 → 57) across
a full steady-state tick — 5 revalidations, 0 billed calls — dropping the poll from
~60 billed calls/hour to ~0 at rest. The same caching covers the per-comment
issue-label lookups (labels/state/etag persist in the cursor, bounded to 200 entries,
pruned by last touch). `GH_NO_ETAG=1` reverts to plain polling if a 304 ever misbehaves;
any parse anomaly degrades to the existing failed-poll no-op semantics. This is the
direct follow-up to the GH rate-limit hold (`GH_RATE_FLOOR`, which pauses all wakes once
quota runs low) — with steady-state cost near zero, that hold should rarely trigger.

(What even scarce, deduped, batched wakes still waste — the real-but-pointless event —
is §12's territory: measure wake *yield*, then filter in the router.)

---

## 2. Spend guards — the org-wide breaker and the per-department brownout

**The failure:** the chat era's 40-wakes-per-hour breaker capped *frequency* — a proxy.
The rebuild caps the real thing: dollars and tokens, read from the same ledger
everything else trusts (§6).

**How it works — two layers, different blast radii:**

- **The org-wide daily breaker** lives in the runner: before firing LIVE wakes it sums
  today's `cost_usd` from `state/spend.jsonl`, and at `SPEND_BREAKER_DAILY_USD`
  (default $25) it prints `HELD` and fires nothing until the ledger rolls over. Honest
  edge: a held wake's events are neither fired nor spooled and the cursor still
  advances — they stay visible on the issue, but re-delivery takes a fresh event on
  the thread or a re-wake by hand (unlike a *failed* wake, whose events re-queue — §1).
- **The per-department brownout** (`scripts/budget_gate.py`, consulted by
  `agent-run.sh` before every cycle) enforces each node's org-chart
  `envelope.daily_token_budget` — a norm that used to be prompt-level prose no code
  read. Over budget → **non-direct wakes are skipped** ("BUDGET BROWNOUT", logged);
  **direct wakes always run** — a spent budget must never block a runner-routed event
  (the Chairman's issue, a deploy failure), and the overage stays visible in the log
  and ledger. Two details worth copying: cache reads are *excluded* (the budget bounds
  fresh work), and the gate **fails open with a reason on stderr** — a broken meter
  must brown out the metering, never the org, but must not be silently unlimited either.

---

## 3. The kill switch — one file, checked by every loop

**The failure it forecloses:** an autonomous org you can only stop by hunting down its
processes. The stop button must be dumber than the org.

**How it works:** a `.halt` file in the repo root (constitution invariant 5).
`scripts/halt.sh` drops it; the runner checks it first thing every tick and
`agent-run.sh` again before every cycle, so it stops the dispatcher *and* any wake
dispatched by hand. Only a human removes it (`scripts/resume.sh`). No daemon to signal,
no API to call, nothing that can itself be down — a file's existence is the protocol.

---

## 4. Single-flight agent locks — because two dispatchers means double execution

**The failure — a real incident:** two dispatch paths woke IT at the same time, and
**two concurrent IT cycles built the identical PR at once** — Double Execution
([patterns.md](patterns.md) Pattern 7) at the infrastructure layer, where no charter can
fix it. Still possible today: the runner, a manual `agent-run.sh`, and a runner restart
overlapping an in-flight child can all collide.

**How it works** (`scripts/agent-run.sh`): one lock per agent, taken before anything else:

- **`mkdir` as the lock primitive** — atomic on every filesystem, no `flock` dependency
  (macOS doesn't ship one). The holder's pid is written inside the lock dir.
- **Staleness is pid-liveness first, TTL second.** A lock whose recorded pid is gone is
  stale immediately. Past the TTL (30 minutes) it's stale **only if the pid is no longer
  actually an agent runner** (checked via the process's command line). The earlier rule
  — "older than TTL = stale, regardless of pid" — got field-corrected after a CEO cycle
  legitimately ran 2h08m: age-alone staleness would have stolen a *live* lock mid-cycle,
  while the command check still catches what the age rule really defended against — a
  SIGKILLed runner whose pid got recycled.
- **Stale locks are reclaimed atomically**: rename the dir aside, then re-acquire — two
  racers can't both win a rename; the loser falls through to the contended path.
- **Exit code 75 for contended direct wakes** (EX_TEMPFAIL — "retry me"); a contended
  non-direct wake exits 0. The 75 is honored now, not just logged: any nonzero dispatch
  re-queues the wake's events through the spool (§1), and the retry ceiling is sized to
  ride out one full lock-TTL-length cycle of the same agent before dead-lettering.
- **Bound the cycle so the lock always frees.** The pid check protects against a *dead*
  holder; a wall-clock timeout protects against a *live-but-wedged* one
  (`agent_cycle.py`: `CYCLE_TIMEOUT_S`, default 1500s — deliberately under the 1800s
  lock TTL — plus a 60-turn cap; normal cycles run 11–27 turns). A timed-out cycle
  logs TIMEOUT, writes a zero-cost error row to the spend ledger, exits non-zero, and
  its EXIT trap frees the lock.

---

## 5. Tool-scoped worker rosters — no shell at depth, asserted in CI

**The failure it forecloses:** an ephemeral worker subagent — spun up mid-cycle for
parallel decomposition — inheriting its parent's full toolset. A worker with a shell
can git-push, comment via `gh`, or trigger spend three delegation levels below anything
the Chairman sees; authority must not silently deepen.

**How it works:** workers are declared as plain data (`scripts/worker_policy.py`,
stdlib-only) — named specs, each with a pinned tier and an explicit tool allowlist;
`agent_cycle.py` turns each spec into an SDK `AgentDefinition`:

| Worker | Model | Tools | Can't |
|---|---|---|---|
| researcher | haiku | Read, Grep, Glob, WebSearch, WebFetch | edit, run, post, spend |
| drafter | haiku | Read, WebSearch, WebFetch | edit, run, post, spend |
| implementer | sonnet | Read, Grep, Glob, Edit, Write | **no shell** — can't test, commit, push, or deploy |

A worker's only channel back to the parent is its returned text. The parent cycle holds
all authority — it runs the build and tests, does the commit/PR, and answers to the
gates; even the code-writing implementer gets no Bash.

The load-bearing part: **CI asserts the roster.** `scripts/test_agent_workers.py`
(stdlib-only, runs without the SDK) checks every worker has an explicit allowlist (a
missing one would inherit everything), that none carries Bash / Task / Agent / Skill,
that research workers are read-only, and that every worker is tier-pinned. A future
"helpful" edit that hands a worker a shell fails the build, not the postmortem.

---

## 6. Spend observability — you can't govern a budget you can't see

**The failure:** the org's early burn was reconstructed after the fact from logs — spend
had accumulated in wakes nobody was metering. A governance layer that gates a $10 SPEND
proposal while its own token spend goes untracked is theater.

**How it works:** one append-only ledger (`state/spend.jsonl`, via `scripts/spend_log.py`),
one row per Claude call, written at the moment of spend:

- **Full agent wakes** report cost from the SDK's own result stats (source=cycle) — and
  since the SDK exposes a per-model breakdown, a cycle writes **one row per model
  touched**, the per-model costs partitioning the cycle total exactly (even a plain
  cycle touches a helper Haiku alongside its brain). **Every offload** is metered by a
  wrapper capturing the call's token/cost JSON (source=offload); worker subagents roll
  into their parent cycle's rows — one SDK session, no double-counting.
- **Every row of a wake carries the same `wake_id`** (minted by `agent-run.sh`), so a
  grep can join "what did this wake do" to "what did it cost" across every ledger and log.
- **Append-only JSONL** because single-line appends are atomic across concurrent agents,
  self-describing, and trivially parsed. Writes are best-effort: a metering failure
  never breaks an agent cycle.
- **A metering outage is loud, not silent.** `agent-run.sh` preflights the spend hook
  and logs "this wake will be UNMETERED" if it's broken; the cycle runner prints the
  same banner if the import fails at runtime — visible on the first cycle, not after a
  week of invisible burn. Failed cycles still get a zero-cost `status=error` row.

`scripts/costs.py` totals and trends by source / agent / model / day, and
`scripts/efficiency.py` divides it by shipped output. Note what the metering *costs*:
nothing — pure local accounting, zero Claude tokens — so there is no temptation to turn
it off to save money. Every number the governance layer talks about (the breaker, the
brownouts, an offload's claimed savings) is auditable against this ledger. That's the
standard: a mechanism that claims to save tokens must log the evidence.

---

## 7. Model-tier pinning — a tier is a name; its meaning lives in one map

**The failure it forecloses:** the org talks in *tiers* everywhere — `model: sonnet` in
the chart, "sonnet" in the spend ledger, a PROMOTE that raises an agent to opus. But
"sonnet" is not a model; it's a label that has to resolve to an *exact* API id at the
call boundary. Let each caller resolve it however it likes and a provider's silent
default (or a half-updated script) can drift two agents onto two different models while
both logs still say "sonnet" — untraceable, and it corrupts every per-model cost number.

**How it works:** one map — `global.model_ids` in `org-chart.yaml` — is the single place
a tier becomes an id (`sonnet → claude-sonnet-5`, …). A tiny resolver
(`scripts/model_id.py`) reads it at the SDK boundary and nowhere else — `agent_cycle.py`
resolves the cycle's brain and every worker spec's tier as it builds the SDK options;
argv, banners, ledger rows, and brownouts keep speaking tiers. Anything already shaped
like a full id passes through unchanged, so you can pin an exact id in an emergency
without touching the chart. The governance payoff is the clean split: a **PROMOTE**
changes *one agent's* tier; editing the **map** upgrades what a tier *means* for the
whole org, in one line — two changes, two authorities, one obvious place each.

---

## 8. Typed handoffs — the messages a machine reads carry a schema, not prose

**The failure:** the handoffs that drive automation — "we agreed on X," "here's the
demo," "the review passed" — started as free prose, and the gates consuming them had to
*parse* it. Parsing English is where a governance gate quietly starts guessing.

**How it works today:** the four machine-consumed types — `[AGREEMENT]`, `[DEMO]`,
`[CODEREVIEW]`, and `[CLOSE]` (the work-item closure payload, §10) — carry a **fenced
YAML payload with required keys** in the relevant issue/PR comment; the authoritative
schema is `agents/_policy.md` §handoffs (per the constitution §4). A `[CODEREVIEW]` requires `pr` / `head_sha` / `verdict` / `findings` /
`review_url` / `evidence_run` — `head_sha` binds the verdict to the code, and citations
chain as key lookups, not paraphrase. Human-facing messages stay prose; only what a
gate acts on gets a schema.

**The honest enforcement status:** the chat-era enforcers (the bus's malformed-payload
warning, the Warden citation — Part II §R6) died in the demolition; their Actions
successor was built next — and became a paid-for lesson of its own: a hosted workflow
triggered on every marker-bearing comment runs at agent frequency, and its rounded-up
per-job minutes helped burn the account's monthly Actions quota in days (patterns.md
Pattern 17), so the per-comment hosted validator is **retired by default**.
`scripts/handoff_check.py` remains the authoritative key list — it checks every comment
that leads a **paragraph** with a handoff marker (a malformed payload fails with a
reply naming the missing keys), its home is operator-local compute (the runner's wake
path; the pre-push CI suite of §13), and a CI test keeps `_policy.md` §handoffs — the
human-readable copy — from drifting off the code. Paragraph-leading, not line-leading, is a paid-for lesson: the
original any-line-start rule fired on a hard line-wrap that landed a bare `[CODEREVIEW]`
mention at column 0 in ordinary prose, and the agent's response was to write around the
checker rather than report it (patterns.md Pattern 13). The banner norm means a real
handoff marker always follows a blank line, so the tighter discriminator costs nothing. The *facts* the payloads assert are checked separately, as
before (`demo-verify.sh` re-derives the evidence run from the head SHA; `last-ship.sh`
re-derives shipped-ness), so a payload can no longer lie about form, and never could
lie about outcome. The drift specimen that used to live here — `_policy.md` citing the
deleted `handoff.go` as its schema source — was fixed by exactly this mechanism
becoming real.

---

## 9. Output-gated ceremony — predicates in code, ceremony that can't outrun delivery

**The failure:** process running ahead of delivery — reviews reviewing nothing, PI
Planning convened over an increment that incremented nothing, demos "verified" against
code that changed after the evidence was generated. Prose rules decay; predicates don't.

**How it works — three read-only scripts, each printing eval-friendly `key=value` lines,
each degrading safely when `gh` is absent:**

- **`scripts/last-ship.sh`** — the ship predicate: `shipped=yes` only when the product
  repo's deploy workflow has a **green run on `main`** (scoped to main deliberately: the
  only historical green deploy ran on a throwaway branch and must not count). This is
  invariant 4's enforcement point: `shipped=no` closes any due review with one line and
  blocks new feature work over a deploy-blocked critical path.
- **`scripts/pi-tick.sh`** — the cadence counter: one completed iteration == one
  **closed `[REVIEW]` issue** on the org repo (it used to count bus posts; the
  demolition moved the count to the system of record). It derives PI/iteration numbers
  from `global.pi_interval` and splits **due** from **eligible**: PI Planning is *due*
  by cadence but *eligible* only if `last-ship.sh` says `shipped=yes` — you cannot plan
  a Program Increment for a program that has incremented nothing. It only exposes a
  counter; convening remains a human/CEO act.
- **`scripts/demo-verify.sh`** — the evidence predicate: a `[DEMO]` is real only if the
  product repo's demo-evidence workflow has a **green run on the PR's current head
  SHA**. A stale run (evidence generated, then more commits pushed) does **not**
  verify; the run URL is cited in the `[DEMO]` payload for the humans to open.

---

## 10. The work-item tree — sub-issues with a closure gate

**The failure:** decomposition theater (patterns.md Pattern 12) — objectives decompose
into ever more open issues, because decomposing is cheap and looks like progress, and
nothing defines what evidence closes each level. The tree only grows.

**How it works:** objectives are tree roots on **native GitHub sub-issues** —
`[OBJECTIVE] → [PROPOSAL]*` (competing means; epics descend from the accepted one) and
`[OBJECTIVE] → [EPIC] → [FEATURE] → [STORY]` — kind carried by a plain label + title
prefix (GitHub issue *types* need an org account; labels work everywhere). A story's
plan is a `## Plan` section in its body, not a fifth level. `pm-gh.sh create --type
<kind> --parent N` births children correctly: kind prefix + label applied, the parent's
`dept:*` routing inherited (the runner routes children with **zero** added poll cost),
taxonomy edges pre-flighted so a bad link is refused before the issue exists, and a
feature/story refused unless its description carries the acceptance line its closure
will later bind to. `pm-gh.sh tree --id N` renders the rollup.

The upward path is the point. `pm-gh.sh done` is the only closing path, and it runs
`scripts/workitems.py can-close` first — the facts layer, live from GitHub: no open
children (any kind); a story cites a merged repo-qualified PR or a done-when; a feature
cites its accepted `[DEMO]` or a done-when when it has no product surface;
epics/objectives close by rollup; proposals close freely (rejected must be cheap to
bury). Anything unverifiable — offline, 404 — refuses rather than passes. The evidence
lands as a typed `[CLOSE]` comment (§8) before the issue closes. Two norms ride on top
in the mandates: decompose **just-in-time**, and never close around the gate's refusal.

---

## 11. The warden, reborn — a deterministic engine with a haiku residue

**The failure:** the operator is single-threaded, and the org's picture of "what does
the human need to do" and "does the board match the code" drifts the moment it's
hand-built. The live org's Chairman action queue (one epic whose sub-issues are the
human-only actions) was assembled once by the CEO — and began rotting immediately: 3 of
8 entries stale within a day, because upkeep was nobody's *mechanical* job.

**The failure this section originally undersold (2026-07-16):** the queue is not a
tidiness feature — it is the org's **only channel for "we are waiting on you,"** and this
note was read as optional until an org proved what that costs. A second org, stamped from
this playbook, sat silent for five hours: every thread sequenced behind one product PR
that had been open, clean and mergeable since 00:03; the supply department woke, said so,
and correctly went quiet. Nothing was broken — invariants 2 and Pattern 12 were working
exactly as designed, and *that is what produced the silence*. The Chairman saw an idle org
and reasonably concluded his agents were broken.

Worse, the omission was **invisible**. That org had inherited every moving part —
`wake-rules.yaml` routed `dept:warden` from the bootstrap, `warden.py` was vendored
repo-correct with 22 passing tests, the pre-wake engine injection was byte-identical. Only
the *state* was missing: no department, no `dept:warden` label, no queue epic, no pin. So
`warden.py` exited on an unresolvable `chairman_queue_issue` — and nothing noticed, because
nothing wakes a department that doesn't exist. Every check you'd think to run said
"installed."

Two rules came out of it, both now enforced in `bin/orggen` rather than advised here:
**the warden is an organ, not a department you choose** (stamped unconditionally;
`--departments` cannot drop it), and **the pins are commissioning steps, not optional
config** (`org-chart.yaml` ships them `null` and the stamp checklist makes setting them
step 5). See patterns.md Pattern 14.

**Assign the children to the human.** The original queue used a `Chairman:` title prefix
and a `dept:warden` label and set no GitHub assignee — so reading it meant remembering to
open the org repo's board. Pass the chart's `chairman.github` through to
`gh issue create --assignee` and the queue lands in the one inbox every operator already
reads: *Assigned to me*. A queue he must remember to visit is a queue he stops visiting.

**Keep the queue epic UNTYPED.** The original was labeled `objective` — a repurposed
Chairman objective from before the work-item tree existed. Once objectives are a reserved
power (invariant 1), a container labeled `objective` is a lie that the taxonomy will
eventually be asked to enforce. `workitems.py` permits an untyped side anywhere, so untyped
parent + untyped children is a legal, honest tree: infrastructure, not work intake.

**How it works:** the org re-chartered a `warden` department (2026-07-11, via a
`decisions.yaml` charter — the name deliberately reclaimed from the retired chat-era
checker, §R6) with an unusual shape: **the engine is a deterministic, token-free script;
the agent brain is the org's smallest.**

- **`scripts/warden.py`**, on plain launchd/cron, reconciles *desired state from ground
  truth*: the Chairman-queue epic's sub-issues are derived from the open `blockers.yaml`
  entries, Chairman-ready PRs, and open `[PROPOSAL]`s (each child keyed by a
  `warden-source:` marker so the sync is idempotent); the project board is reconciled
  **forward-only** from PR/code truth (backward drift is reported, never auto-moved, and
  the holding columns are exempt — a parked item with a PR must not be yanked back);
  in-flight friction between departments (merge conflicts, same-file PRs, unmet declared
  dependencies) is convened as marker-managed `[SYNC]` issues labeled for both sides,
  auto-closed when the fact clears.
- **Pin the coordinates in the chart, not the code:** the queue epic's issue number and
  the project number live in `org-chart.yaml` `global:` — the one place scripts resolve
  them from, so recreating the board or the epic is a one-line re-pin.
- **The haiku brain wakes only on `dept:warden` events** — the judgment residue the
  engine flags, not the reconciliation itself. Its budget is the org's smallest on
  purpose. Route the queue epic's churn to `dept:warden` so queue upkeep never wakes the
  CEO.
- **Engine-first wakes** — the commissioning lesson, paid for on the warden's first live
  wake, which burned ~10 of its 37 turns *hunting the filesystem for its own script*.
  When an agent's job is to act on a deterministic engine's output, run the engine
  *before* the cycle and inject its output into the wake prompt ("your engine ALREADY
  RAN this wake; here is its report") — never make the model rediscover or re-run what
  the loop could hand it.

Keep the checker deterministic when you build yours — the §R6 rule survived its own
mechanism: a warden that needs a model to judge a violation is just another agent to
argue with.

---

## 12. Wake yield and the wake filter — measure what a wake changed, then defer the pointless ones

**The failure:** "no event, no wake, no spend" (§1) is necessary but not sufficient.
Real events can still be pointless to wake on: a peer's comment landing on an
**already-closed** thread, or the fourth consecutive wake in a row that mutates nothing
— each boots a full model cycle whose entire output is "already handled, ending
silently." The live org measured it: **60–75% of wakes mutated nothing**, ~$43 on an
active day, invisible to every existing guard because each wake individually behaved
correctly.

**How it works — measure first, then filter:**

- **Tag every wake with its outcome** (`wake_outcome.py`): did the cycle `ship`
  (PR/commit), `post` (issue/comment mutation), or `noop`? One extra field on the spend
  ledger row. The steering KPI this unlocks is **wake yield** — the fraction of wakes
  that mutate the record. A `$/day` cap can't see this number: it happily rewards an org
  that gets *cheaper at idling*. Yield is the difference between "spent less" and
  "wasted less."
- **Filter in the router, for $0** (`wake_filter.py`): a pure function over (event,
  GitHub state, recent outcomes) that the runner consults before dispatching. The live
  org's first two rules (**v1**): **closed-thread echo defer** (a non-human comment on a
  closed issue defers the wake) and **noop-streak cooldown** (N consecutive `noop`
  outcomes for a department defers its next non-direct wake). Hard piercing rules ride
  above both: a human's comment, an unsigned comment, a workflow run, or any
  direct/Chairman event **always wakes** — the filter may only defer what provably
  cannot need a brain. **v2** (business#286) closed the gap v1 left open: an edited
  comment bumps its parent issue's `updatedAt` to the exact same timestamp, so the
  runner mints a fresh *issue* event that v1's comment-side rule never saw — attribution
  (`attribute_issue_bumps`, pre-dedup) stamps the bump's department when a same-poll
  comment shares its exact timestamp, then defers it as `issue-self-echo`
  (open issue) or `issue-closed-thread-echo` (closed); unattributed issue events —
  human edits, label changes, CI — still fire exactly as v1.
- **Shadow first.** The filter ships logging would-defer decisions without deferring
  (same discipline as the runner's own `RUNNER_MODE=shadow`), and flips on only after
  the shadow log shows zero false defers. A filter that eats a real wake is worse than
  every noop it prevents — this is the R2 lesson (measure, then enforce) applied one
  layer up.
- **The flip is runtime config, and it will sit unflipped.** `WAKE_FILTER_MODE` lives
  in the runner host's `.env`, which no PR can edit — the live org named the
  shadow→live flip as a "companion action" in a PR body, nothing tracked it, and the
  storm it would have stopped burned for another day. A shadow→live flip is a
  `blockers.yaml` entry with the exact edit as its `action:`, opened the moment the
  shadow audit passes (blocker-ledger.md §2). Two verification traps, both paid for:
  the wrapper re-sources `.env` every tick (no restart needed), but a tick already in
  flight logs its verdicts late under the OLD env — read the *next* tick's
  `wake-filter.jsonl` `mode` field before declaring the filter live; and on a host
  running sibling orgs, confirm which org's `.env` you edited — the live flip landed
  in the neighbor's first.

This is the direct successor to §1's backpressure list: dedup, batching, and echo-skip
remove *duplicate* wakes; yield + the filter remove *pointless* ones — and only the
measurement layer can tell you which you have.

---

## 13. The zero-spend CI gate — the suite runs at push time, on the operator's machine

**The failure:** Pattern 17 (patterns.md) — the metered-CI burn. A per-comment hosted
validator billed a rounded-up minute at agent frequency, drained the account's monthly
Actions quota in days, and GitHub's block-at-quota took CI dark **account-wide**: every
required status check unreportable, every PR unmergeable without an admin override,
governance stalled in two orgs at once.

**How it works:** the org spends **$0** on hosted Actions and runs the CI suite where
compute is free — on the operator's machine, at push time.

- **A `pre-push` hook mirrors `ci.yml` job-for-job** (`scripts/hooks/pre-push`): python
  syntax + every unit test; shellcheck + the constitution/blockers/decisions linters;
  playbook drift checked against the pin via a cheap `--shared` clone of the local
  golden checkout — warn-and-skip when it can't be evaluated, because a stale local
  golden must not block a push it can't judge. `SKIP_CI_HOOK=1 git push` is the
  deliberate override, and the hook's refusal message names it, so the escape hatch is
  never a secret.
- **Wired once, covers every worktree:** `make install-hooks` sets
  `core.hooksPath = scripts/hooks` on the runtime repo. Linked worktrees share repo
  config, so the isolated PR worktrees (`org-worktree.sh`) and the runtime checkout all
  run the same hooks with no per-clone setup.
- **Hosted workflows are off by default** (`gh workflow disable` — reversible with
  `enable`), and re-enabling anything needs the Pattern 17 budget line first. The
  `workflow_dispatch`-only deploy gate keeps its workflow: rare-and-deliberate is what
  the quota is *for*. Budget facts and rules live in the reference org's
  `docs/actions-budget.md`.
- **Honesty note:** a git hook is per-clone convention, not server enforcement — an
  unhooked clone can push, and nothing on GitHub's side refuses it. That trade is
  deliberate: required status checks are exactly the coupling that turned quota
  exhaustion into a merge freeze. Where the plan supports branch protection with
  required *reviews* (no status checks), keep it — the review gate doesn't ride the
  meter.

**The counter-ratchet line:** this replaces the hosted CI workflow (disabled, not
deleted) — one gate moved, none added.

## 14. The GraphQL budget — board coordinates are cached, list reads ride REST

**The failure:** the org ground to an hour-long halt with 96% of its REST quota
unused. GitHub meters REST, GraphQL, and search as SEPARATE hourly pools, and every
Projects-v2 command (`gh project …`) is GraphQL-only — as are `gh issue list` /
`gh pr list` under the hood. The old `pm-gh.sh set_status` re-discovered the board on
every call: a by-title project lookup, the project id, a 500-item scan to find one
issue's item id, and the Status field fetched twice — ~9 GraphQL requests per board
move, exactly one of which (the write itself) did any work, with the scan growing as
the board grew. A busy stretch of creates and moves drained the whole 5,000-point
GraphQL hour while the runner's ETag'd REST polls (§1) cost nothing, and the
`GH_RATE_FLOOR` hold then — correctly — parked every wake until the window reset.

**How it works:** three rules, all in `scripts/pm-gh.sh`:

- **Pin, don't look up.** The board number resolves from `$PM_PROJECT_NUMBER`, then the
  org-chart `global.pm_project_number` pin (§11's REQUIRED pin — the warden already
  refused to run without it; now pm-gh.sh reads it too). The by-title lookup survives
  only as the un-pinned fallback.
- **Cache the immutable coordinates.** Project id, Status field id, and option ids
  change only when `github-pm-setup.sh` reprovisions — so they live in
  `state/pm-board.tsv` (runtime state, gitignored), written on first use, deleted by
  setup, refreshed at most once per call when a write looks stale. Finding one issue's
  board item is a single targeted GraphQL query (issue → `projectItems`), never a page
  through the whole board; `create` hands the item id it already got back from
  `item-add` straight to `set_status`, so its status set is one request.
- **List reads ride REST.** `tasks`, label reads, and parent-label inheritance go
  through `gh api repos/…` — the separately-metered REST pool that was sitting idle
  while GraphQL starved. (The warden's full-board `item-list` stays GraphQL: it
  genuinely reconciles every item, on a bounded cron cadence, not per agent action.)

A warm board move now costs 2 GraphQL requests (item lookup + write) and a create's
status set costs 1; the steady state touches GraphQL nowhere else. The rate floor
still guards the edge — it just shouldn't trip in normal operation anymore.

**The counter-ratchet line:** no new gate — this is a cost fix inside an existing
mechanism, plus one cache file that setup already knows how to invalidate.

---

# Part II — Retired with the chat stack (2026-07-05)

Six mechanisms from this file's previous edition died with the demolition
(GITHUB-NATIVE-PLAN.md, item 4). Each was real, tuned, and paid for by an incident — and
each is kept at one paragraph because *why it existed* outlives *that it existed*. If
you run a chat-substrate org, Part II is your Part I.

**R1. Wake backpressure — cooldowns, circuit breaker, watermark no-op.** Born from the
self-wake storm ([patterns.md](patterns.md) Pattern 11): agents mostly woke each other,
so the dispatcher grew a 90s per-agent cooldown, a 40-wakes/hour org breaker,
coalesce-don't-drop retries, and a pre-model watermark peek that made duplicate wakes
cost zero tokens — all retired with the dispatcher. **Replaced by** §1–§2: an event
model where wakes are scarce by construction, the dedup ring and per-dept batching
handle the residue, and the breaker meters dollars instead of frequency.

**R2. The haiku RESPOND/DEFER pre-gate.** A $0.001 doorman for $0.10 rooms: broadcast
channels woke every department, so a haiku classifier decided per-agent whether a
broadcast merited a full cycle (~75:1 payoff per DEFER, logged not asserted). **Replaced
by nothing — deliberately.** There are no broadcast channels to gate; an event routes to
the departments its `dept:*` labels name and no one else. Companion lesson kept: a
second shadow-mode gate never proved net-positive and was deleted — measure, then
enforce, in both directions.

**R3. The collaboration channel (`#projects`, `[DELEGATE]`, the 8-reply turn budget).**
Built because every Business↔IT handoff routed through the CEO — the org's most
expensive agent had become its slowest message bus. **Replaced by multi-label issues:**
a joint ticket carries both `dept:*` labels, each side's comments wake the peer (minus
the echo-skip, §1), and escalation is adding `dept:ceo` for a decision, not a relay.
The `[AGREEMENT]` exit survives as a typed handoff (§8). The turn budget has no direct
successor — a runaway thread's backstops are now the brownout and the daily breaker
(§2), which is blunter; watch that spot.

**R4. The deploy-hold watcher.** A 30-minute watcher re-derived the open deploy/revenue
blockers and posted hold OPENED/LIFTED state changes to `#board` — because a held org
that stayed *busy* burned tokens on motion without progress ([patterns.md](patterns.md)
Patterns 9 and 10 compounding). **The concept survives, the watcher doesn't:** the hold
*is* the ledger now — an open `blockers.yaml` entry with `blocks: [deploy]` or
`[revenue]` puts the org in deploy-hold per the shared policy (hibernate: no new
initiatives, no speculative inventory, one-line held reviews), invariant 4 output-gates
the ceremony (§9), and `deploy.yml` runs wake IT via `wake-rules.yaml`. Nothing
announces the hold anymore; the planned status page inherits that job.

**R5. Semantic memory (Ollama embeddings, `memory recall`).** Recall without re-reading
the whole org: a watcher embedded every channel post into a local vector store (local
embeddings, zero Claude tokens — where the removed local-generation tier's hardware
went). **Deleted with the memory service; no replacement.** The honest accounting: the
system of record moved to issues/PRs/ledgers — grep-able in a way channel scroll never
was — but "what did we decide about the mug CTA?" is once again a search problem, not
a recall query. If the org re-grows a memory, it will be over GitHub objects.

**R6. The Warden (chat-era).** A deterministic, non-LLM checker that matched process
invariants against what agents actually *posted* — uncited demos, bare PR references,
STATUS posts restating a hold — and filed a `[WARDEN]` alert past a weight threshold.
Retired with the channels it read. **The philosophy — enforcement in code, not in a
prompt — moved into CI and the platform:** `lint_constitution.py` and `decisions.py
check` are required merge checks (`test_agent_workers.py` rides the same test job),
CODEOWNERS routes every `decisions.yaml` PR to the Chairman, and deploys run only from
the Chairman's manual `workflow_dispatch`. The typed-handoff validation duty found its
Actions successor (§8, built). And the Warden itself came back: re-chartered 2026-07-11
as a department around a deterministic engine (§11) — the philosophy held; only the
channels it read didn't.

---

## The meta-lesson

Every mechanism above earned its place the same way: a real failure, a measured cost, a
small deterministic fix at the *loop* level — not a smarter prompt. The retirements
follow the same standard, up to demolishing an entire substrate: the chat stack was six
mechanisms deep in wake suppression because its architecture made wakes abundant; the
rebuild deleted the problem instead of tuning the mitigation. The counter-ratchet is
now constitutional — every new gate, rule, or watcher names the one it replaces or
carries a sunset date tied to its incident. Wire in a mechanism's measurement first; if
you can't see it paying for itself, delete it — and if you're tuning the fourth
mitigation for one failure class, delete the failure class.
