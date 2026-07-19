# Root convenience wrapper for the GitHub-native org. `make` with no target prints help.
SHELL := /bin/bash
.DEFAULT_GOAL := help

.PHONY: help test halt resume blockers warden tick preview logs sync-playbook check-playbook

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) \
		| awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

## ── The nervous system ────────────────────────────────────────────────────
tick: ## Run one runner tick (poll GitHub → route → wake, per RUNNER_MODE)
	@./scripts/runner-watch.sh

preview: ## Dry-run a tick: show would-be wakes without firing or advancing the cursor
	@set -a; [ -f .env ] && . ./.env; set +a; PYTHONPATH=scripts python3 scripts/runner.py preview

logs: ## Follow every org log in one colored feed (runner · agents · offload · subagents · ops)
	@./scripts/watch.sh

## ── Kill switch ──────────────────────────────────────────────────────────
halt: ## Engage the kill switch (pause the runner and all agents)
	@./scripts/halt.sh

resume: ## Clear the kill switch
	@./scripts/resume.sh

## ── Org status ───────────────────────────────────────────────────────────
blockers: ## Show the open human-task ledger (what only the Chairman can clear)
	@python3 scripts/blockers.py open

warden: ## Read-only warden report: Chairman-queue drift + hygiene findings
	@set -a; [ -f .env ] && . ./.env; set +a; PYTHONPATH=scripts python3 scripts/warden.py audit

## ── Tests (same suite CI runs) ────────────────────────────────────────────
test: ## Python unit tests + constitution lint + blocker-runbook lint + decisions-ledger check
	@set -e; for t in scripts/test_*.py; do echo "→ $$t"; PYTHONPATH=scripts python3 "$$t"; done
	@python3 scripts/lint_constitution.py
	@python3 scripts/blockers.py lint
	@python3 scripts/decisions.py check

## ── Governance playbook (vendored from the golden) ─────────────────────────
# playbook/ is a vendored snapshot of the scrum-jail-governance-playbook golden. These targets
# keep it honest: sync pulls the golden's root docs in + pins the source commit; check fails on
# drift. Business-specific files (DEPLOY-RUNBOOK.md, templates/) are NOT synced.
GOLDEN_REPO ?= $(HOME)/codebase/scrum-jail-governance-playbook
PLAYBOOK_VENDORED := patterns.md safe.md blocker-ledger.md authorization-gate.md envelopes.yaml README.md RUNBOOK.md org-chart.yaml FIELD-NOTES.md

sync-playbook: ## Pull the golden's governance docs into playbook/ + pin the source (GOLDEN_REPO=...)
	@test -d "$(GOLDEN_REPO)" || { echo "GOLDEN_REPO not found: $(GOLDEN_REPO)"; exit 1; }
	@for f in $(PLAYBOOK_VENDORED); do cp "$(GOLDEN_REPO)/$$f" "playbook/$$f"; done
	@printf '# Vendored snapshot\n\nplaybook/ mirrors the governance golden. Do not hand-edit the\nvendored docs — change them in the golden and re-run `make sync-playbook`.\n\nSource: scrum-jail-governance-playbook @ %s\nVendored files: $(PLAYBOOK_VENDORED)\n' \
		"$$(git -C "$(GOLDEN_REPO)" rev-parse --short HEAD 2>/dev/null || echo unknown)" > playbook/SOURCE.md
	@echo "✓ synced $(words $(PLAYBOOK_VENDORED)) docs from $(GOLDEN_REPO) → playbook/ (pinned in playbook/SOURCE.md)"

check-playbook: ## Fail if playbook/ has drifted from the golden (GOLDEN_REPO=...)
	@test -d "$(GOLDEN_REPO)" || { echo "GOLDEN_REPO not found ($(GOLDEN_REPO)) — skipping drift check"; exit 0; }
	@drift=0; for f in $(PLAYBOOK_VENDORED); do \
		diff -q "$(GOLDEN_REPO)/$$f" "playbook/$$f" >/dev/null 2>&1 || { echo "DRIFT: playbook/$$f ≠ golden"; drift=1; }; \
	done; \
	if [ "$$drift" = 1 ]; then echo "→ run: make sync-playbook"; exit 1; else echo "✓ playbook/ matches the golden ($(words $(PLAYBOOK_VENDORED)) docs)"; fi
