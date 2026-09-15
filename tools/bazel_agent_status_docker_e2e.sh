#!/usr/bin/env bash
set -eu

common="${RUNFILES_DIR:-}/__main__/tools/bazel_test_common.sh"
if [ ! -f "$common" ]; then
    common="$(dirname "$(readlink -f "$0")")/bazel_test_common.sh"
fi
test -f "$common"
. "$common"
repo="$(bazel_test_source_root "$0")"
cd "$repo"

if bazel_test_in_container; then
    bazel_test_prepare_environment
    claude --version
    codex --version
    opencode --version
    YOLOMUX_AGENT_DOCKER_E2E=1 \
    YOLOMUX_WORKTREE_WRITER_SLOT="${YOLOMUX_WORKTREE_WRITER_SLOT:-/tmp/yolomux-bazel-writer-$$}" \
        python3 -m pytest \
        tests/test_fake_llm_server.py tests/test_agent_status_docker.py -q -rs
    exit $?
fi

YOLOMUX_AGENT_DOCKER_E2E=1 \
    YOLOMUX_WORKTREE_WRITER_CONTAINER_SLOT="/tmp/yolomux-bazel-writer-$$" \
    docker/run-tests.sh -- python3 -m pytest \
        tests/test_fake_llm_server.py tests/test_agent_status_docker.py -q -rs
