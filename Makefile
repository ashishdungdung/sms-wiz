# SMS Wiz — one-command everything.
#
# The same targets work in any editor (Zed, Antigravity, Claude Code, VS Code):
# nothing here opens Xcode, and nothing depends on a GUI setting.
#
#   make verify     the gate: contracts current + cross-language parity + tests
#   make swift-test real Swift tests (honest skip if no toolchain)
#
# Override the interpreter if you use a different venv:
#   make test PY=python3

PY      ?= ./.venv/bin/python
PIP     ?= ./.venv/bin/pip
PYTHON  ?= python3
PORT    ?= 8000
HOST    ?= 127.0.0.1

.DEFAULT_GOAL := help
.PHONY: help install test demo serve contracts verify swift-test check clean skills

help: ## Show this help
	@echo "SMS Wiz — India-first SMS organiser"
	@echo
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
	  | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

install: ## Create .venv and install with dev extras
	$(PYTHON) -m venv .venv
	$(PIP) install -q --upgrade pip
	$(PIP) install -q -e ".[dev]"
	@echo "ready: make demo | make test | make serve"

test: ## Run the pytest suite
	$(PY) -m pytest

demo: ## 14 Indian edge cases, end to end (deterministic, no network)
	$(PY) -m sms_wiz.cli demo

serve: ## FastAPI on 127.0.0.1:$(PORT) — landing page at /  (use HOST=0.0.0.0 to expose)
	$(PY) -m sms_wiz.cli serve --host $(HOST) --port $(PORT)

contracts: ## Regenerate contracts/ and the filter-rule fixtures
	$(PY) scripts/export_contracts.py
	$(PY) scripts/gen_rule_fixtures.py

verify: ## Fail on stale contracts, stale rule fixtures, Python/Swift drift, or failing tests
	$(PY) scripts/export_contracts.py --check
	$(PY) scripts/verify_contracts.py
	$(PY) scripts/gen_rule_fixtures.py --check
	$(PY) -m pytest

swift-test: ## Run SMSCore tests on a Mac/Linux with Swift installed
	@if command -v swift >/dev/null 2>&1; then \
	  cd ios/Packages/SMSCore && swift test; \
	else \
	  echo "swift: no toolchain on this machine — SMSCore tests NOT run (a skip, not a pass)"; \
	  echo "       macOS: xcode-select --install   ·   swift.org for Linux"; \
	fi

check: verify demo ## What CI runs: verify + a deterministic demo smoke test

skills: ## List the agent skills in .agents/skills
	@for f in .agents/skills/*/SKILL.md; do \
	  printf "  %-34s %s\n" "$$(basename $$(dirname $$f))" "$$(sed -n 's/^title: //p' $$f | head -1)"; \
	done

clean: ## Remove caches and local databases (keeps .venv)
	rm -rf .pytest_cache .coverage htmlcov
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
	rm -f sms_wiz.db
