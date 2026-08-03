---
name: safe-cadence
description: Apply output-gated review cadence using complete controller observations.
---

# Output-gated cadence

Elapsed time and wake count do not prove delivery. Before a review or planning decision,
use controller-mediated workflow reads and require a complete successful production run on
the reviewed `main` SHA. UNKNOWN or incomplete means “not proven,” never “not shipped” and
never permission to mutate a record.

If no ship is proven, record the single blocker and suppress ceremony. If a ship is proven,
review evidence against the objective and choose at most one process change. Cadence does
not create objectives, dispatch workflows, merge, deploy, spend, or alter governance.
