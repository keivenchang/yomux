#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
. "$repo_root/tools/bazel_test_common.sh"

if [ "${1:-}" = "--help" ] || [ "${1:-}" = "-h" ]; then
  echo "usage: $0 [bazel build arguments...]" >&2
  exit 0
fi

bazel_args=(--bazelrc=/dev/null build --jobs=auto)
sdk_version="$(bazel_go_sdk_version)"
if [ -n "$sdk_version" ]; then
  bazel_args+=("--@rules_go//go/toolchain:sdk_version=$sdk_version")
fi
bazel_args+=("$@" //cmd/yomux-go:yomux-go //tools/staticgen:staticgen)
exec bazel "${bazel_args[@]}"
