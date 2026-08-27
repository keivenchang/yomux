#!/bin/bash
# Run a command in a throwaway YOLOmux test container with its own HOME.
set -euo pipefail

REPO_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
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
  docker build \
    --build-arg "RUNNER_UID=$(id -u)" \
    --build-arg "RUNNER_GID=$(id -g)" \
    -f "$REPO_ROOT/docker/Dockerfile.test" \
    -t "$IMAGE" \
    "$REPO_ROOT" >&2
fi

# Linked worktrees store their gitdir outside the worktree bind mount. Mount that common
# directory at the identical absolute path, read-only, so git reads work without restoring
# a shared writable index inside the container.
git_mount=()
if [ -f "$REPO_ROOT/.git" ]; then
  git_common="$(git -C "$REPO_ROOT" rev-parse --path-format=absolute --git-common-dir)"
  git_mount=(-v "$git_common:$git_common:ro")
fi

agent_mounts=()
claude_bin="$(command -v claude 2>/dev/null || true)"
if [ -n "$claude_bin" ]; then
  agent_mounts+=(-v "$(realpath "$claude_bin"):/usr/local/bin/claude:ro")
fi
if [ -d /usr/share/fonts ]; then
  agent_mounts+=(-v "/usr/share/fonts:/usr/share/fonts:ro")
fi

codex_bin="$(command -v codex 2>/dev/null || true)"
if [ -n "$codex_bin" ]; then
  codex_pkg="$(realpath "$codex_bin")"
  codex_pkg="${codex_pkg%/bin/*}"
  agent_mounts+=(-v "$codex_pkg:/opt/agent-pkg/codex:ro")
fi

# The complete forwarded-environment allowlist, in one place. pytest re-executes itself inside
# this container (see conftest.pytest_cmdline_main), and `docker run` passes through only the
# names listed here. A variable that gates a test and is missing from this list does not fail
# loudly: the node is silently skipped and the run still reports green. Any new admission flag
# must be added here in the same change that introduces it.
FORWARDED_TEST_ENV=(
  YOLOMUX_TEST_MOCK_TRANSCRIPTS
  YOLOMUX_WORKTREE_WRITER_TOKEN
  YOLOMUX_LATENCY_CERTIFICATION
  YOLOMUX_CHAT_LATENCY_CERTIFICATION
  YOLOMUX_MEASURE_SYSTEM_STATUS
  YOLOMUX_STATS_APPEND_FLUSH_SECONDS
)
test_env=()
for forwarded_name in "${FORWARDED_TEST_ENV[@]}"; do
  if [ -n "${!forwarded_name:-}" ]; then
    test_env+=(-e "$forwarded_name")
  fi
done

# Browser failure evidence must outlive the throwaway container. Mount one host /tmp
# directory at the same absolute path so the failure message remains actionable.
evidence_dir="${YOLOMUX_E2E_EVIDENCE_DIR:-}"
remove_empty_evidence_dir=0
if [ -z "$evidence_dir" ]; then
  evidence_dir="$(mktemp -d /tmp/yolomux-e2e-browser-evidence.XXXXXX)"
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

# --rm provides a fresh writable layer and HOME. Do not tmpfs-mount /home/runner: that
# would hide image-owned ~/.local/bin. --init reaps tmux/chromium children; the enlarged
# shared-memory segment prevents Chromium renderer crashes.
set +e
docker run --rm --init \
  --shm-size=1g \
  "${owner_label[@]+"${owner_label[@]}"}" \
  -v "$REPO_ROOT:/w" \
  "${git_mount[@]+"${git_mount[@]}"}" \
  "${agent_mounts[@]+"${agent_mounts[@]}"}" \
  -w "$workdir" \
  -e HOME=/home/runner \
  -e YOLOMUX_CHECK_IN_CONTAINER=1 \
  "${test_env[@]+"${test_env[@]}"}" \
  "$IMAGE" \
  "$@"
status=$?
set -e
if [ "$remove_empty_evidence_dir" = 1 ]; then
  rmdir "$evidence_dir" 2>/dev/null || true
fi
exit "$status"
