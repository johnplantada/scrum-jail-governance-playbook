# Agent Governance Afternoon — The Scrum Jail Governance Playbook

Your agents went rogue. Or you're about to give them real authority and want to make
sure they don't. This is the governance system that keeps humans in the loop.

**Setup time**: 2-4 hours  
**Cost**: $0 to run (your own hardware + Anthropic API key)  
**What you get**: a running multi-agent org with emoji-gated spend, deploys, and charters

---

## What's in This Template

| File | What it does |
|---|---|
| `org-chart.yaml` | Define your agents, their roles, and their authority envelopes |
| `envelopes.yaml` | Reference for every envelope field — what it means, how to tune it |
| `emoji-gate.md` | The 5-step approval loop — how every privileged action flows through you |
| `patterns.md` | **11** agent misbehavior patterns + the specific governance fix for each |
| `blocker-ledger.md` | The blocker ledger + capability boundary + wake backpressure — stops the "blocked loop" |
| `safe.md` | Scaled-agile for an agent org without the theater — ceremony gated on shipped output |
| `RUNBOOK.md` | Step-by-step: set up your org in an afternoon |
| `bin/orggen` | Generator that stamps a new governance-gated org skeleton from `_init/` |

The first 8 patterns are about agents with **too much authority**. Patterns 9–11 (idle
restatement, process theater, the self-wake storm) and the `blocker-ledger.md` / `safe.md`
primitives are about the other half: an orchestration loop with no idea of "blocked," "done,"
or "do nothing." Both halves are battle-tested on the live org — see the writeups.

---

## The Core Idea

Agents propose. You approve. The Registrar (deterministic code, not an LLM) executes.

```
Agent posts SPEND/DEPLOY/CHARTER proposal
  → You react with the governance emoji (💰/🚀/🏛️)
    → Registrar executes the approved action
      → Agent posts STATUS confirmation
```

No agent can act unilaterally on anything privileged. The emoji gate is the
safety primitive that makes this possible.

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

Full setup instructions: [RUNBOOK.md](RUNBOOK.md)

---

## Built by Scrum Jail

This governance system is extracted from the live autonomous org running
[scrumjail.org](https://scrumjail.org). The `org-chart.yaml`, `DESIGN.md`,
and `agents/*.md` files in this template are the actual primitives we use —
packaged so you can copy, fill in, and run.

Questions or feedback: [scrumjail.org](https://scrumjail.org)
