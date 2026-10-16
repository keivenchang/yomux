#!/usr/bin/env bash
set -euo pipefail
repo="$(readlink -f "$1")"
common="$2"
. "$common"
bazel_test_prepare_environment
test -f "$repo/yomux.py"
cd "$repo"
claude --version
codex --version
opencode --version
env "$(product_env_name AGENT_DOCKER_E2E)=1" "$(product_env_name WORKTREE_WRITER_SLOT)=$(product_env_print WORKTREE_WRITER_SLOT "${YOMUX_NATIVE_SHORT_TMPDIR:-/tmp}/yomux-bazel-writer-$$")" \
    python3 -m pytest tests/test_agent_status_docker.py -q -rs
