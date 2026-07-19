#!/usr/bin/env python3
"""test_agent_workers.py — stdlib-only governance checks for the declarative worker subagents
(no pytest, no SDK; runs in CI via `python3 scripts/test_agent_workers.py`). It checks the plain
WORKER_SPECS policy in worker_policy.py — the same data agent_cycle.py turns into AgentDefinitions.

The load-bearing invariant: NO worker may carry shell or implicit-everything authority. A worker's
only channel back to the parent is its returned text; if one had Bash it could git-push, post to
the bus, run offload.sh, spend, or deploy at depth — exactly what must never delegate.
These checks fail CI if a future edit hands a worker Bash or drops its tool allowlist."""
import sys

from worker_policy import WORKER_SPECS

# Anything that can mutate the world outside the worker's own returned text. Bash is the big one
# (git/gh/bus/offload.sh/terraform all run through it); the others would let a worker post/route.
FORBIDDEN_FOR_ALL = {"Bash", "Task", "Agent", "Skill"}
READ_ONLY = {"researcher", "drafter"}
WRITE_TOOLS = {"Edit", "Write", "NotebookEdit"}

failures = []


def check(name, cond):
    if cond:
        print(f"ok   - {name}")
    else:
        print(f"FAIL - {name}")
        failures.append(name)


check("workers defined", set(WORKER_SPECS) >= {"researcher", "drafter", "implementer"})

for name, spec in WORKER_SPECS.items():
    tools = set(spec.get("tools") or [])
    # Every worker MUST declare an explicit, non-empty allowlist (a missing one would inherit all).
    check(f"{name}: has an explicit tool allowlist", bool(spec.get("tools")))
    # No worker may have shell or delegation/post authority.
    bad = FORBIDDEN_FOR_ALL & tools
    check(f"{name}: no shell/delegation tools ({bad or 'clean'})", not bad)
    # Every worker is tier-pinned (never inherits, so cost is predictable and metered per-model).
    check(f"{name}: pinned to a model (got {spec.get('model')!r})", bool(spec.get("model")))
    # Every worker needs a description (how the orchestrator routes) and a prompt (its charter).
    check(f"{name}: has description + prompt", bool(spec.get("description")) and bool(spec.get("prompt")))

for name in READ_ONLY:
    tools = set(WORKER_SPECS[name].get("tools") or [])
    check(f"{name}: read-only (no Edit/Write)", not (WRITE_TOOLS & tools))
    check(f"{name}: pinned to haiku", WORKER_SPECS[name].get("model") == "haiku")

impl = WORKER_SPECS["implementer"]
itools = set(impl.get("tools") or [])
check("implementer: can author code (Edit+Write)", {"Edit", "Write"} <= itools)
check("implementer: still no Bash", "Bash" not in itools)
check("implementer: on sonnet (code needs the brain)", impl.get("model") == "sonnet")

if failures:
    print(f"\n{len(failures)} FAILED: {failures}")
    sys.exit(1)
print("\nall worker-subagent governance checks passed")
