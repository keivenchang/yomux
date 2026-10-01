#!/usr/bin/env bash
set -eu

binary="$1"
script="$2"
if [ ! -x "$binary" ]; then
  echo "Go browser E2E binary is missing or not executable: $binary" >&2
  exit 1
fi
if [ ! -f "$script" ]; then
  echo "Go browser E2E script is missing: $script" >&2
  exit 1
fi

common="${RUNFILES_DIR:-}/_main/tools/bazel_test_common.sh"
if [ ! -f "$common" ]; then
  common="${RUNFILES_DIR:-}/__main__/tools/bazel_test_common.sh"
fi
if [ ! -f "$common" ]; then
  script_directory="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
  if [ -f "$script_directory/tools/bazel_test_common.sh" ]; then
    common="$script_directory/tools/bazel_test_common.sh"
  else
    common="$script_directory/bazel_test_common.sh"
  fi
fi
if [ ! -f "$common" ]; then
  echo "Bazel test helper is missing: $common" >&2
  exit 1
fi
. "$common"

if [ "${YOMUX_BAZEL_EXECUTION-${YOLOMUX_BAZEL_EXECUTION:-}}" = native ] && ! bazel_test_in_container; then
  if ! bazel_native_browser_resolve; then
    echo "SKIP: Go browser E2E requires Chrome or Chromium and chromedriver on the host" >&2
    exit 0
  fi
  export CHROME_BIN="$BAZEL_NATIVE_CHROME_BIN"
  export CHROMEDRIVER_BIN="$BAZEL_NATIVE_CHROMEDRIVER_BIN"
fi

binary_host="$(bazel_resolve_path "$binary")"
if [ ! -x "$binary_host" ]; then
  echo "Resolved Go browser E2E binary is missing or not executable: $binary_host" >&2
  exit 1
fi

# Bazel supplies the binary and script from runfiles, but the browser must run in the same
# disposable Docker boundary as the regular Python gate. Resolve the source checkout for the
# canonical runner and pass the Bazel-built binary into the container read-only.
repo="$(bazel_test_source_root "$0")"
script_host="$(bazel_resolve_path "$script")"
if [ ! -f "$script_host" ]; then
  echo "Resolved Go browser E2E script is missing: $script_host" >&2
  exit 1
fi

cd "$repo"
if [ "${YOMUX_BAZEL_EXECUTION-${YOLOMUX_BAZEL_EXECUTION:-}}" = native ] || bazel_test_in_container; then
  bazel_test_prepare_environment
  PYTHONPATH="$repo${PYTHONPATH:+:$PYTHONPATH}" \
  YOLOMUX_GO_BINARY="$binary_host" \
    python3 "$script_host" "$binary_host"
  exit $?
fi

YOLOMUX_GO_BINARY="$binary_host" \
YOLOMUX_WORKTREE_WRITER_CONTAINER_SLOT="/tmp/yolomux-bazel-writer-$$" \
  docker/run-tests.sh -- env PYTHONPATH=/w python3 tools/bazel_go_browser_e2e.py /tmp/yolomux-go-binary
