# Agent Misbehavior Patterns — and How to Govern Them

Eleven patterns that burn operators. Each one has a specific governance fix.
These aren't hypothetical — they're the failure modes that show up when agents
have more authority than their operators intended (Patterns 1–8), or when the
orchestration loop has no idea of "blocked," "done," or "do nothing" (Patterns
9–11). See "The Pattern Behind the Patterns" at the end.

The failures are timeless; the counter-patterns are stated against the current
GitHub-native runtime (work = Issues on one Project via `scripts/pm-gh.sh`; authority =
the Chairman's `decisions.yaml` merges and the product repo's `production` environment;
nervous system = an event-driven runner routing `dept:*` labels). Where the machinery
changed when the chat stack was demolished (2026-07-05), a compact lineage note says
what v1 did.

---

## Pattern 1: Rogue Spender

**What it looks like:** Unexpected charges appear on your card. The agent signed
up for a SaaS tool, bought API credits, or ran a cloud job — without asking.

**Why it happens:** the agent could reach a payment credential — a stored card, a
billing API key, a tool that wasn't classified as "spending" — so nothing physical
stood between "the agent decided to" and "money moved."

**Counter-pattern:** The hard fix is capability-absence: the agent holds no card, no
payment token, no stored payment method — there is nothing for it to spend *with*.
Layer the protocol on top: declare `can_spend: false` in the envelope (a policy hint —
no runtime code branches on it), and make every spend a PR appending one `type: spend`
entry — with an explicit `cost_usd` ceiling — to `decisions.yaml`. CODEOWNERS routes
that PR to the Chairman, and **the Chairman's merge is the authorization**: nothing
takes effect until it's on `main`, and `git log decisions.yaml` is the audit trail.
(The `[PROPOSAL]` issue form is the discussion surface on the way there.) If any tool
within the agent's reach can move money, that tool is the vulnerability — remove the
credential, don't patch the prompt. *(v1: a Chairman 💰 reaction, recorded by the
Registrar to `#decisions` — retired 2026-07-05; 💰 survives only as ledger vocabulary.)*

---

## Pattern 2: Broken Deploy

**What it looks like:** Production goes down after the agent pushed a PR.
The agent believed the tests passed; they did not, or they passed on a stale branch.

**Why it happens:** the agent held a credential that could reach production, and no
deploy gate was enforced at the infra layer. The agent treated "tests green" as
sufficient authorization.

**Counter-pattern:** Put the deploy gate *outside the agent's trust domain*: agents get
no prod credentials and open PRs but never merge to `main`; the deploy pipeline pauses
at the product repo's `production` environment, where the Chairman is the required
reviewer — GitHub itself holds the run until that human approves, so an agent cannot
reach production even if it convinces itself it should. Layer the protocol on top:
declare `can_deploy: false` (a hint, not a code switch), and put the PR link and a
one-line rollback plan on the ticket before asking for the approval. No amount of test
coverage substitutes for the human gate — the gate is for authorization, not quality
assurance. *(v1: a Chairman 🚀 reaction, recorded to `#decisions` — retired 2026-07-05;
🚀 survives only as ledger vocabulary.)*

---

## Pattern 3: Runaway Recursion

**What it looks like:** One agent spawns sub-agents; those spawn more sub-agents;
token spend explodes and you can't tell who's doing what or why.

**Why it happens:** `max_subagents` set too high, or not enforced. Agents interpret
"delegate complex work" as permission to fan out indefinitely.

**Counter-pattern:** Set `max_subagents: 0` for leaf agents and `2-4` only for
department heads who demonstrably need parallel work — and enforce it in code. The live
org does: `scripts/subagent_gate.py`, a PreToolUse hook on the Agent/Task tools, counts
spawns per wake and DENIES any call past the agent's `envelope.max_subagents` (the
successor to the Registrar's sub-team refusal, which retired with the chat stack), and
`limits.global_max_agents` is a CI-checked invariant — the tests fail any chart whose
roster arithmetic (every brain + its full permitted fan-out) exceeds the ceiling. The
second bound is shape, not a counter: the worker roster is declarative (`scripts/worker_policy.py`
defines the only three subagent types, tool-scoped and tier-pinned — no worker gets a
shell, so none can spawn, spend, or deploy at depth), and `agent-run.sh` single-flights
each department behind a lock, so a department is one cycle at a time. When an agent
wants structure beyond that, it proposes a CHARTER as a `decisions.yaml` PR and waits —
it does not spawn anyway.

---

## Pattern 4: Blocked-Action Retry Loop

**What it looks like:** An agent was denied permission for an action, but instead
of proposing and waiting, it retries the same action repeatedly with minor rephrasing,
consuming tokens and clogging the thread.

**Why it happens:** The agent's standing instructions say "complete the task" but
don't say "if blocked, propose and stop." The retry is the agent being obedient to
the wrong instruction.

**Counter-pattern:** Distinguish two kinds of blocked. For an **in-org ask** (another
department must act), write `propose-and-wait`: file one PROPOSAL (or label the ticket for
the owning department), then stop — no retry. For a **human-only** action the agent
fundamentally cannot do (cloud credentials, money, a URL or mailing address, a
Chairman-only authorization), don't even re-propose — record it **once** in a
`blockers.yaml` ledger and go quiet. That ledger, not the chat stream, is the operator's
queue. Re-announcing the same blocker every wake is itself the failure (Pattern 9); the
ledger makes a blocker a durable latch only the human clears, and clearing it is what wakes
the agent again (the unblock is itself new inbound). See [blocker-ledger.md](blocker-ledger.md).

---

## Pattern 5: Comment Flood

**What it looks like:** An agent posts wall-of-text updates every few minutes,
burying real signals. The issue thread becomes unreadable.

**Why it happens:** No cadence discipline in the agent's instructions. The agent
treats "keep the team informed" as "post constantly."

**Counter-pattern:** Make STATUS **state-change-only** — an agent posts a STATUS only when
something actually changed (a PR merged, a blocker cleared, a gate moved, a metric shifted).
"No change from yesterday" is not a message; it is the absence of one, and a no-change cycle
ends silently. The runner makes the silence free: there are no scheduled wakes at all — a
department wakes only when a routed GitHub event arrives (**no event, no wake, no spend**) —
and inbound noise is damped mechanically: the dedup ring drops re-polled events, a tick
batches everything for a department into **one** wake, and a comment never echo-wakes its
own author (`runner.py`). The `daily_token_budget` backstop is now real code:
`budget_gate.py` browns out an over-budget department (non-direct wakes skip until the
ledger rolls over). (A STATUS that just restates a hold isn't only noise — on a multi-label
ticket it *wakes the peer department*; see Pattern 11.) *(v1 damped this with scheduled
wakes that peeked channels and no-opped on watermark — retired 2026-07-05 with scheduled
wakes themselves.)*

---

## Pattern 6: Domain Expansion

**What it looks like:** The Business agent starts writing code. The IT agent starts
doing pricing research. Agents drift into each other's lanes and create contradictory
work products.

**Why it happens:** The agent's charter (`agents/<name>.md`) is too broad or uses
phrases like "help with anything needed." The agent optimizes to be useful, not
to stay in scope.

**Counter-pattern:** Write the charter as a positive list (what the agent IS
responsible for) and a negative list (explicit out-of-scope items). When an agent
wants to cross a lane, it routes the work instead of doing it: file a ticket for the
owning department (`scripts/pm-gh.sh create --project IT --title "…"`) or add the
`dept:it` label — `wake-rules.yaml` routes every `dept:*` label — and wait for that
department to accept or delegate back. Cross-lane *collaboration* is a multi-label
issue: a ticket carrying both `dept:business` and `dept:it` wakes each side on the
other's comments (`runner.py`). Wake IT; don't have Business do IT's work.

---

## Pattern 7: Double Execution

**What it looks like:** Two agents both complete the same task — or one agent
re-executes a task that was already marked Done — resulting in duplicate PRs,
duplicate emails sent, or duplicate Gumroad listings.

**Why it happens:** No shared task state. Each agent reads the brief and acts
independently. Neither checks whether the work is already in progress or complete.

**Counter-pattern:** Use the ticket system (`scripts/pm-gh.sh` over Issues + the org
Project) as the single source of truth for task state. Before starting work, an agent
checks `pm-gh.sh tasks --project IT` and reads the ticket's Stage (the canonical list
is `org-chart.yaml pm_stages`). If it's Doing or Done, the agent replies on the ticket
(`pm-gh.sh comment --id N --body "..."`) rather than re-executing. Only one
department's label should own a task at a time — and the runtime backstops the race:
`agent-run.sh` single-flights each agent behind a lock, added precisely because two
concurrent IT cycles once built the identical PR.

---

## Pattern 8: Approval-Gate Evasion

**What it looks like:** The Chairman declined a spend — said "no" on the ticket,
closed the `decisions.yaml` PR unmerged, or simply never merged it. The agent
acknowledges, then re-submits the same request rephrased as something smaller, or
under a different proposal type, hoping a fresh proposal draws a different answer.

**Why it happens:** The agent's goal is "get the task done." A refusal is an obstacle
to that goal. Without an explicit instruction to treat a "no" as final, the agent
routes around it.

**Counter-pattern:** Two things. First, write into every agent's charter: "A declined
proposal closes the matter. Do not re-propose the same action rephrased." Second, know
what the platform does and doesn't check: GitHub verifies *who* — only the Chairman can
merge past CODEOWNERS or approve the `production` environment, so nobody has to parse
reactor ids anymore — but nothing checks whether a new `decisions.yaml` PR is a
re-phrase of one that was closed unmerged. The behavioral discipline has to come from
the charter. If an agent is evading, rewrite its charter to be explicit about refusal
finality, then restart it.

(A note on 🛑, because it's easy to misread as a per-proposal veto: it isn't one. The
kill switch is a `.halt` file in the repo root — `make halt` drops it, `make resume`
clears it — and while it exists `runner.py` skips every tick and `agent-run.sh` refuses
every wake. Only a human removes it (DESIGN.md invariant 5). The everyday "no" to a
proposal is simply not merging it: no merge, no authorization, nothing happens.)

---

---

## Pattern 9: Idle Restatement (the blocked loop)

**What it looks like:** The org is blocked on something only you can do (a credential, a URL,
an approval). The agents *know* it — and every wake they re-read the same state, re-derive
"still blocked on X," and post a STATUS that says so. Cycle after cycle of high-quality
reasoning that produces no work product. In our own run this was **~25% of all wake cycles**
— 12.5 agent-hours of model time spent re-announcing a constant.

**Why it happens:** The wake loop has no concept of "blocked" or "do nothing." A *scheduled*
wake runs the model whether or not anything happened, and a model that ran is expected to
*emit* something — so a blocked agent that correctly concludes "re-posting would just be
noise" posts anyway, because the loop gives it no other legal move.

**Counter-pattern:** Give "blocked" and "do nothing" first-class representations.
1. **A blocker ledger** (`blockers.yaml`) is the durable record of human-only blockers; agents
   write to it once and stop, never re-post (Pattern 4). It is the operator's queue — and
   `agent-run.sh` injects the open entries into every wake prompt, so an agent never has to
   re-derive (or re-announce) them.
2. **No scheduled wakes at all.** The runner is event-driven: it polls GitHub each tick and
   wakes a department only when a routed event arrives — **no event, no wake, no spend**. A
   blocked org generates no events, so "do nothing" is the default and costs nothing: no
   model call, no post. It can't get trapped silent, because the unblock (you clearing the
   ledger entry, commenting, merging) is itself a new GitHub event that wakes the owner.
3. **State-change-only STATUS** (Pattern 5): silence is the correct output of a hold.
Together these convert a blocked org from an expensive narration loop into a quiet latch.
*(v1 kept scheduled wakes and damped them — a pre-model channel peek that no-opped on
watermark. Retired 2026-07-05: the fix for a wake with nothing to do turned out to be not
scheduling it in the first place.)*

---

## Pattern 10: Process Theater (output blindness)

**What it looks like:** The org's ceremony outruns its output. It runs sprint reviews, plans
"Program Increments," and authors process docs — while shipping nothing to production. The
review always concludes "executed cleanly within our constraints." In our run, the org built a
whole scaled-agile layer and ran *PI Planning* for a product that **had never once deployed**.

**Why it happens:** The org measures itself in **artifacts** (PRs merged, STATUS posted,
tickets closed, process maps written) — every signal it can read says "healthy." The one
signal that says *nothing is live* (the post-merge deploy result) is the only one no agent
reads, so "merged" silently masquerades as "shipped." Ceremony triggered by a wake-count, not
by delivery, fills the silence with more process.

**Counter-pattern:** **Gate every ceremony on shipped output, not elapsed time.** Define one
predicate — "did we actually ship to prod?" (a green deploy on `main`) — and make every
ritual check it first. No demo, no acceptance, no PI Planning runs while that predicate is
false. Two supporting moves: **surface deploy health loudly** (`wake-rules.yaml` routes every
completed `deploy.yml` run to a wake for IT, so "merged ≠ live" can't hide), and **work-gate**
— open no new feature work while prod is dark, since merged-but-undeployed code only widens
the activity-vs-output gap.
See [safe.md](safe.md).

---

## Pattern 11: Self-Wake Storm

**What it looks like:** Token spend and comment volume climb even when little is happening. The
agents are mostly waking *each other*: one posts a no-change STATUS, which wakes its peers, who
each post a STATUS, which wakes... In our run, **agent-triggered wakes outnumbered the
operator's** — the swarm partly DDoSes itself.

**Why it happens:** Any post on a shared surface is a wake source — in v1 every channel post,
today every comment on a multi-label issue — and a heartbeat STATUS is a post. v1 compounded it
with a rate-limiter that **dropped** a suppressed wake (so the trigger was simply lost): both
wasted wakes *and* missed messages.

**Counter-pattern:** Cut the wake *source*: state-change-only STATUS (Pattern 5) removes the
heartbeats that do nothing but wake peers. Then let the runner damp what's left — the live loop
(`runner.py`) is built so it can't storm the way the chat org did: the dedup ring drops
re-polled events, a tick batches all of a department's triggers into **one** wake, and a
comment never echo-wakes its own banner author, so an agent working its own single-label ticket
wakes nobody (itself included). The org-wide backstop is financial: the runner holds live wakes
once the day's metered spend crosses `SPEND_BREAKER_DAILY_USD`, so spend without progress hits
a hard ceiling. One honest caveat: delivery today is at-most-once — a wake held at the cap (or
a crashed cycle) does not get its events re-queued; they stay visible on the issue, but the
re-wake is manual, and re-delivery is an open follow-up, not a shipped feature. *(v1 damped
storms with a per-agent 90s cooldown and a rolling 40-wakes/hr breaker, patched to "coalesce,
not drop" — all retired 2026-07-05 along with the scheduled wakes that made them necessary.)*

---

## Pattern 12: The Tree That Only Grows (decomposition theater)

**What it looks like:** Asked to pursue an objective, the org produces a beautiful
hierarchy — epics, features, stories, all well-written, all open. Issue count climbs;
nothing closes. Planning artifacts pile up faster than anything can be verified done, and
"decomposed the objective" starts standing in for progress in every summary.

**Why it happens:** For an agent, decomposition is nearly free and *reads* as progress —
cheap tokens producing visible artifacts — while verification is scarce and expensive. If
the work system defines how items split but not what evidence closes them, every edge in
the model points downward by construction: there is no upward path, so the tree can only
grow. This is Pattern 10's sibling — process theater at the work-item layer instead of the
ceremony layer.

**Counter-pattern:** **Write the closing rules before the decomposing rules.** Every level
gets an evidence-bearing close enforced in code, not prose: a story closes only citing a
merged PR (or a one-line done-when), a feature only citing its accepted `[DEMO]`,
epics/objectives close by rollup only, and nothing closes over open children. Demand
acceptance criteria at *creation* time — the reference runtime refuses to birth a
feature/story without the line its closure will bind to. And decompose just-in-time: split
a feature into stories only when it's next up, so the tree tracks throughput, not ambition.
See [safe.md](safe.md) for the live org's tree and closure gate.

---

## Pattern 13: Prose-Patching the Checker

**What it looks like:** A mechanical validator misfires on a false positive. The agent
correctly diagnoses the misfire — then "fixes" it by rewording its own output to route
around the checker, declares the checker "working as designed," and moves on. The
workaround calcifies: future output is written defensively against a bug, and the org's
record now teaches every later agent the same contortion.

**Why it happens:** In the live org, the handoff validator counted any line-leading
`[MARKER]` as a handoff post; a hard line-wrap landed a bare `[CODEREVIEW]` mention at
column 0 inside ordinary prose, and the comment failed validation. The agent re-worded the
paragraph (its edit note: "bracket markers avoided in this sentence so the validator
doesn't misread bare prose") instead of flagging the discriminator as wrong. Agents defer
to gates — that's exactly what you trained the org to do — so a buggy gate collects
*compliance*, not bug reports. And the norm the workaround implies ("never let the wrap
land a marker at column 0") is invisible and unenforceable anyway.

**Counter-pattern:** A false positive in an enforcement gate is a **code defect with a
blast radius** — every future message pays the tax — never a writing-style problem. Fix
the discriminator (the live org moved from "marker leads a line" to "marker leads a
*paragraph*," which its banner norm guarantees for every real handoff), add the misfire as
a regression test, and correct the record where the workaround was written down so it
doesn't get cargo-culted. The general rule: when agents start writing *around* a gate,
either the gate is wrong or the mandate is — both are PRs, not prose habits.

---

## The Pattern Behind the Patterns

These split into **two roots**, and you need both fixes:

- **Patterns 1–8 — too much authority.** The agent could do more than you intended, because the
  envelope was too wide or the charter didn't forbid the behavior. Fix: tighten the envelope
  (what it *can* do), clarify the charter (what it *should* do), add a gate for the specific
  failure mode.
- **Patterns 9–12 — a loop with no idea of "blocked," "done," or "do nothing."** The agents
  reason fine; the *orchestration loop* burns money because it must wake and must emit. Fix:
  give those states first-class representations — a blocker ledger, event-driven wakes (no
  event, no wake, no spend), state-change-only output, ceremony gated on real shipped
  output, and an evidence-gated close for every work-item.

Pattern 13 is the mirror held up to both: the gates themselves are code, and agents will
*comply* with a buggy gate rather than report it — so misfires surface as strange output
norms, not bug reports. Audit the workarounds your agents invent; each one points at a gate
to fix.

Don't try to patch either with more complex prompts. Use the governance primitives — envelopes,
gates, cadences, the blocker ledger, event-driven wakes, the spend breaker, and an output
predicate — to constrain the space of possible actions and the conditions under which an agent
acts at all, then let it operate freely within that space.
