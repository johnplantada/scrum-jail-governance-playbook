# Field-Tested Mechanisms — what the live org runs that the docs alone won't give you

The rest of this playbook is the governance layer: gates, envelopes, ledgers. This file
is the other half — the mechanisms the live Scrum Jail org grew *after* going live, each
built in response to a real failure that cost real tokens (or nearly shipped a real
mistake). None is speculative; every mechanism in Part I is running today. Part II is
the graveyard: on **2026-07-05** the org demolished its entire chat-era runtime
(Mattermost, the bus/pm/registrar/memory/vox services, the watcher fleet, emoji
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

**How it works:** one poller (`scripts/runner.py`, the org's *only* scheduled process,
cron'd via `runner-watch.sh` every ~5 minutes). Each tick: read a cursor → ask GitHub
what changed since (issues + comments on both repos, completed product-repo workflow
runs) → normalize to events → route through `wake-rules.yaml` → wake the owning
departments via `agent-run.sh` → advance the cursor.

- **There are no scheduled agent wakes at all.** No heartbeats, no standups, no wake
  floors — DESIGN.md §3 states it as an operating principle: **no event, no wake, no
  spend.** A blocked org on a quiet day costs zero model tokens — the old watermark
  no-op's guarantee, now free because there's nothing to no-op.
- **GitHub is the durable queue.** The runner lives on a laptop; a closed lid is a clean
  pause, not an outage. The next tick drains a longer backlog, **oldest event first**
  (routing sorts by event timestamp), so catch-up order is event order.
- **Routing is a committed, PR-reviewed file.** `wake-rules.yaml` replaced the wake
  logic scattered across the watcher fleet — first matching rule wins, and an event
  matching nothing wakes nobody (logged as unrouted).
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
   banner (`**🛠️ IT —** …`) is the only author signal. The runner parses it off each
   comment (`banner_dept()`) and excludes the author's own department from the fan-out —
   an agent never echo-wakes itself. **It fails safe:** an unsigned comment (a human, or
   an agent forgetting its banner) wakes every labeled department, because a wasted wake
   beats a stalled thread.

The honest caveat, documented in the code itself: delivery is currently **at-most-once**
— the cursor advances per tick even if a dispatched cycle crashes, so its events are not
re-delivered. `agent-run.sh` makes the abort loud ("events were NOT re-queued; re-wake
by hand if needed"); cursor hold-back on failure is an open follow-up.

---

## 2. Spend guards — the org-wide breaker and the per-department brownout

**The failure:** the chat era's 40-wakes-per-hour breaker capped *frequency* — a proxy.
The rebuild caps the real thing: dollars and tokens, read from the same ledger
everything else trusts (§6).

**How it works — two layers, different blast radii:**

- **The org-wide daily breaker** lives in the runner: before firing LIVE wakes it sums
  today's `cost_usd` from `state/spend.jsonl`, and at `SPEND_BREAKER_DAILY_USD`
  (default $25) it prints `HELD` and fires nothing until the ledger rolls over. Events
  aren't lost — GitHub is still the queue.
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

## 3. The 🛑 kill switch — one file, checked by every loop

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
  non-direct wake exits 0. Honest footnote: the runner *logs* the 75 but does not yet
  re-deliver — the at-most-once caveat from §1 again.
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
the chart, "sonnet" in the spend ledger, a 💎 PROMOTE that raises an agent to opus. But
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
without touching the chart. The governance payoff is the clean split: a **💎 PROMOTE**
changes *one agent's* tier; editing the **map** upgrades what a tier *means* for the
whole org, in one line — two changes, two authorities, one obvious place each.

---

## 8. Typed handoffs — the messages a machine reads carry a schema, not prose

**The failure:** the handoffs that drive automation — "we agreed on X," "here's the
demo," "the review passed" — started as free prose, and the gates consuming them had to
*parse* it. Parsing English is where a governance gate quietly starts guessing.

**How it works today:** the three machine-consumed types — `[AGREEMENT]`, `[DEMO]`,
`[CODEREVIEW]` — carry a **fenced YAML payload with required keys** in the relevant
issue/PR comment; the authoritative schema is `agents/_policy.md` §handoffs (per the
constitution §4). A `[CODEREVIEW]` requires `pr` / `head_sha` / `verdict` / `findings` /
`review_url` / `evidence_run` — `head_sha` binds the verdict to the code, and citations
chain as key lookups, not paraphrase. Human-facing messages stay prose; only what a
gate acts on gets a schema.

**The honest enforcement status:** the chat-era enforcers (the bus's malformed-payload
warning, the Warden citation — Part II §R6) died in the demolition; their Actions
successor is now built. `scripts/handoff_check.py` holds the authoritative key list,
the handoff-validator workflow runs it on every comment that leads a line with a
handoff marker (a malformed payload fails the run and gets a reply naming the missing
keys), and a CI test keeps `_policy.md` §handoffs — the human-readable copy — from
drifting off the code. The *facts* the payloads assert are checked separately, as
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

**R6. The Warden.** A deterministic, non-LLM checker that matched process invariants
against what agents actually *posted* — uncited demos, bare PR references, STATUS posts
restating a hold — and filed a `[WARDEN]` alert past a weight threshold. Retired with
the channels it read. **The philosophy — enforcement in code, not in a prompt — moved
into CI and the platform:** `lint_constitution.py` and `decisions.py check` are required
merge checks (`test_agent_workers.py` rides the same test job), CODEOWNERS routes every
`decisions.yaml` PR to the Chairman, and the `production` environment holds deploys for
his review. The one Warden duty without a successor is typed-handoff shape validation —
planned as an Actions check, not built (§8). Keep it deterministic when you rebuild it:
a warden that needs a model to judge a violation is just another agent to argue with.

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
