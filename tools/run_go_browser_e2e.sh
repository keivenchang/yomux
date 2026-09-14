#!/usr/bin/env bash
set -eu

binary="$1"
script="$2"
test -x "$binary"
test -f "$script"
binary_host="$(readlink -f "$binary")"
test -x "$binary_host"

# Bazel supplies the binary and script from runfiles, but the browser must run in the same
# disposable Docker boundary as the regular Python gate. Resolve the source checkout for the
# canonical runner and pass the Bazel-built binary into the container read-only.
repo="${BUILD_WORKSPACE_DIRECTORY:-${TEST_SRCDIR:-}/__main__}"
resolved_script="$(readlink -f "$0")"
if [ -f "$resolved_script" ]; then
  repo="$(cd -- "$(dirname -- "$resolved_script")/.." && pwd)"
fi
if [ ! -f "$repo/docker/Dockerfile.test" ]; then
  repo="${RUNFILES_DIR:-$(dirname -- "$0")}/__main__"
fi
if [ ! -f "$repo/docker/Dockerfile.test" ]; then
  echo "Docker E2E source tree is unavailable: $repo" >&2
  exit 1
fi

cd "$repo"
image="$(python3 tools/docker_image.py --name)"
YOLOMUX_TEST_IMAGE="$image" \
YOLOMUX_GO_BINARY="$binary_host" \
YOLOMUX_CHECK_CONTAINER=0 \
YOLOMUX_CHECK_IN_CONTAINER=0 \
YOLOMUX_WORKTREE_WRITER_CONTAINER_SLOT="/tmp/yolomux-bazel-writer-$$" \
  docker/run-tests.sh -- python3 tools/bazel_go_browser_e2e.py /tmp/yolomux-go-binary
