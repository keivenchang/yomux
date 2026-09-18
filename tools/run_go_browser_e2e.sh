#!/usr/bin/env bash
set -eu

binary="$1"
script="$2"
test -x "$binary"
test -f "$script"
binary_host="$(readlink -f "$binary")"
test -x "$binary_host"

common="${RUNFILES_DIR:-}/__main__/tools/bazel_test_common.sh"
if [ ! -f "$common" ]; then
  common="$(dirname "$(readlink -f "$0")")/bazel_test_common.sh"
fi
test -f "$common"
. "$common"

# Bazel supplies the binary and script from runfiles, but the browser must run in the same
# disposable Docker boundary as the regular Python gate. Resolve the source checkout for the
# canonical runner and pass the Bazel-built binary into the container read-only.
repo="$(bazel_test_source_root "$0")"
script_host="$(readlink -f "$script")"
test -f "$script_host"

cd "$repo"
if bazel_test_in_container; then
  bazel_test_prepare_environment
  PYTHONPATH="$repo${PYTHONPATH:+:$PYTHONPATH}" \
  YOLOMUX_GO_BINARY="$binary_host" \
    python3 "$script_host" "$binary_host"
  exit $?
fi

YOLOMUX_GO_BINARY="$binary_host" \
YOLOMUX_WORKTREE_WRITER_CONTAINER_SLOT="/tmp/yolomux-bazel-writer-$$" \
  docker/run-tests.sh -- env PYTHONPATH=/w python3 tools/bazel_go_browser_e2e.py /tmp/yolomux-go-binary
