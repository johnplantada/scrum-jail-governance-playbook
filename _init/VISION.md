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
A small governance-gated company; the Human Principal (you) holds the gates (`DESIGN.md` invariant 1).

**Production chain (first line) — reports to the Project Orchestrator**
- **Project Orchestrator** *(objective coordination)* — decomposes Human-created objectives into
  role-owned epics and resolves delivery dependencies.
  Sets outcomes; never executes, and never opens an objective.
- **Commercial Planner** *(commercial planning)* — translates the north star into product requirements and
  owns `[DEMO]` acceptance — the customer's voice.
- **Software Implementer** *(software delivery)* — builds the product and its pipeline. Opens PRs; never merges to prod.

**Independent organs — report to the Board**
<!-- CUSTOMIZE: keep, adapt, or delete these depending on your roster. -->
- **Policy Reviewer** *(independent review, optional)* — owns the corpus + citation standard and the
  independence boundary; reviews policy-facing output and posts a typed review verdict.
  Its sign-off is a required input to Commercial Planner's `[DEMO]` acceptance where applicable.
  It flags; it never authorizes.
- **Governance Monitor** *(deterministic service)* — computes complete tri-state findings and
  reconciliation plans from controller-owned state.
- **Governance Reviewer** *(governance judgment)* — handles only ambiguous residue emitted
  by the monitor.

## How work flows
1. **The Human Principal injects** an `[OBJECTIVE]` issue (labeled `role:project-orchestrator`) — the label is the wake.
   The Human Principal is the *only* one who may: work intake is a reserved power (DESIGN.md
   invariant 1). An agent that sees an uncovered pillar opens a `[PROPOSAL]` asking for one.
2. **The Project Orchestrator decomposes** into role-owned **epics** and delegates — never into sibling
   objectives.
3. **Software Implementer builds** and opens PRs; **Commercial Planner** defines acceptance.
4. **Policy Reviewer** reviews policy-facing output when chartered and posts a review verdict.
5. **Commercial Planner accepts** the `[DEMO]` — only with that review input where applicable.
6. **The Human Principal authorizes and executes** spending, activates org-shape changes, merges
   PRs, and dispatches deployments. There is no agent path into those capabilities.

## Growing the org (counter-ratchet — invariant 5)
A small roster is deliberate. Schema 1.0 permits only the canonical roster and may enable
the optional Policy Reviewer when a real bottleneck justifies it. Any additional role
requires a new reviewed schema and control release activated by the Human Principal; a
workspace decision or mandate cannot create authority.
