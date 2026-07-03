# Field-Tested Mechanisms — what the live org runs that the docs alone won't give you

The rest of this playbook is the governance layer: gates, envelopes, ledgers. This file
is the other half — the mechanisms the live Scrum Jail org grew *after* going live,
each one built in response to a real failure that cost real tokens (or nearly shipped a
real mistake). None of them is speculative; every one is running today. For each: what
it is, the failure it exists to prevent, and how the real implementation works — so you
can build the same thing into your runtime without paying the tuition twice.

---

## 1. Wake backpressure — cooldowns, a circuit breaker, and the watermark no-op

**The failure:** the self-wake storm ([patterns.md](patterns.md) Pattern 11). Every post
in a channel is a wake source, so agents mostly woke *each other* — at one point
agent-triggered wakes outnumbered the operator's. Worse, each wake loaded a full model
cycle even when nothing had changed since the agent last read.

**How it works — three layers:**

1. **A per-agent cooldown + a global circuit breaker** in the dispatcher. The live
   values: an agent won't be re-woken by *agent* traffic more than once per **90
   seconds**, and agent-initiated wakes are capped at **40 per rolling hour** across the
   whole org. The Chairman bypasses the guard entirely — human posts always wake.
2. **Coalesce, don't drop.** A suppressed wake isn't discarded — the dispatcher
   schedules exactly one delayed retry per agent (timed to when the cooldown or breaker
   window will have cleared). If it's *still* rate-limited after that one retry, it
   drops — and the watermark catch-up recovers the messages on the next real wake.
   Without this, backpressure silently eats real messages.
3. **The watermark no-op check** in the wake runner. Before the model starts, the agent
   *peeks* every channel it scans (a read that does not advance its cursor). Nothing new
   since it last read → the wake exits without a model call. This applies to **every**
   wake class, including direct wakes — a coalesced or duplicate trigger costs zero
   tokens. It fails open: if the peek itself errors (bus down), a direct wake runs
   anyway rather than risk swallowing a Chairman directive.

The no-op is safe because an unblock is itself new inbound: the operator clearing a
blocker or a watcher posting a state change is a new post, so a quiet agent can never
get trapped silent. A blocked org on a quiet day costs zero model tokens.

---

## 2. The haiku RESPOND/DEFER pre-gate — a $0.001 doorman for $0.10 rooms

**The failure:** broadcast channels (`#feedback`, `#reviews`) wake every department. If
each woken agent runs a full cycle just to conclude "my peer already said this," a
single post costs N full cycles.

**How it works:** a broadcast wake doesn't go straight to a full cycle. If nothing new
landed in the agent's *own* lane, the runner first asks **haiku** one question, grounded
in the agent's one-line mandate and the new broadcast content: should this agent
RESPOND (it has material, in-mandate input not already in the thread) or DEFER? One
word out. DEFER → the wake ends with no full cycle.

The economics are logged, not asserted: every verdict appends a line to a savings log
with its estimated saving — **~75k tokens for a skipped full cycle vs ~1k for the gate
call**, a ~75:1 payoff per DEFER — and the operating rule is to sum that log against
the gate's cost and confirm net savings *before* flipping a gate from shadow to
enforce. Three design points worth copying:

- **Never gate away owner-lane work.** If anything new sits in the agent's own channel,
  the verdict is forced to RESPOND — the gate only ever suppresses the *broadcast
  reply*, never real tasks.
- **Fail open.** An unclear classifier answer counts as RESPOND. The gate can waste one
  cycle; it must never eat a real contribution.
- **Stagger the fan-out.** The dispatcher wakes broadcast recipients ~30s apart,
  departments first, CEO last — so each agent's pre-gate sees the earlier replies and
  can defer to them. The CEO wakes into a thread that's already converged.

The honest counterexample: a second gate (an "is this scheduled wake actionable?" haiku
check) ran in shadow mode, never accumulated evidence that it beat the watermark no-op,
and was deleted as net-negative dead weight. Measure, then enforce — in both directions.

---

## 3. Single-flight agent locks — because two dispatchers means double execution

**The failure — a real incident:** the scheduler and the event dispatcher both woke IT
at the same time, and **two concurrent IT cycles built the identical PR at once**.
Double Execution (Pattern 7) at the infrastructure layer, where no charter can fix it.

**How it works:** one lock per agent, taken before anything else in the wake:

- **`mkdir` as the lock primitive** — atomic on every filesystem, no `flock` dependency
  (macOS doesn't ship one). The holder's pid is written inside the lock dir.
- **A TTL for staleness** (30 minutes — longer than any legitimate wake). A lock older
  than the TTL is treated as stale *regardless of whether its pid looks alive*: a
  SIGKILL plus a recycled pid would otherwise wedge the agent forever. Stale locks are
  reclaimed atomically (rename the dir aside, then re-acquire — two racers can't both
  win a rename).
- **Exit code 75 for contended direct wakes.** A contended *scheduled* wake just exits
  0 — the next tick covers it. But a contended **direct** wake (a Chairman post, an
  owner-channel task) exits 75 so the dispatcher knows to re-trigger it rather than
  drop it. A contended wake also never advances read watermarks, so its messages are
  re-read by whoever runs next.

---

## 4. Tool-scoped worker rosters — no shell at depth, asserted in CI

**The failure it forecloses:** an ephemeral worker subagent — spun up mid-cycle for
parallel decomposition — inheriting its parent's full toolset. A worker with a shell
can git-push, post to the bus, or trigger spend three delegation levels below anything
the Chairman sees. Authority must not silently deepen.

**How it works:** workers are declared as plain data — a roster of named specs, each
with a pinned model and an explicit tool allowlist:

| Worker | Model | Tools | Can't |
|---|---|---|---|
| researcher | haiku | Read, Grep, Glob, WebSearch, WebFetch | edit, run, post, spend |
| drafter | haiku | Read, WebSearch, WebFetch | edit, run, post, spend |
| implementer | sonnet | Read, Grep, Glob, Edit, Write | **no shell** — can't test, commit, push, or deploy |

A worker's only channel back to the parent is its returned text. The parent cycle
holds all authority: it runs the build and tests, does the commit/PR, and answers to
the gates. Even the code-writing implementer gets no Bash.

The load-bearing part: **CI asserts the roster.** A stdlib-only test checks every
worker has an explicit allowlist (a missing one would inherit everything), that none
carries Bash / Task / Agent / Skill, that research workers are read-only, and that
every worker is tier-pinned. A future "helpful" edit that hands a worker a shell fails
the build, not the postmortem.

---

## 5. The collaboration workspace — stop making the lead agent a message shuttle

**The failure:** every Business↔IT handoff was routed through the CEO. Each leg cost a
serialization hop and an extra wake, and the CEO — relaying summaries of work it didn't
do — started asserting state it hadn't checked. The org's most expensive agent had
become its slowest message bus.

**How it works:** a dedicated collab channel (`#projects`) with different wake rules:

- The CEO posts **one `[DELEGATE]`** naming the joint task, then steps back.
- The participant pair (Business + IT) **wake each other directly**: each threaded
  reply wakes only the peer, not the whole org.
- **A per-thread turn budget** (8 replies in the live org) replaces the global rate
  limiter inside a thread — within budget, replies bypass the global breaker so a
  productive convergence is never starved by it.
- Two exits, both toward the CEO-as-**arbiter**: the pair posts an **`[AGREEMENT]`**
  (the converged plan — this wakes the CEO to review it), or the thread **spends its
  budget** without converging, which escalates to the CEO to break the stall.

The CEO arbitrates outcomes; it no longer carries the traffic.

---

## 6. Deploy-hold hibernation — a held org should be quiet, not busy

**The failure:** with prod dark behind a human-only blocker, the org stayed *busy* —
drafting a fifth copy variant, re-debating settled plans, running ceremony — all of it
motion, none of it progress, all of it tokens ([patterns.md](patterns.md) Patterns 9
and 10 compounding).

**How it works:** while `blockers.yaml` has an open entry that blocks deploy or
revenue, the org is in a declared **deploy-hold**, and the shared policy switches every
agent's default from "produce" to "hold":

- Do the minimum; open no new initiatives or work-streams.
- **Stop producing speculative inventory** — one or two staged, ready-to-ship assets
  per track is enough; more just ages invisibly on the shelf.
- No SAFe ceremony while the output predicate says `shipped=no`; a due review closes
  with a one-line held-conclusion.
- The only permitted build work: unblocking the deploy, or deploy observability.
- Always allowed: answering the Chairman, recording a genuinely new blocker once, and
  surfacing a ready, un-gated move as **one crisp yes/no decision ask** — don't bury a
  ready move, but don't re-pitch it either.

A watcher makes the hold state *loud*: it re-derives the set of open deploy/revenue
blockers on a 30-minute cadence and posts to `#board` only on a **state change**
("deploy-hold OPENED" / "deploy-hold LIFTED"). The hold lifts itself — the clearing
post is new inbound that wakes everyone back to full work.

---

## 7. Semantic memory — recall without re-reading the whole org

**The failure:** an agent's context is its channels since its last read. Anything
older — "what did we decide about the mug CTA?" — meant either re-dumping whole
channel histories into the prompt (expensive) or answering from vibes (wrong).

**How it works:** a small ingest/recall pair, deliberately outside the paid-token
economy:

- **Ingest** (a 30-minute watcher) pulls recent posts from every org channel and embeds
  them into a local vector store via **local Ollama embeddings — zero Claude tokens**.
  It's idempotent (chunks dedupe on post id) and read-only against Mattermost, so it's
  safe to run even while the org is halted.
- **Recall** (`memory recall "<query>"`) returns the top-k most relevant chunks, so an
  agent retrieves a small relevant slice of org history instead of paging through
  channels.

This is also where the removed local-generation tier's hardware went: the measured
lesson was that local models are worth running for *embeddings*, not generation (see
the offload note in the constitution).

---

## 8. Spend observability — you can't govern a budget you can't see

**The failure:** the org's early burn was reconstructed after the fact from logs —
spend had accumulated in wakes nobody was metering. A governance layer that gates a $10
SPEND proposal while its own token spend goes untracked is theater.

**How it works:** one append-only ledger (`state/spend.jsonl`), one row per Claude
call, written at the moment of spend:

- **Full agent wakes** report their cost from the SDK's own result stats
  (source=cycle); **every offload** is metered by a wrapper that captures the call's
  token/cost JSON (source=offload). Worker subagents roll into their parent cycle's
  row — no double-counting.
- **Append-only JSONL** because single-line appends are atomic across concurrent
  agents, self-describing, and trivially parsed. Writes are best-effort: a metering
  failure never breaks an agent cycle.
- **A metering outage is loud, not silent.** Each wake preflights the spend hook and
  logs "this wake will be UNMETERED" if it's broken — visible on the first cycle, not
  after a week of invisible burn.
- A reporter totals and trends by source / agent / model / day and exports CSV.

Note what the metering *costs*: nothing. It's pure local accounting — SDK-reported
stats and file appends, **zero Claude tokens** — so there is no temptation to turn it
off to save money. Every number the governance layer talks about (the declared
`daily_token_budget`, an offload's claimed savings, the pre-gate's 75:1 ratio) is
auditable against this ledger. That's the standard: a mechanism that claims to save
tokens must log the evidence.

---

## The meta-lesson

Every mechanism above earned its place the same way: a real failure, a measured cost, a
small deterministic fix at the *loop* level — not a smarter prompt. And the two that
didn't survive (the local generation tier, the shadow-mode actionability gate) were
removed by the same standard. If you add a mechanism to your org, wire in its
measurement first; if you can't see it paying for itself, delete it.
