#!/bin/sh
set -eu

repo="${BUILD_WORKSPACE_DIRECTORY:-${TEST_SRCDIR:-}/__main__}"
if [ -f "$(readlink -f "$0")" ]; then
    repo="$(dirname "$(dirname "$(readlink -f "$0")")")"
fi
if [ ! -f "$repo/docker/Dockerfile.test" ]; then
    repo="${RUNFILES_DIR:-$(dirname "$0")}/__main__"
fi
if [ ! -f "$repo/docker/Dockerfile.test" ]; then
    echo "Docker E2E source tree is unavailable: $repo" >&2
    exit 1
fi

binary="$(mktemp "${TMPDIR:-/tmp}/yolomux-go-stats.XXXXXX")"
build_root="$(mktemp -d "${TMPDIR:-/tmp}/yolomux-go-stats-build.XXXXXX")"
trap 'rm -f "$binary"; chmod -R u+w "$build_root" 2>/dev/null || true; rm -rf "$build_root"' EXIT
cd "$repo"
mkdir -p "$build_root/go-path" "$build_root/go-mod-cache" "$build_root/go-cache"
GOWORK=off GO111MODULE=on GOPATH="$build_root/go-path" GOMODCACHE="$build_root/go-mod-cache" GOCACHE="$build_root/go-cache" \
    go build -buildvcs=false -trimpath -o "$binary" ./cmd/yolomux-go

image="$(python3 tools/docker_image.py --name)"
YOLOMUX_TEST_IMAGE="$image" YOLOMUX_GO_BINARY="$binary" \
    YOLOMUX_CHECK_CONTAINER=0 YOLOMUX_CHECK_IN_CONTAINER=0 \
    YOLOMUX_WORKTREE_WRITER_SLOT="${TMPDIR:-/tmp}/yolomux-bazel-writer" \
    docker/run-tests.sh -- python3 -m pytest \
        tests/test_browser_stats_widen.py::test_go_stats_capabilities_validate_and_load_fifteen_minute_history -q -rs
