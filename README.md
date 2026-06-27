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
| `patterns.md` | 8 agent misbehavior patterns + the specific governance fix for each |
| `RUNBOOK.md` | Step-by-step: set up your org in an afternoon |

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

1. **Fork this repo** as your org repo
2. **Edit `org-chart.yaml`** — fill in your Mattermost user ID and bot IDs
3. **Follow `RUNBOOK.md`** — step by step, including the gate verification tests
4. **Read `patterns.md`** — before your agents go live

Full setup instructions: [RUNBOOK.md](RUNBOOK.md)

---

## Built by Scrum Jail

This governance system is extracted from the live autonomous org running
[scrumjail.org](https://scrumjail.org). The `org-chart.yaml`, `DESIGN.md`,
and `agents/*.md` files in this template are the actual primitives we use —
packaged so you can copy, fill in, and run.

Questions or feedback: [scrumjail.org](https://scrumjail.org)
