# GitHub Actions: the zero-spend budget

This is the stamped default: the Chairman pays $0 for Actions. The account's included
quota is the entire budget, and when it runs out with no valid payment method, GitHub
**blocks all runs** until the monthly reset — an account-wide CI blackout, not a
per-repo one. The reference org lived through exactly that on 2026-07-17 (it logged a
`github-actions-billing-blocked` blocker until the monthly reset cleared it), and the
rules below are what came out of it. If your org does fund Actions, keep rule 2 anyway —
per-event triggers scale with agent chatter, which is unbounded. Adjust the rest to your
budget. Source of truth: GitHub's billing docs
(<https://docs.github.com/en/billing/concepts/product-billing/github-actions> and
<https://docs.github.com/en/billing/reference/actions-minute-multipliers>).

## The facts (from GitHub's docs)

- Included minutes per month: **2,000 (Free) / 3,000 (Pro)** — private repos only; both
  org and product repos are private, and every repo bills against the same account quota.
- Public repos on standard runners are free and don't touch the quota.
- **"GitHub rounds the minutes and partial minutes each job uses up to the nearest whole
  minute."** A 5-second job bills 1 minute. A workflow with 3 jobs bills ≥3 minutes per
  run no matter how fast it finishes.
- "At the start of each month, the minutes used by the account are reset to zero."
- "If your account does not have a valid payment method on file, usage is blocked once
  you use up your quota." — this is the enforcement backstop; it guarantees $0 spend at
  the cost of an outage for the rest of the month.
- Linux runners consume the quota at 1× (Windows 2×, macOS 10×) — all our workflows use
  `ubuntu-latest`.

## How the quota died in 3 days

Per-job rounding × event-triggered workflows is the killer combination.
`handoff-validator` triggered on **every issue comment**, and the warden's self-echo
storm produced comments around the clock (283 wakes in one day at its peak). At a
1-minute-minimum per run, ~200–300 runs/day ≈ 200–300 minutes/day — a 3,000-minute
quota gone in roughly 10 days, faster in a storm. The wake-filter going live
(2026-07-18) removed the storm's engine, but the structural exposure — a workflow
triggered per comment — remains whenever the validator is re-enabled.

## The rules

1. **Hosted CI is off by default.** All workflows in both repos are disabled
   (`gh workflow disable`). The CI suite runs locally instead, as a `pre-push` git hook
   that mirrors `ci.yml` job-for-job — wire it once per clone with `make install-hooks`.
   `main` has no branch protection, so nothing on GitHub blocks a merge; the hook is
   the gate.
2. **Nothing may trigger on `issue_comment`, `issues`, or other per-event chatter.**
   Those fire at agent-activity frequency, and agents comment hundreds of times a day.
   Validation that used to run per-comment belongs in the runner/warden on the
   Chairman's machine, where compute is free.
3. **Re-enabling a workflow needs a budget line.** Before `gh workflow enable`, count:
   (jobs per run) × (runs per month) × 1-minute minimum ≤ a small share of 3,000.
   Consolidate multi-job workflows into one job first — `ci.yml`'s three jobs bill 3×
   the minutes of the same steps in one job.
4. **The future `deploy.yml` is fine.** `workflow_dispatch`-only, a few runs a month,
   single job — noise against the quota. The quota is spent on frequency, not on rare
   deliberate runs.
5. **Watch usage at Settings → Billing & plans → Usage** (the `gh` CLI needs the `user`
   scope for the billing API: `gh auth refresh -h github.com -s user`). GitHub emails at
   75/90/100% of the included quota; treat 75% mid-month as an incident.
