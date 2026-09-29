#!/usr/bin/env bash
set -euo pipefail

# Shared by Bazel E2E wrappers so runfiles, bazel test sandboxes, and the outer test container use
# one source-root resolver and one containment decision.
bazel_test_in_container() {
  [ "${YOLOMUX_TEST_CONTAINER:-}" = 1 ] || return 1
  [ -f /.dockerenv ] || return 1
  [ -f /opt/yolomux-test-image.marker ] || return 1
  grep -Fxq 'yolomux-test-image-v1' /opt/yolomux-test-image.marker || return 1
  awk '$5 == "/" { for (i = 1; i <= NF; i++) if ($i == "-") { print $(i + 1); exit } }' /proc/self/mountinfo \
    | grep -Eq '^(overlay|fuse-overlayfs|aufs)$'
}

bazel_test_source_root() {
  local script_path="${1:-}"
  local candidate=""
  local candidates=()

  if [ -n "${BUILD_WORKSPACE_DIRECTORY:-}" ]; then
    candidates+=("$BUILD_WORKSPACE_DIRECTORY")
  fi
  if [ -n "${YOLOMUX_TEST_SOURCE_ROOT:-}" ]; then
    candidates+=("$YOLOMUX_TEST_SOURCE_ROOT")
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
      # TEST_TMPDIR after the action, and cp -RL dereferences only the files already present in
      # the declared runfiles tree.
      if [ -L "$candidate/docker/Dockerfile.test" ]; then
        local materialized_root
        materialized_root="$(mktemp -d "${TEST_TMPDIR:-/tmp}/yolomux-bazel-source.XXXXXX")"
        # The source context is bind-mounted into the rootless Docker runner. mktemp creates a
        # 0700 directory owned by the host UID, which the container's runner UID cannot traverse;
        # make this action-owned read-only context traversable before handing it to Docker.
        chmod 0755 -- "$materialized_root"
        # Do not preserve source ownership/timestamps: runfile targets can be owned by a different
        # account, while executable mode is part of the Docker input for the wrapper scripts.
        \cp -RL --no-preserve=ownership,timestamps -- "$candidate/." "$materialized_root/"
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
  local seed_root=/opt/yolomux-bazel-repository-cache
  mkdir -p -- "$cache_root"
  # Bazel creates temporary files while reading a repository cache. Keep the image seed immutable
  # and give this invocation a private writable copy so parallel actions cannot share cache state.
  if ! \cp -a -- "$seed_root/." "$cache_root/"; then
    echo "Bazel repository cache seed could not be copied: $seed_root" >&2
    return 1
  fi
  # The image seed is mode 0555 so even an accidental root process cannot alter it. The private
  # copy belongs to this runner and must let Bazel create its extraction temp files.
  find "$cache_root" -type d -exec chmod u+rwx {} +
  find "$cache_root" -type f -exec chmod u+rw {} +
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
    chmod 700 -- "$root/$directory"
  done

  # Keep only the Bazel runner controls needed after this boundary. Product roots, client
  # database/transcript overrides, tmux selection, and Python import roots must not leak from
  # the host or a parent action into Go/browser fixtures.
  while IFS= read -r variable; do
    case "$variable" in
      YOLOMUX_TEST_CONTAINER|YOLOMUX_GO_BINARY|YOLOMUX_GO_STATS_BROWSER_TEST|YOLOMUX_TEST_SOURCE_ROOT|YOLOMUX_WORKTREE_WRITER_SLOT)
        continue
        ;;
      YOLOMUX_*|XDG_*|OPENCODE_*|CODEX_*|CLAUDE_*|TMUX|TMUX_PANE|TMUX_TMPDIR|PYTHONPATH|PYTHONHOME|PYTHONPYCACHEPREFIX)
        unset "$variable"
        ;;
      ANTHROPIC_API_KEY|ANTHROPIC_AUTH_TOKEN|AWS_ACCESS_KEY_ID|AWS_SECRET_ACCESS_KEY|AWS_SESSION_TOKEN|GH_TOKEN|GITHUB_TOKEN|GOOGLE_API_KEY|GOOGLE_APPLICATION_CREDENTIALS|HF_TOKEN|HUGGINGFACE_HUB_TOKEN|LINEAR_KEY|NGC_API_KEY|OPENAI_API_KEY)
        unset "$variable"
        ;;
    esac
  done < <(compgen -e)

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
  # TEST_TMPDIR is intentionally action-owned, but Bazel's nested execution path can exceed
  # Chromium's path budget when Selenium asks ChromeDriver to create its temporary profile. Keep
  # this writable root unique and private per action while giving Unix-socket-backed tools a short
  # lexical path. The outer Docker container is disposable, and the EXIT trap removes this exact
  # mktemp-created directory after the wrapper has reaped its children.
  short_tmpdir="$(mktemp -d /tmp/yolomux-bazel-tmp.XXXXXX)"
  chmod 700 -- "$short_tmpdir"
  export YOLOMUX_BAZEL_TMPDIR="$short_tmpdir"
  bazel_test_cleanup() {
    local status="$?"
    local cleanup_status=0
    trap - EXIT
    if [ -e "${YOLOMUX_BAZEL_TMPDIR:-}" ]; then
      if ! rm -rf -- "$YOLOMUX_BAZEL_TMPDIR"; then
        echo "Bazel test temporary directory cleanup failed: $YOLOMUX_BAZEL_TMPDIR" >&2
        cleanup_status=1
      elif [ -e "$YOLOMUX_BAZEL_TMPDIR" ]; then
        echo "Bazel test temporary directory remained after cleanup: $YOLOMUX_BAZEL_TMPDIR" >&2
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
  export YOLOMUX_BAZEL_TEST="1"
}
