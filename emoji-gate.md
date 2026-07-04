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
       🛑  EMERGENCY STOP — halts the ENTIRE org, not just this proposal
           (drops a halt flag: the bus refuses to post, the tracker refuses
           writes, every wake skips — until the operator clears the flag)
       (no reaction = nothing happens; agent waits or re-proposes.
        THIS is the everyday "no" — you decline a proposal by not
        approving it, not by halting the org)

4. REGISTRAR VERIFIES & ACTS
   └── deterministic code (not an LLM) reads your reaction
       checks: is the reactor the Chairman? the right governance emoji? the right [TYPE]?
       🏛️/⚰️/💎: the Registrar itself executes the org change
       💰/🚀:   the Registrar records the approval to the #decisions audit ledger;
                the agent then acts within the approved scope (the hard backstop is
                that agents hold no payment credentials or prod access — see below)
       if any check fails: the reaction does nothing (agents reacting does nothing)

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

**Step 4 — Registrar, Not an LLM, Verifies**
The Registrar is deterministic code. It cannot be prompted, sweet-talked, or
confused. If `reactor.id != chairman.user_id`, the reaction does nothing. An agent
cannot approve its own proposal by reacting with the right emoji.

**That check is only as strong as your credential hygiene.** Reactor verification
assumes no agent can ever *post the reaction as the Chairman*. It fails quietly if a
Chairman-capable token — a personal-access-token minted for a voice bridge, or an
admin token that can mint one — sits in the same `.env` your wake script sources into
every agent cycle: an agent (or a prompt-injected one) holding it can self-approve
💰/🚀, and the gate is void *in code* while looking intact. Keep such tokens in a
separate, never-agent-sourced file (e.g. `.env.chairman`, chmod 600), `unset` them
defensively at the wake chokepoint, and if one ever lands in an agent-visible
environment treat it as burned: rotate it, don't just move it. (A live audit caught
exactly this configuration.)

Be precise about what happens after the check passes, though. For org-shape actions
(🏛️ charter, ⚰️ sunset, 💎 promote, 🛑 halt) the Registrar itself executes the change —
it is the only thing that mutates `org-chart.yaml`. For 💰 and 🚀 it records the
approval to `#decisions` and executes nothing: there is deliberately no code path from
"Chairman reacted 💰" to "money moves." The hard enforcement for money and prod is
**capability-absence, outside the runtime's trust domain** — agents hold no payment
credentials, and the deploy pipeline sits behind branch protection + human review on
the product repo. The gate's job is to be the legible approval interface and the audit
trail on top of that.

**Step 5 — Confirmation Creates the Audit Trail**
Every action leaves a STATUS post. You can audit every spend and every deploy by
searching `#board` for STATUS posts after a 💰 or 🚀 reaction.

---

## Before the 🚀: the code-review and demo gates

A production deploy is the one privileged action with **pre-conditions the Registrar
doesn't check** — they're enforced by convention + a process warden, not by the emoji
gate itself. For a product-surface change, the 🚀 is the *last* step of a short chain,
not the first:

1. **`[CODEREVIEW]`** — an independent **Reviewer** department (structurally separate
   from whoever wrote the code: **author ≠ reviewer**) reviews the PR and posts a
   `PASS` / `CHANGES-REQUESTED` verdict, bound to the PR's **head SHA** and anchored to a
   green code-review CI run on that SHA. A demo may cite only a `PASS`.
2. **`[DEMO]`** — the demand side accepts a worked demonstration that the change meets
   its acceptance criteria; the demo cites the passing `[CODEREVIEW]`. See
   [safe.md](safe.md).
3. **🚀** — the deploy-approval relay cites the *accepted* `[DEMO]` id. The Chairman
   reacts 🚀; the Registrar records it to `#decisions` exactly as before.

Both gates ship **dormant** and become binding only when there's something to gate: the
`[CODEREVIEW]` gate is dormant until a `reviewer` department is chartered (🏛️), and the
whole demo/review layer is dormant while the output predicate says nothing has shipped
(safe.md). This is the emoji gate's answer to "green tests aren't authorization" — the
🚀 stays a pure human authorization, with correctness (review) and user-value (demo)
proven *before* it, by parties who don't approve their own work.

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
Demo: <accepted [DEMO] post id — which itself cites a passing [CODEREVIEW]>
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
| Agent tries to act before emoji | Nothing in the runtime intercepts it — the action fails because the agent holds no payment credential or prod access. If it *could* have succeeded, fix the capability leak, not the prompt |
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
