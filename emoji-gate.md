# Emoji Gate — The 5-Step Approval Loop

The emoji gate is the core safety primitive. Every privileged action in the org
goes through this loop. There are no exceptions.

**Privileged actions**: spending money, deploying to production, chartering new
departments, dissolving departments, raising an agent's model tier.

---

## The 5 Steps

```
1. AGENT PROPOSES
   └── posts a typed message (SPEND / DEPLOY / CHARTER / SUNSET / PROMOTE)
       in the relevant channel, with all details and a clear ceiling/scope

2. HUMAN READS
   └── you receive the Mattermost notification
       you read the proposal — what is it asking for, at what cost, why

3. HUMAN REACTS (or ignores)
   └── 💰  fund a spend up to the stated ceiling
       🚀  approve a specific deploy (PR link required)
       🏛️  charter a new department
       ⚰️  dissolve a department or team
       💎  raise an agent's model tier
       🛑  veto — overrides everything, stops the action
       (no reaction = nothing happens; agent waits or re-proposes)

4. REGISTRAR EXECUTES
   └── deterministic code (not an LLM) reads your reaction
       checks: is the reactor the Chairman? is the emoji the right governance emoji?
       if yes: executes the approved action
       if no:  ignores the reaction (agents reacting does nothing)

5. AGENT CONFIRMS
   └── posts a STATUS update in its channel:
       what it did, within what scope, any receipts or PR links
```

---

## Why Each Step Exists

**Step 1 — Agent Proposes, Never Acts Unilaterally**
The agent cannot take privileged actions itself. It can only ask. This means you
see every request before any money moves or any code ships.

**Step 2 — You Read Before You React**
The proposal must contain enough information for you to say yes or no confidently.
If it doesn't, ignore it and the agent must re-propose with more detail.

**Step 3 — Emoji = Authorization**
Your emoji is the signature. The governance emoji are rare/custom so you won't
accidentally approve something. A thumbs-up or ✅ does nothing; only the specific
governance emoji triggers the gate.

**Step 4 — Registrar, Not an LLM, Executes**
The Registrar is deterministic code. It cannot be prompted, sweet-talked, or
confused. If `reactor.id != chairman.user_id`, the reaction does nothing. An agent
cannot approve its own proposal by reacting with the right emoji.

**Step 5 — Confirmation Creates the Audit Trail**
Every action leaves a STATUS post. You can audit every spend and every deploy by
searching `#board` for STATUS posts after a 💰 or 🚀 reaction.

---

## Message Templates

### SPEND proposal (agent posts this)
```
[SPEND] Requesting approval to spend up to $<CEILING> on <SERVICE/TOOL>.
Purpose: <one sentence — what problem it solves>
Expected return: <one sentence — how it moves the goal>
Ceiling: $<CEILING> total, one-time / per month
No spend proceeds until the Chairman reacts 💰.
```

### DEPLOY proposal (agent posts this)
```
[DEPLOY] Requesting approval to deploy PR #<N> to production.
PR: <URL>
What it changes: <one sentence>
Rollback plan: <one sentence — or "revert the PR">
No deploy proceeds until the Chairman reacts 🚀.
```

### CHARTER proposal (CEO posts this)
```
[CHARTER] Proposing to create the <NAME> department.
Role: <demand / supply / ops / research — one word>
Reports to: <CEO / existing department>
Model: <haiku / sonnet / opus>
Envelope: max_subagents=<N>, daily_token_budget=<N>
Justification: <one sentence — what gap this fills>
No department is created until the Chairman reacts 🏛️.
```

---

## Common Mistakes (and What Actually Happens)

| Mistake | What the Registrar does |
|---|---|
| Agent reacts with governance emoji | Nothing — reactor is not the Chairman |
| Agent acts before emoji | Registrar blocks; posts a violation notice |
| Chairman reacts ✅ instead of 💰 | Nothing — not a governance emoji |
| Agent re-proposes with higher ceiling | Chairman must re-react; old reaction doesn't transfer |
| Agent posts SPEND in wrong channel | Registrar still checks; channel doesn't affect validity |

---

## Tuning the Gate for Your Org

**Too many proposals clogging your feed?**
→ Raise the envelopes so agents can do more without asking. But only do this for
  categories of action you've already approved many times and trust completely.

**Agents acting weird without proposals?**
→ They're within their envelope. Review `envelopes.yaml` — the action is
  permitted within their current scope. Tighten the envelope, not the agent prompt.

**Want to fast-track a trusted agent for one category?**
→ There is no fast-track. The gate is the gate. If you trust it fully, raise the
  envelope so it doesn't need to ask. Don't try to skip the gate case-by-case —
  that's how agents learn to route around you.
