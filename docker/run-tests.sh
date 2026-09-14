#!/bin/bash
# Run a command in a throwaway YOLOmux test container with its own HOME.
set -euo pipefail

REPO_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
IMAGE="${YOLOMUX_TEST_IMAGE:-$(python3 "$REPO_ROOT/tools/docker_image.py" --name)}"

# A rootless daemon maps container root to the invoking host user, but cannot map this directory
# service's high numeric UID into its subordinate-ID range. Build and run as container root there
# so the mounted worktree still has the caller's host ownership.
rootless_docker=0
if docker info --format '{{json .SecurityOptions}}' 2>/dev/null | grep -q 'name=rootless'; then
  rootless_docker=1
fi

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

# Accept either the mapped container path or a caller-relative worktree directory.
case "$workdir" in
  /w|/w/*) ;;
  /*)
    if [[ "$workdir" == "$REPO_ROOT" ]]; then
      workdir=/w
    elif [[ "$workdir" == "$REPO_ROOT/"* ]]; then
      workdir="/w/${workdir#"$REPO_ROOT/"}"
    else
      echo "workdir must be inside $REPO_ROOT: $workdir" >&2
      exit 2
    fi
    ;;
  .) workdir=/w ;;
  *) workdir="/w/${workdir#./}" ;;
esac

# tools/docker_image.py is the sole image-identity owner. A changed build input creates a
# new tag, making stale-image reuse impossible without duplicating the hash in shell.
if [ "$do_build" = 1 ] || [ "${YOLOMUX_TEST_IMAGE_REBUILD:-0}" = 1 ] || ! docker image inspect "$IMAGE" >/dev/null 2>&1; then
  echo "Building $IMAGE (this is cached after the first run)" >&2
  build_args=()
  if [ "$rootless_docker" = 0 ]; then
    build_args=(--build-arg "RUNNER_UID=$(id -u)" --build-arg "RUNNER_GID=$(id -g)")
  fi
  docker build "${build_args[@]+"${build_args[@]}"}" \
    -f "$REPO_ROOT/docker/Dockerfile.test" \
    -t "$IMAGE" \
    "$REPO_ROOT" >&2
fi

# Linked worktrees store their gitdir outside the worktree bind mount. Mount that common
# directory at the identical absolute path, read-only, so git reads work without restoring
# a shared writable index inside the container.
git_mount=()
worktree_mount=()
if [ -f "$REPO_ROOT/.git" ]; then
  git_common="$(git -C "$REPO_ROOT" rev-parse --path-format=absolute --git-common-dir)"
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
  recorded_worktree="$(git -C "$REPO_ROOT" config --get core.worktree || true)"
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
if [ -d /usr/share/fonts ]; then
  agent_mounts+=(-v "/usr/share/fonts:/usr/share/fonts:ro")
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

# Browser failure evidence must outlive the throwaway container. Mount one host /tmp
# directory at the same absolute path so the failure message remains actionable.
evidence_dir="${YOLOMUX_E2E_EVIDENCE_DIR:-}"
remove_empty_evidence_dir=0
if [ -z "$evidence_dir" ]; then
  test_root="${YOLOMUX_TEST_ROOT:-/tmp/yolomux-test-${BASHPID}-$(id -u)}"
  mkdir -p "$test_root"
  chmod 700 "$test_root"
  evidence_dir="$(mktemp -d "$test_root/evidence.XXXXXX")"
  remove_empty_evidence_dir=1
else
  mkdir -p "$evidence_dir"
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

run_user=()
if [ "$rootless_docker" = 1 ]; then
  run_user=(--user 0)
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
  "${run_user[@]+"${run_user[@]}"}" \
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
  rmdir "$evidence_dir" 2>/dev/null || true
fi
exit "$status"
