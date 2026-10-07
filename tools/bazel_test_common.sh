#!/usr/bin/env bash
set -euo pipefail

source "$(cd -- "${BASH_SOURCE[0]%/*}" && pwd)/product_environment.sh"
yomux_normalize_environment

# Shared by Bazel E2E wrappers so runfiles, bazel test sandboxes, and the outer test container use
# one source-root resolver and one containment decision.
bazel_go_sdk_version() {
  if [ "$(uname -s)" = Darwin ]; then
    printf '1.27.1\n'
  fi
}

bazel_assert_value() {
  local name="$1"
  local expected="$2"
  local actual="${!name-}"
  if [ "$actual" != "$expected" ]; then
    echo "$name must be $expected, got $actual" >&2
    exit 1
  fi
}

bazel_test_assert_tool_environment() {
  bazel_assert_value GOWORK off
  bazel_assert_value GO111MODULE on
  bazel_assert_value GOTOOLCHAIN local
  case "$(product_env_print BAZEL_EXECUTION "container")" in
    native)
      bazel_assert_value GOPROXY https://proxy.golang.org,direct
      bazel_assert_value GOSUMDB sum.golang.org
      bazel_assert_value "$(product_env_name TEST_CONTAINER)" 0
      bazel_assert_value GOPATH ""
      bazel_assert_value GOCACHE ""
      bazel_assert_value GOMODCACHE ""
      return
      ;;
    container|docker)
      bazel_assert_value GOPROXY off
      bazel_assert_value GOSUMDB off
      bazel_assert_value GOMODCACHE /opt/yomux-go-mod-cache
      bazel_assert_value "$(product_env_name TEST_CONTAINER)" 1
      ;;
    *) echo "unknown Bazel execution mode" >&2; return 2 ;;
  esac
  bazel_assert_value GOPATH /tmp/yomux-bazel-private/go-path
  bazel_assert_value GOCACHE /tmp/yomux-bazel-private/go-cache
}

bazel_test_in_container() {
  [ "$(product_env_print_present TEST_CONTAINER "")" = 1 ] || return 1
  [ -f /.dockerenv ] || return 1
  [ -f /opt/yomux-test-image.marker ] || return 1
  grep -Fxq 'yomux-test-image-v1' /opt/yomux-test-image.marker || return 1
  awk '$5 == "/" { for (i = 1; i <= NF; i++) if ($i == "-") { print $(i + 1); exit } }' /proc/self/mountinfo \
    | grep -Eq '^(overlay|fuse-overlayfs|aufs)$'
}

bazel_native_browser_resolve() {
  local chrome="${CHROME_BIN:-}"
  local chromedriver="${CHROMEDRIVER_BIN:-}"
  local candidate
  if [ -z "$chrome" ]; then
    for candidate in chromium chromium-browser google-chrome google-chrome-stable; do
      if command -v "$candidate" >/dev/null 2>&1; then
        chrome="$(command -v "$candidate")"
        break
      elif [ -x "$candidate" ]; then
        chrome="$candidate"
        break
      fi
    done
  fi
  if [ -z "$chromedriver" ] && command -v chromedriver >/dev/null 2>&1; then
    chromedriver="$(command -v chromedriver)"
  fi
  if [ -n "$chrome" ] && [ ! -x "$chrome" ]; then
    chrome="$(command -v "$chrome" || true)"
  fi
  if [ -n "$chromedriver" ] && [ ! -x "$chromedriver" ]; then
    chromedriver="$(command -v "$chromedriver" || true)"
  fi
  if [ -n "$chrome" ] && [ -x "$chrome" ] && [ -n "$chromedriver" ] && [ -x "$chromedriver" ]; then
    BAZEL_NATIVE_CHROME_BIN="$chrome"
    BAZEL_NATIVE_CHROMEDRIVER_BIN="$chromedriver"
    return 0
  fi
  return 1
}

# Generated Go SDK output can be read-only. Repair only this invocation's private
# execution tree; persistent repository and action caches never enter this helper.
bazel_test_remove_run_root() {
  local root="$1"
  if rm -rf -- "$root" 2>/dev/null; then
    return 0
  fi
  echo "Repairing read-only generated entries in private Bazel run root: $root" >&2
  find "$root" -type d -exec chmod u+rwx {} + || return
  find "$root" -type f -exec chmod u+rw {} + || return
  rm -rf -- "$root"
}

bazel_resolve_path() {
  python3 -c 'import pathlib, sys; print(pathlib.Path(sys.argv[1]).resolve())' "$1"
}

bazel_test_source_root() {
  local script_path="${1:-}"
  local candidate=""
  local candidates=()

  if [ -n "${BUILD_WORKSPACE_DIRECTORY:-}" ]; then
    candidates+=("$BUILD_WORKSPACE_DIRECTORY")
  fi
  local configured_source_root="$(product_env_print_present TEST_SOURCE_ROOT "")"
  if [ -n "$configured_source_root" ]; then
    candidates+=("$configured_source_root")
  fi
  if [ -n "${TEST_SRCDIR:-}" ]; then
    candidates+=("$TEST_SRCDIR/__main__")
    candidates+=("$TEST_SRCDIR/_main")
  fi
  if [ -n "${RUNFILES_DIR:-}" ]; then
    candidates+=("$RUNFILES_DIR/__main__")
    candidates+=("$RUNFILES_DIR/_main")
  fi
  if [ -n "$script_path" ] && [ -e "$script_path" ]; then
    # Keep the lexical runfiles path. Resolving a Bazel runfile symlink here points back to the
    # source checkout and permits undeclared inputs to bypass Bazel's data graph.
    candidates+=("$(cd -- "$(dirname -- "$script_path")/.." && pwd)")
  fi
  candidates+=("/w")

  for candidate in "${candidates[@]}"; do
    if [ -f "$candidate/docker/Dockerfile.test" ]; then
      # Docker's BuildKit daemon cannot dereference a runfiles Dockerfile symlink whose target
      # lives outside the sandbox. Materialize the declared runfiles tree into the action-owned
      # TEST_TMPDIR instead of resolving the symlink back to the source checkout. Bazel removes
      # TEST_TMPDIR after the action, and the copy dereferences only declared runfiles.
      if [ -L "$candidate/docker/Dockerfile.test" ] && [ "$(product_env_print_present BAZEL_EXECUTION "")" != native ]; then
        local materialized_root
        materialized_root="$(mktemp -d "${TEST_TMPDIR:-/tmp}/yolomux-bazel-source.XXXXXX")"
        # The source context is bind-mounted into the rootless Docker runner. mktemp creates a
        # 0700 directory owned by the host UID, which the container's runner UID cannot traverse;
        # make this action-owned read-only context traversable before handing it to Docker.
        chmod 0755 "$materialized_root"
        # Do not preserve source ownership/timestamps: runfile targets can be owned by a different
        # account, while executable mode is part of the Docker input for the wrapper scripts.
        python3 - "$candidate" "$materialized_root" <<'PY_COPY'
import shutil
import sys
shutil.copytree(sys.argv[1], sys.argv[2], symlinks=False, dirs_exist_ok=True)
PY_COPY
        candidate="$materialized_root"
      fi
      (cd -- "$candidate" && pwd)
      return 0
    fi
  done
  echo "Docker E2E source tree is unavailable" >&2
  return 1
}

bazel_test_prepare_repository_cache() {
  local cache_root="$1"
  local seed_root=/opt/yomux-bazel-repository-cache
  mkdir -p -- "$cache_root"
  # Bazel creates temporary files while reading a repository cache. Keep the image seed immutable
  # and give this invocation a private writable copy so parallel actions cannot share cache state.
  if ! \cp -a -- "$seed_root/." "$cache_root/"; then
    echo "Bazel repository cache seed could not be copied: $seed_root" >&2
    return 1
  fi
  find "$cache_root" -type d -exec chmod u+rwx {} +
  find "$cache_root" -type f -exec chmod u+rw {} +
}

bazel_test_rlocation() {
  local runfile="$1"
  python3 - "$runfile" "${RUNFILES_DIR:-}" "${RUNFILES_MANIFEST_FILE:-}" <<'PY_RLOCATION'
from pathlib import Path
import sys
name, runfiles_dir, manifest = sys.argv[1:]
if runfiles_dir:
    candidate = Path(runfiles_dir) / name
    if candidate.exists():
        print(candidate)
        raise SystemExit(0)
if manifest and Path(manifest).is_file():
    for line in Path(manifest).read_text(encoding="utf-8").splitlines():
        key, separator, value = line.partition(" ")
        if separator and key == name and Path(value).exists():
            print(value)
            raise SystemExit(0)
raise SystemExit(f"required Bazel runfile is missing: {name}")
PY_RLOCATION
}

bazel_test_prepare_environment() {
  local test_root="${TEST_TMPDIR:-}"
  if [ -z "$test_root" ]; then
    echo "Bazel test environment requires TEST_TMPDIR" >&2
    return 2
  fi

  local root="$test_root/yolomux-agent-environment"
  local short_tmpdir
  local directory
  local variable
  mkdir -p -- "$root"
  for directory in home xdg-config xdg-data xdg-state xdg-cache codex-home claude-config xdg-runtime tmp; do
    mkdir -p -- "$root/$directory"
    chmod 700 "$root/$directory"
  done

  # Keep only the Bazel runner controls needed after this boundary. Product roots, client
  # database/transcript overrides, tmux selection, and Python import roots must not leak from
  # the host or a parent action into Go/browser fixtures.
  while IFS= read -r variable; do
    product_setting="$(product_env_suffix "$variable")" || product_setting=
    case "$product_setting" in
      ENVIRONMENT_NORMALIZED|ENVIRONMENT_ALIASES|ENVIRONMENT_LEGACY_INPUT)
        continue
        ;;
    esac
    case "$product_setting" in
      BAZEL_CONTRACT|BAZEL_IMAGE_ID|BAZEL_APPARMOR_SHA256|BAZEL_EXECUTION|TEST_CONTAINER|TEST_SOURCE_ROOT|TEST_IMAGE|TEST_IMAGE_REBUILD|IMAGE_RUNNER_UID|IMAGE_RUNNER_GID|GO_BINARY|GO_STATS_BROWSER_TEST|WORKTREE_WRITER_SLOT)
        continue
        ;;
      ?*)
        unset "$variable"
        continue
        ;;
    esac
    case "$variable" in
      XDG_*|OPENCODE_*|CODEX_*|CLAUDE_*|TMUX|TMUX_PANE|TMUX_TMPDIR|PYTHONPATH|PYTHONHOME|PYTHONPYCACHEPREFIX)
        unset "$variable"
        ;;
      ANTHROPIC_API_KEY|ANTHROPIC_AUTH_TOKEN|AWS_ACCESS_KEY_ID|AWS_SECRET_ACCESS_KEY|AWS_SESSION_TOKEN|GH_TOKEN|GITHUB_TOKEN|GOOGLE_API_KEY|GOOGLE_APPLICATION_CREDENTIALS|HF_TOKEN|HUGGINGFACE_HUB_TOKEN|LINEAR_KEY|NGC_API_KEY|OPENAI_API_KEY)
        unset "$variable"
        ;;
    esac
  done < <(compgen -e)

  # Native actions use their declared runfiles directly; routing pytest to Docker would require undeclared inputs.
  if [ "$(product_env_print BAZEL_EXECUTION "container")" = native ]; then
    product_env_assign "CHECK_CONTAINER=0"
  fi

  # Bazel gives every test action its own TEST_TMPDIR. Do not point parallel actions at shared
  # client state; the remaining client roots are fixture-owned below.
  export HOME="$root/home"
  export XDG_CONFIG_HOME="$root/xdg-config"
  export XDG_DATA_HOME="$root/xdg-data"
  export XDG_STATE_HOME="$root/xdg-state"
  export XDG_CACHE_HOME="$root/xdg-cache"
  export CODEX_HOME="$root/codex-home"
  export CLAUDE_CONFIG_DIR="$root/claude-config"
  export XDG_RUNTIME_DIR="$root/xdg-runtime"
  export PYTHONDONTWRITEBYTECODE=1
  # TEST_TMPDIR is intentionally action-owned, but Bazel's nested execution path can exceed
  # Chromium's path budget when Selenium asks ChromeDriver to create its temporary profile. Keep
  # this writable root unique and private per action while giving Unix-socket-backed tools a short
  # lexical path. The outer Docker container is disposable, and the EXIT trap removes this exact
  # mktemp-created directory after the wrapper has reaped its children.
  short_tmpdir="$(mktemp -d /tmp/yolomux-bazel-tmp.XXXXXX)"
  chmod 700 "$short_tmpdir"
  product_env_assign "BAZEL_TMPDIR=$short_tmpdir"
  bazel_test_cleanup() {
    local status="$?"
    local cleanup_status=0
    trap - EXIT
    if [ -e "$(product_env_print BAZEL_TMPDIR "")" ]; then
      if ! rm -rf -- "$(product_env_print BAZEL_TMPDIR)"; then
        echo "Bazel test temporary directory cleanup failed: $(product_env_print BAZEL_TMPDIR)" >&2
        cleanup_status=1
      elif [ -e "$(product_env_print BAZEL_TMPDIR)" ]; then
        echo "Bazel test temporary directory remained after cleanup: $(product_env_print BAZEL_TMPDIR)" >&2
        cleanup_status=1
      fi
    fi
    if [ "$cleanup_status" -ne 0 ] && [ "$status" -eq 0 ]; then
      status=1
    fi
    exit "$status"
  }
  trap bazel_test_cleanup EXIT
  export TMPDIR="$short_tmpdir"
  export GOPATH="$root/go-path"
  export GOCACHE="$root/go-cache"
  export GOMODCACHE="$root/go-module-cache"
  mkdir -p -- "$GOPATH" "$GOCACHE" "$GOMODCACHE"
  product_env_assign "BAZEL_TEST=1"
  yomux_normalize_environment
}
