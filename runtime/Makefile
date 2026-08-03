# Root convenience wrapper for the GitHub-native org. `make` with no target prints help.
SHELL := /bin/bash
.DEFAULT_GOAL := help

.PHONY: help test halt resume blockers governance-monitor governance-monitor-sync tick preview logs install-hooks check-playbook

PYTHON := $(if $(wildcard .venv/bin/python),.venv/bin/python,python3)

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) \
		| awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

## ── The nervous system ────────────────────────────────────────────────────
tick: ## Run one runner tick (poll GitHub → route → wake, per RUNNER_MODE)
	@./scripts/runner-watch.sh

preview: ## Dry-run a tick: show would-be wakes without firing or advancing the cursor
	@PYTHONPATH=scripts $(PYTHON) scripts/runner.py preview

logs: ## Follow every org log in one colored feed (runner · parent agents · named workers · ops)
	@./scripts/watch.sh

## ── Kill switch ──────────────────────────────────────────────────────────
halt: ## Engage the kill switch (pause the runner and all agents)
	@./scripts/halt.sh

resume: ## Clear the kill switch
	@./scripts/resume.sh

## ── Org status ───────────────────────────────────────────────────────────
blockers: ## Show the open human-task ledger (what only the Human Principal can clear)
	@$(PYTHON) scripts/blockers.py open

governance-monitor: ## Read-only Human Principal queue drift + governance findings
	@PYTHONPATH=scripts $(PYTHON) scripts/governance_monitor.py audit

governance-monitor-sync: ## Run one deterministic Human Principal queue reconciliation
	@./scripts/governance-monitor-sync.sh

## ── Tests (same suite CI runs) ────────────────────────────────────────────
test: ## Python unit tests + constitution lint + blocker-runbook lint + decisions-ledger check
	@set -e; for t in scripts/test_*.py; do echo "→ $$t"; PYTHONPATH=scripts $(PYTHON) "$$t"; done
	@$(PYTHON) scripts/lint_constitution.py
	@$(PYTHON) scripts/blockers.py lint
	@$(PYTHON) scripts/decisions.py check
	@$(PYTHON) scripts/verify-provenance.py

install-hooks: ## Wire the guard hooks (pre-commit + the pre-push CI suite) for every worktree
	@git config core.hooksPath scripts/hooks
	@echo "✓ core.hooksPath = scripts/hooks — pre-commit + pre-push now cover this checkout and every linked worktree"

## ── Stamped provenance ─────────────────────────────────────────────────────
check-playbook: ## Verify every stamped security-bearing file against SOURCE.json
	@$(PYTHON) scripts/verify-provenance.py
