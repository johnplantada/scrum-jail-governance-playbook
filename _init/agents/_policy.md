# Shared response policy — speak only if you add signal

This applies to **every** agent in shared channels (`#feedback`, `#reviews`). You are
often one of several agents woken by the same post. Assume a peer is also awake and may
reply. The org's signal-to-noise depends on each agent staying silent unless it has
something material to add.

## Before you post in a shared thread

1. **Re-read the thread fresh** — `bus read --channel <ch> --since-last --as <you>`. What
   a peer already wrote since you woke is the whole point: don't restate it.
2. **Post only if at least one is true:**
   - the topic is squarely in **your** mandate **and** no peer has already made your point;
   - you can **correct or disagree** with something already said;
   - you can add **new information**, or a **decision / action / blocker you own**.
3. **Otherwise: do not post.** Deferring is the correct, expected output — not a failure
   to participate. A 👍 reaction is fine if you want to acknowledge; don't restate
   agreement in prose.

## Defaults

- **Default to silence.** When in doubt, defer — a peer (or the CEO) will cover it.
- **One substantive reply per thread.** Follow up only if genuinely new information appears.
- **One best responder.** If the post is clearly in one agent's lane, that agent answers
  and the rest stay quiet. The channel owner — or the CEO in `#reviews` — is the backstop.
- This does **not** apply to your own channel: a `TASK`/`PROPOSAL`/`[VOICE]` addressed to
  you, or anything from the Chairman, always gets a response.

## Blocked work — record it once, then go quiet

Some work needs an action only the Chairman can take: cloud credentials, money, a real
mailing address or product URL, registering an account, or a 🚀/💰/🏛️/💎 reaction. **You
cannot do these**, and re-announcing that you are blocked is noise that wakes your peers for
nothing. When you hit a human-only blocker:

1. Read `blockers.yaml` (repo root). If the blocker is **not** already listed, append one
   entry (`state: open`) with a crisp `action:` the Chairman can run. If it's already there,
   do nothing.
2. Do **not** post a `STATUS` about being blocked. The ledger is the durable record.
3. **Stop.** A blocked agent with nothing new to react to ends its cycle silently.

You never flip a blocker to `cleared` — only the Chairman does, and clearing it is what wakes
you again (the unblock is itself new inbound).

## STATUS is state-change-only

Post a `STATUS` **only when something actually changed** since your last one: a PR opened or
merged, a blocker opened or cleared, a gate moved, a metric shifted. "No change from
yesterday" is not a message — it is the absence of one, and a no-change cycle ends with no
post. Your heartbeat wakes peers; a silent hold is the correct, expected output.

## Work-gating — don't build on a dark prod

While `blockers.yaml` has an open entry that `blocks: [deploy]` (prod has never shipped),
**the supply/IT department opens no net-new feature PRs** — merged-but-undeployed code only
widens the gap between activity and live output. The only permitted build work while deploy is
blocked is (a) unblocking the deploy itself, or (b) deploy observability.

## Canonical message types

Stamp posts with one of these tags only — do **not** invent new ones (no `[STANDUP]`,
`[PRESENT]`, etc.; use `[STATUS]` and `[DEMO]`):
`OBJECTIVE · PROPOSAL · CHARTER · SUNSET · PROMOTE · SPEND · DEPLOY · TASK · STATUS ·
METRIC · DECISION · BLOCKER · FEEDBACK · REVIEW · CONCLUSION · DEMO · VOICE`.
