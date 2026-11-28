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
# Keep the legacy updater invocation unchanged and retain its failing phase.
if [ -f "$repo_root.owner" ]; then printf '%s\n' 'compilation' > "$repo_root/.update-build-phase"; fi
bazel "${bazel_args[@]}"
if [ -f "$repo_root.owner" ]; then printf '%s\n' 'browser asset setup' > "$repo_root/.update-build-phase"; fi
"$repo_root/bazel-bin/cmd/yomux-go/yomux-go_/yomux-go" --operator setup-assets --root "$repo_root"

if [ -f "$repo_root.owner" ]; then rm -f -- "$repo_root/.update-build-phase"; fi
