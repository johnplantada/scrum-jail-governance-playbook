# Architecture — Scrum Jail Governance Playbook

This document explains **how the governance playbook is put together** and the mechanism it
encodes: how a fleet of autonomous LLM agents can do real work while a human stays firmly in
control. It is the architectural companion to the conceptual docs
([`emoji-gate.md`](../emoji-gate.md), [`patterns.md`](../patterns.md),
[`blocker-ledger.md`](../blocker-ledger.md), [`safe.md`](../safe.md)) and the setup walkthrough
([`RUNBOOK.md`](../RUNBOOK.md)).

> **One-line summary:** *Agents propose. The human Chairman approves with a rare emoji. A
> deterministic Registrar — code, never an LLM — executes.* This repo packages that model as
> copyable primitives plus a generator (`bin/orggen`) that stamps a fresh governance-gated org.

---

## The Scrum Jail ecosystem

This repo is one of three that together form a small, self-contained experiment in autonomous
software organizations. Each repo stands alone, but they only make full sense as a triangle:

```mermaid
flowchart LR
    GP["📓 scrum-jail-governance-playbook<br/><b>Methodology</b><br/>emoji-gate model · 11 patterns · orggen generator"]
    BIZ["🏛️ scrum-jail-business<br/><b>The autonomous org — runtime</b><br/>Go bus / registrar / pm · Mattermost · Claude agents"]
    PROD["🌐 scrum-jail<br/><b>The product — scrumjail.org</b><br/>React SPA · Go Lambda · AWS · Terraform"]

    GP -->|"orggen stamps an org<br/>make sync-playbook vendors the docs"| BIZ
    BIZ -->|"IT agent ships PRs<br/>🚀 authorizes each deploy"| PROD
    PROD -->|"site CTAs / PLAYBOOK_URL link back"| GP
    BIZ -.->|"governance model extracted from the live org"| GP

    style GP fill:#ffe08a,stroke:#b8860b,stroke-width:3px
```

| Repo | Role | What lives here |
|---|---|---|
| **scrum-jail-governance-playbook** *(you are here)* | **Methodology** | The governance model, the 11 misbehavior patterns + fixes, and `orggen` — packaged so anyone can copy it. |
| [scrum-jail-business](https://github.com/johnplantada/scrum-jail-business) | **Runtime** | The live multi-agent org that runs scrumjail.org. It *vendors* this repo's docs and is the org these primitives were extracted from. |
| [scrum-jail](https://github.com/johnplantada/scrum-jail) | **Product** | The actual website the org builds and ships. |

**This repo is the "golden source."** The live org (`scrum-jail-business`) pulls these docs in
as a pinned, read-only snapshot (`make sync-playbook`, drift-checked in CI). So the patterns and
gates here are not theory — they are the literal primitives a running org dogfoods every day.

---

## What this repo is — and is not

This is a **GitHub template repo**: documentation + YAML config + one Python generator. It
contains **no runtime**. The Go services that actually watch Mattermost and enforce the gate
(the *Registrar*, the *bus*, the *pm* CLI) live in the separate `scrum-jail-business` repo. When
you `orggen init` a new org, the generator prints the next step: copy that Go runtime in.

```
scrum-jail-governance-playbook/
├── README.md            entry point + file inventory
├── RUNBOOK.md           afternoon setup, incl. the 3 gate-verification tests
├── emoji-gate.md        the core safety primitive (5-step loop)
├── patterns.md          11 misbehavior patterns + counter-patterns
├── blocker-ledger.md    the anti-"blocked loop" primitives
├── safe.md              scaled-agile without the theater
├── envelopes.yaml       authority-envelope field reference + presets
├── org-chart.yaml       a concrete example org (the Registrar's source of truth)
├── bin/orggen           the generator — stamps a new org from _init/
└── _init/               the template orggen fills in
    ├── DESIGN.md         the constitution (PRODUCT/GOAL placeholders)
    ├── org-chart.yaml    chart template ({{CHAIRMAN_ID}}, {{DEPARTMENTS}})
    ├── blockers.yaml     empty human-task ledger
    ├── .env.example      secrets/config template
    └── agents/           _policy.md (shared) + department.tmpl.md
```

---

## The core idea — propose → approve → execute

Every privileged action (spend money, deploy to prod, charter or dissolve a department, raise a
model tier) flows through the same five steps. The human is the only one who can authorize, and
authorization is a **rare emoji reaction** — something a casual 👍 can never trigger.

```mermaid
sequenceDiagram
    participant A as Agent
    participant M as Mattermost
    participant C as Chairman
    participant R as Registrar (code)
    A->>M: 1. Post typed proposal [SPEND]/[CHARTER], then stop
    M-->>C: notification
    Note over C: 2. Read the proposal
    C->>M: 3. React with governance emoji 💰/🏛️/🚀
    M-->>R: reaction_added (WebSocket)
    Note over R: 4. Verify reactor == chairman.user_id<br/>AND emoji matches the message [TYPE]
    alt authorized
        R->>R: execute (spend / deploy / mutate org-chart)
        R->>M: announce [DECISION] to #decisions
        A->>M: 5. Post [STATUS] confirmation
    else wrong reactor or wrong emoji
        R-->>M: ignored / threaded misfire notice
    end
```

Why this works: the only authority is `chairman.user_id`, checked in code. An agent that reacts
with the same emoji does **nothing**. The Registrar holds no spend or deploy credentials itself —
for money and prod it merely *records* the Board's approval; the actual spend/deploy happens
downstream within the approved ceiling. See [`emoji-gate.md`](../emoji-gate.md) for the message
templates and the common-mistakes table.

---

## The governance model — org chart, envelopes, emoji

Authority is declared, not improvised. `org-chart.yaml` is the Registrar's source of truth for
*who exists*; each node carries an **envelope** bounding what it may do without asking.

```mermaid
flowchart TD
    Board["🏛️ Board / Chairman<br/>holds the keys — emoji reaction is the signing key"]
    CEO["CEO<br/>chief-executive · sonnet · max_subagents 0"]
    BIZ["Business<br/>demand · sonnet · envelope 4 / 500k tokens"]
    IT["IT<br/>supply · sonnet · envelope 4 / 500k tokens"]
    Board --> CEO
    CEO --> BIZ
    CEO --> IT
    Rule["Every envelope: can_spend = false · can_deploy = false<br/>global_max_agents = 16 (hard org-wide ceiling)"]
```

An envelope has four fields (full reference + presets in [`envelopes.yaml`](../envelopes.yaml)):

| Field | Meaning | Hitting the limit is the *protocol*, not an error |
|---|---|---|
| `max_subagents` | how many sub-teams this node may spawn | exceed it → post a `[CHARTER]` to the Board, wait for 🏛️ |
| `daily_token_budget` | declared per-day token target — *not* code-enforced (see [`envelopes.yaml`](../envelopes.yaml)) | audited against the spend ledger; cost is actually bounded by wake backpressure + tier-pinning |
| `can_spend` | almost always `false` | attempt → must post `[SPEND]`, wait for 💰 |
| `can_deploy` | almost always `false` | attempt → must post `[DEPLOY]`, wait for 🚀 |

The six governance emoji and the `[TYPE]` each may act on:

| Emoji | Name | Action | Acts on |
|---|---|---|---|
| 🏛️ | `classical_building` | charter a new department | `[CHARTER]` |
| ⚰️ | `coffin` | sunset (dissolve) a department | `[SUNSET]` |
| 💎 | `gem` | promote a node's model tier | `[PROMOTE]` |
| 💰 | `moneybag` | fund (record spend approval) | `[SPEND]` |
| 🚀 | `rocket` | ship (record deploy approval) | `[DEPLOY]` |
| 🛑 | `octagonal_sign` | emergency stop — halts the **whole org** (not a per-proposal veto) | *any* message |

---

## The two failure roots — and the 11 patterns

The patterns documented here are battle scars. They cluster into two roots: agents given **too
much authority**, and an orchestration loop with **no concept of "blocked," "done," or "do
nothing."**

```mermaid
mindmap
  root((11 patterns, two roots))
    Too much authority, patterns 1 to 8
      Rogue spender
      Broken deploy
      Runaway recursion
      Blocked-action retry loop
      Channel flood
      Domain expansion
      Double execution
      Emoji-gate evasion
    No concept of blocked, done, or nothing, patterns 9 to 11
      Idle restatement
      Process theater
      Self-wake storm
```

The first root is cured by the emoji gate and tight envelopes (above). The second root is cured by
three smaller primitives, described next. Full write-ups in [`patterns.md`](../patterns.md).

---

## Stopping the "blocked loop"

A naive agent loop, when it hits something only a human can do, re-states the blocker forever and
burns tokens. Three primitives (see [`blocker-ledger.md`](../blocker-ledger.md)) fix this.

**1 — The capability boundary.** A hard list of things *no* agent can ever do: hold credentials,
move money, register a domain, or perform the governance reactions. These are the human's alone.

**2 — The blocker ledger.** `blockers.yaml` is a durable operator queue. When an agent hits a
human-only action it appends **one** entry and goes quiet — it never re-posts. Only the Chairman
clears an entry, and that clearing is itself new inbound that wakes the org.

```mermaid
stateDiagram-v2
    [*] --> none
    none --> open: agent hits a human-only action<br/>appends ONE entry, goes quiet
    open --> open: re-wake sees the open entry, stays quiet (no re-post)
    open --> cleared: Chairman flips the entry (the only writer)
    cleared --> [*]: clearing is new inbound, wakes the org, blocked work resumes
```

**3 — Wake backpressure.** Wakes are classified by *reason*, and a wake with nothing to do costs
zero tokens because the model never starts.

```mermaid
flowchart TD
    W["Agent wake"] --> Q{"Wake reason?"}
    Q -->|"direct: owner TASK / Chairman / VOICE"| Full["Run a full cycle"]
    Q -->|"broadcast: shared-channel post"| Pre{"Cheap classifier:<br/>anything to add?"}
    Pre -->|yes| Full
    Pre -->|no| Noop["No-op (never the priciest model)"]
    Q -->|"scheduled: cron floor"| Peek{"Peek channels:<br/>anything new?"}
    Peek -->|no| Noop2["No-op — model never starts"]
    Peek -->|yes| Full
```

Two reliability corollaries: suppressed wakes **coalesce** into one delayed retry rather than
dropping, and a read cursor only advances after a **successful** cycle (at-least-once delivery).

---

## Scaled-agile without the theater

Ceremony (demos, reviews, planning) is gated on **shipped output, not elapsed time**. A single
predicate — `last-ship.sh`, "is there a green prod deploy on `main`?" — decides whether the whole
process layer is awake. Full rules in [`safe.md`](../safe.md).

```mermaid
flowchart TD
    Cer["Any ceremony begins"] --> Pred{"last-ship.sh:<br/>green prod deploy on main?"}
    Pred -->|"shipped = no"| Dormant["Process layer dormant:<br/>no demo · no acceptance · no PI planning<br/>accepted PRs merge on green CI, queue in Demo column"]
    Pred -->|"shipped = yes"| Active["[CODEREVIEW] (Reviewer, author≠reviewer) → [DEMO] → Business accepts → CEO relays DEPLOY → Chairman 🚀<br/>PI planning only if due AND shipped-in-window"]
```

This is why the capabilities can ship **dormant** behind a 🏛️ charter: until the org actually
ships something, no one runs a planning meeting about it.

Two gates sit in front of any product-surface deploy (see [`safe.md`](../safe.md) /
[`emoji-gate.md`](../emoji-gate.md)): a **`[CODEREVIEW]`** from an independent **Reviewer**
department — structurally separate from whoever wrote the code (**author ≠ reviewer**),
head-SHA-bound, and dormant until a `reviewer` is chartered — proves the code is *correct*, and
the **`[DEMO]`** proves it *reaches a user*. Only an accepted demo (citing its passing review)
earns the 🚀.

---

## `orggen` — the generator

`bin/orggen` is a dependency-free Python 3 script that stamps a fresh, governance-gated org repo
from `_init/`. Its load-bearing trick: a single list of department tuples drives **both** the
`org-chart.yaml` and the per-department `agents/*.md` mandates, so the chart and the agent files
can never disagree about who exists.

```mermaid
flowchart LR
    Args["orggen init &lt;target&gt;<br/>--departments ceo,business,it"] --> Tup["departments(): list of<br/>(name, role, channel, reports_to, cadence) tuples"]
    Tup --> Chart["dept_block() → org-chart.yaml<br/>{{DEPARTMENTS}} block"]
    Tup --> Agents["fill(template) → agents/&lt;name&gt;.md<br/>one mandate per tuple"]
    Args --> Docs["copy VERBATIM_DOCS (7):<br/>patterns · safe · emoji-gate · envelopes · blocker-ledger · FIELD-NOTES · RUNBOOK"]
    Args --> Fill["fill DESIGN.md, .env.example; copy blockers.yaml"]
    Chart --> Inv(["Invariant: one tuple list →<br/>chart and agents/ never disagree"])
    Agents --> Inv
```

Usage:

```bash
bin/orggen init ../my-org --product "myproduct.com" --goal "$10k/month" \
  --chairman-id <YOUR_MATTERMOST_USER_ID> --departments ceo,business,it
```

`ceo` is special-cased to `(ceo, chief-executive, board, board, weekly)` with `max_subagents: 0`;
every other name maps to `(name, name, name, ceo, daily)` with an envelope of `4 / 500k`. The seven
governance docs are copied **verbatim** (they are meant to travel unchanged); `DESIGN.md`,
`.env.example`, and the org chart are filled from your flags.

---

## Where this sits in the generator family

`orggen` is the org-governance sibling of two code generators used to build the actual product:

```mermaid
flowchart LR
    subgraph Goldens["Golden template + generator family"]
        spagen["spagen<br/>react-single-page-app-golden"]
        lambdagen["lambdagen<br/>go-lambda-golden"]
        orggen["orggen<br/>scrum-jail-governance-playbook"]
    end
    spagen --> P["scrum-jail product<br/>(frontend)"]
    lambdagen --> P2["scrum-jail product<br/>(backend services)"]
    orggen --> O["a governance-gated org<br/>e.g. scrum-jail-business"]
```

Same philosophy — *the pattern lives in a golden repo, and a generator copies + parameterizes it*
— applied to organizational governance instead of code.

---

## Provenance

Everything here was extracted from the live org running [scrumjail.org](https://scrumjail.org).
The `org-chart.yaml`, `DESIGN.md`, and `agents/*.md` are the real primitives that org uses,
generalized with placeholders and packaged so you can copy, fill in, and run them. The arrows in
the [ecosystem diagram](#the-scrum-jail-ecosystem) are not aspirational — they describe a system
that runs today.
