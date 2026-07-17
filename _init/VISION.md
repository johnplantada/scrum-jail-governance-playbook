# VISION — {{PRODUCT}}

*The one-page "why + who." `DESIGN.md` (the constitution) says what must stay true;
`org-chart.yaml` says who exists and their envelopes; this file says where we're going and how
the org is shaped to get there.*

## Vision
<!-- CUSTOMIZE: 2-4 sentences. What does {{PRODUCT}} prove or change if this works?
Write the ambitious-but-checkable version, not marketing copy. -->

## Mission
<!-- CUSTOMIZE: one paragraph. What the org concretely builds/operates and the standard
its output must meet — the mission should be falsifiable against the north star below. -->

**North star (the acceptance bar):** {{GOAL}}. Every objective answers to it.

## The org — the lines
A small governance-gated company; the Chairman (you) holds the gates (`DESIGN.md` invariant 1).

**Production chain (first line) — reports to the CEO**
- **CEO** *(chief-executive)* — sets direction, decomposes the Chairman's objectives into
  department-owned epics, arbitrates between departments, gates ceremony on shipped output.
  Sets outcomes; never executes, and never opens an objective.
- **Business** *(demand)* — translates the north star into product requirements and
  owns `[DEMO]` acceptance — the customer's voice.
- **IT** *(supply)* — builds the product and its pipeline. Opens PRs; never merges to prod.

**Independent organs — report to the Board**
<!-- CUSTOMIZE: keep, adapt, or delete these depending on your roster. -->
- **Compliance** *(assurance, optional)* — owns the corpus + citation standard and the
  independence boundary; reviews assurance-facing output and posts `COMPLIANCE-OK` or withholds
  with `COMPLIANCE-HOLD`. Its sign-off is a required input to Business's `[DEMO]` acceptance.
  It flags; it never authorizes.
- **Warden** *(hygiene)* — keeps the Chairman action queue true and the board honest from code
  truth; the deterministic engine (`scripts/warden.py`) does the work, the brain handles
  judgment residue only.

## How work flows
1. **The Chairman injects** an `[OBJECTIVE]` issue (labeled `dept:ceo`) — the label is the wake.
   The Chairman is the *only* one who may: work intake is a reserved power (DESIGN.md
   invariant 1). An agent that sees an uncovered pillar opens a `[PROPOSAL]` asking for one.
2. **The CEO decomposes** into department-owned **epics** and delegates — never into sibling
   objectives.
3. **IT builds** and opens PRs; **Business** defines acceptance.
4. **The second line reviews** assurance-facing output (if chartered) → `COMPLIANCE-OK` / `COMPLIANCE-HOLD`.
5. **Business accepts** the `[DEMO]` — only with the second line's sign-off where it applies.
6. **The Chairman authorizes** money (`decisions.yaml` merge), org-shape (charter), and deploys
   (`workflow_dispatch`). There is no other path into the gates.

## Growing the org (counter-ratchet — invariant 5)
A small roster is deliberate; growth must be earned. Charter a new department via a
`decisions.yaml` PR (the `board-proposals` skill) only when a real bottleneck names it.
Every new role names the bottleneck it relieves, or it doesn't get chartered.
