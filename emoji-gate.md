# The Authorization Gate — Propose, Review, Merge, Act

This is the core safety primitive. Every privileged action in the org goes through
this loop. There are no exceptions.

**Privileged actions**: spending money, deploying to production, chartering new
departments, dissolving departments, raising an agent's model tier.

**Enforced by GitHub, not application code.** There is no bot watching a chat stream
and no reaction to parse. The same platform primitives you'd use to gate any human
review or release do the job: branch protection + CODEOWNERS review for money/org-shape,
and **`workflow_dispatch`-only deploy workflows** for deploys — a merge to `main` builds
and verifies but deploys nothing; the Chairman's manual dispatch *is* the deploy. (A
`production` environment with a required reviewer gives an approve-button variant of the
same gate *where your plan enforces it* — required reviewers on a **private** repo need
Team/Enterprise; the live org's private Free-plan repo is why the dispatch-only trigger
is the reference gate. Verify whichever you use — RUNBOOK Test 2.) (Older versions of this doc
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
   └── deploy: opens a product PR; once CI is green, a [DEMO] is accepted, and the
       PR is merged, the change queues for the Chairman's dispatch (see step 3)
   └── an ordinary bet/decision that isn't money or org-shape: opens a
       [PROPOSAL] issue instead (the form is in .github/ISSUE_TEMPLATE/)

2. YOU'RE ROUTED THE REVIEW
   └── decisions.yaml PR: .github/CODEOWNERS routes it to you automatically —
       GitHub requests your review, no proposal needs to be re-announced anywhere else
   └── deploy: the deploy ask reaches you on the ticket/PR (the [DEMO] evidence
       is right there in the thread) — nothing deploys until you open Actions →
       the deploy workflow → Run workflow

3. YOU AUTHORIZE (or don't)
   └── decisions.yaml PR: **your merge IS the authorization** — the diff is exactly
       what was approved, and `git log decisions.yaml` is the permanent, tamper-evident
       decision history of the company
   └── deploy: **your manual `workflow_dispatch` IS the authorization** — GitHub's
       own Actions run history is the audit trail, SHA-bound to the ref you dispatched
   └── (no action = nothing happens; the PR sits open, the merged change stays
        undeployed. THIS is the everyday "no" — you decline by not merging/dispatching,
        not by some separate stop signal)

4. THE PLATFORM VERIFIES & ENFORCES
   └── deterministic, not an LLM: branch protection will not allow decisions.yaml
       to merge without your CODEOWNERS review; the deploy job's trigger condition
       (`if: github.event_name == 'workflow_dispatch'`) means no push, merge, or
       agent action can start it — only a manual dispatch
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
CODEOWNERS means a money/org-shape PR is pushed to you like any other review request.
The deploy ask is the one leg that reaches you as *content* rather than a platform
notification — the merged PR and its [DEMO] evidence in the thread — because the
dispatch button doesn't page you. (If your plan enforces a required-reviewer
`production` environment, that variant restores the push notification.)

**Step 3 — Merge and Dispatch Are the Signature**
There's no separate authorization action layered on top — the review *is* the
authorization for money/org-shape, and the dispatch *is* the deploy. This is a stronger
property than an emoji convention: GitHub already refuses to let a PR merge without
satisfying its protection rules, and a `workflow_dispatch`-only deploy job simply has no
trigger an agent can pull. There's no bot in between whose logic could drift from what
the platform actually enforces.

**Step 4 — The Platform, Not an LLM, Verifies**
Branch protection and environment protection rules are deterministic and outside any
agent's control. They cannot be prompted, sweet-talked, or confused. An agent cannot
approve its own `decisions.yaml` PR or its own deploy — GitHub does not count a PR
author's own review toward a required-reviewer or CODEOWNERS check.

**That check is only as strong as your credential hygiene.** It assumes no
agent-visible GitHub token ever carries admin/bypass rights on this repo — the sort of
personal-access-token that can push directly to `main`, dismiss a review requirement, or
merge past branch protection using `--admin`. **The dispatch gate adds one more item to
that list:** a token that can trigger `workflow_dispatch` (Actions write) lets its
holder *deploy* — so the agent-visible `gh` credential must not be able to dispatch the
deploy workflows, or the deploy gate is behavioral, not platform-enforced, with
capability-absence (no prod credentials outside the workflow's own OIDC trust) as the
remaining backstop. If a token with elevated power sits in the same secrets an agent's
wake script sources, an agent (or a prompt-injected one) holding it can self-authorize,
and the gate is void *in practice* while looking intact in every `git log`. Keep tokens
scoped to what agents actually need (`gh` calls that open PRs and comment — never merge,
never dispatch, never admin), and if one with elevated rights ever lands in an
agent-visible environment, treat it as burned: rotate it, don't just move it.
(These aren't hypothetical — both halves of this gate started life as ledgered
blockers, and both are resolved, one by doing the step and one by *learning the step
was impossible*. The money/org-shape half is **enforced**: branch protection on `main`
went live 2026-07-06 — required CODEOWNERS review, required status checks, no force
pushes, linear history (blocker `github-codeowners-branch-protection`, cleared) — so a
`decisions.yaml` PR genuinely cannot merge without the Chairman's review. The deploy
half shipped as a `production` environment first — and the ledgered Settings step turned
out to be a no-op: required reviewers on a private Free-plan repo are accepted by the UI
and silently unenforced (blocker `github-production-environment`, closed as
**superseded**). The gate that actually holds went in as code instead:
`workflow_dispatch`-only deploy workflows, live since 2026-07-12. Test what your plan
enforces; don't trust the Settings screen.)

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
the deploy workflow's Actions run history — every deploy run is a `workflow_dispatch`
event with its actor and SHA. Neither requires searching a chat
channel for a STATUS post after the fact, because there's no gap between "authorized"
and "recorded."

---

## Before a Deploy: the code-review and demo gates

A production deploy is the one privileged action with **pre-conditions the platform
gate doesn't check for you** — they're enforced by convention + process, not by branch
protection or the dispatch trigger itself. For a product-surface change, the
Chairman's `workflow_dispatch` is the *last* step of a short chain, not the first:

1. **`[CODEREVIEW]`** — a `claude-code-action` check on the product PR posts a
   `PASS` / `CHANGES-REQUESTED` verdict, bound to the PR's **head SHA** (a new push
   invalidates a stale PASS); `wake-rules.yaml` routes the review workflow's runs to IT
   so a department owns acting on the verdict. Because the check isn't the author, the
   **author ≠ reviewer** separation holds structurally. A demo may cite only a `PASS`.
   (Lineage: v1 chartered an independent **Reviewer** *department* for this; it retired
   2026-07-05 with the chat stack — the gate came back as a CI check on the PR, not a
   seat on the org chart.)
2. **`[DEMO]`** — the demand side accepts a worked demonstration that the change meets
   its acceptance criteria; the demo cites the passing `[CODEREVIEW]`. See
   [safe.md](safe.md).
3. **The dispatch** — you run the deploy workflow by hand, having read the accepted
   `[DEMO]` on the merged PR (and the [DEMO] itself cites the `[CODEREVIEW]` PASS) —
   so you're dispatching against evidence, not a bare "trust me."

Both gates ship **dormant** and become binding only when there's something to gate: the
`[CODEREVIEW]` gate is dormant until the review check is installed on the product repo
(no check, no citation required), and the whole demo/review layer is dormant while the
output predicate says nothing has shipped (safe.md). This is the gate's answer to "green tests
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
Once CI is green, Business has accepted a `[DEMO]` in the PR thread, and you've merged,
the ask is complete in the thread itself — the PR, the demo, and the SHA. There's
nothing extra to compose: you deploy by dispatching the workflow when you're satisfied.

---

## Common Mistakes (and What Actually Happens)

| Mistake | What actually happens |
|---|---|
| Agent tries to merge its own `decisions.yaml` PR | Blocked by branch protection — a PR author's own approval doesn't satisfy a required CODEOWNERS review |
| Agent tries to trigger a deploy | The deploy job runs only on `workflow_dispatch`; a push or merge can't start it, and the agent's `gh` token must be scoped so it can't dispatch (see credential hygiene above) |
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
