#!/usr/bin/env bash

# Shared, source-only startup guards for boot.sh and yolo-dev-start.

yolomux_start_lock_path() {
  printf '%s' "${YOLOMUX_START_LOCK_DIR:-${XDG_CACHE_HOME:-$HOME/.cache}/yolomux/start.lock}"
}

yolomux_release_start_lock() {
  if [[ "${YOLOMUX_START_LOCK_ACQUIRED:-0}" -eq 1 ]]; then
    local lock_dir
    lock_dir="$(yolomux_start_lock_path)"
    rm -f "$lock_dir/pid"
    rmdir "$lock_dir" 2>/dev/null || true
    YOLOMUX_START_LOCK_ACQUIRED=0
  fi
}

yolomux_acquire_start_lock() {
  local lock_dir owner_pid
  lock_dir="$(yolomux_start_lock_path)"
  mkdir -p "$(dirname "$lock_dir")"
  owner_pid=""
  if mkdir "$lock_dir" 2>/dev/null; then
    printf '%s\n' "$$" > "$lock_dir/pid"
    YOLOMUX_START_LOCK_ACQUIRED=1
    return 0
  fi
  owner_pid="$(cat "$lock_dir/pid" 2>/dev/null || true)"
  if [[ "$owner_pid" =~ ^[0-9]+$ ]] && ! kill -0 "$owner_pid" 2>/dev/null; then
    rm -f "$lock_dir/pid"
    rmdir "$lock_dir" 2>/dev/null || true
    if mkdir "$lock_dir" 2>/dev/null; then
      printf '%s\n' "$$" > "$lock_dir/pid"
      YOLOMUX_START_LOCK_ACQUIRED=1
      return 0
    fi
  fi
  printf 'ERROR: another YOLOmux stack start is already in progress%s\n' "${owner_pid:+ (pid $owner_pid)}" >&2
  return 1
}

yolomux_system_load_snapshot() {
  local python_bin="$1"
  "$python_bin" - <<'PY'
import os
import platform

detected_cpus = max(1, os.cpu_count() or 1)
cpus = min(detected_cpus, 8) if platform.system() == "Darwin" else detected_cpus
load1, load5, _load15 = os.getloadavg()
ok = load1 <= cpus * 0.75 and load5 <= cpus * 2.0
print(f"load1={load1:.2f}/{cpus * 0.75:.2f} load5={load5:.2f}/{cpus * 2.0:.2f} cpu_budget={cpus}")
raise SystemExit(0 if ok else 1)
PY
}

yolomux_wait_for_system_capacity() {
  local python_bin="$1"
  local max_wait="${2:-${YOLOMUX_START_LOAD_WAIT_SECONDS:-300}}"
  local deadline=$((SECONDS + max_wait))
  local snapshot=""
  while [[ "$SECONDS" -lt "$deadline" ]]; do
    if snapshot="$(yolomux_system_load_snapshot "$python_bin")"; then
      printf 'ramp  capacity available: %s\n' "$snapshot"
      return 0
    fi
    printf 'ramp  waiting for system capacity: %s\n' "$snapshot"
    sleep 5
  done
  printf 'ERROR: system load did not recover within %ss; refusing to start another YOLOmux server\n' "$max_wait" >&2
  return 1
}

yolomux_macos_launch_target() {
  local port="$1"
  printf 'gui/%s/local.yolomux.%s' "$(id -u)" "$port"
}

yolomux_bootout_macos_server() {
  local port="$1"
  local target
  target="$(yolomux_macos_launch_target "$port")"
  if launchctl print "$target" >/dev/null 2>&1; then
    launchctl bootout "$target"
  fi
}

yolomux_macos_server_launcher() {
  printf '%s' 'repo=$1; launch_path=$2; shell_bin=$3; python_bin=$4; script=$5; primary_port=$6; shift 6; cd "$repo" && export PATH="$launch_path" SHELL="$shell_bin" PYTHONUNBUFFERED=1 TERM=xterm-256color MALLOC_ARENA_MAX=2 YOLOMUX_BACKGROUND_OWNER_PRIMARY_PORT="$primary_port" && unset TMUX TMUX_PANE && exec "$python_bin" -u "$script" "$@"'
}

yolomux_submit_macos_server() {
  local repo_root="$1"
  local python_bin="$2"
  local shell_bin="$3"
  local launch_path="$4"
  local port="$5"
  local log_path="$6"
  local primary_port="$7"
  shift 7
  local label="local.yolomux.$port"
  local launcher
  launcher="$(yolomux_macos_server_launcher)"
  launchctl submit -l "$label" -o "$log_path" -e "$log_path" -- \
    /bin/bash -c "$launcher" bash "$repo_root" "$launch_path" "$shell_bin" "$python_bin" "$repo_root/yolomux.py" "$primary_port" "$@"
}
