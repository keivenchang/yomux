#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

set -euo pipefail

repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
port="${YOLOMUX_DEV1_PORT:-8001}"
host="${YOLOMUX_HOST:-0.0.0.0}"
log_path="${YOLOMUX_DEV1_LOG:-/tmp/yolomux.dev1.8001.log}"
unit_name="yolomux-dev1-${port}"

export PATH="${HOME}/.local/bin:${PATH:-}"
export TERM="${TERM:-xterm-256color}"
export PYTHONUNBUFFERED=1
extra_env=()
if [[ -n "${YOLOMUX_TEST_AUTH_BYPASS:-}" ]]; then
  extra_env+=("YOLOMUX_TEST_AUTH_BYPASS=${YOLOMUX_TEST_AUTH_BYPASS}")
fi

if [[ "${1:-}" == "--help" ]]; then
  printf 'Usage: %s [--print-command]\n' "$0"
  printf 'Restart YOLOmux dev1 on HTTPS port %s with --dang and --self-signed.\n' "$port"
  exit 0
fi

if [[ "${1:-}" == "--print-command" ]]; then
  printf 'PATH=%s\n' "$PATH"
  printf 'cd %q\n' "$repo_root"
  printf 'env TERM=%q PYTHONUNBUFFERED=%q PATH=%q' "$TERM" "$PYTHONUNBUFFERED" "$PATH"
  for item in "${extra_env[@]}"; do
    printf ' %q' "$item"
  done
  printf ' /usr/bin/python3 %q --host %q --port %q --dang --self-signed\n' "${repo_root}/yolomux.py" "$host" "$port"
  exit 0
fi

stop_port_listener() {
  mapfile -t existing_pids < <(ss -ltnp "sport = :${port}" 2>/dev/null | sed -n 's/.*pid=\([0-9][0-9]*\).*/\1/p' | sort -u)
  if [[ "${#existing_pids[@]}" -eq 0 ]]; then
    return
  fi
  kill "${existing_pids[@]}"
  for pid in "${existing_pids[@]}"; do
    timeout 8 tail --pid="$pid" -f /dev/null 2>/dev/null || true
  done
}

cd "$repo_root"

systemctl --user stop "$unit_name" 2>/dev/null || true
stop_port_listener
if systemd-run --user --quiet --collect --unit="$unit_name" --working-directory="$repo_root" \
  env TERM="$TERM" PYTHONUNBUFFERED="$PYTHONUNBUFFERED" PATH="$PATH" "${extra_env[@]}" \
  /usr/bin/python3 "${repo_root}/yolomux.py" --host "$host" --port "$port" --dang --self-signed
then
  printf 'Restarted %s on https://localhost:%s/; journal: journalctl --user -u %s -f\n' "$unit_name" "$port" "$unit_name"
  exit 0
fi

stop_port_listener

setsid nohup env TERM="$TERM" PYTHONUNBUFFERED="$PYTHONUNBUFFERED" PATH="$PATH" "${extra_env[@]}" \
  /usr/bin/python3 "${repo_root}/yolomux.py" --host "$host" --port "$port" --dang --self-signed \
  > "$log_path" 2>&1 < /dev/null &

printf 'Restarted %s with fallback pid %s on https://localhost:%s/; log: %s\n' "$unit_name" "$!" "$port" "$log_path"
