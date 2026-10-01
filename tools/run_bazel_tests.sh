#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
COMMON="$REPO_ROOT/tools/bazel_test_common.sh"
. "$COMMON"

usage() {
  echo "usage: $0 [--jobs N] [--target LABEL] [-- <bazel test arguments>]" >&2
  exit "${1:-2}"
}

jobs="${YOLOMUX_BAZEL_JOBS:-}"
target="//:go_tests"
while [ "$#" -gt 0 ]; do
  case "$1" in
    --jobs)
      [ "$#" -ge 2 ] || usage 2
      jobs="$2"
      shift 2
      ;;
    --target)
      [ "$#" -ge 2 ] || usage 2
      target="$2"
      shift 2
      ;;
    --)
      shift
      break
      ;;
    -h|--help)
      usage 0
      ;;
    *)
      break
      ;;
  esac
done
if [ -z "$jobs" ]; then
  jobs="$(getconf _NPROCESSORS_ONLN 2>/dev/null || printf '2')"
fi
case "$jobs" in
  ''|*[!0-9]*)
    echo "--jobs must be a positive integer: $jobs" >&2
    exit 2
    ;;
esac
if [ "$jobs" -lt 1 ]; then
  echo "--jobs must be a positive integer: $jobs" >&2
  exit 2
fi

set -- "$target" "$@"

# The Go Chromium fixture copies the served frontend into its isolated source root. Generate it
# from the checked-out partials first so a source-only commit works without a tracked bundle.
python3 "$REPO_ROOT/tools/static_build.py"

# Every invocation gets private Bazel and Go writable roots. The module and repository caches are
# image-owned and read-only; only these per-run directories may be mutated, so parallel check runs
# cannot corrupt one another or accidentally write into the checkout.
bazel_run_root="$(mktemp -d /tmp/yolomux-bazel-run.XXXXXX)"
bazel_repository_cache="$bazel_run_root/repository-cache"
bazel_run_cleanup() {
  local status="$?"
  local cleanup_status=0
  trap - EXIT
  if [ -e "$bazel_run_root" ]; then
    if ! rm -rf -- "$bazel_run_root" 2>/dev/null; then
      # Bazel's generated standard-library tree can deliberately be read-only even though it is
      # under this invocation's private root. Repair only that exact tree before retrying; never
      # broaden the target or change permissions in the checkout.
      echo "Bazel run root contains read-only generated entries; repairing only: $bazel_run_root" >&2
      permission_repair_status=0
      if ! find "$bazel_run_root" -type d -exec chmod u+rwx {} + 2>/dev/null; then
        permission_repair_status=1
      fi
      if ! find "$bazel_run_root" -type f -exec chmod u+rw {} + 2>/dev/null; then
        permission_repair_status=1
      fi
      if ! rm -rf -- "$bazel_run_root" 2>/dev/null; then
        if bazel_test_in_container; then
          # This root lives inside the disposable outer test container. A genuinely foreign-owned
          # child cannot be repaired by the runner, but --rm still removes the complete container.
          if [ "$permission_repair_status" -eq 0 ]; then
            echo "Bazel run root retained container-owned entries; outer disposable container will remove it: $bazel_run_root" >&2
          else
            echo "Bazel run root cleanup could not repair every entry; outer disposable container will remove it: $bazel_run_root" >&2
          fi
        else
          echo "Bazel run root cleanup failed: $bazel_run_root" >&2
          cleanup_status=1
        fi
      fi
    elif [ -e "$bazel_run_root" ]; then
      echo "Bazel run root remained after cleanup: $bazel_run_root" >&2
      cleanup_status=1
    fi
  fi
  if [ "$cleanup_status" -ne 0 ] && [ "$status" -eq 0 ]; then
    status=1
  fi
  exit "$status"
}
trap bazel_run_cleanup EXIT

bazel_args=(
  --bazelrc=/dev/null
  --output_user_root="$bazel_run_root/output-user-root"
  test
  --repository_cache="$bazel_repository_cache"
  --symlink_prefix="$bazel_run_root/bazel-"
  --repository_disable_download
  --registry=file:///opt/yomux-bazel-registry
  --jobs="$jobs"
  --test_output=errors
  --test_summary=terse
  --test_env=YOMUX_TEST_CONTAINER=1
  --test_env=YOLOMUX_TEST_CONTAINER=1
  --action_env=GOMODCACHE=/opt/yomux-go-mod-cache
  --action_env=YOMUX_BAZEL_GOMODCACHE=/opt/yomux-go-mod-cache
  --action_env=LANG=C.UTF-8
  --action_env=LC_ALL=C.UTF-8
  --action_env=GOPATH="$bazel_run_root/go-path"
  --action_env=GOCACHE="$bazel_run_root/go-cache"
  --action_env=GOPROXY=off
  --action_env=GOSUMDB=off
  --action_env=GOWORK=off
  --action_env=GO111MODULE=on
  --action_env=GOTOOLCHAIN=local
  --repo_env=GO_REPOSITORY_USE_HOST_MODCACHE=1
  --repo_env=GOMODCACHE=/opt/yomux-go-mod-cache
  --repo_env=GOPATH="$bazel_run_root/go-path"
  --repo_env=GOCACHE="$bazel_run_root/go-cache"
  --repo_env=GOPROXY=off
  --repo_env=GOSUMDB=off
  --repo_env=GOWORK=off
  --repo_env=GO111MODULE=on
  --repo_env=GOTOOLCHAIN=local
  "$@"
)

if bazel_test_in_container; then
  bazel_test_prepare_repository_cache "$bazel_repository_cache"
  # Keep this shell process alive until Bazel exits so the EXIT trap removes the private
  # output-user root when this script runs in the test container.
  bazel "${bazel_args[@]}"
  exit $?
fi

"$REPO_ROOT/docker/run-tests.sh" -- bash -lc '
  set -euo pipefail
  . /w/tools/bazel_test_common.sh
  bazel_test_prepare_repository_cache "$1"
  shift
  exec bazel "$@"
' bash "$bazel_repository_cache" "${bazel_args[@]}"
exit $?
