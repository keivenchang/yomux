#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
set -euo pipefail

if [ "$#" -eq 0 ]; then
  echo "usage: $0 COMMAND [ARG...]" >&2
  exit 2
fi

slots="$(python3 /w/tools/bazel_execution_contract.py --output-slots)"
for ((slot = 0; slot < slots; slot++)); do
  root="/var/cache/yomux-bazel-output-slot-$slot"
  [ -d "$root" ] || continue
  set +e
  flock -n -E 200 "$root/.yomux-bazel-output.lock" \
    env YOMUX_BAZEL_OUTPUT_USER_ROOT="$root" "$@"
  status=$?
  set -e
  if [ "$status" -ne 200 ]; then
    exit "$status"
  fi
done

root=/var/cache/yomux-bazel-output-slot-0
if [ -d "$root" ]; then
  exec flock -x "$root/.yomux-bazel-output.lock" \
    env YOMUX_BAZEL_OUTPUT_USER_ROOT="$root" "$@"
fi

exec "$@"
