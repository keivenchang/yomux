#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

set -euo pipefail

assert_value() {
  local name="$1"
  local expected="$2"
  local actual="${!name-}"
  if [ "$actual" != "$expected" ]; then
    echo "$name must be $expected, got $actual" >&2
    exit 1
  fi
}

assert_value GOWORK off
assert_value GO111MODULE on
assert_value GOTOOLCHAIN local
mode="${YOMUX_BAZEL_EXECUTION-${YOLOMUX_BAZEL_EXECUTION:-docker}}"
if [ "$mode" = native ]; then
  assert_value GOPROXY https://proxy.golang.org,direct
  assert_value GOSUMDB sum.golang.org
  case "${GOMODCACHE-}" in
    /tmp/yolomux-bazel-run.*/gomodcache) ;;
    *) echo "GOMODCACHE must be private to this Bazel invocation: ${GOMODCACHE-}" >&2; exit 1 ;;
  esac
  assert_value YOMUX_TEST_CONTAINER 0
  assert_value YOLOMUX_TEST_CONTAINER 0
else
  assert_value GOPROXY off
  assert_value GOSUMDB off
  assert_value GOMODCACHE /opt/yomux-go-mod-cache
  assert_value YOMUX_TEST_CONTAINER 1
  assert_value YOLOMUX_TEST_CONTAINER 1
fi

case "${GOPATH-}" in
  /tmp/yolomux-bazel-run.*/go-path) ;;
  *) echo "GOPATH must be private to this Bazel invocation: ${GOPATH-}" >&2; exit 1 ;;
esac
case "${GOCACHE-}" in
  /tmp/yolomux-bazel-run.*/go-cache) ;;
  *) echo "GOCACHE must be private to this Bazel invocation: ${GOCACHE-}" >&2; exit 1 ;;
esac

common="${RUNFILES_DIR:-}/_main/tools/bazel_test_common.sh"
if [ ! -f "$common" ]; then
  common="${RUNFILES_DIR:-}/__main__/tools/bazel_test_common.sh"
fi
if [ ! -f "$common" ]; then
  common="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)/bazel_test_common.sh"
fi
if [ ! -f "$common" ]; then
  echo "Bazel environment helper is missing from runfiles" >&2
  exit 1
fi
export YOMUX_ROOT=/host/yomux
export YOMUX_TMUX_SOCKET=/host/yomux-tmux.sock
export YOLOMUX_ROOT=/host/yolomux
export YOLOMUX_OPENCODE_DATABASE=/host/opencode.db
export YOLOMUX_STATS_DATABASE=/host/stats.db
export YOLOMUX_CLAUDE_TRANSCRIPT_ROOT=/host/claude
export YOLOMUX_CODEX_TRANSCRIPT_ROOT=/host/codex-transcripts
export CODEX_HOME=/host/codex
export OPENCODE_HOME=/host/opencode
export OPENCODE_DATABASE=/host/opencode.db
export YOLOMUX_TMUX_SOCKET=/host/tmux.sock
export TMUX=/host/tmux,1,0
export TMUX_PANE=%1
export TMUX_TMPDIR=/host/tmux
export XDG_CONFIG_HOME=/host/xdg-config
export XDG_STATE_HOME=/host/xdg-state
export XDG_CACHE_HOME=/host/xdg-cache
export XDG_RUNTIME_DIR=/host/xdg-runtime
export CLAUDE_CONFIG_DIR=/host/claude-config
export TMPDIR=/host/tmp
export PYTHONPATH=/host/python
export ANTHROPIC_API_KEY=host-anthropic-key
export ANTHROPIC_AUTH_TOKEN=host-anthropic-token
export AWS_ACCESS_KEY_ID=host-aws-access
export AWS_SECRET_ACCESS_KEY=host-aws-secret
export AWS_SESSION_TOKEN=host-aws-session
export GH_TOKEN=host-gh-token
export GITHUB_TOKEN=host-github-token
export GOOGLE_API_KEY=host-google-key
export GOOGLE_APPLICATION_CREDENTIALS=/host/google-credentials.json
export HF_TOKEN=host-hf-token
export HUGGINGFACE_HUB_TOKEN=host-huggingface-token
export LINEAR_KEY=host-linear-key
export NGC_API_KEY=host-ngc-key
export OPENAI_API_KEY=host-openai-key
. "$common"
bazel_test_prepare_environment
for name in YOMUX_ROOT YOMUX_TMUX_SOCKET YOLOMUX_ROOT YOLOMUX_OPENCODE_DATABASE YOLOMUX_STATS_DATABASE YOLOMUX_CLAUDE_TRANSCRIPT_ROOT YOLOMUX_CODEX_TRANSCRIPT_ROOT OPENCODE_DATABASE OPENCODE_HOME YOLOMUX_TMUX_SOCKET TMUX TMUX_PANE TMUX_TMPDIR PYTHONPATH ANTHROPIC_API_KEY ANTHROPIC_AUTH_TOKEN AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY AWS_SESSION_TOKEN GH_TOKEN GITHUB_TOKEN GOOGLE_API_KEY GOOGLE_APPLICATION_CREDENTIALS HF_TOKEN HUGGINGFACE_HUB_TOKEN LINEAR_KEY NGC_API_KEY OPENAI_API_KEY; do
  if [ -n "${!name-}" ]; then
    echo "host input $name survived synthetic Bazel environment setup" >&2
    exit 1
  fi
done
if [ "$HOME" != "$TEST_TMPDIR/yolomux-agent-environment/home" ] || \
  [ "$XDG_CONFIG_HOME" != "$TEST_TMPDIR/yolomux-agent-environment/xdg-config" ] || \
  [ "$XDG_DATA_HOME" != "$TEST_TMPDIR/yolomux-agent-environment/xdg-data" ] || \
  [ "$XDG_STATE_HOME" != "$TEST_TMPDIR/yolomux-agent-environment/xdg-state" ] || \
  [ "$XDG_CACHE_HOME" != "$TEST_TMPDIR/yolomux-agent-environment/xdg-cache" ] || \
  [ "$CODEX_HOME" != "$TEST_TMPDIR/yolomux-agent-environment/codex-home" ] || \
  [ "$CLAUDE_CONFIG_DIR" != "$TEST_TMPDIR/yolomux-agent-environment/claude-config" ] || \
  [ "$XDG_RUNTIME_DIR" != "$TEST_TMPDIR/yolomux-agent-environment/xdg-runtime" ] || \
  [ "$TMPDIR" != "$YOLOMUX_BAZEL_TMPDIR" ]; then
  echo "Bazel test HOME/XDG/TMPDIR roots are not synthetic and action-owned" >&2
  exit 1
fi

exit 0
