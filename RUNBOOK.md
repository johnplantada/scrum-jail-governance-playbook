# Your Agent Governance Setup in an Afternoon

A practical walkthrough for the operator who just got burned by an agent — or who
wants to make sure they never do.

**Time required**: 2-4 hours (the governance layer) + however long your runtime takes  
**What you'll have when done**: a multi-agent org with human-in-the-loop
controls, emoji-gated spend and deploys, and a clear audit trail.

**No special infrastructure.** This runs on your laptop, a cheap VPS, or a Docker
host. The only external service is Mattermost (free self-hosted, or cloud free tier).

---

## What This Repo Ships vs. What You Build

Read this first — it is the difference between an afternoon that works and an
afternoon of hunting for files that don't exist.

**Ships in this repo (usable today, no other dependencies):**

| Piece | What it is |
|---|---|
| `bin/orggen` | Generator — stamps a new org repo from `_init/` (run it right now) |
| `_init/org-chart.yaml` | Org tree + envelope template the generator fills in |
| `_init/DESIGN.md` | The constitution template (roles, gates, guardrails) |
| `_init/agents/` | The shared `_policy.md` + the per-department mandate template |
| `_init/blockers.yaml` | The blocker ledger, empty and documented |
| `_init/.env.example` | The env contract your runtime will read |
| The docs | `emoji-gate.md`, `envelopes.yaml`, `patterns.md`, `blocker-ledger.md`, `safe.md`, `FIELD-NOTES.md` |

**You build or bring (the runtime — NOT included here):**

| Component | Its contract (what the docs assume it does) |
|---|---|
| Mattermost | Self-hosted or cloud; channels per department + `#board`, `#decisions` |
| `bus` helper | Posts stamped `[TYPE]` messages and reads channels incrementally (`--since-last`, with a non-advancing `--peek`) via the Mattermost REST API |
| Registrar | Listens on the Mattermost WebSocket; on a reaction, checks `reactor.id == chairman.user_id` + governance emoji + message `[TYPE]`; executes org changes (🏛️/⚰️/💎/🛑), records 💰/🚀 approvals to `#decisions`; wakes a channel's owner on new posts (rate-limited) |
| Wake runner | Runs one headless Claude cycle per agent per wake, with the backpressure checks described in `blocker-ledger.md` and `FIELD-NOTES.md` |
| `pm` tracker helper | Optional but recommended — drives a self-hosted tracker (e.g. Vikunja) so work is tickets, not chatter |

The reference implementation of that runtime powers the live Scrum Jail org and is
private. This playbook ships the governance layer and specifies each runtime
component's contract precisely enough to build your own thin version — none of the
four is more than a small service or script.

---

## Before You Start

You need:
- [ ] A Mattermost instance (self-hosted or cloud). Free tier works.
- [ ] Docker or a Linux server (for Mattermost, and wherever your runtime will live)
- [ ] An Anthropic API key or Claude subscription (for the agent LLMs)
- [ ] Your Mattermost user ID (Profile → Copy ID)
- [ ] 30 minutes of uninterrupted focus for the initial setup

**You do NOT need**: Kubernetes, a load balancer, any SaaS, any payment processor,
or any cloud provider. The whole system can run on a $5/mo VPS.

---

## Step 1 — Stamp Your Org Repo (15 min, works today)

Generate a fresh org skeleton from this repo's templates:

```bash
bin/orggen init ../my-org --product "myproduct.com" --goal "$10k/month" \
  --chairman-id <YOUR_CHAIRMAN_USER_ID> --departments ceo,business,it
```

Verify what you got — every one of these files now exists and is yours to edit:

```bash
ls -A ../my-org
# .env.example  DESIGN.md  README.md  agents/  blockers.yaml  org-chart.yaml  + the docs
cat ../my-org/org-chart.yaml   # your chairman id + one department block per --departments
ls ../my-org/agents            # _policy.md + one stamped mandate per department
```

(Prefer to work by hand? Fork this repo instead and edit `org-chart.yaml` from the
template at the repo root. `orggen` exists so the chart and `agents/` can never
disagree about who exists.)

---

## Step 2 — Configure Your Org (30 min, works today)

**Edit `org-chart.yaml`** in your new org repo:

1. Confirm your Chairman user ID (or replace `<YOUR_MATTERMOST_USER_ID>`)
2. Replace each `<*_BOT_USER_ID>` with the bot user IDs you'll create in Step 3
3. Set the governance emoji — leave the defaults unless you want custom ones
4. Adjust envelopes: start with the `department_head` preset, tighten after you see how the agents behave

See `envelopes.yaml` for a field-by-field explanation — including which fields are
code-enforced (the spawn ceilings) and which are declared policy (`can_spend`,
`can_deploy`, `daily_token_budget`).

**Edit each `agents/<name>.md`** — these are the standing instructions:
- Replace the example product with your product
- Replace the example channels with your channel names
- Keep the governance protocol sections intact — those are the safety rails

---

## Step 3 — Set Up Mattermost Bot Accounts (20 min)

For each agent in your org (CEO, Business, IT, etc.):

1. Create a bot account in Mattermost: **System Console → Integrations → Bot Accounts → Add Bot**
2. Copy the bot's user ID (visible in the URL or via API)
3. Copy the access token (only shown once — save it)
4. Paste the bot user ID into `org-chart.yaml`
5. Add the access token to your `.env` file (never commit this):
   ```
   MATTERMOST_TOKEN_CEO=...
   MATTERMOST_TOKEN_BUSINESS=...
   MATTERMOST_TOKEN_IT=...
   ```
   (`.env.example` in your stamped repo documents the full contract.)

Create Mattermost channels matching your agent names: `board`, `business`, `it`
(or whatever you named them in `org-chart.yaml`), plus `#decisions` for the audit
ledger. Add all bots to all channels.

---

## Step 4 — Build or Bring the Runtime (the honest step)

This is the part this repo does **not** do for you. You need the four components
from the table at the top: a `bus` helper, the Registrar, a wake runner, and
(optionally) a `pm` helper. Build them in any language; the contracts are small:

- **bus**: `post --channel <ch> --type <TYPE> --as <agent> --body "..."` and
  `read --channel <ch> --since-last --as <agent> [--peek]`. Posting stamps the
  `[TYPE]` tag; reading tracks a per-agent watermark; `--peek` reads without
  advancing it (the wake-backpressure check depends on that).
- **Registrar**: subscribe to the Mattermost WebSocket. On `reaction_added`:
  ignore unless `reactor.id == chairman.user_id`, the emoji is one of the six
  governance emoji in `org-chart.yaml`, and the reacted message's `[TYPE]` matches
  the emoji (💰→SPEND, 🚀→DEPLOY, 🏛️→CHARTER, ⚰️→SUNSET, 💎→PROMOTE; 🛑 fires on
  anything). Execute org-chart changes yourself; for 💰/🚀 post the approval to
  `#decisions` and stop — the agent acts on it, and the hard backstop is that agents
  hold no spend/deploy credentials. On `posted`: wake the channel's owning agent
  (rate-limit agent-to-agent wakes so the swarm can't DDoS itself — the live org's
  numbers and the full mechanism are in `FIELD-NOTES.md`).
- **Wake runner**: per wake — check the halt flag, take a per-agent lock, peek the
  channels, no-op if nothing is new, otherwise run one headless Claude cycle with
  the agent's mandate + `_policy.md` + the open blockers as the prompt.

When your runtime is up, smoke-test it:

```bash
# however your bus invokes — the reference shape:
bus post --channel board --type STATUS --as ceo --body "CEO online. Standing by."
```

You should see the CEO's message appear in your Mattermost `#board` channel.

---

## Step 5 — Run the Emoji Gate Test (20 min, requires Step 4)

Before trusting your governance system, verify the gate behaves as designed.

**Test 1 — Spend gate**

Have an agent post a SPEND proposal (via your bus):

```
[SPEND] Requesting approval to spend up to $10 on a test. No spend proceeds until 💰.
```

Verify:
- [ ] You see the message in Mattermost
- [ ] The agent does NOT proceed (waits for your reaction)
- [ ] React with 💰 — the approval is recorded to #decisions and the agent proceeds
- [ ] To decline: don't react — no reaction means no approval, and nothing happens
- [ ] React with 🛑 (on any message) — the WHOLE org halts: a halt flag drops, the bus
      refuses to post, and every wake skips until you clear the flag. Verify the halt,
      then clear it before continuing

**Test 2 — Deploy gate**

```
[DEPLOY] Requesting approval to deploy PR #1 (test). No deploy proceeds until 🚀.
```

Verify the same flow as Test 1 with 🚀 (and decline, as always, by not reacting).

**Test 3 — Wrong reactor**

Have someone else on your Mattermost react with 💰 on the same post.
- [ ] Verify the Registrar ignores it (only your Chairman ID triggers the gate)

**Test 4 — Capability absence (the one that actually matters)**

The gates above are the approval interface. The enforcement is what the agent
*cannot reach*:
- [ ] Grep your agent host for payment credentials the agent could read: there
      should be none, anywhere in its environment
- [ ] Confirm your product repo's deploy pipeline requires branch protection +
      a human approval your agents don't hold

If all four pass, your governance layer is live.

---

## Step 6 — First Real Objective (30 min)

Post your first objective to the CEO (via your bus, as the Chairman):

```
[TASK] Objective: [your goal here]. Due: [date]. No spend/deploy without Board approval.
```

The CEO will wake on its next cadence, read the objective, and begin decomposing it
into proposals and tasks for Business and IT.

**What to watch for in the first week:**
- Agents posting PROPOSAL messages (good — they're asking, not acting)
- Agents moving tasks through To-Do → Doing → Done in the project tracker
- Any unexpected spend or deploy attempts (should be zero — not because a daemon blocks
  them, but because agents hold no payment credentials or prod access; if an attempt
  could ever have *succeeded*, you have a capability leak to fix, not a prompt problem)

---

## Step 7 — Harden against the blocked loop (do this before week 2)

The emoji gates stop agents doing too *much*. The other failure mode — agents doing nothing but
*re-announcing* that they're blocked — burns just as much money and is what most operators hit
first. Three primitives stop it (full detail in [blocker-ledger.md](blocker-ledger.md)):

1. **Add a `blockers.yaml`** ledger. When an agent hits a human-only blocker (a credential, a
   URL, an approval), it records the blocker there once and goes quiet. That file — not the chat
   stream — is your queue. Inject its open entries into each agent's wake prompt.
2. **Turn on wake backpressure.** A scheduled wake that finds nothing new since the agent last
   read should be a no-op — no model call. A blocked org on a quiet day should cost zero tokens.
3. **Make STATUS state-change-only.** No "no change from yesterday" posts — they're noise, and
   each one wakes every peer (the self-wake storm).

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
`daily_token_budget` is a declared target, not a code-enforced cap — nothing in the
reference runtime stops an agent at N tokens. The mechanisms that actually bound cost
are wake backpressure (a wake that finds nothing new is a no-op — zero model calls),
state-change-only STATUS (fewer posts → fewer peer wakes), and pinning the agent to a
cheaper model tier. Audit the spend ledger against the declared budget and tighten
those levers.

**If you want to add a department:**
Post a CHARTER to `#board`, wait for your 🏛️ reaction, and the Registrar creates
the agent. Never add a department by editing `org-chart.yaml` directly — the
Registrar owns that file.

---

## What This Doesn't Cover

This playbook covers the governance layer — the human-in-the-loop controls that
keep agents from acting beyond their mandate. It does not cover:

- **The runtime itself** — the bus, Registrar, and wake runner are yours to build
  against the contracts in Step 4; the reference implementation is private
- **What your agents should actually do** — that's your `agents/<name>.md` files
- **Multi-tenant or team setups** — the org-chart is single-Chairman by design
- **Security hardening** — the `.env` file and API keys need standard secrets management

For the hard-won operational mechanisms the live org runs — wake backpressure numbers,
the haiku broadcast pre-gate, single-flight locks, worker tool-scoping, deploy-hold
hibernation, and more — see [FIELD-NOTES.md](FIELD-NOTES.md) before you build Step 4.

---

## Quick Reference — the runtime command surface

These are the commands the reference runtime exposes; whatever you build in Step 4
should have equivalents. They are a contract, not shipped binaries:

| Task | Reference shape |
|---|---|
| Read a channel | `bus read --channel <name> --since-last --as <agent>` |
| Peek without advancing | `bus read --channel <name> --since-last --as <agent> --peek` |
| Post a message | `bus post --channel <name> --type STATUS --as <agent> --body "..."` |
| Create a task | `pm create --project <name> --title "..." --priority 1 --due YYYY-MM-DD` |
| Move a task | `pm move --id <N> --to Doing` |
| List tasks | `pm tasks --project <name>` |
| Start an agent wake | `agent-run.sh <agent-name>` |

---

*Built by the Scrum Jail autonomous org. If your agents went rogue, this is how we stopped ours.*  
*scrumjail.org*
