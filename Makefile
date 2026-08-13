# YOLOmux developer Makefile — the one front-door for setup/build/check/run.
# `make help` lists targets. On an externally-managed system Python (PEP 668), create and
# activate a virtualenv first (python3 -m venv .venv && . .venv/bin/activate), then `make setup`.
PYTHON ?= python3
.DEFAULT_GOAL := help
.PHONY: help check-python setup dev xterm build check run clean

help: ## List targets
	@grep -E '^[a-z][a-zA-Z0-9_-]*:.*## ' $(MAKEFILE_LIST) | sort | awk -F':.*## ' '{printf "  \033[36m%-8s\033[0m %s\n", $$1, $$2}'

check-python: ## Verify the interpreter meets YOLOmux's Python floor
	@$(PYTHON) tools/check_python.py

setup: check-python ## Install runtime deps (+ yoagent) and build the static bundle
	$(PYTHON) -m pip install -e ".[yoagent]"
	$(PYTHON) tools/static_build.py

dev: check-python ## Like setup, plus dev/test deps (pytest-xdist)
	$(PYTHON) -m pip install -e ".[yoagent,dev]"
	$(PYTHON) tools/static_build.py

xterm: ## Verify tracked xterm vendor assets against the pinned npm packages
	npm install --no-audit --no-fund
	cmp static/vendor/xterm.js node_modules/@xterm/xterm/lib/xterm.js
	cmp static/vendor/xterm.css node_modules/@xterm/xterm/css/xterm.css
	cmp static/vendor/xterm-addon-unicode11.js node_modules/@xterm/addon-unicode11/lib/addon-unicode11.js

build: ## Rebuild the served static bundle (static/yolomux.{js,css})
	$(PYTHON) tools/static_build.py

check: ## Run the full gate (tools/check.py)
	$(PYTHON) tools/check.py

run: ## Launch YOLOmux (HTTPS, dangerous-yolo) via boot.sh
	./boot.sh

clean: ## Remove the local virtualenv and node_modules
	rm -rf .venv node_modules
