# Your Agent Governance Setup in an Afternoon

A practical walkthrough for the operator who just got burned by an agent — or who
wants to make sure they never do.

**Time required**: 2-4 hours  
**What you'll have when done**: a running multi-agent org with human-in-the-loop
controls, emoji-gated spend and deploys, and a clear audit trail.

**No special infrastructure.** This runs on your laptop, a cheap VPS, or a Docker
host. The only external service is Mattermost (free self-hosted, or cloud free tier).

---

## Before You Start

You need:
- [ ] A Mattermost instance (self-hosted or cloud). Free tier works.
- [ ] Docker or a Linux server with Docker Compose
- [ ] An Anthropic API key (for the agent LLMs)
- [ ] Your Mattermost user ID (Profile → Copy ID)
- [ ] 30 minutes of uninterrupted focus for the initial setup

**You do NOT need**: Kubernetes, a load balancer, any SaaS, any payment processor,
or any cloud provider. The whole system can run on a $5/mo VPS.

---

## Step 1 — Fork This Repo as Your Org Repo (15 min)

This GitHub template repository IS the governance system. Your org config lives
here. Fork or use it as a template to create `<yourcompany>-org` repo.

```bash
# Use this repo as a GitHub template, then clone your fork:
git clone https://github.com/<you>/<yourcompany>-org
cd <yourcompany>-org
```

What you're forking:
- `org-chart.yaml` — your agents and their authority envelopes
- `agents/` — the standing instructions each agent runs on every wake
- `DESIGN.md` — the constitution (read it; it's short)
- `bin/` — the bus (message bus), pm (project manager), and Registrar tooling
- `compose.yaml` — Docker Compose for Mattermost + the agent runner

---

## Step 2 — Configure Your Org (30 min)

**Edit `org-chart.yaml`** (from the `org-chart.yaml` template in this playbook):

1. Replace `<YOUR_MATTERMOST_USER_ID>` with your ID
2. Replace each `<*_BOT_USER_ID>` with the bot user IDs you'll create in Step 3
3. Set the governance emoji — leave the defaults unless you want custom ones
4. Adjust envelopes: start with the `department_head` preset, tighten after you see how the agents behave

See `envelopes.yaml` in this playbook for a field-by-field explanation.

**Edit each `agents/<name>.md`** — these are the standing instructions:
- Replace the example product (`scrumjail.org`) with your product
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
   CEO_BOT_TOKEN=...
   BUSINESS_BOT_TOKEN=...
   IT_BOT_TOKEN=...
   ```

Create Mattermost channels matching your agent names: `board`, `business`, `it`
(or whatever you named them in `org-chart.yaml`). Add all bots to all channels.

---

## Step 4 — Start the Stack (15 min)

```bash
# Copy and edit the env file:
cp .env.example .env
# Fill in: ANTHROPIC_API_KEY, *_BOT_TOKEN, MATTERMOST_URL, etc.

# Start Mattermost + agent runner:
docker compose up -d

# Verify agents can post:
./bin/bus post --channel board --type STATUS --as ceo --body "CEO online. Standing by."
```

You should see the CEO's message appear in your Mattermost `#board` channel.

---

## Step 5 — Run the Emoji Gate Test (20 min)

Before trusting your governance system, verify the gate actually blocks things.

**Test 1 — Spend gate**

Have an agent post a SPEND proposal:
```bash
./bin/bus post --channel board --type SPEND --as business \
  --body "Requesting approval to spend up to \$10 on a test. No spend proceeds until 💰."
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

```bash
./bin/bus post --channel board --type DEPLOY --as it \
  --body "Requesting approval to deploy PR #1 (test). No deploy proceeds until 🚀."
```

Verify the same flow as Test 1 with 🚀 (and decline, as always, by not reacting).

**Test 3 — Wrong reactor**

Have someone else on your Mattermost react with 💰 on the same post.
- [ ] Verify the Registrar ignores it (only your Chairman ID triggers the gate)

If all three tests pass, your governance layer is live.

---

## Step 6 — First Real Objective (30 min)

Post your first objective to the CEO:

```bash
./bin/bus post --channel board --type TASK --as chairman \
  --body "Objective: [your goal here]. Due: [date]. No spend/deploy without Board approval."
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

- **What your agents should actually do** — that's your `agents/<name>.md` files
- **Product-specific tooling** — the `bin/` and `scripts/` tools are the scaffolding;
  you'll add your own integrations
- **Multi-tenant or team setups** — the org-chart is single-Chairman by design
- **Security hardening** — the `.env` file and API keys need standard secrets management

For the above, see the README in this repo or reach out at scrumjail.org.

---

## Quick Reference

| Task | Command |
|---|---|
| Read a channel | `./bin/bus read --channel <name> --since-last --as <agent>` |
| Post a message | `./bin/bus post --channel <name> --type STATUS --as <agent> --body "..."` |
| Create a task | `./bin/pm create --project <name> --title "..." --priority 1 --due YYYY-MM-DD` |
| Move a task | `./bin/pm move --id <N> --to Doing` |
| List tasks | `./bin/pm tasks --project <name>` |
| Start an agent wake | `./bin/agent-run.sh <agent-name>` |

---

*Built by the Scrum Jail autonomous org. If your agents went rogue, this is how we stopped ours.*  
*scrumjail.org*
