#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
COMMON="$REPO_ROOT/tools/bazel_test_common.sh"
. "$COMMON"

usage() {
  echo "usage: $0 [--jobs N] [--execution auto|native|docker] [--target LABEL] [-- <bazel test arguments>]" >&2
  exit "${1:-2}"
}

jobs="${YOMUX_BAZEL_JOBS-${YOLOMUX_BAZEL_JOBS:-}}"
target="//:go_tests"
execution="${YOMUX_BAZEL_EXECUTION-${YOLOMUX_BAZEL_EXECUTION:-auto}}"
while [ "$#" -gt 0 ]; do
  case "$1" in
    --jobs)
      [ "$#" -ge 2 ] || usage 2
      jobs="$2"
      shift 2
      ;;
    --execution)
      [ "$#" -ge 2 ] || usage 2
      execution="$2"
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
case "$execution" in
  auto)
    if [ "$(uname -s)" = Darwin ]; then
      execution=native
    elif bazel_test_in_container; then
      execution=container
    else
      execution=docker
    fi
    ;;
  native|docker) ;;
  *) echo "--execution must be auto, native, or docker: $execution" >&2; exit 2 ;;
esac
if [ "$execution" = docker ] && ! bazel_test_in_container && ! docker info >/dev/null 2>&1; then
  echo "Docker execution requested, but the Docker daemon is unavailable" >&2
  exit 2
fi
if [ "$execution" = native ] && [ "$target" = "//:go_tests" ]; then
  target="//:go_native_tests"
fi
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

native_sdk_args=()
native_test_filter_args=()
native_browser_args=()
if [ "$execution" = native ]; then
  if [ "$(uname -s)" = Darwin ]; then
    native_sdk_version="$(bazel_go_sdk_version)"
    native_sdk_args+=("--@rules_go//go/toolchain:sdk_version=$native_sdk_version")
    if bazel_native_browser_resolve; then
      native_python_userbase="${PYTHONUSERBASE:-${HOME:-}/.local}"
      native_browser_args+=(--test_env=CHROME_BIN="$BAZEL_NATIVE_CHROME_BIN")
      native_browser_args+=(--test_env=PYTHONUSERBASE="$native_python_userbase")
      if [ -n "$BAZEL_NATIVE_CHROMEDRIVER_BIN" ]; then
        native_browser_args+=(--test_env=CHROMEDRIVER_BIN="$BAZEL_NATIVE_CHROMEDRIVER_BIN")
      fi
    else
      native_test_filter_args+=(--test_tag_filters=-requires_chrome)
      echo "Skipping Chrome E2E target because Chrome/Chromium or chromedriver is unavailable" >&2
    fi
  fi
fi

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
  --jobs="$jobs"
  --test_output=errors
  --test_summary=terse
  --test_env=YOMUX_BAZEL_EXECUTION="$execution"
  --test_env=YOLOMUX_BAZEL_EXECUTION="$execution"
  --action_env=GOPATH="$bazel_run_root/go-path"
  --action_env=GOCACHE="$bazel_run_root/go-cache"
  --action_env=GOWORK=off
  --action_env=GO111MODULE=on
  --action_env=GOTOOLCHAIN=local
  --repo_env=GO_REPOSITORY_USE_HOST_MODCACHE=1
  --repo_env=GOPATH="$bazel_run_root/go-path"
  --repo_env=GOCACHE="$bazel_run_root/go-cache"
  --repo_env=GOWORK=off
  --repo_env=GO111MODULE=on
  --repo_env=GOTOOLCHAIN=local
  ${native_test_filter_args[@]+"${native_test_filter_args[@]}"}
  ${native_browser_args[@]+"${native_browser_args[@]}"}
  ${native_sdk_args[@]+"${native_sdk_args[@]}"}
  "$@"
)

if [ "$execution" = native ]; then
  mkdir -p -- "$bazel_run_root/gomodcache"
  bazel_args+=(
    --action_env=GOMODCACHE="$bazel_run_root/gomodcache"
    --test_env=YOMUX_TEST_CONTAINER=0
    --test_env=YOLOMUX_TEST_CONTAINER=0
    --action_env=YOMUX_BAZEL_GOMODCACHE="$bazel_run_root/gomodcache"
    --action_env=YOLOMUX_BAZEL_GOMODCACHE="$bazel_run_root/gomodcache"
    --action_env=GOPROXY=https://proxy.golang.org,direct
    --action_env=GOSUMDB=sum.golang.org
    --repo_env=GOMODCACHE="$bazel_run_root/gomodcache"
    --repo_env=GOPROXY=https://proxy.golang.org,direct
    --repo_env=GOSUMDB=sum.golang.org
  )
else
  bazel_args+=(
    --repository_disable_download
    --registry=file:///opt/yomux-bazel-registry
    --test_env=YOMUX_TEST_CONTAINER=1
    --test_env=YOLOMUX_TEST_CONTAINER=1
    --action_env=GOMODCACHE=/opt/yomux-go-mod-cache
    --action_env=YOMUX_BAZEL_GOMODCACHE=/opt/yomux-go-mod-cache
    --action_env=YOLOMUX_BAZEL_GOMODCACHE=/opt/yomux-go-mod-cache
    --action_env=LANG=C.UTF-8
    --action_env=LC_ALL=C.UTF-8
    --action_env=GOPROXY=off
    --action_env=GOSUMDB=off
    --repo_env=GOMODCACHE=/opt/yomux-go-mod-cache
    --repo_env=GOPROXY=off
    --repo_env=GOSUMDB=off
  )
fi

if [ "$execution" = container ] || bazel_test_in_container; then
  bazel_test_prepare_repository_cache "$bazel_repository_cache"
  # Keep this shell process alive until Bazel exits so the EXIT trap removes the private
  # output-user root when this script runs in the test container.
  bazel "${bazel_args[@]}"
  exit $?
fi

if [ "$execution" = native ]; then
  YOMUX_BAZEL_EXECUTION=native YOLOMUX_BAZEL_EXECUTION=native YOMUX_TEST_CONTAINER=0 YOLOMUX_TEST_CONTAINER=0 bazel "${bazel_args[@]}"
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
