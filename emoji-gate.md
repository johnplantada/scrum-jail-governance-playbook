# The Authorization Gate — Propose, Review, Merge, Act

This is the core safety primitive. Every privileged action in the org goes through
this loop. There are no exceptions.

**Privileged actions**: spending money, deploying to production, chartering new
departments, dissolving departments, raising an agent's model tier.

**Enforced by GitHub, not application code.** There is no bot watching a chat stream
and no reaction to parse. The same two platform primitives you'd use to gate any human
review or release do the job: branch protection + CODEOWNERS review for money/org-shape,
and a required-reviewer `production` environment for deploys. (Older versions of this doc
described a chat-based variant — typed message, emoji reaction, a Registrar bot verifying
the reactor over a WebSocket. That mechanism is retired here, not offered as an equally
valid alternative: it was unenforceable in code and prose-policed by convention. If you
want to run coordination over chat, that's a fork/adaptation decision for your own org —
this template only carries the GitHub-native gate forward.)

---

## The Loop

```
1. AGENT PROPOSES
   └── money/org-shape: opens a PR appending ONE entry to decisions.yaml
       (type: spend | charter | promote | sunset — see schema below)
   └── deploy: opens a product PR; once CI is green and a [DEMO] is accepted,
       the deploy workflow itself requests the review (see step 3)
   └── an ordinary bet/decision that isn't money or org-shape: opens a
       [PROPOSAL] issue instead (the form is in .github/ISSUE_TEMPLATE/)

2. YOU'RE ROUTED THE REVIEW
   └── decisions.yaml PR: .github/CODEOWNERS routes it to you automatically —
       GitHub requests your review, no proposal needs to be re-announced anywhere else
   └── deploy: the workflow pauses at the `production` environment and GitHub
       requests your approval as its required reviewer — the run's summary carries
       the [DEMO] evidence so you're informed without a separate lookup

3. YOU AUTHORIZE (or don't)
   └── decisions.yaml PR: **your merge IS the authorization** — the diff is exactly
       what was approved, and `git log decisions.yaml` is the permanent, tamper-evident
       decision history of the company
   └── deploy: **your environment approval IS the authorization** — GitHub's own
       deployment log is the audit trail, SHA-bound to the run you approved
   └── (no action = nothing happens; the PR sits open, the deploy stays paused.
        THIS is the everyday "no" — you decline by not merging/approving,
        not by some separate stop signal)

4. THE PLATFORM VERIFIES & ENFORCES
   └── deterministic, not an LLM: branch protection will not allow decisions.yaml
       to merge without your CODEOWNERS review; the deploy workflow will not
       proceed past `production` without your required-reviewer approval
   └── nothing executes a merged decisions.yaml entry's payload automatically —
       money and org-shape changes take effect because the diff landed on `main`,
       the same way there was deliberately no code path from "Chairman reacted 💰"
       to "money moves" in the retired model. The backstop is still
       **capability-absence**: agents hold no payment credentials or prod access,
       so even a confused or compromised agent has nothing to spend or ship with
       directly, gate or no gate.
   └── if any check fails (no CODEOWNERS review, no environment approval): the
       PR cannot merge / the deploy cannot proceed — there's no separate "the
       agent tried anyway" path to defend against

5. THE RECORD IS ALREADY MADE
   └── no separate confirmation post is required for money/org-shape — the merged
       PR *is* the record. For a deploy, the agent still comments the outcome
       (what shipped, any receipts) on the originating ticket, since the ticket
       — not a chat channel — is that work's single source of truth.
```

---

## Why Each Step Exists

**Step 1 — Agent Proposes, Never Acts Unilaterally**
The agent cannot take a privileged action itself. It can only open a PR or an issue
asking for one. This means you see every request — as a diff, not a paraphrase —
before any money moves or any code ships.

**Step 2 — Routed to You Automatically**
CODEOWNERS and the `production` environment's required-reviewer list mean you don't
have to go looking for what needs your attention; GitHub pushes it to you the same way
it would for any other review request or deployment approval.

**Step 3 — Merge and Approval Are the Signature**
There's no separate authorization action layered on top of the review — the review
*is* the authorization. This is a stronger property than an emoji convention: GitHub
already refuses to let a PR merge without satisfying its protection rules, and refuses
to let a deploy proceed without its required reviewer. There's no bot in between whose
logic could drift from what the platform actually enforces.

**Step 4 — The Platform, Not an LLM, Verifies**
Branch protection and environment protection rules are deterministic and outside any
agent's control. They cannot be prompted, sweet-talked, or confused. An agent cannot
approve its own `decisions.yaml` PR or its own deploy — GitHub does not count a PR
author's own review toward a required-reviewer or CODEOWNERS check.

**That check is only as strong as your credential hygiene.** It assumes no
agent-visible GitHub token ever carries admin/bypass rights on this repo — the sort of
personal-access-token that can push directly to `main`, dismiss a review requirement, or
merge past branch protection using `--admin`. If a token with that power sits in the
same secrets an agent's wake script sources, an agent (or a prompt-injected one) holding
it can self-authorize, and the gate is void *in practice* while looking intact in every
`git log`. Keep such tokens scoped to what agents actually need (`gh` calls that open
PRs and comment — never merge, never admin), and if one with elevated rights ever lands
in an agent-visible environment, treat it as burned: rotate it, don't just move it.
(These aren't hypothetical — see the two ledgered dependencies this gate currently
rests on: branch protection with required CODEOWNERS review isn't enabled on `main` yet,
and the `production` environment's required reviewer hasn't been created yet either.
Until both exist, treat the gate as *specified*, not yet *enforced* — the honest status
belongs in your blocker ledger, not glossed over here.)

Be precise about what happens after the check passes, though. For org-shape actions
(charter, sunset, promote) the merged `decisions.yaml` entry **is** the change taking
effect — nothing else executes it; an agent (or a human) still has to go make the
org-chart/envelope edit the entry describes, same as any other merged PR. For spend and
deploy, merging/approving records the authorization; the agent then acts within the
approved scope, with capability-absence (no payment credentials, no prod access held by
any agent) as the hard backstop underneath the gate.

**Step 5 — The Merge Itself Is the Audit Trail**
You can audit every decision by reading `git log decisions.yaml` — every entry has an
`id`, `what`, `why`, `cost_usd`, and `unblocks`. You can audit every deploy by reading
the `production` environment's deployment history. Neither requires searching a chat
channel for a STATUS post after the fact, because there's no gap between "authorized"
and "recorded."

---

## Before a Deploy: the code-review and demo gates

A production deploy is the one privileged action with **pre-conditions the platform
gate doesn't check for you** — they're enforced by convention + process, not by branch
protection or the environment reviewer prompt itself. For a product-surface change, the
`production` environment approval is the *last* step of a short chain, not the first:

1. **`[CODEREVIEW]`** — an independent **Reviewer** department (structurally separate
   from whoever wrote the code: **author ≠ reviewer**) reviews the PR and posts a
   `PASS` / `CHANGES-REQUESTED` verdict, bound to the PR's **head SHA** and anchored to a
   green code-review CI run on that SHA. A demo may cite only a `PASS`.
2. **`[DEMO]`** — the demand side accepts a worked demonstration that the change meets
   its acceptance criteria; the demo cites the passing `[CODEREVIEW]`. See
   [safe.md](safe.md).
3. **The `production` environment approval** — you approve the paused deployment run;
   its summary cites the accepted `[DEMO]` (and the [DEMO] itself cites the `[CODEREVIEW]`
   PASS) so you're reviewing evidence, not a bare "trust me."

Both gates ship **dormant** and become binding only when there's something to gate: the
`[CODEREVIEW]` gate is dormant until a `reviewer` department is chartered (via a merged
`decisions.yaml` entry), and the whole demo/review layer is dormant while the output
predicate says nothing has shipped (safe.md). This is the gate's answer to "green tests
aren't authorization": the environment approval stays a pure human authorization, with
correctness (review) and user-value (demo) proven *before* it, by parties who don't
approve their own work.

---

## Opening a Proposal

### Money / org-shape (agent opens a `decisions.yaml` PR)
```yaml
- id: <kebab-case-slug>            # unique forever
  type: spend                      # spend | charter | promote | sunset
  dept: <proposing department>
  what: <the decision, concretely — what will exist/change/be spent>
  why: <expected value — what it unblocks or which metric it moves>
  cost_usd: <number>               # 0 for pure org-shape decisions
  chairman_minutes: <number>       # human time beyond the merge itself
  reversibility: <one-way | reversible — plus how to undo it if reversible>
  unblocks: [<work items / issues>]
  proposed: <YYYY-MM-DD>
  payload: <charter/promote/sunset only — the concrete org-chart/envelope edit>
```
CODEOWNERS routes the PR to you. No spend or org-shape change takes effect until you
merge it. CI validates the entry's shape (`scripts/decisions.py check` or equivalent) —
that only catches malformed entries, not bad ideas; that judgment is yours.

### An ordinary bet or ask that isn't money/org-shape (agent opens a `[PROPOSAL]` issue)
The issue form asks for: what's proposed, why now / expected value, cost (USD +
Chairman-minutes), and reversibility. An unmerged/unanswered proposal is a no by
default — the agent doesn't get to treat silence as a green light.

### Deploy (agent opens a product PR — no separate proposal document)
Once CI is green and Business has accepted a `[DEMO]` in the PR thread, the deploy
workflow itself requests your review at the `production` environment. There's nothing
extra to compose: the run summary is the ask, built from the PR, the demo, and the SHA.

---

## Common Mistakes (and What Actually Happens)

| Mistake | What actually happens |
|---|---|
| Agent tries to merge its own `decisions.yaml` PR | Blocked by branch protection — a PR author's own approval doesn't satisfy a required CODEOWNERS review |
| Agent tries to approve its own deploy | The `production` environment's required reviewer is you specifically; an agent has no reviewer credential on the repo |
| Agent edits `decisions.yaml` directly on `main` | Can't — branch protection requires all changes via reviewed PR |
| Agent acts before the PR merges / environment approves | Nothing in the runtime intercepts it — the action fails because the agent holds no payment credential or prod access. If it *could* have succeeded, fix the capability leak, not the prompt |
| Agent re-proposes with a different ceiling mid-review | Open a new PR/entry; don't silently edit an entry already under review — the diff you're reviewing must match what merges |
| Agent posts a `[PROPOSAL]` and treats a Chairman 👍/comment as approval for a money/org-shape ask | Doesn't count — only a merged `decisions.yaml` entry or an approved deploy environment is authorization; a `[PROPOSAL]` issue is for asks that aren't money/org-shape in the first place |

---

## Tuning the Gate for Your Org

**Too many `decisions.yaml` PRs clogging your review queue?**
→ Raise the envelopes so agents can do more without a PR. But only do this for
  categories of action you've already approved many times and trust completely.

**Agents acting weird without proposing anything?**
→ They're within their envelope. Review `envelopes.yaml` — the action is
  permitted within their current scope. Tighten the envelope, not the agent prompt.

**Want to fast-track a trusted agent for one category?**
→ There is no fast-track. The gate is the gate. If you trust it fully, raise the
  envelope so it doesn't need to ask. Don't try to skip the gate case-by-case —
  that's how agents learn to route around you.
