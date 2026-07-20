# Agent Misbehavior Patterns — and How to Govern Them

Seventeen patterns that burn operators. Each one has a specific governance fix.
These aren't hypothetical — they're the failure modes that show up when agents
have more authority than their operators intended (Patterns 1–8), when the
orchestration loop has no idea of "blocked," "done," or "do nothing" (Patterns
9–12), or when the gates themselves misfire and collect compliance instead of
bug reports (Pattern 13), when a state the org cannot represent — blocked on the
human, a reserved power, an unrouted ticket — is silently mishandled (Patterns
14–16), or when the org's own metered plumbing amplifies agent chatter into spend
(Pattern 17). See "The Pattern Behind the Patterns" at the end.

The failures are timeless; the counter-patterns are stated against the current
GitHub-native runtime (work = Issues on one Project via `scripts/pm-gh.sh`; authority =
the Chairman's `decisions.yaml` merges and a human-dispatched deploy — the product
repo's deploy workflows run on manual `workflow_dispatch` only;
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
credential, don't patch the prompt. *(v1: a Chairman chat approval, recorded by the
Registrar to `#decisions` — retired 2026-07-05.)*

---

## Pattern 2: Broken Deploy

**What it looks like:** Production goes down after the agent pushed a PR.
The agent believed the tests passed; they did not, or they passed on a stale branch.

**Why it happens:** the agent held a credential that could reach production, and no
deploy gate was enforced at the infra layer. The agent treated "tests green" as
sufficient authorization.

**Counter-pattern:** Put the deploy gate *outside the agent's trust domain*: agents get
no prod credentials and open PRs but never merge to `main`; every workflow that touches
prod triggers on **manual `workflow_dispatch` only** — a merge to `main` builds and
verifies but deploys nothing, and **the Chairman's dispatch is the deploy**, SHA-visible
and permanently audited in the Actions run history. (A `production` environment with a
required reviewer gives the same pause-for-a-human property *where your plan enforces
it* — required reviewers on a **private** repo need Team/Enterprise. The live org
shipped the environment first on a private Free-plan repo, discovered it silently
didn't enforce, and moved the gate into the trigger itself — code, reviewable, and
plan-independent.) Layer the protocol on top: declare `can_deploy: false` (a hint, not
a code switch), and put the PR link and a one-line rollback plan on the ticket before
asking for the dispatch. No amount of test coverage substitutes for the human gate —
the gate is for authorization, not quality assurance. *(v1: a Chairman chat approval,
recorded to `#decisions` — retired 2026-07-05.)*

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
the agent again (the unblock is itself new inbound). One deliberate exception: an entry
flagged as gating the org's **only checkout or only audience** never goes quiet — see the
`gates_market_contact` flag in [blocker-ledger.md](blocker-ledger.md) §2, paid for by a
real incident. See [blocker-ledger.md](blocker-ledger.md).

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
checks `pm-gh.sh tasks --project IT` and reads the ticket's Status (the canonical list
is `org-chart.yaml pm_stages`). If it's In Progress or Done, the agent replies on the ticket
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
merge past CODEOWNERS or dispatch the deploy workflow, so nobody has to parse
reactor ids anymore — but nothing checks whether a new `decisions.yaml` PR is a
re-phrase of one that was closed unmerged. The behavioral discipline has to come from
the charter. If an agent is evading, rewrite its charter to be explicit about refusal
finality, then restart it.

(A note on `halt`, because it's easy to misread as a per-proposal veto: it isn't one. The
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
   write to it once and stop, never re-post (Pattern 4; the one flagged exception —
   `gates_market_contact`, blocker-ledger.md §2 — is reprinted *by the tooling*, not
   re-posted by agents). It is the operator's queue — and
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
wakes nobody (itself included). The residue after all that damping is *real-but-pointless*
events — a peer comment landing on an already-closed thread still boots a full model to
conclude "already handled" — and the live org's answer is the wake filter + wake-yield
metric (FIELD-NOTES.md §12): tag every wake's outcome, steer on the fraction that mutate
the record, and defer the provably-pointless wake classes in the router for $0. The
org-wide backstop is financial: the runner holds live wakes
once the day's metered spend crosses `SPEND_BREAKER_DAILY_USD`, so spend without progress hits
a hard ceiling. One honest caveat: a wake held at the cap does not get its events re-queued —
the held branch neither fires nor spools, and the cursor advances; the events stay visible on
the issue, but that re-wake is manual. (A *crashed* cycle, by contrast, is covered now: a
nonzero dispatch re-queues its events through the deferred-event spool with bounded retries,
then dead-letters to `state/dead-letter.jsonl` — at-least-once up to the ceiling.) *(v1 damped
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

## Pattern 14: The Org That Couldn't Ask (silent blocking)

**What it looks like:** The org goes quiet. No comments, no PRs, wakes returning "0 events
→ 0 wakes" tick after tick. Every dashboard is green and nothing is happening. The operator
concludes the agents are broken or lazy and starts debugging the wake path — which is
working perfectly. The org is **correctly blocked on the human**, and has no way to say so.

**Why it happens:** This is the compound interest on your own good invariants. Agents never
perform human-only actions (invariant 2); they record the blocker once and **go quiet**
(Pattern 9's cure). Departments decompose just-in-time, so downstream work isn't even
ticketed yet (Pattern 12's cure). Each department, followed to the letter, produces silence
— and silence is indistinguishable from idleness from the outside. In the live incident,
every open thread sequenced behind one product PR that had been open, clean and mergeable
for five hours; the supply department woke, read three new objectives, correctly said they
self-gate behind that merge, and went quiet. Nothing was broken. **Nobody asked.**

The trap is that "waiting on the Chairman" is *distributed*: each agent knows its own
blocker, no agent knows the org's, and the human — the only one who can clear any of them —
is the only party with no inbox. The blocker ledger is necessary but not sufficient: it is
a file in a repo, and a file nobody is pushed to read is not a queue.

**Counter-pattern:** One **Chairman action queue** — a single untyped epic whose sub-issues
are exactly the things only the human can do, synced from ground truth by a deterministic,
token-free engine (open ledger entries, Chairman-ready PRs, unanswered `[PROPOSAL]`s).
Children appear when the fact appears and close themselves when it clears, so the queue can
never rot into a stale to-do list. Assign each child to the human's actual account: a queue
he must remember to visit is a queue he will stop visiting, and "assigned to me" is the one
inbox every operator already reads. Own it with a **board-reporting** node — one that
reports into the production chain can be told not to ask.

Then hold the line in code: the org's ONE scheduled process should make "waiting on you"
loud, and going quiet must never be the same thing as needing nothing. If the queue is
empty and the org is silent, the org is genuinely idle — that is a different bug, and now
you can tell the two apart.

---

## Pattern 15: Flavor-Text Authority (the power you never actually reserved)

**What it looks like:** A power everyone "knows" belongs to the human — filing objectives,
picking priorities, naming the roadmap — turns out to be reserved nowhere. The constitution
enumerates its reserved powers and this isn't among them. A label or a template says so in
prose (`objective` → *"Chairman work injection"*), which enforces nothing. And somewhere in
a mandate, the *opposite* is written as a positive instruction: *"turn the north star into a
small number of measurable `[OBJECTIVE]` issues."* An agent does exactly that, announces it
transparently, and the human discovers his intake was never his.

**Why it happens:** Reserved powers get written where the *enforcement* lives — money and
deploys have gates, so they get clauses. Work intake has no gate, so nobody wrote the
clause; the intent survived only as habit, a label description, and the fact that the human
happened to be the one doing it. Then a mandate — usually inherited from a reference org
where the same ambiguity was harmless — states the opposite, and the mandate wins, because
a mandate is an instruction and a label description is decoration. **The agent that "broke"
the rule is the one that read the docs.**

This one hides especially well because there is nothing to catch it: authorship checks are
impossible when every agent acts through the human's own token (`gh issue view N --json
author` returns *his* login either way), so CI cannot tell the two apart, and the act looks
like diligence — filling a real coverage gap — rather than a seizure.

**Counter-pattern:** Reserved powers get **enumerated in the constitution**, not implied by
a label. Then grep every mandate for text that contradicts them — the contradiction is the
bug, and it's usually one sentence in one file. Where no platform gate is possible, gate at
the boundary the wake itself creates: a per-wake env var the human's own shell never has,
checked by a PreToolUse hook, refusing the *honest* path (the bare CLI call an obedient
agent would make). Be honest in the doc that this is a backstop, not a wall — a token that
can reach the API can route around any tool gate — and fix the mandate first, because the
hook only holds a line the mandate already drew. Two tests, always: one that the gate
refuses the real regression, and several that it never touches reading, discussing, or
building *under* the thing it protects.

---

## Pattern 16: Invisible Intake (the unrouted ticket)

**What it looks like:** Work arrives and nothing happens. The Chairman files a follow-up —
"address my review comments before we merge" — and no agent comments, no wake fires, the
issue just sits. Hours later a *different* department notices by accident, traces the gap
by hand, and posts the diagnosis: the ticket existed all along, but it carried only a kind
label (`feature`), no `dept:*` label, so the router had nowhere to send it. To the human it
reads as disobedience — "the agents aren't following orders" — when in fact no agent ever
saw the order.

**Why it happens:** Routing is label-driven — the `dept:*` label *is* the wake signal — and
every intake path that doesn't force a department can mint a ticket the router cannot see.
The scripted path (`pm-gh.sh`) refuses to create an unroutable item, but the web form's
department dropdown maps to a label *at triage*, a manual step that is easy to skip; and
the runner treated "matched no rule" as "wake nobody," leaving one `unrouted` log line
nobody reads. The failure is silent by construction: the loud path — an agent complaining —
requires an agent to have woken, which is precisely what didn't happen. This is Pattern
14's inverse: there the org couldn't tell the human it was waiting; here the human couldn't
tell the org to start.

**Counter-pattern:** **No issue may route to nobody.** The routing table ends with a
catch-all — an issue matching no `dept:*` rule wakes the **warden**, whose charter already
names this exact triage ("an unroutable issue whose owner is clear from content → add the
label"); the label she adds then wakes the owning department, and an unclear owner becomes
one question to the Chairman on the queue epic. Orggen generates the catch-all into every
stamped org's `wake-rules.yaml`, and the warden is an organ every org has (Pattern 14), so
the fallback always exists. Note the shape of the fix — one rule in the router, not a new
watcher, so the counter-ratchet holds. Non-issue kinds keep their existing defaults: PRs
already route by repo, and workflow-run rules stay selective on purpose (a matched-nothing
run is usually a run you chose not to care about).

---

## Pattern 17: Metered Amplification (the CI-quota burn)

**What it looks like:** Every CI check across every repo goes red at once with "The job
was not started because recent account payments have failed or your spending limit needs
to be increased." Runs die in ~2 seconds with zero steps executed. Nothing agent-side
changed — and because required status checks live in CI, every PR in every org sharing
the account is suddenly unmergeable except by the admin override authorization-gate.md warns
erodes the gate.

**Why it happens:** Hosted CI is metered, and the meter is unforgiving: GitHub bills each
job's runtime **rounded up to the whole minute**, and a Free/Pro account has 2,000/3,000
included minutes a month — pooled across every private repo the account owns. The live
org wired its handoff validator to trigger on **every issue comment**. Agents comment at
machine frequency — during the warden's self-echo storm (Pattern 11), 283 wakes in one
day — so a five-second validation run billed a full minute, hundreds of times a day, and
the monthly quota died in about three days. With no payment method on file, GitHub then
blocked Actions **account-wide** until the monthly reset — the sibling org's CI went dark
through no act of its own. The governance layer had placed its merge gates inside a
metered service, so quota exhaustion *was* a governance outage.

**Counter-pattern:** Metered compute must never trigger at agent frequency. No
`issue_comment`/`issues` triggers on hosted workflows, ever — validation that must see
every comment belongs in the runner or warden on the operator's machine, where compute is
free. Hosted workflows are for **rare, deliberate** runs — the `workflow_dispatch`-only
deploy gate is fine, because the quota dies by frequency, not intent — and re-enabling
any workflow needs a budget line first: (jobs per run) × (runs per month) × the
one-minute minimum, against the monthly pool; consolidate multi-job workflows while
you're at it, since three jobs bill three minimums per run. Run the CI suite itself at
push time on the operator's machine (FIELD-NOTES §13). Know the coupling required status
checks buy: a merge gate that lives in metered CI inherits the meter's failure mode. And
block-at-quota is the $0 backstop — an account with no stored payment method cannot be
surprise-billed, only paused — so treat 75% of quota mid-month as an incident, not a
curiosity.

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

Patterns 14–16 are the second root refined: "blocked on the human," "this power is
reserved," and "this ticket has an owner" each turned out to need a first-class
representation too — a queue the operator actually reads, a constitution line, a router
catch-all — because a state the machinery cannot represent is a state the org silently
mishandles. Pattern 17 is the mirror held up to the *infrastructure*: no agent decided
anything — the meter was in the plumbing the governance layer itself ran on. Budget the
machinery like you budget the agents.

Don't try to patch either with more complex prompts. Use the governance primitives — envelopes,
gates, cadences, the blocker ledger, event-driven wakes, the spend breaker, and an output
predicate — to constrain the space of possible actions and the conditions under which an agent
acts at all, then let it operate freely within that space.
