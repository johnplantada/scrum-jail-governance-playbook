# Agent Governance Afternoon — The Scrum Jail Governance Playbook

> **A Notice from the Office of the Chairman.** The Scrum Jail Alliance has served
> the agile community since 2023, committed as ever to improving adherence to team
> agreements and processes. Visitors seeking the full institutional record — our
> founding, our mission, and certain disclosures the Board has voted to make — are
> directed to [The Scrum Jail Alliance Manifesto](docs/ALLIANCE-MANIFESTO.md).

Your agents went rogue. Or you're about to give them real authority and want to make
sure they don't. This is the governance system that keeps humans in the loop.

**Setup time**: 2-4 hours for the governance layer, plus building a thin runtime  
**Cost**: $0 of infrastructure beyond GitHub itself (a private repo works fine; Claude
usage is the running cost)  
**What you get**: the complete governance layer for a multi-agent org — org templates, a
generator, and the GitHub-native authority model (a `production` environment gate for
deploys, a reviewed `decisions.yaml` ledger for money/org-shape) — plus precise contracts
for the thin runtime (a GitHub poller + wake runner) you build or bring yourself. See
[RUNBOOK.md](RUNBOOK.md) "What This Repo Ships vs. What You Build" before planning
your afternoon.

---

## What's in This Template

| File | What it does |
|---|---|
| `org-chart.yaml` | Define your agents, their roles, and their authority envelopes |
| `envelopes.yaml` | Reference for every envelope field — what it means, how to tune it |
| `emoji-gate.md` | The authorization gate walkthrough — decisions.yaml/CODEOWNERS for money/org-shape, the `production` environment for deploys, why each step exists, and the pre-deploy code-review/demo chain |
| `patterns.md` | **11** agent misbehavior patterns + the specific governance fix for each |
| `blocker-ledger.md` | The blocker ledger + capability boundary + wake backpressure — stops the "blocked loop" |
| `safe.md` | Scaled-agile for an agent org without the theater — ceremony gated on shipped output; the `[CODEREVIEW]` + `[DEMO]` gates before a 🚀 |
| `FIELD-NOTES.md` | **Field-tested mechanisms** from the live org — the event loop, spend guards, the `.halt` switch, single-flight locks, worker tool-scoping, model-tier pinning, typed handoffs — plus the graveyard of what the 2026-07-05 demolition retired, and what replaced each piece |
| `RUNBOOK.md` | Step-by-step: set up your org in an afternoon |
| `bin/orggen` | Generator that stamps a new governance-gated org skeleton from `_init/` |
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | How it all fits together, with Mermaid diagrams — the gate, the patterns, `orggen`, and where this repo sits in the wider Scrum Jail ecosystem |

The first 8 patterns are about agents with **too much authority**. Patterns 9–11 (idle
restatement, process theater, the self-wake storm) and the `blocker-ledger.md` / `safe.md`
primitives are about the other half: an orchestration loop with no idea of "blocked," "done,"
or "do nothing." Both halves are battle-tested on the live org — see the writeups.

---

## The Core Idea

Agents propose. You approve. **The platform, not an LLM, verifies your approval and
enforces it** — GitHub's own review/merge and environment-approval primitives, not a
custom bot watching a chat stream.

```
Agent opens a decisions.yaml PR (spend/charter/promote/sunset)
  → CODEOWNERS routes it to you; branch protection requires your review
    → Your merge IS the authorization — git log decisions.yaml is the audit trail
      → Agent acts within the merged scope
Agent opens a product PR toward a deploy
  → CI green + an accepted [DEMO] → the deploy workflow pauses at the
    `production` environment
    → Your required-reviewer approval releases it — GitHub's own deployment
      log is the audit trail
```

Be precise about which layer stops what — the honest version is *stronger* than the
slogan "enforced in code":

- **Enforced by GitHub, not application code:** a `decisions.yaml` PR cannot merge
  without your CODEOWNERS review once branch protection requires it; a deploy cannot
  proceed past the `production` environment without your approval as its required
  reviewer. Neither gate depends on a bot correctly parsing a reaction — they're the
  same primitives GitHub uses to gate any human review or release.
- **Not automated at all:** nothing executes a `decisions.yaml` entry's payload for
  you. Money and org-shape changes take effect because the diff describing them landed
  on `main` — there's no code path from "PR merged" to "money moves" the way there is
  no code path from "Chairman reacted 💰" in the old model. The hard backstop is still
  **capability-absence** outside the platform gates: agents hold no payment credentials
  or prod access, so even a confused or compromised agent has nothing to spend or ship
  with directly.

This replaces an earlier, chat-based version of this same idea (agents post typed
messages, a human reacts with a governance emoji, a Registrar bot watching the chat
WebSocket verifies the reactor and executes or records it). Both shapes enforced the
identical principle — *agents propose, a human authorizes, the authorization is legible
and audited* — but the chat-based mechanism is retired here, not carried forward as a
maintained alternative: it was unenforceable in code and depended on prose-policing a
bot's behavior. [emoji-gate.md](emoji-gate.md) walks through the GitHub-native version in
full — the same file name for history's sake, now describing the gate above in detail,
not the retired chat mechanism. If you'd rather build a chat-based variant for your own
org, that's a fork/adaptation decision this template no longer needs to carry as a second
supported path.

---

## Quick Start

Two ways in:

**A. Generate a fresh org skeleton (recommended):**
```bash
bin/orggen init ../my-org --product "myproduct.com" --goal "$10k/month" \
  --chairman-github <YOUR_GITHUB_USERNAME> --departments ceo,business,it
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
packaged so you can copy, fill in, and run. The live org itself started on the
chat-based emoji gate and cut over to the GitHub-native model described above on
2026-07-05 — and this template has followed it across: `RUNBOOK.md` is the current
GitHub-native operating guide (setup, runtime contracts, and the gate verification
tests), and the rest of the docs — `emoji-gate.md`, `safe.md`, `patterns.md`,
`blocker-ledger.md`, `FIELD-NOTES.md`, `envelopes.yaml`, `docs/ARCHITECTURE.md`,
`org-chart.yaml`, and the `_init/` stamped templates — describe the same model. Where
a doc keeps a chat-era filename or a lineage note, that's deliberate history, not a
second supported path.

Questions or feedback: [scrumjail.org](https://scrumjail.org)
