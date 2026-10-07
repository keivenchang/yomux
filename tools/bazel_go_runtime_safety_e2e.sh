#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
set -euo pipefail
common="$3"
. "$common"
binary="$(bazel_test_rlocation "$1")"
repo="$(readlink -f "$2")"
bazel_test_prepare_environment
test -x "$binary"
cd "$repo"
python3 tools/verify_go_stats_restart.py --binary "$binary" --output "$TEST_UNDECLARED_OUTPUTS_DIR/stats-restart.json"
