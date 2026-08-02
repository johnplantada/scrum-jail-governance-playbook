SHELL := /bin/bash
.DEFAULT_GOAL := test

.PHONY: test test-control test-generator test-stamp verify

test: test-control test-generator test-stamp

test-control:
	@PYTHONPATH=control python3 -m unittest discover -s control/tests -p 'test_*.py'

test-generator:
	@python3 bin/test_orggen.py

test-stamp:
	@set -euo pipefail; \
	stamp="$$(mktemp -d /tmp/sjg-release-test.XXXXXX)"; rmdir "$$stamp"; \
	trap 'rm -rf "$$stamp"' EXIT; \
	python3 bin/orggen init "$$stamp" --org test-org --product test-product \
	  --goal verified --principal-github test-principal \
	  --product-repo test-principal/test-product --allow-dirty >/dev/null; \
	$(MAKE) -C "$$stamp" test

verify: test
	@python3 -m py_compile control/sjg_control/*.py bin/orggen bin/sjg-install bin/sjg-activate runtime/scripts/*.py
	@shellcheck --severity=error runtime/scripts/*.sh runtime/scripts/hooks/* runtime/scripts/worker-run control/run-tests.sh
	@git diff --check
