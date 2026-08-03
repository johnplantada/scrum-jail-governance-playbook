# Human blocker ledger

`blockers.yaml` is an operator-facing record of actions only the Human Principal can
perform. It is not an authorization channel and is never injected into a wake as authority.

Agents may propose one deduplicated open entry through their scoped repository branch. Only
the Human Principal clears an entry. A blocker must identify the affected work, why agent
capability is absent, the exact Human action, value class, effort estimate, open date, and
state. Never put a credential value in the ledger.

`scripts/blockers.py open` renders the queue for the Human Principal. `blockers.py lint`
fails malformed or non-actionable open entries. `global.unlock_wip_limit` adds a Human-facing
warning; it does not grant an agent authority or enforce product prioritization.

Clearing a blocker does not itself merge, dispatch, deploy, spend, or activate governance.
Those actions remain on their distinct authenticated Human paths. The controller may observe
the resulting GitHub event and route ordinary follow-up work only after a complete read.
