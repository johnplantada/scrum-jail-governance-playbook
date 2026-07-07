# The Constitution — {{PRODUCT}} org, GitHub-native

One page. A small multi-agent "company" runs **{{PRODUCT}}** toward **{{GOAL}}**; the human
owner participates as the **Chairman of the Board**, and GitHub is the substrate — the record
lives where the output lives, enforced by the platform, not parsed out of chat. Parameters
live in `org-chart.yaml`, routing in `wake-rules.yaml`, procedure in `.claude/skills/`,
per-role duty in `agents/*.md`. This file states only what must stay true.
*(`wake-rules.yaml`, the skills, and every script named below ship with the reference
runtime, not with this stamp — see the README. The reference org also lints prose against
these sources in CI (`scripts/lint_constitution.py`) so the constitution can't silently
restate what the config owns; adopt that with the runtime.)*

## 1 · The five invariants

1. **Only the Chairman authorizes** money, prod deploys, and org-shape changes.
   Enforcement is platform-native: deploys pause at the product repo's `production`
   environment (required reviewer = the Chairman's GitHub account); money and org-shape are
   PRs appending to `decisions.yaml`, CODEOWNERS-routed — **the Chairman's merge is the
   authorization**, and `git log decisions.yaml` is the decision history. There is no other
   path into the gates. *(Honest scope: this holds only after the two one-time Settings
   steps — the `production` environment and branch protection requiring Code Owner review —
   which only the Chairman can perform; until then it is declared, not enforced.)*
2. **Agents never perform human-only actions.** Credentials, accounts, real URLs, publishing
   from personal accounts, repo Settings. Hitting one → record it once in `blockers.yaml`
   (EV-annotated: `value`, `effort_minutes`) and go quiet. Only the Chairman clears an entry;
   past `global.unlock_wip_limit` open entries, agents must not start new work whose critical
   path ends in another human-only unlock. *(The WIP rule is prompt-enforced — the injected
   queue carries the warning; the going-quiet is policy, backstopped by capability-absence:
   agents simply hold no credentials.)*
3. **Every Claude call is metered** into `state/spend.jsonl` (`spend_log.py`); the runner
   holds all wakes once today's metered cost reaches `SPEND_BREAKER_DAILY_USD`;
   `efficiency.py` reports cost per unit of shipped output.
4. **Ceremony is output-gated.** Process never runs ahead of delivery: while
   `scripts/last-ship.sh` reports `shipped=no`, reviews close with one line, PI Planning is
   suppressed, and no new feature work opens over a deploy-blocked critical path.
5. **The 🛑 kill switch stops every loop.** A `.halt` file in the repo root halts the runner
   and every agent cycle; only a human removes it.

## 2 · The work system

Work is **GitHub Issues** on the org Project (`scripts/pm-gh.sh`; Stage field =
`org-chart.yaml global.pm_stages`, the only place the stage list is defined). The Chairman
injects work by filing an issue (forms in `.github/ISSUE_TEMPLATE`); the `dept:*` label
routes it. Product code ships as PRs against `$PRODUCT_GH_REPO` from branch
`agent/it/<desc>`; **agents open PRs, never merge to main** — CI plus invariant 1 gate the
merge. Org-repo changes go through an isolated worktree + PR (the `org-worktree` skill),
never the runtime checkout.

## 3 · The nervous system

`scripts/runner.py` polls GitHub each tick and wakes departments per `wake-rules.yaml` —
GitHub is the durable queue; a closed laptop is a clean pause, and catch-up is oldest-first.
Wakes execute through `agent-run.sh` (locking, metering, the Claude Code SDK on the
Chairman's plan). There are no channels, no standups, and no scheduled wake floors: no
event, no wake, no spend. First boots run `RUNNER_MODE=shadow` (poll + log the would-be
wakes) before going live.

## 4 · Typed handoffs

Cross-role handoffs carry a fenced yaml payload in the relevant issue/PR comment — typed,
not prose-by-convention. Required keys per type live in `agents/_policy.md` §handoffs, the
authoritative schema. *(Nothing machine-validates it yet; an Actions validator is the
planned enforcement — per §5, it must name this gap as what it replaces.)*

## 5 · Counter-ratchet

Every new gate, rule, or watcher must name the one it replaces, or carry a sunset date tied
to the incident that spawned it. The org runs on one watcher (the runner); growing past a
handful should be hard.
