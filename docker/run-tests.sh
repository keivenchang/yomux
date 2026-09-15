#!/bin/bash
# Run a command in a throwaway YOLOmux test container with its own HOME.
set -euo pipefail

REPO_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"

# Rootless Docker maps container root to the invoking host user, but the image's non-root runner
# account can still read the read-only worktree. Keep the image-selected USER at runtime so a
# rootless invocation cannot mutate image-owned caches or gain unnecessary container privileges.
rootless_docker=0
if docker info --format '{{json .SecurityOptions}}' 2>/dev/null | grep -q 'name=rootless'; then
  rootless_docker=1
fi

if [ "$rootless_docker" = 1 ]; then
  export YOLOMUX_IMAGE_RUNNER_UID=1000
  export YOLOMUX_IMAGE_RUNNER_GID=1000
else
  export YOLOMUX_IMAGE_RUNNER_UID="$(id -u)"
  export YOLOMUX_IMAGE_RUNNER_GID="$(id -g)"
fi
IMAGE="${YOLOMUX_TEST_IMAGE:-$(python3 "$REPO_ROOT/tools/docker_image.py" --name)}"

usage() {
  echo "usage: $0 [--build] [--workdir <dir>] [-- <command>]" >&2
  exit "${1:-2}"
}

do_build=0
workdir=/w
while [ $# -gt 0 ]; do
  case "$1" in
    --build) do_build=1; shift ;;
    --workdir)
      [ $# -ge 2 ] || usage 2
      workdir="$2"
      shift 2
      ;;
    --) shift; break ;;
    -h|--help) usage 0 ;;
    *) break ;;
  esac
done

# Accept either the mapped container path or a caller-relative worktree directory. Normalize the
# result before Docker sees it; otherwise `--workdir ../` becomes `/w/../` and silently escapes the
# read-only source mount's intended working tree.
normalize_container_workdir() {
  local candidate="$1"
  local normalized
  normalized="$(realpath -m -- "$candidate")"
  case "$normalized" in
    /w|/w/*) printf '%s\n' "$normalized" ;;
    *)
      echo "workdir must remain inside /w: $candidate" >&2
      exit 2
      ;;
  esac
}

case "$workdir" in
  /w|/w/*) workdir="$(normalize_container_workdir "$workdir")" ;;
  /*)
    host_workdir="$(realpath -e -- "$workdir")"
    repo_root_real="$(realpath -e -- "$REPO_ROOT")"
    if [[ "$host_workdir" == "$repo_root_real" ]]; then
      workdir=/w
    elif [[ "$host_workdir" == "$repo_root_real/"* ]]; then
      workdir="$(normalize_container_workdir "/w/${host_workdir#"$repo_root_real/"}")"
    else
      echo "workdir must be inside $REPO_ROOT: $workdir" >&2
      exit 2
    fi
    ;;
  .) workdir=/w ;;
  *) workdir="$(normalize_container_workdir "/w/${workdir#./}")" ;;
esac

# tools/docker_image.py is the sole image-identity owner. A changed build input creates a
# new tag, making stale-image reuse impossible without duplicating the hash in shell.
if [ "$do_build" = 1 ] || [ "${YOLOMUX_TEST_IMAGE_REBUILD:-0}" = 1 ] || ! docker image inspect "$IMAGE" >/dev/null 2>&1; then
  echo "Building $IMAGE (this is cached after the first run)" >&2
  build_args=()
  go_mod_context="$(mktemp -d /tmp/yolomux-go-mod-context.XXXXXX)"
  go_mod_context_cleanup() {
    local context_status="$?"
    trap - EXIT
    if ! rm -rf -- "$go_mod_context"; then
      echo "Go module build context cleanup failed: $go_mod_context" >&2
      [ "$context_status" -ne 0 ] || context_status=1
    elif [ -e "$go_mod_context" ]; then
      echo "Go module build context remained after cleanup: $go_mod_context" >&2
      [ "$context_status" -ne 0 ] || context_status=1
    fi
    exit "$context_status"
  }
  trap go_mod_context_cleanup EXIT
  if ! python3 "$REPO_ROOT/tools/docker_image.py" --prepare-go-mod-cache "$go_mod_context"; then
    echo "No complete local Go module cache is available; the Docker builder will use its network." >&2
  fi
  build_context_args=(--build-context "go-mod-cache=$go_mod_context")
  if [ "$rootless_docker" = 0 ]; then
    build_args=(--build-arg "RUNNER_UID=$(id -u)" --build-arg "RUNNER_GID=$(id -g)")
  fi
  # Image construction is the only network-enabled phase: it resolves pinned dependencies and
  # bakes them into the image. The test container below is always network-disabled. Use Docker's
  # default builder network; host networking would expose the host network namespace to a build.
  docker build "${build_args[@]+"${build_args[@]}"}" \
    "${build_context_args[@]+"${build_context_args[@]}"}" \
    -f "$REPO_ROOT/docker/Dockerfile.test" \
    -t "$IMAGE" \
    "$REPO_ROOT" >&2
  build_status=$?
  trap - EXIT
  if ! rm -rf -- "$go_mod_context"; then
    echo "Go module build context cleanup failed: $go_mod_context" >&2
    [ "$build_status" -ne 0 ] || build_status=1
  elif [ -e "$go_mod_context" ]; then
      echo "Go module build context remained after cleanup: $go_mod_context" >&2
      [ "$build_status" -ne 0 ] || build_status=1
  fi
  if [ "$build_status" -ne 0 ]; then
    exit "$build_status"
  fi
fi

# Linked worktrees store their gitdir outside the worktree bind mount. Mount that common
# directory at the identical absolute path, read-only, so git reads work without restoring
# a shared writable index inside the container.
git_mount=()
worktree_mount=()
if [ -f "$REPO_ROOT/.git" ]; then
  # The read-only source mount can be owned by a different numeric UID (for example an NFS UID
  # on the host while this wrapper runs as the image's runner). Trust this exact mounted checkout
  # for metadata discovery without weakening Git's global safe-directory policy.
  git_common="$(git -c "safe.directory=$REPO_ROOT" -C "$REPO_ROOT" rev-parse --path-format=absolute --git-common-dir)"
  git_mount=(-v "$git_common:$git_common:ro")
  # The .git file can retain the alternate /home spelling of the same NFS checkout. Git resolves
  # that literal path before consulting commondir, so expose the same common metadata at both
  # canonical spellings instead of making tests care which mount spelling they received.
  git_common_alt="${git_common/\/nfs\/keivenc/\/home\/keivenc}"
  if [ "$git_common_alt" != "$git_common" ] && [ -d "$git_common_alt" ]; then
    git_mount+=(-v "$git_common_alt:$git_common_alt:ro")
  fi
  # Linked-worktree config stores the checkout's host path. Mount the checkout at
  # that path too, so Git can honor core.worktree inside the /w test mount.
  recorded_worktree="$(git -c "safe.directory=$REPO_ROOT" -C "$REPO_ROOT" config --get core.worktree || true)"
  if [ -n "$recorded_worktree" ] && [ "$recorded_worktree" != "$REPO_ROOT" ]; then
    worktree_mount=(-v "$REPO_ROOT:$recorded_worktree:ro")
  else
    worktree_mount=(-v "$REPO_ROOT:$REPO_ROOT:ro")
  fi
fi

agent_mounts=()
test_env=()
go_binary_mount=()
if [ -n "${YOLOMUX_GO_BINARY:-}" ]; then
  go_binary_host="${YOLOMUX_GO_BINARY}"
  case "$go_binary_host" in
    /w|/w/*) go_binary_host="$REPO_ROOT/${go_binary_host#/w/}" ;;
  esac
  if [ ! -x "$go_binary_host" ]; then
    echo "YOLOMUX_GO_BINARY is not an executable host path: $go_binary_host" >&2
    exit 2
  fi
  go_binary_mount=(-v "$go_binary_host:/tmp/yolomux-go-binary:ro")
  test_env+=(-e YOLOMUX_GO_BINARY=/tmp/yolomux-go-binary)
fi
# The complete forwarded-environment allowlist, in one place. pytest re-executes itself inside
# this container (see conftest.pytest_cmdline_main), and `docker run` passes through only the
# names listed here. A variable that gates a test and is missing from this list does not fail
# loudly: the node is silently skipped and the run still reports green. Any new admission flag
# must be added here in the same change that introduces it.
FORWARDED_TEST_ENV=(
  YOLOMUX_TEST_MOCK_TRANSCRIPTS
  YOLOMUX_WORKTREE_WRITER_TOKEN
  YOLOMUX_WORKTREE_WRITER_SLOT
  YOLOMUX_WORKTREE_WRITER_CONTAINER_SLOT
  YOLOMUX_LATENCY_CERTIFICATION
  YOLOMUX_CHAT_LATENCY_CERTIFICATION
  YOLOMUX_MEASURE_SYSTEM_STATUS
  YOLOMUX_STATS_APPEND_FLUSH_SECONDS
  YOLOMUX_AGENT_DOCKER_E2E
  YOLOMUX_CHECK_IN_CONTAINER
)
REAL_AGENT_ENV=(
  OPENAI_BASE_URL
  ANTHROPIC_BASE_URL
)
for forwarded_name in "${FORWARDED_TEST_ENV[@]}"; do
  if [ -n "${!forwarded_name:-}" ]; then
    test_env+=(-e "$forwarded_name")
  fi
done
if [ -n "${YOLOMUX_WORKTREE_WRITER_CONTAINER_SLOT:-}" ]; then
  test_env+=(-e "YOLOMUX_WORKTREE_WRITER_SLOT=$YOLOMUX_WORKTREE_WRITER_CONTAINER_SLOT")
elif [ -z "${YOLOMUX_WORKTREE_WRITER_SLOT:-}" ]; then
  test_env+=(-e "YOLOMUX_WORKTREE_WRITER_SLOT=/tmp/yolomux-writer-${BASHPID}")
fi
if [ "${YOLOMUX_AGENT_DOCKER_E2E:-0}" = 1 ]; then
  for forwarded_name in "${REAL_AGENT_ENV[@]}"; do
    if [ -n "${!forwarded_name:-}" ]; then
      test_env+=(-e "$forwarded_name")
    fi
  done
fi

validate_tmp_directory() {
  local candidate="$1"
  local label="$2"
  case "$candidate" in
    /tmp/*) ;;
    *) echo "$label must be a child of /tmp: $candidate" >&2; exit 2 ;;
  esac
  local normalized
  normalized="$(realpath -m -- "$candidate")"
  case "$normalized" in
    /tmp/*) ;;
    *) echo "$label resolves outside /tmp: $normalized" >&2; exit 2 ;;
  esac
  local component="$candidate"
  while [ "$component" != "/tmp" ]; do
    if [ -L "$component" ]; then
      echo "$label must not contain a symlink: $component" >&2
      exit 2
    fi
    component="$(dirname -- "$component")"
  done
  mkdir -p -- "$candidate"
  if [ -L "$candidate" ]; then
    echo "$label must not be a symlink: $candidate" >&2
    exit 2
  fi
  candidate="$(realpath -e -- "$candidate")"
  case "$candidate" in
    /tmp/*) ;;
    *) echo "$label resolves outside /tmp: $candidate" >&2; exit 2 ;;
  esac
  if [ ! -O "$candidate" ]; then
    echo "$label must be owned by the invoking user: $candidate" >&2
    exit 2
  fi
  chmod 700 -- "$candidate"
}

# Browser failure evidence must outlive the throwaway container. Mount one host /tmp directory at
# the same absolute path so the failure message remains actionable; arbitrary host paths are refused.
evidence_dir="${YOLOMUX_E2E_EVIDENCE_DIR:-}"
remove_empty_evidence_dir=0
if [ -z "$evidence_dir" ]; then
  test_root="${YOLOMUX_TEST_ROOT:-/tmp/yolomux-test-${BASHPID}-$(id -u)}"
  validate_tmp_directory "$test_root" "YOLOMUX_TEST_ROOT"
  evidence_dir="$(mktemp -d "$test_root/evidence.XXXXXX")"
  remove_empty_evidence_dir=1
else
  validate_tmp_directory "$evidence_dir" "YOLOMUX_E2E_EVIDENCE_DIR"
fi
# Rootless Docker maps the image's non-root UID to a subordinate host UID, so it cannot write a
# host-owned 0700 bind source. The parent created above is 0700 and therefore private to the caller;
# make only the explicitly mounted leaf writable so the container can preserve browser evidence.
if [ "$rootless_docker" = 1 ]; then
  chmod 777 -- "$evidence_dir"
else
  chmod 700 -- "$evidence_dir"
fi
agent_mounts+=(-v "$evidence_dir:$evidence_dir")
test_env+=(-e "YOLOMUX_E2E_EVIDENCE_DIR=$evidence_dir")

# Stamp the container with this gate run's owner label when tools/check.py minted a token, so its
# retirement probe can prove ownership by the exact token instead of the shared image ancestor - a
# foreign agent's container from the identical image then neither blocks nor falsely clears our
# certification. Absent the token (a bare `docker/run-tests.sh` invocation) the container carries no
# owner label and image-ancestor discovery remains the fallback.
owner_label=()
if [ -n "${YOLOMUX_CHECK_RUN_TOKEN:-}" ]; then
  owner_label+=(--label "yolomux.check.run=$YOLOMUX_CHECK_RUN_TOKEN")
fi

# --rm provides a fresh writable layer and HOME. Do not tmpfs-mount /home/runner: that
# would hide image-owned ~/.local/bin. --init reaps tmux/chromium children; the enlarged
# shared-memory segment prevents Chromium renderer crashes. Network access is disabled for
# test execution; the explicit dead proxies prevent libraries from bypassing that boundary.
set +e
docker run --rm --init \
  --network none \
  --shm-size=1g \
  "${owner_label[@]+"${owner_label[@]}"}" \
  -v "$REPO_ROOT:/w:ro" \
  "${git_mount[@]+"${git_mount[@]}"}" \
  "${worktree_mount[@]+"${worktree_mount[@]}"}" \
  "${agent_mounts[@]+"${agent_mounts[@]}"}" \
  "${go_binary_mount[@]+"${go_binary_mount[@]}"}" \
  -w "$workdir" \
  -e HOME=/home/runner \
  -e XDG_CONFIG_HOME=/home/runner/.config \
  -e XDG_DATA_HOME=/home/runner/.local/share \
  -e XDG_STATE_HOME=/home/runner/.local/state \
  -e CODEX_HOME=/home/runner/.codex \
  -e HTTP_PROXY=http://127.0.0.1:9 \
  -e HTTPS_PROXY=http://127.0.0.1:9 \
  -e ALL_PROXY=http://127.0.0.1:9 \
  -e http_proxy=http://127.0.0.1:9 \
  -e https_proxy=http://127.0.0.1:9 \
  -e all_proxy=http://127.0.0.1:9 \
  -e NO_PROXY=127.0.0.1,localhost \
  -e no_proxy=127.0.0.1,localhost \
  -e YOLOMUX_CHECK_IN_CONTAINER="${YOLOMUX_CHECK_IN_CONTAINER:-1}" \
  "${test_env[@]+"${test_env[@]}"}" \
  "$IMAGE" \
  "$@"
status=$?
set -e
if [ "$remove_empty_evidence_dir" = 1 ]; then
  if [ "$status" -ne 0 ]; then
    echo "Retaining non-empty E2E evidence directory: $evidence_dir" >&2
  else
    cleanup_succeeded=1
    if ! rm -rf -- "$evidence_dir"; then
      # A rootless engine maps the image runner to a subordinate host UID. The test container must
      # keep that non-root identity, but a child can leave a directory owned by that unmapped UID.
      # Use a disposable, network-disabled root container only for this exact cleanup mount; under
      # rootless Docker its container root maps back to the invoking host user.
      echo "Host evidence cleanup could not remove all entries; retrying inside a disposable cleanup container: $evidence_dir" >&2
      if docker run --rm --network none --user=0:0 -v "$evidence_dir:$evidence_dir" "$IMAGE" \
          sh -c 'find "$1" -mindepth 1 -depth -exec rm -rf -- {} +' cleanup "$evidence_dir"; then
        # The cleanup container cannot remove the bind-mount root while it is mounted. Remove the
        # empty host-side mountpoint after Docker has released it.
        if ! rm -rf -- "$evidence_dir"; then
          cleanup_succeeded=0
        fi
      else
        cleanup_succeeded=0
      fi
    fi
    if [ "$cleanup_succeeded" -eq 1 ] && [ -e "$evidence_dir" ]; then
      echo "E2E evidence directory remained after cleanup: $evidence_dir" >&2
      cleanup_succeeded=0
    fi
    if [ "$cleanup_succeeded" -eq 0 ]; then
      echo "Could not remove E2E evidence directory: $evidence_dir" >&2
      status=1
    fi
  fi
fi
exit "$status"
