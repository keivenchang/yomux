# YOmux setup and runtime builds require Go/Bazel. Python is development-only.
PYTHON ?= python3
.DEFAULT_GOAL := help
.PHONY: help check-python setup install dev xterm build check run clean
PREFIX ?= $(HOME)/.local

help: ## List targets
	@grep -E '^[a-z][a-zA-Z0-9_-]*:.*## ' $(MAKEFILE_LIST) | sort | awk -F':.*## ' '{printf "  \033[36m%-8s\033[0m %s\n", $$1, $$2}'

check-python: ## Verify the interpreter meets YOmux's Python floor
	@$(PYTHON) tools/check_python.py

setup: install ## Build and install the native runtime

install: build ## Install native yomux/yolomux commands below PREFIX
	tools/install.sh --prefix "$(PREFIX)"

dev: check-python ## Install Python developer tools and build native runtime
	$(PYTHON) -m pip install -e ".[dev]"
	$(MAKE) build

xterm: ## Verify tracked xterm vendor assets against the pinned npm packages
	npm ci --ignore-scripts --no-audit --no-fund
	cmp static/vendor/xterm.js node_modules/@xterm/xterm/lib/xterm.js
	cmp static/vendor/xterm.css node_modules/@xterm/xterm/css/xterm.css
	cmp static/vendor/xterm-addon-unicode11.js node_modules/@xterm/addon-unicode11/lib/addon-unicode11.js

build: ## Build the native server and publish browser assets
	tools/build_go.sh
	bazel-bin/cmd/yomux-go/yomux-go_/yomux-go --operator build-assets --root "$(CURDIR)"

check: ## Run the full gate (tools/check.py)
	$(PYTHON) tools/check.py

run: ## Launch YOmux (HTTPS, dangerous-yolo) via boot.sh
	./boot.sh

clean: ## Remove the local virtualenv and node_modules
	rm -rf .venv node_modules
