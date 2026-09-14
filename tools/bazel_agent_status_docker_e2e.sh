#!/bin/sh
set -eu

# `bazel run` provides the source workspace; `bazel test` runs in a sandbox and
# exposes the declared source tree through the main runfiles repository.
repo="${BUILD_WORKSPACE_DIRECTORY:-${TEST_SRCDIR:-}/__main__}"
if [ -f "$(readlink -f "$0")" ]; then
    repo="$(dirname "$(dirname "$(readlink -f "$0")")")"
fi
if [ ! -f "$repo/docker/Dockerfile.test" ]; then
    # Local Bazel tests execute from an execroot whose declared files are
    # flattened into runfiles. Reuse the source checkout when Bazel exposes it,
    # otherwise resolve the Dockerfile and invoke the canonical runner from the
    # runfiles tree.
    repo="${RUNFILES_DIR:-$(dirname "$0")}/__main__"
fi
if [ ! -f "$repo/docker/Dockerfile.test" ]; then
    echo "Docker E2E source tree is unavailable: $repo" >&2
    exit 1
fi
cd "$repo"
image="$(python3 tools/docker_image.py --name)"
if ! docker image inspect "$image" >/dev/null 2>&1; then
    build_args=""
    if ! docker info --format '{{json .SecurityOptions}}' 2>/dev/null | grep -q 'name=rootless'; then
        build_args="--build-arg RUNNER_UID=$(id -u) --build-arg RUNNER_GID=$(id -g)"
    fi
    # shellcheck disable=SC2086
    docker build --network=host $build_args -f docker/Dockerfile.test -t "$image" .
fi
docker run --rm --network none --entrypoint sh "$image" -lc \
    'claude --version && codex --version && opencode --version'
YOLOMUX_TEST_IMAGE="$image" YOLOMUX_AGENT_DOCKER_E2E=1 \
    YOLOMUX_CHECK_CONTAINER=0 YOLOMUX_CHECK_IN_CONTAINER=0 \
    YOLOMUX_WORKTREE_WRITER_SLOT="${TMPDIR:-/tmp}/yolomux-bazel-writer" \
    docker/run-tests.sh -- python3 -m pytest \
        tests/test_fake_llm_server.py tests/test_agent_status_docker.py -q
