#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

set -euo pipefail

fail() {
  echo "Docker runtime contract failed: $1" >&2
  exit 1
}

[ "${YOLOMUX_TEST_CONTAINER:-}" = 1 ] || fail "test-container marker is absent"
[ -f /.dockerenv ] || fail "generic Docker marker is absent"
[ -f /opt/yolomux-test-image.marker ] || fail "test-image marker is absent"
[ "$(cat /opt/yolomux-test-image.marker)" = yolomux-test-image-v1 ] || fail "wrong test image marker"
case "${HOME:-}" in
  ""|/home/runner|/tmp/*) ;;
  *) fail "host HOME leaked into the test action: ${HOME:-unset}" ;;
esac

for name in OPENAI_API_KEY ANTHROPIC_API_KEY GH_TOKEN GITHUB_TOKEN LINEAR_KEY; do
  if printenv "$name" >/dev/null 2>&1; then
    fail "credential environment variable was forwarded: $name"
  fi
done

source_probe="/w/.yolomux-docker-runtime-probe-$$"
if (umask 077; : > "$source_probe") 2>/dev/null; then
  rm -f -- "$source_probe"
  fail "/w is writable"
fi

for cache_root in /opt/yolomux-go-mod-cache /opt/yolomux-bazel-repository-cache; do
  [ -d "$cache_root" ] || fail "image cache is absent: $cache_root"
  cache_probe="$cache_root/.yolomux-docker-runtime-probe-$$"
  if mkdir -- "$cache_probe" 2>/dev/null; then
    rmdir -- "$cache_probe"
    fail "image cache is writable: $cache_root"
  fi
done

for interface_path in /sys/class/net/*; do
  interface="${interface_path##*/}"
  [ "$interface" = lo ] || fail "non-loopback network interface is present: $interface"
done

if timeout 2 python3 -c 'import socket; socket.create_connection(("198.51.100.1", 80), 1)' 2>/dev/null; then
  fail "network access is available inside the test container"
fi

exit 0
