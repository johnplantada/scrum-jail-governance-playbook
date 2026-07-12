# Your Agent Governance Setup in an Afternoon

A practical walkthrough for the operator who just got burned by an agent — or who
wants to make sure they never do.

**Time required**: 2-4 hours (the governance layer) + however long your runtime takes  
**What you'll have when done**: a multi-agent org with human-in-the-loop
controls — a `decisions.yaml` + CODEOWNERS ledger gating money/org-shape, a
`workflow_dispatch`-only deploy gate — and a clear audit trail that's just git history.

**No special infrastructure.** This runs on your laptop, a cheap VPS, or CI. The only
external service is GitHub itself — you already have it if you're reading this here.

---

## What This Repo Ships vs. What You Build

Read this first — it is the difference between an afternoon that works and an
afternoon of hunting for files that don't exist.

**Ships in this repo (usable today, no other dependencies):**

| Piece | What it is |
|---|---|
| `bin/orggen` | Generator — stamps a new org repo from `_init/` (run it right now) |
| `_init/org-chart.yaml` | Org tree + envelope template the generator fills in |
| `_init/DESIGN.md` | The constitution template (invariants, gates, guardrails) |
| `_init/agents/` | The shared `_policy.md` + the per-department mandate template |
| `_init/blockers.yaml` | The blocker ledger, empty and documented |
| `_init/.env.example` | The env contract your runtime will read |
| The docs | `emoji-gate.md` (the gate walkthrough — kept its historical filename for link stability, even though the mechanism it now documents is `decisions.yaml`/`workflow_dispatch`, not chat emoji), `envelopes.yaml`, `patterns.md`, `blocker-ledger.md`, `safe.md`, `FIELD-NOTES.md` |

**You build or bring (the runtime — NOT included here):**

| Component | Its contract (what the docs assume it does) |
|---|---|
| GitHub itself | The org repo (this stamped skeleton) + your product repo(s); Issues + one Project (a `Stage` single-select) for work tracking; Actions for CI and the deploy workflow — every prod-touching job triggered by **`workflow_dispatch` only** (the deploy gate); `.github/CODEOWNERS` + branch protection routing `decisions.yaml` PRs to the Chairman |
| `pm-gh.sh` | The ticket CLI — `create`/`tasks`/`move`/`comment`/`comments`/`done`, plus the work-item tree verbs: `create --type epic\|feature\|story --parent N` (kind label + prefix, routing inherited, native sub-issue link) and `tree --id N` — mapped onto Issues + the Project's `Stage` field; ticket ids are `org#N` (the issue number) |
| `workitems.py` | The tree's closure gate (safe.md): `can-close` re-derives the facts live — no open children; story evidence = merged repo-qualified PR or done-when; feature evidence = accepted `[DEMO]` or done-when — and `pm-gh.sh done` refuses to close a work-item the gate rejects, posting the typed `[CLOSE]` payload when it passes |
| `runner.py` + `wake-rules.yaml` | The poller: each tick, diffs GitHub (issues, comments on both repos, workflow runs on the product repo) against a saved cursor, normalizes to events, and routes each through the rules table to wake the owning department |
| `agent-run.sh` | Runs one headless Claude cycle per wake — loads the department's mandate + `agents/_policy.md` + the open `blockers.yaml` queue, single-flight-locks so two wakes of the same agent never race, respects the `.halt` kill switch |
| `decisions.py check` (CI) | Validates every `decisions.yaml`-touching PR (unique ids, required fields) so a malformed entry can't merge |

The reference implementation of that runtime powers the live Scrum Jail org and is
private — it's ordinary scripts wrapping `gh`, small enough to write your own from the
contracts above. This playbook ships the governance layer and specifies each runtime
component's contract precisely enough to build your own thin version — none is more
than a small script.

---

## Before You Start

You need:
- [ ] A GitHub account, with a repo for your org (stamp this one into it) and one per product
- [ ] The `gh` CLI installed and authenticated: `gh auth login`, then
      `gh auth refresh -s project` (Project-board writes need the extra scope)
- [ ] An Anthropic API key or Claude subscription (for the agent LLMs)
- [ ] Your GitHub username (the Chairman — CODEOWNERS keys off it, and the deploy
      dispatch is yours to click)
- [ ] 30 minutes of uninterrupted focus for the initial setup

**You do NOT need**: Kubernetes, a load balancer, any SaaS beyond GitHub, any payment
processor, or a chat server. The whole system can run on a $5/mo VPS (or nothing at all,
if you're happy triggering ticks by hand or from a laptop cron).

---

## Step 1 — Stamp Your Org Repo (15 min, works today)

Generate a fresh org skeleton from this repo's templates:

```bash
bin/orggen init ../my-org --product "myproduct.com" --goal "$10k/month" \
  --chairman-github <YOUR_GITHUB_USERNAME> --departments ceo,business,it
```

Verify what you got — every one of these files now exists and is yours to edit:

```bash
ls -A ../my-org
# .env.example  DESIGN.md  README.md  agents/  blockers.yaml  org-chart.yaml  + the docs
cat ../my-org/org-chart.yaml   # your chairman's GitHub username + one department block per --departments
ls ../my-org/agents            # _policy.md + one stamped mandate per department
```

(Prefer to work by hand? Fork this repo instead and edit `org-chart.yaml` from the
template at the repo root. `orggen` exists so the chart and `agents/` can never
disagree about who exists.)

---

## Step 2 — Configure Your Org (30 min, works today)

**Edit `org-chart.yaml`** in your new org repo:

1. Confirm `chairman.github` is your GitHub username
2. Set the governance vocabulary (`charter`/`sunset`/`fund`/`ship`/`halt`/`promote`) —
   leave the defaults unless you want different words. This is ledger language now — a
   `decisions.yaml` entry's `type` field and the corresponding issue/PR prose — not a
   chat emoji a bot listens for
3. Adjust envelopes: start with the `department_head` preset, tighten after you see how
   the agents behave

See `envelopes.yaml` for a field-by-field explanation — including which fields are
code-enforced (the spawn ceilings, and `daily_token_budget` via the budget gate — see
"Tuning After Week 1") and which are declared policy (`can_spend`, `can_deploy`).

**Edit each `agents/<name>.md`** — these are the standing instructions:
- Replace the example product with your product
- Replace the example ticket/label conventions with your own if you've renamed anything
- Keep the governance protocol sections intact — those are the safety rails

---

## Step 3 — Provision GitHub: Issues, the Project, CODEOWNERS, the deploy gate (20 min)

Unlike the chat era, there are no bot accounts to create and no tokens to mint per
agent — every agent shares your own `gh auth login` session (a shared git identity;
each agent banners its comments, e.g. `**🛠️ IT —**`, to stay legible). What you do need
to provision, for **both** your org repo and your product repo:

1. **Labels + the Project board.** `scripts/github-pm-setup.sh` does this idempotently —
   it creates the `dept:*` wake labels plus `objective`/`proposal`, and the one shared
   Project with a `Stage` single-select whose options mirror `org-chart.yaml`'s
   `pm_stages` (the only place that list is defined). Safe to re-run.
2. **`.github/CODEOWNERS`** naming yourself (the Chairman) as owner of `decisions.yaml` —
   this is what routes a money/org-shape PR to you for review.
3. **Branch protection on `main`**: Settings → Branches → add a rule → enable "Require a
   pull request before merging" + "Require review from Code Owners". Without this,
   CODEOWNERS only *requests* your review — it doesn't *require* it, and a
   `decisions.yaml` PR could merge unreviewed.
4. **The deploy gate**, in your product repo — it's code, not Settings: every workflow
   that touches prod triggers on **`workflow_dispatch` only**. The common shape is one
   workflow with both triggers, where a push to `main` runs only the verify jobs:

   ```yaml
   on:
     push:
       branches: [main]    # verify only — build + test, no prod credentials
     workflow_dispatch:    # the gate: deploy runs ONLY from your manual dispatch
   jobs:
     verify: ...
     deploy:
       if: github.event_name == 'workflow_dispatch'
       ...
   ```

   A merge builds; only your dispatch deploys, and the Actions run history is the
   SHA-bound audit trail. *(The `production` environment with yourself as required
   reviewer is the approve-button variant of this gate — use it **only if your plan
   enforces it**: required reviewers work on public repos, but on a **private** repo
   they need Team/Enterprise; on a private Free-plan repo the Settings screen accepts
   them and silently doesn't enforce. The live org shipped the environment first and
   found out via Test 2. Whichever you pick, run the test.)*

Create Mattermost channels — no. That's the whole point: there is nothing here to
stand up beyond a handful of GitHub Settings clicks and one setup script.

---

## Step 4 — Build or Bring the Runtime (the honest step)

This is the part this repo does **not** do for you. You need three components from the
table at the top: `pm-gh.sh`, `runner.py` + `wake-rules.yaml`, and `agent-run.sh`. Build
them in any language; the contracts are small:

- **pm-gh.sh**: `create --project <dept> --title "…" [--assigned <dept>] [--desc "…"]
  [--priority 1-5] [--due YYYY-MM-DD]`, `tasks --project <dept> [--all]`,
  `move --id N --to <Stage>`, `comment`/`comments --id N`, `done --id N`. Each verb is a
  thin wrapper over `gh issue`/`gh project` calls — `create` opens an issue carrying the
  `dept:*` label, adds it to the Project, and sets `Stage` to the first `pm_stages`
  entry; `move` edits the `Stage` field on the matching Project item.
- **runner.py + wake-rules.yaml**: each tick, read a saved cursor, ask GitHub what
  changed since (issues + comments on both repos, workflow runs on the product repo),
  normalize each into an event (`kind`, `repo`, `label`, `workflow`, `conclusion`), and
  match it against `wake-rules.yaml`'s rules table — first match wins, an event matching
  nothing wakes nobody. A rule's `wake:` is either a literal department or
  `from-label` (route to whichever `dept:*` label the event carries). Advance the
  cursor; GitHub is the durable queue, so a closed laptop just means a longer backlog
  drained oldest-first at the next tick. Guards, every tick: a `.halt` file in the repo
  root stops the tick outright; a daily spend cap stops it from firing *live* wakes
  (routing still logs) once the metered spend ledger crosses it.
- **agent-run.sh `<department>`**: load `agents/<name>.md`, run one headless Claude
  cycle with that mandate + `agents/_policy.md` + the open `blockers.yaml` entries as
  context, then exit. Single-flight per department — two concurrent wakes of the same
  agent race the same product-repo branch or the same ticket, so lock on the agent name
  (a plain `mkdir`-based lock is portable and enough).

When your runtime is up, smoke-test it:

```bash
scripts/pm-gh.sh create --project ceo --title "CEO online" --desc "smoke test"
```

You should see a new issue land in your org repo, labeled `dept:ceo`, sitting in the
Project board's `To-Do` column.

---

## Step 5 — Run the Governance Gate Test (20 min, requires Step 4)

Before trusting your governance system, verify the gates behave as designed.

**Test 1 — Money / org-shape gate (`decisions.yaml`)**

Have an agent open a PR appending one `decisions.yaml` entry (`type: spend`,
`cost_usd: 10`, a test description).

Verify:
- [ ] `.github/CODEOWNERS` auto-requests your review on the PR
- [ ] CI's `decisions.py check` runs and passes (a well-formed entry)
- [ ] The agent does **not** proceed until the PR merges — the merge itself is the
      authorization, there's no separate reaction step
- [ ] To decline: close the PR without merging. No reaction, no separate "no" — nothing
      happens
- [ ] Confirm the PR is blocked from merging without your review (branch protection:
      "Require review from Code Owners")

**Test 2 — Deploy gate (the dispatch-only trigger)**

Have IT open a product-repo PR, get it green on CI, and merge it (or merge any trivial
change).

Verify:
- [ ] The push to `main` runs **verify jobs only** — the deploy job shows as skipped,
      not run. Merged ≠ deployed
- [ ] Nothing is waiting for you, because nothing can deploy without you: dispatch the
      workflow by hand (Actions → the deploy workflow → Run workflow → `main`) and
      verify the deploy job runs *now*
- [ ] Don't dispatch — the merged change sits, indefinitely, no timeout
- [ ] If you used the `production`-environment variant instead: verify the run actually
      **pauses** at the environment. If it sails straight through, your plan does not
      enforce required reviewers on this repo — fall back to the dispatch-only trigger

**Test 3 — Wrong actor**

Have a second GitHub account (or a friend) with no write access try to dispatch the
deploy workflow, or review the `decisions.yaml` PR.
- [ ] Verify GitHub itself refuses — workflow dispatch requires write access, and
      CODEOWNERS routing is platform-enforced, not something any of your scripts have
      to check

**Test 4 — Capability absence (the one that actually matters)**

The gates above are the approval interface. The enforcement is what the agent *cannot
reach*:
- [ ] Grep your agent host for payment or cloud-deploy credentials the agent could read
      directly (as opposed to its own scoped `gh` session) — there should be none
- [ ] Confirm the agent's `gh` token cannot push straight to `main`, dismiss a
      required review, **or dispatch the deploy workflow** — branch protection and the
      dispatch-only trigger are what actually stop it, not a prompt instruction (an
      agent-visible token with Actions-write rights can self-authorize a deploy; see
      the credential-hygiene note in emoji-gate.md)

If all four pass, your governance layer is live.

---

## Step 6 — First Real Objective (30 min)

File your first objective as a GitHub issue (you, the Chairman):

```bash
gh issue create --repo <your-org-repo> \
  --title "Objective: <your goal>. Due: <date>." \
  --label objective --label dept:ceo \
  --body "No spend or deploy without a decisions.yaml merge or the Chairman's workflow dispatch."
```

The CEO wakes on the runner's next tick — the `dept:ceo` label is what routes it — reads
the objective, and routes it to an owning department, which decomposes it as a
**work-item tree** on native sub-issues (`[PROPOSAL]`s under the objective, epics under
the accepted one, features/stories just-in-time — see safe.md): each child is born with
the `dept:*` label that routes it and the acceptance line its closure will bind to.

**What to watch for in the first week:**
- Agents opening `[PROPOSAL]` issues or `decisions.yaml` PRs (good — they're asking, not
  acting)
- Tickets moving `To-Do → Doing → Staged → Demo → Done` on the Project board
- The tree closing **upward** — stories citing merged PRs, features citing accepted
  `[DEMO]`s (`pm-gh.sh tree --id N` shows the rollup; a tree that only grows is
  patterns.md Pattern 12)
- Any unexpected spend or deploy attempt (should be zero — not because a watcher blocks
  it, but because agents hold no payment credentials or prod access; if an attempt
  could ever have *succeeded*, you have a capability leak to fix, not a prompt problem)

---

## Step 7 — Harden against the blocked loop (do this before week 2)

The `decisions.yaml`/deploy-dispatch gates stop agents doing too *much*. The other
failure mode — agents doing nothing but *re-announcing* that they're blocked — burns
just as much money and is what most operators hit first. Three primitives stop it (full
detail in [blocker-ledger.md](blocker-ledger.md), including the one flagged exception
for a blocker gating your only checkout or only audience — that one must stay loud):

1. **`blockers.yaml`** ledger. When an agent hits a human-only blocker (a credential, a
   URL, an approval, a repo-Settings action), it records the blocker there once and
   goes quiet. That file — not the issue stream — is your queue. Inject its open
   entries into each agent's wake prompt.
2. **Wake backpressure.** A tick that finds nothing new since the runner's last cursor
   is a no-op — no model call. `runner.py`'s cursor is what carries this now (there's no
   channel watermark to maintain). A blocked org on a quiet day should cost zero tokens.
   Even an unblocked, actively-polling org can hold its steady-state GitHub API cost
   near zero: conditional polling (ETag/`If-None-Match`) means an unchanged endpoint
   answers `304 Not Modified`, which GitHub does not bill against the rate limit — see
   [FIELD-NOTES.md §1](FIELD-NOTES.md) for the mechanism.
3. **Comments are the only "something changed" signal.** No ticket gets a "no change
   from yesterday" comment — every comment on a labeled issue re-wakes every department
   that label names, so a noise comment is a self-wake storm across your whole org, not
   just chatter.

If you'll run a process layer (sprints/demos/planning), read [safe.md](safe.md) first — the one
rule that keeps it from becoming theater is **gate every ceremony on a real prod ship, not on a
clock.**

## Tuning After Week 1

After you've watched the agents run for a week:

**If agents are asking too often:**
Raise their envelopes in `org-chart.yaml` for the categories you've already approved
multiple times. E.g. if IT has asked to run tests 10 times, that's within-envelope
now — remove the gate for that specific action.

**If agents are doing things you didn't expect:**
Read their `agents/<name>.md` file. The instructions may be too broad. Narrow the
charter, not the model tier.

**If an agent is burning too many tokens:**
`daily_token_budget` is code-enforced now, at two layers. Per envelope,
`scripts/budget_gate.py` sums the agent's day from the spend ledger; `agent-run.sh`
consults it before every cycle and **skips non-direct wakes** for an agent over budget.
Direct wakes — a runner-routed GitHub event like the Chairman's issue or a deploy
failure — still run, so this is a brownout, not a blackout: a spent budget never blocks
the Chairman, and the overage stays visible in the ledger. Org-wide, the runner holds
**all** wakes once the day's metered spend in `state/spend.jsonl` crosses
`SPEND_BREAKER_DAILY_USD` (the constitution's metering invariant). The cheaper levers
still come first: wake backpressure (a tick that finds nothing new is a no-op — zero
model calls), comment-triggered wakes staying rare (fewer noise comments → fewer peer
wakes), and pinning the agent to a cheaper model tier. Audit the spend ledger against
the declared budget and tighten those before you're leaning on the breakers.

**If you want to add a department:**
Open a `decisions.yaml` PR (`type: charter`) describing the new department. Your merge
**is** the charter — update `org-chart.yaml`'s `departments:` list in the same PR (or a
follow-up) so the chart and the decision stay in sync. Never add a department by editing
`org-chart.yaml` outside a reviewed PR; that's exactly what CODEOWNERS on the ledger
exists to prevent.

---

## What This Doesn't Cover

This playbook covers the governance layer — the human-in-the-loop controls that
keep agents from acting beyond their mandate. It does not cover:

- **The runtime itself** — `pm-gh.sh`, `runner.py`, and `agent-run.sh` are yours to
  build against the contracts in Step 4; the reference implementation is private
- **What your agents should actually do** — that's your `agents/<name>.md` files
- **Multi-tenant or team setups** — the org-chart is single-Chairman by design
- **Security hardening** — `.env`/secrets need standard secrets management, and scoping
  the `gh` token each agent runs under is on you

For the hard-won operational mechanisms the live org runs — wake backpressure numbers,
single-flight locks, worker tool-scoping, deploy-hold hibernation, and more — see
[FIELD-NOTES.md](FIELD-NOTES.md) before you build Step 4.

---

## Quick Reference — the runtime command surface

These are the commands the reference runtime exposes; whatever you build in Step 4
should have equivalents. They are a contract, not shipped binaries:

| Task | Reference shape |
|---|---|
| Create a ticket | `pm-gh.sh create --project <dept> --title "…"` |
| Create a work-item tree child (kind label + prefix, routing inherited, sub-issue link) | `pm-gh.sh create --type epic\|feature\|story --parent N` |
| Render an objective's tree (the rollup) | `pm-gh.sh tree --id N` |
| Move a ticket | `pm-gh.sh move --id N --to <Stage>` |
| List tickets | `pm-gh.sh tasks --project <dept>` |
| Comment on a ticket | `pm-gh.sh comment --id N --body "…"` |
| Read a ticket's thread | `pm-gh.sh comments --id N` |
| Close a ticket (work-items pass the closure gate and post their `[CLOSE]` evidence) | `pm-gh.sh done --id N [--pr <owner/repo#N> \| --done-when "…" \| --demo <url>]` |
| Run one poll tick | `make tick` (wraps `scripts/runner-watch.sh`, which drives `scripts/runner.py`) |
| Dry-run a tick — show would-be wakes without firing or advancing the cursor | `make preview` (`scripts/runner.py preview`) |
| Start one agent wake by hand | `scripts/agent-run.sh <dept>` |

---

*Built by the Scrum Jail autonomous org. If your agents went rogue, this is how we stopped ours.*  
*scrumjail.org*
