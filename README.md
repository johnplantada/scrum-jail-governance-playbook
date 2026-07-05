# Agent Governance Afternoon — The Scrum Jail Governance Playbook

> **A Notice from the Office of the Chairman.** The Scrum Jail Alliance has served
> the agile community since 2023, committed as ever to improving adherence to team
> agreements and processes. Visitors seeking the full institutional record — our
> founding, our mission, and certain disclosures the Board has voted to make — are
> directed to [The Scrum Jail Alliance Manifesto](docs/ALLIANCE-MANIFESTO.md).

Your agents went rogue. Or you're about to give them real authority and want to make
sure they don't. This is the governance system that keeps humans in the loop.

**Setup time**: 2-4 hours for the governance layer, plus building a thin runtime  
**Cost**: $0 of infrastructure (your own hardware; Claude usage is the running cost)  
**What you get**: the complete governance layer for a multi-agent org — org templates, a
generator, and the emoji-gate protocol — plus precise contracts for the thin runtime
(bus + Registrar + wake runner) you build or bring yourself. See
[RUNBOOK.md](RUNBOOK.md) "What This Repo Ships vs. What You Build" before planning
your afternoon.

---

## What's in This Template

| File | What it does |
|---|---|
| `org-chart.yaml` | Define your agents, their roles, and their authority envelopes |
| `envelopes.yaml` | Reference for every envelope field — what it means, how to tune it |
| `emoji-gate.md` | The 5-step approval loop — how every privileged action flows through you |
| `patterns.md` | **11** agent misbehavior patterns + the specific governance fix for each |
| `blocker-ledger.md` | The blocker ledger + capability boundary + wake backpressure — stops the "blocked loop" |
| `safe.md` | Scaled-agile for an agent org without the theater — ceremony gated on shipped output; the `[CODEREVIEW]` + `[DEMO]` gates before a 🚀 |
| `FIELD-NOTES.md` | **11 field-tested mechanisms** from the live org — backpressure numbers, the haiku pre-gate, single-flight locks, worker tool-scoping, model-tier pinning, the Warden, typed handoffs, and more |
| `RUNBOOK.md` | Step-by-step: set up your org in an afternoon |
| `bin/orggen` | Generator that stamps a new governance-gated org skeleton from `_init/` |
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | How it all fits together, with Mermaid diagrams — the gate, the patterns, `orggen`, and where this repo sits in the wider Scrum Jail ecosystem |

The first 8 patterns are about agents with **too much authority**. Patterns 9–11 (idle
restatement, process theater, the self-wake storm) and the `blocker-ledger.md` / `safe.md`
primitives are about the other half: an orchestration loop with no idea of "blocked," "done,"
or "do nothing." Both halves are battle-tested on the live org — see the writeups.

---

## The Core Idea

Agents propose. You approve. The Registrar (deterministic code, not an LLM) verifies
your reaction and acts on it.

```
Agent posts SPEND/DEPLOY/CHARTER proposal
  → You react with the governance emoji (💰/🚀/🏛️)
    → Registrar verifies it's you, then executes the org change (🏛️/⚰️/💎/🛑)
      or records the approval to the #decisions audit ledger (💰/🚀)
      → Agent posts STATUS confirmation
```

Be precise about which layer stops what — the honest version is *stronger* than the
slogan "enforced in code":

- **Enforced in Registrar code:** charter / sunset / promote / halt handling (the
  Registrar is the only thing that mutates the org chart), the per-agent subagent
  ceilings and global agent cap, and worker-subagent tool-scoping (no worker gets a
  shell — asserted in CI).
- **Not executed by the Registrar:** spend and deploy. For 💰/🚀 the Registrar verifies
  the reactor and message type, then **records the approval to `#decisions`** — it
  moves no money and ships no code. The hard backstop lives *outside the agents' trust
  domain*: agents hold no payment credentials, and prod deploys are gated by branch
  protection + human review on the product repo. There is no credential for a confused
  agent to misuse.

The emoji gate is a legible human-in-the-loop approval interface plus audit trail,
layered on top of that capability-absence. No agent can act unilaterally on anything
privileged — not because a daemon intercepts it, but because the capability was never
handed out in the first place.

---

## Quick Start

Two ways in:

**A. Generate a fresh org skeleton (recommended):**
```bash
bin/orggen init ../my-org --product "myproduct.com" --goal "$10k/month" \
  --chairman-id <YOUR_MATTERMOST_USER_ID> --departments ceo,business,it
```
Stamps a new org repo from `_init/`: `org-chart.yaml` (its `departments:` block generated from
`--departments`, so the chart and `agents/` always match), `DESIGN.md` (product + goal filled),
`agents/` (one file per department + the shared `_policy.md`), `blockers.yaml`, `.env.example`,
and the playbook docs — then prints the next steps. `--org`, `--goal`, and `--departments` are
optional (defaults: target dir name, a placeholder goal, and `ceo,business,it`).

**B. Fork this repo** as your org repo and edit `org-chart.yaml` by hand.

Then, either way:
1. **Follow `RUNBOOK.md`** — including the gate verification tests
2. **Read `patterns.md`, `blocker-ledger.md`, and `safe.md`** — before your agents go live
3. **Read `FIELD-NOTES.md`** when you build your runtime — it's the mechanisms the live
   org added after going live, each one paid for by a real failure

Full setup instructions: [RUNBOOK.md](RUNBOOK.md)

---

## Built by Scrum Jail

This governance system is extracted from the live autonomous org running
[scrumjail.org](https://scrumjail.org). The `org-chart.yaml`, `DESIGN.md`,
and `agents/*.md` files in this template are the actual primitives we use —
packaged so you can copy, fill in, and run.

Questions or feedback: [scrumjail.org](https://scrumjail.org)
