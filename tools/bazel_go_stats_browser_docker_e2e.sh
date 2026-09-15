#!/usr/bin/env bash
set -eu

binary="$1"
test -x "$binary"
binary_host="$(readlink -f "$binary")"
test -x "$binary_host"
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
    YOLOMUX_GO_BINARY="$binary_host" \
    YOLOMUX_WORKTREE_WRITER_SLOT="${YOLOMUX_WORKTREE_WRITER_SLOT:-/tmp/yolomux-bazel-writer-$$}" \
        python3 -m pytest \
        tests/test_browser_stats_widen.py::test_go_stats_capabilities_validate_and_load_fifteen_minute_history -q -rs
    exit $?
fi

YOLOMUX_GO_BINARY="$binary_host" \
    YOLOMUX_WORKTREE_WRITER_CONTAINER_SLOT="/tmp/yolomux-bazel-writer-$$" \
    docker/run-tests.sh -- python3 -m pytest \
        tests/test_browser_stats_widen.py::test_go_stats_capabilities_validate_and_load_fifteen_minute_history -q -rs
