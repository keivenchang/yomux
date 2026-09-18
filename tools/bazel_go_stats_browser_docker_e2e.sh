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
    # Bazel test actions may replace PATH with the runner's minimal login path even though the
    # pinned Go SDK is present in the image. Resolve the image-owned tool explicitly and expose its
    # directory to child compilers; falling through to a host Go or silently skipping parity would
    # make this target non-hermetic.
    go_bin="$(command -v go || true)"
    if [ -z "$go_bin" ] && [ -x /usr/local/go/bin/go ]; then
        go_bin=/usr/local/go/bin/go
    fi
    if [ -z "$go_bin" ]; then
        echo "pinned Go SDK is unavailable in the test image" >&2
        exit 127
    fi
    export PATH="$(dirname "$go_bin"):${PATH}"
    # Run the cross-language Go tests in the same disposable image as Chromium. These tests invoke
    # Python's current-only materializer/protocol oracles against identical fixtures; keeping them
    # here catches a container-only module, fixture, or schema mismatch before the browser gate.
    YOLOMUX_GO_BINARY="$binary_host" \
    GOTOOLCHAIN=local GOWORK=off GO111MODULE=on GOPROXY=off GOSUMDB=off \
    GOMODCACHE=/opt/yolomux-go-mod-cache GOCACHE="$YOLOMUX_BAZEL_TMPDIR/go-cache" \
        "$go_bin" test -count=1 ./internal/stats ./cmd/yolomux-go
    YOLOMUX_GO_BINARY="$binary_host" \
    YOLOMUX_WORKTREE_WRITER_SLOT="${YOLOMUX_WORKTREE_WRITER_SLOT:-/tmp/yolomux-bazel-writer-$$}" \
        python3 -m pytest \
        tests/test_browser_stats_coverage.py tests/test_browser_stats_widen.py -q -rs
    exit $?
fi

YOLOMUX_GO_BINARY="$binary_host" \
    YOLOMUX_WORKTREE_WRITER_CONTAINER_SLOT="/tmp/yolomux-bazel-writer-$$" \
    docker/run-tests.sh -- python3 -m pytest \
        tests/test_browser_stats_coverage.py tests/test_browser_stats_widen.py -q -rs
