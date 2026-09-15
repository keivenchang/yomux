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
assert_value GOPROXY off
assert_value GOSUMDB off
assert_value GOMODCACHE /opt/yolomux-go-mod-cache
assert_value YOLOMUX_TEST_CONTAINER 1

case "${GOPATH-}" in
  /tmp/yolomux-bazel-run.*/go-path) ;;
  *) echo "GOPATH must be private to this Bazel invocation: ${GOPATH-}" >&2; exit 1 ;;
esac
case "${GOCACHE-}" in
  /tmp/yolomux-bazel-run.*/go-cache) ;;
  *) echo "GOCACHE must be private to this Bazel invocation: ${GOCACHE-}" >&2; exit 1 ;;
esac

exit 0
