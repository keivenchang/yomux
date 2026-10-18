#!/usr/bin/env bash
set -euo pipefail
common="$3"
. "$common"
binary="$(bazel_test_rlocation "$1")"
repo="$(readlink -f "$2")"
bazel_test_prepare_environment
test -x "$binary"
test -f "$repo/version.yaml"
cd "$repo"
env "$(product_env_name GO_BINARY)=$binary" "$(product_env_name WORKTREE_WRITER_SLOT)=$(product_env_print WORKTREE_WRITER_SLOT "${YOMUX_NATIVE_SHORT_TMPDIR:-/tmp}/yomux-bazel-writer-$$")" python3 -c '
import os
import pytest

from tools.test_plan import BAZEL_GO_BROWSER_TESTS

args = [*BAZEL_GO_BROWSER_TESTS, "-q", "-rs"]
from tools.product_environment import product_env
selector = product_env("GO_STATS_BROWSER_TEST", "").strip()
if selector:
    args.extend(("-k", selector))
raise SystemExit(pytest.main(args))
'
