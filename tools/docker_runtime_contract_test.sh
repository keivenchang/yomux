#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

set -euo pipefail

source "$1"

fail() {
  echo "Docker runtime contract failed: $1" >&2
  exit 1
}

[ "$(product_env_print_present TEST_CONTAINER '')" = 1 ] || fail "test-container marker is absent"
[ -n "$(product_env_print BAZEL_CONTRACT '')" ] || fail "execution contract identity is absent"
[ ! -e /.dockerenv ] || fail "outer container filesystem is visible"
[ ! -e /w ] || fail "undeclared checkout is visible"
[ ! -e /home/runner ] || fail "outer HOME is visible"
[ ! -e /var/cache/yomux-bazel ] || fail "shared action cache is visible"
for slot in 0 1 2 3 4 5 6 7; do
  output_root="/var/cache/yomux-bazel-output-slot-$slot"
  [ ! -e "$output_root" ] || fail "persistent Bazel output root is visible: $output_root"
done
[ -f /opt/yomux-test-image.marker ] || fail "test-image marker is absent"
[ "$(cat /opt/yomux-test-image.marker)" = yomux-test-image-v1 ] || fail "wrong test image marker"
case "${HOME:-}" in
  ""|/home/runner|/tmp/*) ;;
  *) fail "host HOME leaked into the test action: ${HOME:-unset}" ;;
esac

for name in OPENAI_API_KEY ANTHROPIC_API_KEY GH_TOKEN GITHUB_TOKEN LINEAR_KEY; do
  if printenv "$name" >/dev/null 2>&1; then
    fail "credential environment variable was forwarded: $name"
  fi
done

for tool_root in /usr /bin /lib /lib64 /etc /opt; do
  source_probe="$tool_root/.yomux-docker-runtime-probe-$$"
  if (umask 077; : > "$source_probe") 2>/dev/null; then
    rm -f -- "$source_probe"
    fail "image tools are writable: $tool_root"
  fi
done
python3 - <<'PY_CAPABILITIES'
from pathlib import Path
status = dict(line.split(':', 1) for line in Path('/proc/self/status').read_text().splitlines())
for field in ('CapEff', 'CapPrm', 'CapInh', 'CapAmb'):
    assert int(status[field].strip(), 16) == 0, field
PY_CAPABILITIES

for cache_root in /opt/yomux-go-mod-cache /opt/yomux-bazel-repository-cache; do
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
