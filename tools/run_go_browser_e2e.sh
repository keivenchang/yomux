#!/usr/bin/env bash
set -euo pipefail
common="$3"
. "$common"
binary="$(bazel_test_rlocation "$1")"
repo="$(readlink -f "$2")"
bazel_test_prepare_environment
test -x "$binary"
test -f "$repo/tools/bazel_go_browser_e2e.py"
cd "$repo"
env PYTHONPATH="$repo" "$(product_env_name GO_BINARY)=$binary" python3 "$repo/tools/bazel_go_browser_e2e.py" "$binary"
