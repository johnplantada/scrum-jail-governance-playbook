# Agent Governance Afternoon — The Scrum Jail Governance Playbook

> **A Notice from the Office of the Chairman.** The Scrum Jail Alliance has served
> the agile community since 2023, committed as ever to improving adherence to team
> agreements and processes. Visitors seeking the full institutional record — our
> founding, our mission, and certain disclosures the Board has voted to make — are
> directed to [The Scrum Jail Alliance Manifesto](docs/ALLIANCE-MANIFESTO.md).

Your agents went rogue. Or you're about to give them real authority and want to make
sure they don't. This is the governance system that keeps humans in the loop.

**Setup time**: an afternoon — the stamp now includes the runtime  
**Cost**: $0 of infrastructure beyond GitHub itself (a private repo works fine; Claude
usage is the running cost)  
**What you get**: a complete multi-agent org in one stamp — the governance layer (org
templates, the GitHub-native authority model: a human-dispatched deploy gate —
`workflow_dispatch`-only deploy workflows — and a reviewed `decisions.yaml` ledger for
money/org-shape) AND the runtime that runs it (the GitHub poller + wake runner, the
ticket CLI, the deterministic warden, the PreToolUse gates, spend metering, CI). See
[RUNBOOK.md](RUNBOOK.md) "What This Repo Ships vs. What You Build" before planning
your afternoon.

---

## What's in This Template

| File | What it does |
|---|---|
| `org-chart.yaml` | Define your agents, their roles, and their authority envelopes |
| `envelopes.yaml` | Reference for every envelope field — what it means, how to tune it |
| `emoji-gate.md` | The authorization gate walkthrough — decisions.yaml/CODEOWNERS for money/org-shape, the `workflow_dispatch`-only deploy gate, why each step exists, and the pre-deploy code-review/demo chain |
| `patterns.md` | **13** agent misbehavior patterns + the specific governance fix for each |
| `blocker-ledger.md` | The blocker ledger + capability boundary + wake backpressure — stops the "blocked loop" |
| `safe.md` | Scaled-agile for an agent org without the theater — ceremony gated on shipped output; the work-item tree with a closing rule per level; the `[CODEREVIEW]` + `[DEMO]` gates before a 🚀 |
| `FIELD-NOTES.md` | **Field-tested mechanisms** from the live org — the event loop, spend guards, the `.halt` switch, single-flight locks, worker tool-scoping, model-tier pinning, typed handoffs, the work-item tree's closure gate, the deterministic warden + engine-first wakes, wake yield + the wake filter — plus the graveyard of what the 2026-07-05 demolition retired, and what replaced each piece |
| `RUNBOOK.md` | Step-by-step: set up your org in an afternoon |
| `bin/orggen` | Generator that stamps a complete org — governance from `_init/`, runtime from `runtime/` — with every department surface (chart, mandates, wake rules, issue-form dropdowns, agent ceiling) generated from one roster |
| `runtime/` | The full runtime, stamped verbatim: `scripts/` (runner + wake router, `pm-gh.sh`, the work-item closure gate, `agent-run.sh`, the deterministic warden, the subagent/objective PreToolUse gates, spend metering, unit tests), `.claude/` (guardrail settings + the governance skills), org CI, the operator Makefile |
| `_init/agents/` | The shared `_policy.md`, the generic department template, and ready role mandates: `ceo`, `warden` (hygiene organ), `compliance` (assurance second line) |
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | How it all fits together, with Mermaid diagrams — the gate, the patterns, `orggen`, and where this repo sits in the wider Scrum Jail ecosystem |

The first 8 patterns are about agents with **too much authority**. Patterns 9–13 (idle
restatement, process theater, the self-wake storm, the tree that only grows, prose-patching
the checker) and the `blocker-ledger.md` / `safe.md` primitives are about the other half: an
orchestration loop with no idea of "blocked," "done," or "do nothing" — and gates that
collect compliance instead of bug reports when they misfire. Both halves are battle-tested
on the live org — see the writeups.

---

## The Core Idea

Agents propose. You approve. **The platform, not an LLM, verifies your approval and
enforces it** — GitHub's own review/merge and manual workflow-dispatch primitives, not a
custom bot watching a chat stream.

```
Agent opens a decisions.yaml PR (spend/charter/promote/sunset)
  → CODEOWNERS routes it to you; branch protection requires your review
    → Your merge IS the authorization — git log decisions.yaml is the audit trail
      → Agent acts within the merged scope
Agent opens a product PR toward a deploy
  → CI green + an accepted [DEMO] → you merge → the change queues, deployed by
    nothing automatically
    → Your manual workflow_dispatch IS the deploy — GitHub's own Actions run
      history is the audit trail
```

Be precise about which layer stops what — the honest version is *stronger* than the
slogan "enforced in code":

- **Enforced by GitHub, not application code:** a `decisions.yaml` PR cannot merge
  without your CODEOWNERS review once branch protection requires it; a deploy job whose
  only trigger is `workflow_dispatch` cannot start from any push, merge, or agent action
  — only from your manual dispatch. Neither gate depends on a bot correctly parsing a
  reaction — they're the same primitives GitHub uses to gate any human review or release.
  (A required-reviewer `production` environment is the approve-button variant of the
  deploy gate *where your plan enforces it* — on private repos that's Team/Enterprise;
  the live org's private Free-plan repo is why dispatch-only is the reference gate.)
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

**A. Generate a fresh org (recommended):**
```bash
bin/orggen init ../my-org --product "myproduct.com" --goal "$10k/month" \
  --chairman-github <YOUR_GITHUB_USERNAME> --departments ceo,business,it,warden
```
Stamps a complete org repo: governance (`org-chart.yaml`, `DESIGN.md`, `VISION.md`,
`agents/` with role mandates, `blockers.yaml`, `decisions.yaml`, `.env.example`,
`.github/` CODEOWNERS + issue forms) and the runtime (`scripts/`, `.claude/`, CI,
Makefile, `wake-rules.yaml`), plus the vendored `playbook/` docs pinned to this golden's
commit. Everything that names a department is generated from the one `--departments`
roster, so the seven surfaces can never disagree. `--org`, `--goal`, and `--departments`
are optional (defaults: target dir name, a placeholder goal, and `ceo,business,it,warden`;
`compliance` is a ready archetype too).

**B. Fork this repo** as your org repo and edit `org-chart.yaml` by hand.

Then, either way:
1. **Follow `RUNBOOK.md`** — including the gate verification tests
2. **Read `patterns.md`, `blocker-ledger.md`, and `safe.md`** — before your agents go live
3. **Read `FIELD-NOTES.md`** to understand the runtime you now run — it's the mechanisms
   the live org added after going live, each one paid for by a real failure

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
