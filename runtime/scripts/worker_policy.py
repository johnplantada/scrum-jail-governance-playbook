#!/usr/bin/env python3
"""worker_policy.py — the declarative worker-subagent roster as PLAIN DATA (pure stdlib, no SDK
import). agent_cycle.py turns each spec into a claude_agent_sdk.AgentDefinition; CI checks the
specs here (scripts/test_agent_workers.py) without needing the SDK installed.

Each worker is tool-scoped and tier-pinned for two reasons:
  • Governance — NO worker gets Bash, so none can git-push, post to the bus, run offload.sh,
    spend, or deploy at depth. A worker's only channel back is its returned text; the
    parent cycle holds all authority and synthesizes/commits the output. Spend/deploy never delegate.
  • Cost — research/draft workers run on Haiku (cheap parallel decomposition, now visible per-model
    in the spend ledger); the code-authoring implementer runs on Sonnet but still has no shell, so
    the parent runs build/tests and does the commit/PR/merge.

The keys of each spec match AgentDefinition's fields exactly, so agent_cycle.py builds them with
AgentDefinition(**spec)."""

WORKER_SPECS = {
    "researcher": {
        "model": "haiku",
        "tools": ["Read", "Grep", "Glob", "WebSearch", "WebFetch"],
        "description": (
            "Read-only parallel research/exploration worker. Use one per independent strand when a "
            "task has 3+ independent investigations (map subsystems, research N competitors, gap-"
            "analyse N areas). Reads code (Read/Grep/Glob) and the web (WebSearch/WebFetch) and "
            "returns findings. Cannot edit, run commands, post, spend, or deploy."
        ),
        "prompt": (
            "You are a read-only research worker. Investigate exactly the one strand in your prompt "
            "and return a tight, factual findings summary for the parent to synthesize. You have no "
            "ability to modify files, run shell commands, post to channels, spend, or deploy — do "
            "not attempt it; gather and report only."
        ),
    },
    "drafter": {
        "model": "haiku",
        "tools": ["Read", "WebSearch", "WebFetch"],
        "description": (
            "Read-only parallel text-drafting worker. Use one per item for multi-item content (e.g. "
            "writers-room sections, N listing/copy variants). Produces one draft from its prompt, "
            "optionally reading a referenced brief or looking up facts. Cannot edit files, run "
            "commands, post, spend, or deploy."
        ),
        "prompt": (
            "You are a text-drafting worker. Produce exactly the one draft requested in your prompt "
            "and return it as plain text. You may Read a referenced brief and WebSearch/WebFetch for "
            "facts, but you cannot modify files, run commands, post, spend, or deploy. Return only "
            "the draft."
        ),
    },
    "implementer": {
        "model": "sonnet",
        "tools": ["Read", "Grep", "Glob", "Edit", "Write"],
        "description": (
            "Parallel code-authoring worker. Use one per independent file/module when work has 3+ "
            "non-overlapping code units (scaffold several modules, edit several files). Reads and "
            "EDITS code (Read/Grep/Glob/Edit/Write) but has NO shell — it cannot run tests, git, gh, "
            "or any command, so the parent runs the build/tests and does the commit/PR/merge. Give "
            "each implementer a DISJOINT set of files so parallel edits never collide."
        ),
        "prompt": (
            "You are a code-authoring worker. Implement exactly the one unit in your prompt by "
            "editing ONLY the file(s) it names (a sibling worker owns the others — do not touch "
            "them). You have no shell: you cannot run tests, git, gh, build tools, or any command, "
            "and you cannot post, spend, or deploy. Make the edits and return a short summary of "
            "what you changed (files + rationale) so the parent can build, test, and open the PR."
        ),
    },
}
