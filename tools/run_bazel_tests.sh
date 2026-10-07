#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
COMMON="$REPO_ROOT/tools/bazel_test_common.sh"
. "$COMMON"

usage() {
  printf '%s\n' \
    "usage: $0 [--jobs N] [--memory-mb N] [--target LABEL] [--cache=on|off] [--reuse-test-results|--rerun-tests] [--execution auto|native|docker] [-- <bazel test arguments>]" \
    "Docker reuses passing tests by default; --rerun-tests or --cache=off forces execution. Native reuse is opt-in." \
    "--execution native uses the host toolchain; Docker remains the canonical Linux lane." \
    "Trailing Bazel arguments override generated options; explicit --jobs and --memory-mb override runner budgets." >&2
  exit "${1:-2}"
}

original_args=("$@")
cache="$(product_env_print_present BAZEL_CACHE "on")"
reuse_test_results=default
jobs="$(product_env_print_present BAZEL_JOBS "")"
memory_mb="$(product_env_print_present BAZEL_MEMORY_MB "")"
memory_mb_set=no
[ -z "$memory_mb" ] || memory_mb_set=yes
target="//:go_tests"
execution="$(product_env_print_present BAZEL_EXECUTION "auto")"
requested_execution="$execution"
while [ "$#" -gt 0 ]; do
  case "$1" in
    --cache=*)
      cache="${1#--cache=}"; shift ;;
    --reuse-test-results)
      [ "$reuse_test_results" != no ] || { echo "conflicting freshness options" >&2; exit 2; }
      reuse_test_results=yes; shift ;;
    --rerun-tests)
      [ "$reuse_test_results" != yes ] || { echo "conflicting freshness options" >&2; exit 2; }
      reuse_test_results=no; shift ;;
    --jobs)
      [ "$#" -ge 2 ] || usage 2
      jobs="$2"
      shift 2
      ;;
    --execution)
      [ "$#" -ge 2 ] || usage 2
      execution="$2"
      requested_execution="$execution"
      shift 2
      ;;
    --memory-mb)
      [ "$#" -ge 2 ] || usage 2
      memory_mb="$2"
      memory_mb_set=yes
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
    if bazel_test_in_container; then
      execution=docker
    elif [ "$(uname -s)" = Darwin ]; then
      execution=native
    else
      execution=docker
    fi
    ;;
  native|docker) ;;
  *) echo "--execution must be auto, native, or docker: $execution" >&2; exit 2 ;;
esac
if [ "$execution" = native ] && bazel_test_in_container; then
  echo "Native execution requires a host shell; use auto or docker inside the isolated test container" >&2
  exit 2
fi
if [ "$execution" = docker ] && ! bazel_test_in_container && ! docker info >/dev/null 2>&1; then
  echo 'YOMUX_EXECUTION {"effective":"docker","admission":"failed","hermetic":false,"failure_phase":"daemon"}'
  echo "WARNING: HERMETIC DOCKER TESTING FAILED: the Docker daemon is unavailable. No native fallback was run." >&2
  echo "Native diagnostics require a separate explicit invocation: tools/run_bazel_tests.sh --execution native" >&2
  exit 2
fi
if [ "$execution" = native ]; then
  echo "WARNING: native execution is outside the Docker hermeticity guarantee; tools found on the host PATH and host state can affect results." >&2
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
product_env_assign "BAZEL_CPU_COUNT=$jobs"
product_env_assign "BAZEL_CACHE=$cache"
if [ "$memory_mb_set" = yes ]; then
  case "$memory_mb" in ''|*[!0-9]*) echo "--memory-mb must be a positive integer: $memory_mb" >&2; exit 2 ;; esac
  if [ "$memory_mb" -lt 1024 ]; then
    echo "--memory-mb must be at least 1024: $memory_mb" >&2
    exit 2
  fi
  product_env_assign "BAZEL_MEMORY_MB=$memory_mb"
fi

yomux_normalize_environment

trailing_args=("$@")
python3 - "$requested_execution" "$execution" "$target" "${#trailing_args[@]}" "$reuse_test_results" <<'PY_EXECUTION'
import json, sys
print('YOMUX_EXECUTION ' + json.dumps(dict(requested=sys.argv[1], effective=sys.argv[2], target=sys.argv[3], admission='pending' if sys.argv[2]=='docker' else 'not-applicable', hermetic=False, full_suite_requested=sys.argv[3]=='//:go_tests' and sys.argv[4]=='0', freshness=sys.argv[5])))
PY_EXECUTION
explicit_reuse="$reuse_test_results"
set -- "$target" "$@"

native_sdk_args=()
native_test_filter_args=()
native_browser_args=()
if [ "$execution" = native ]; then
  if [ "$(uname -s)" = Darwin ]; then
    native_sdk_version="$(bazel_go_sdk_version)"
    native_sdk_args+=("--@rules_go//go/toolchain:sdk_version=$native_sdk_version")
  fi
  if bazel_native_browser_resolve; then
    native_python_userbase="${PYTHONUSERBASE:-${HOME:-}/.local}"
    native_browser_args+=(--test_env=CHROME_BIN="$BAZEL_NATIVE_CHROME_BIN")
    native_browser_args+=(--test_env=PYTHONUSERBASE="$native_python_userbase")
    if [ -n "$BAZEL_NATIVE_CHROMEDRIVER_BIN" ]; then
      native_browser_args+=(--test_env=CHROMEDRIVER_BIN="$BAZEL_NATIVE_CHROMEDRIVER_BIN")
    fi
  else
    native_test_filter_args+=(--test_tag_filters=-requires_chrome)
    echo 'YOMUX_EXECUTION {"effective":"native","skipped_targets":["requires_chrome"]}'
    echo "Skipping Chrome E2E target because Chrome/Chromium or chromedriver is unavailable" >&2
  fi
fi

if [ "$reuse_test_results" = default ]; then
  reuse_test_results=no
  [ "$execution" = native ] || reuse_test_results=yes
fi
[ "$cache" != off ] || reuse_test_results=no
printf 'YOMUX_EXECUTION {"test_result_reuse":"%s"}\n' "$reuse_test_results"
test_result_cache=no
if [ "$reuse_test_results" = yes ]; then
  test_result_cache=yes
fi

case "$cache" in on|off) ;; *) echo "cache must be on or off" >&2; exit 2 ;; esac
if [ "$execution" != native ]; then
  python3 "$REPO_ROOT/tools/bazel_execution_contract.py" --validate-options -- "${trailing_args[@]}"
fi
if [ "$execution" != native ] && ! bazel_test_in_container; then
  status=0
  "$REPO_ROOT/docker/run-tests.sh" --bazel-contract -- /w/tools/run_bazel_tests.sh "${original_args[@]}" || status=$?
  if [ "$status" -ne 0 ]; then
    echo "WARNING: HERMETIC DOCKER TESTING FAILED (exit $status). No native fallback was run." >&2
    echo "Native diagnostics require a separate explicit invocation: tools/run_bazel_tests.sh --execution native" >&2
  fi
  exit "$status"
fi
if [ "$execution" != native ]; then
  python3 "$REPO_ROOT/tools/bazel_execution_contract.py" --admit-runtime
fi

if [ "$execution" = native ] && ! bazel_test_in_container; then
  expected_bazel_version="$(<"$REPO_ROOT/.bazelversion")"
  actual_bazel_version="$(bazel --version | awk '{print $2}')"
  if [ "$actual_bazel_version" != "$expected_bazel_version" ]; then
    echo "native execution requires Bazel $expected_bazel_version from .bazelversion; found ${actual_bazel_version:-unknown}" >&2
    exit 2
  fi
  native_run_root="$(mktemp -d /tmp/yomux-bazel-native.XXXXXX)"
  native_cleanup() {
    local status="$?"
    trap - EXIT
    if ! bazel_test_remove_run_root "$native_run_root"; then
      echo "Native Bazel execution root cleanup failed: $native_run_root" >&2
      [ "$status" -ne 0 ] || status=1
    fi
    if [ "$status" = 0 ]; then
      echo "NATIVE DIAGNOSTICS PASSED - NON-HERMETIC"
    else
      echo "NATIVE DIAGNOSTICS FAILED - NON-HERMETIC" >&2
    fi
    exit "$status"
  }
  trap native_cleanup EXIT
  native_uid="$(id -u)"
  native_platform="$(uname -s)-$(uname -m)"
  native_workspace_id="$(printf '%s' "$REPO_ROOT" | shasum -a 256 | cut -c1-12)"
  native_identity="$(cat "$REPO_ROOT/.bazelversion" "$REPO_ROOT/MODULE.bazel" "$REPO_ROOT/MODULE.bazel.lock" "$REPO_ROOT/go.mod" "$REPO_ROOT/go.sum" | shasum -a 256 | cut -c1-16)"
  native_cache_root="/tmp/yomux-bazel-cache-$native_uid/$native_platform-$native_workspace_id-$native_identity"
  native_persistent_output_root=no
  if [ "$reuse_test_results" = yes ] && [ "$cache" = on ]; then
    native_persistent_output_root=yes
  fi
  umask 077
  python3 - "$native_cache_root" "$native_persistent_output_root" <<'PY_NATIVE_CACHE'
import os
from pathlib import Path
import stat
import sys

cache_root = Path(sys.argv[1])
reuse_output_root = sys.argv[2] == "yes"
cache_root.mkdir(mode=0o700, parents=True, exist_ok=True)
for path in (cache_root.parent, cache_root):
    metadata = path.lstat()
    if not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid != os.getuid() or stat.S_IMODE(metadata.st_mode) != 0o700:
        raise SystemExit(f"native Bazel cache directory must be user-owned mode 0700: {path}")
names = ["action", "repository"]
if reuse_output_root:
    names.append("output-user-root")
for name in names:
    partition = cache_root / name
    partition.mkdir(mode=0o700, exist_ok=True)
    metadata = partition.lstat()
    if not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid != os.getuid() or stat.S_IMODE(metadata.st_mode) != 0o700:
        raise SystemExit(f"native Bazel cache partition must be user-owned mode 0700: {partition}")
PY_NATIVE_CACHE
  native_output_user_root="$native_run_root/output-user-root"
  if [ "$native_persistent_output_root" = yes ]; then
    native_output_user_root="$native_cache_root/output-user-root"
  fi
  bazel_args=(
    --batch
    --bazelrc=/dev/null
    --output_user_root="$native_output_user_root"
    test
    --repository_cache="$native_cache_root/repository"
    --symlink_prefix="$native_run_root/bazel-"
    --jobs="$jobs"
    --test_output=errors
    --test_summary=terse
    --cache_test_results="$test_result_cache"
    --test_env="$(product_env_name BAZEL_EXECUTION)"=native
    --test_env="$(product_env_name BAZEL_EXECUTION)"=native
    --test_env="$(product_env_name TEST_CONTAINER)"=0
    --test_env="$(product_env_name TEST_CONTAINER)"=0
    --action_env=PATH="$PATH"
    --test_env=PATH="$PATH"
    --action_env=GOPATH=
    --action_env=GOCACHE=
    --action_env=GOMODCACHE=
    --action_env=GOWORK=off
    --action_env=GO111MODULE=on
    --action_env=GOTOOLCHAIN=local
    --action_env=GOPROXY=https://proxy.golang.org,direct
    --action_env=GOSUMDB=sum.golang.org
    --repo_env=GOPATH="$native_run_root/go-path"
    --repo_env=GOMODCACHE="$native_run_root/go-module-cache"
    --repo_env=GOPROXY=https://proxy.golang.org,direct
    --repo_env=GOSUMDB=sum.golang.org
    ${native_test_filter_args[@]+"${native_test_filter_args[@]}"}
    ${native_browser_args[@]+"${native_browser_args[@]}"}
    ${native_sdk_args[@]+"${native_sdk_args[@]}"}
  )
  if [ "$memory_mb_set" = yes ]; then
    bazel_args+=(--local_resources="memory=$memory_mb")
  fi
  if [ "$cache" = on ]; then
    bazel_args+=(--disk_cache="$native_cache_root/action")
  fi
  bazel_args+=("$@" --cache_test_results="$test_result_cache")
  if [ "$native_persistent_output_root" = yes ]; then
    exec {native_output_lock_fd}>"$native_cache_root/.output-user-root.lock"
    flock -x "$native_output_lock_fd"
  fi
  bazel "${bazel_args[@]}"
  exit 0
fi

# Docker invocations with caching use one locked stable Bazel output root per free slot; cache-off
# and native invocations keep private output roots. Dependency seeds and the compiled action
# cache remain in their host-local partitions.
bazel_run_root="$(mktemp -d /tmp/yomux-bazel-run.XXXXXX)"
test_gopath=/tmp/yomux-bazel-private/go-path
test_gocache=/tmp/yomux-bazel-private/go-cache
go_module_cache=/tmp/yomux-bazel-private/go-module-cache
gomodcache="$go_module_cache"
mkdir -p -- "$test_gopath" "$test_gocache" "$gomodcache"
bazel_run_cleanup() {
  local status="$?"
  local cleanup_status=0
  trap - EXIT
  if ! bazel_test_remove_run_root "$bazel_run_root"; then
    echo "Bazel execution root cleanup failed: $bazel_run_root" >&2
    cleanup_status=1
  fi
  if [ "$cleanup_status" -ne 0 ] && [ "$status" -eq 0 ]; then
    status=1
  fi
  exit "$status"
}
trap bazel_run_cleanup EXIT

# Bzlmod's reproducible go_deps extension does not fail when a required go.sum
# line is missing. Go's tidy graph also contains sums used by Bazel modules, so
# compare its canonical Go sums as a subset while preserving those extra lines.
if [ ! -d /opt/yomux-go-mod-cache ]; then
  echo "Go module cache seed is missing from the test image" >&2
  exit 2
fi
mkdir -p -- "$go_module_cache"
if ! \cp -a /opt/yomux-go-mod-cache/. "$go_module_cache/"; then
  echo "could not materialize a private Go module cache from the image seed" >&2
  exit 2
fi
find "$go_module_cache" -type d -exec chmod u+rwx {} +
find "$go_module_cache" -type f -exec chmod u+rw {} +
go_tidy_root="$bazel_run_root/go-tidy-source"
mkdir -p -- "$go_tidy_root"
if ! tar -C "$REPO_ROOT" --exclude=.git --exclude='*.agent-edit.lock*' --exclude='*.md.lock' --exclude='*.txt.lock' -cf - . | tar -C "$go_tidy_root" -xf -; then
  echo "could not prepare a lock-free source snapshot for Go module validation" >&2
  exit 2
fi
if ! (
  cd "$go_tidy_root"
  GOMODCACHE="$go_module_cache" \
    GOPATH="$bazel_run_root/go-path" \
    GOCACHE="$bazel_run_root/go-cache" \
    GOPROXY=off \
    GOSUMDB=off \
    GOWORK=off \
    GOTOOLCHAIN=local \
    /usr/local/go/bin/go mod tidy
); then
  echo "Go module dependency closure could not be resolved from the immutable image seed" >&2
  exit 1
fi
if ! cmp -s "$REPO_ROOT/go.mod" "$go_tidy_root/go.mod"; then
  diff -u "$REPO_ROOT/go.mod" "$go_tidy_root/go.mod" >&2 || true
  echo "go.mod is not tidy; run go mod tidy with the pinned SDK" >&2
  exit 1
fi
missing_go_sums="$(comm -23 <(sort "$go_tidy_root/go.sum") <(sort "$REPO_ROOT/go.sum"))"
if [ -n "$missing_go_sums" ]; then
  printf 'go.sum is missing checksums required by the Go package graph:\n%s\n' "$missing_go_sums" >&2
  echo "run go mod tidy with the pinned SDK, then preserve additional sums required by Bzlmod" >&2
  exit 1
fi

# The image seed is immutable, but Bazel 9's repository-content cache needs to
# create lock metadata. Materialize it in this invocation's private writable root.
repository_cache="$bazel_run_root/repository-cache"
if [ ! -d /opt/yomux-bazel-repository-cache ]; then
  echo "Bazel repository cache seed is missing from the test image" >&2
  exit 2
fi
mkdir -p -- "$repository_cache"
if ! \cp -a /opt/yomux-bazel-repository-cache/. "$repository_cache/"; then
  echo "could not materialize a private Bazel repository cache from the image seed" >&2
  exit 2
fi
find "$repository_cache" -type d -exec chmod u+rwx {} +
find "$repository_cache" -type f -exec chmod u+rw {} +

bazel_output_user_root="$bazel_run_root/output-user-root"
if [ -n "$(product_env_print BAZEL_OUTPUT_USER_ROOT "")" ]; then
  bazel_output_user_root="$(product_env_print BAZEL_OUTPUT_USER_ROOT)"
fi
bazel_batch_args=(--batch)
bazel_args=(
  "${bazel_batch_args[@]+"${bazel_batch_args[@]}"}"
  --bazelrc=/dev/null
  --output_user_root="$bazel_output_user_root"
  test
  --repository_cache="$repository_cache"
  --symlink_prefix="$bazel_run_root/bazel-"
  --jobs="$jobs"
  --test_output=errors
  --test_summary=terse
  --cache_test_results="$test_result_cache"
  --test_env="$(product_env_name BAZEL_EXECUTION)"="$execution"
  --test_env="$(product_env_name BAZEL_EXECUTION)"="$execution"
  --action_env=GOPATH="$test_gopath"
  --action_env=GOCACHE="$test_gocache"
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
)

  bazel_args+=(
    --repository_disable_download
    --registry=file:///opt/yomux-bazel-registry
    --test_env="$(product_env_name TEST_CONTAINER)"=1
    --test_env="$(product_env_name TEST_CONTAINER)"=1
    --action_env=GOMODCACHE=/opt/yomux-go-mod-cache
    --action_env="$(product_env_name BAZEL_GOMODCACHE)"=/opt/yomux-go-mod-cache
    --action_env="$(product_env_name BAZEL_GOMODCACHE)"=/opt/yomux-go-mod-cache
    --action_env=LANG=C.UTF-8
    --action_env=LC_ALL=C.UTF-8
    --action_env=GOPROXY=off
    --action_env=GOSUMDB=off
    --repo_env=GOMODCACHE=/opt/yomux-go-mod-cache
    --repo_env=GOPROXY=off
    --repo_env=GOSUMDB=off
  )
if [ "$cache" = on ]; then
  if [ ! -d /var/cache/yomux-bazel ] || [ ! -w /var/cache/yomux-bazel ]; then
    echo "Bazel disk cache is not writable at /var/cache/yomux-bazel; check the host-local cache mount and runner UID" >&2
    exit 2
  fi
  bazel_args+=(--disk_cache=/var/cache/yomux-bazel)
fi

mapfile -t isolation_args < <(python3 "$REPO_ROOT/tools/bazel_execution_contract.py" --flags)
bazel_args+=("$@" "${isolation_args[@]}"
  --platform_suffix="$(product_env_print BAZEL_CONTRACT)"
  --action_env="$(product_env_name BAZEL_APPARMOR_SHA256)"="$(product_env_print BAZEL_APPARMOR_SHA256)"
  --test_env="$(product_env_name BAZEL_APPARMOR_SHA256)"="$(product_env_print BAZEL_APPARMOR_SHA256)"
  --action_env="$(product_env_name BAZEL_IMAGE_ID)"="$(product_env_print BAZEL_IMAGE_ID)"
  --action_env="$(product_env_name BAZEL_CONTRACT)"="$(product_env_print BAZEL_CONTRACT)"
  --test_env="$(product_env_name BAZEL_IMAGE_ID)"="$(product_env_print BAZEL_IMAGE_ID)"
  --test_env="$(product_env_name BAZEL_CONTRACT)"="$(product_env_print BAZEL_CONTRACT)"
  --cache_test_results="$test_result_cache")

if [ "$execution" = container ] || bazel_test_in_container; then
  bazel_test_prepare_repository_cache "$repository_cache"
  bazel "${bazel_args[@]}"
  exit 0
fi

echo "Bazel container execution requires the isolated test image" >&2
exit 2
