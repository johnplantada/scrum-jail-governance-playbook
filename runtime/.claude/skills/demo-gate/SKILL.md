---
name: demo-gate
description: Produce or accept a [DEMO] — the pre-deploy gate. Use when IT has a product-surface PR ready and must gate it before the Chairman's deploy workflow_dispatch, or when Business must accept or reject a [DEMO] against its acceptance criteria (assurance-facing work additionally needs Compliance's COMPLIANCE-OK before acceptance, where Compliance is chartered). Demos are produced for deployable work only and queue while prod is dark; acceptance criteria scale with the work (a one-line done-when for internal/one-shot tasks).
---

# The [DEMO] gate

The one real gate the SAFe layer adds (`playbook/safe.md`): **no product-surface PR reaches a deploy
`workflow_dispatch` request without a Business-accepted `[DEMO]`.** The typed payload schema
is `scripts/handoff_check.py` (documented in `agents/_policy.md` §handoffs), enforced on
the runner's wake path — never a per-comment hosted workflow (DESIGN.md §4). Substance is on you; the
validator only checks shape.

## First: is a demo even required?

- **Product-surface work** (changes a user can see/use, will deploy) → **yes**, gate it.
- **Internal / process / one-shot work** (a config change, a script, a doc) → **no**. Its
  feature closes on a one-line *done-when* instead (`_policy.md` §handoffs, `[CLOSE]`).
  Over-documenting a one-off is the overhead to avoid.

## Then: is prod reachable?

Check the output predicate: `scripts/last-ship.sh` → `shipped=`.

- **`shipped=no` (prod is dark)** → do **not** post a `[DEMO]`. A demo proves a change
  reaches a user; you cannot demo a feature that can't. Keep the PR open and green, park the
  work in its board stage, and hold — the demo fires when the deploy path clears. During a
  deploy-hold, at most one staged asset per track (`_policy.md` §deploy-hold).
- **`shipped=yes`** → produce the `[DEMO]` below.

## Producing a [DEMO] (IT)

1. **Correctness first.** The PR needs a passing `[CODEREVIEW]` (verdict `PASS`, posted as a
   PR review — `_policy.md` §handoffs). A demo may cite only a PASS.
2. **Compliance sign-off for assurance-facing work, where Compliance is chartered.** If the
   change touches a claim surface, a citation change, or an eval claim, route the item to
   `dept:compliance` and get `COMPLIANCE-OK` **before** asking Business to accept — Business
   must not accept over a missing or held sign-off. Orgs with no chartered Compliance skip
   this step (VISION.md: Compliance is an optional independent organ).
3. **Machine evidence, bound to the head SHA.** Run `scripts/demo-verify.sh <prod-pr-number>`
   and require `verified=yes`. It checks for a green demo-evidence workflow run on the PR's
   *current* head commit; a stale run (evidence generated, then more commits pushed) does not
   verify — re-run the workflow. Cite the `run_url` it prints; never hand-assemble evidence.
4. **Post the payload** as a comment on the product PR — a fenced yaml block with the four
   required keys (`_policy.md` §handoffs):

   ````
   [DEMO] <feature> (org#N, <owner/repo>#M)
   ```yaml
   pr: <owner/repo>#M
   evidence_run: <run_url from demo-verify.sh>
   acceptance:
     - criterion: <AC 1, verbatim from the agreed bar>
       evidence: <url / artifact in the evidence run>
     - criterion: <AC 2>
       evidence: <...>
   ci: green
   ```
   ````

   Check `ci: green` live (`gh pr checks`), not from memory. Every criterion carries its
   evidence; if one fails, it's not ready — fix and re-demo.
5. **Wake the acceptor:** make sure the work item carries `dept:business` so the comment
   routes (labels are the wake wiring).

## Accepting a [DEMO] (Business)

Judge it **against the acceptance criteria you wrote**, in-reply on the PR:

- **Verify, don't trust:** open the `evidence_run` and confirm it's green on the PR's current
  head SHA (re-run `scripts/demo-verify.sh <pr>` if in doubt); spot-check each criterion's
  evidence.
- **Assurance-facing, and Compliance chartered?** Confirm Compliance's `COMPLIANCE-OK` is
  posted on the item. A `COMPLIANCE-HOLD` names what isn't grounded — send the work back;
  never accept over it.
- **Accept:** reply that each criterion is met, evidence verified — then move the work item
  to the deploy queue: **`scripts/pm-gh.sh move --id N --to "Awaiting Deploy"`**. That
  column IS the Chairman's dispatch queue (org-chart `pm_stages`); an accepted demo that
  never lands there is invisible to the person who deploys. An accepted `[DEMO]` is what
  lets the feature close (`pm-gh.sh done`) and lets the CEO put the PR forward for the
  Chairman's merge + manual `workflow_dispatch`.
- **Reject:** name the specific criterion that failed and what evidence is missing. Do not
  accept a partial demo.

## What this skill never does

It never merges, never deploys, never spends. The Chairman's `workflow_dispatch` is the only
path to prod (DESIGN.md invariant 1); an accepted `[DEMO]` is the *precondition* for asking,
not the authorization.
