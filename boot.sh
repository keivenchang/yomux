#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
source "$repo_root/tools/startup_common.sh"
if [[ "$(uname -s)" == "Darwin" ]]; then
  platform_default_port=8880
else
  platform_default_port=7110
fi
primary_port="${YOLOMUX_PORT:-$platform_default_port}"
# An explicit port names this launcher's primary owner. Do not let an inherited server's owner
# port redirect a separately configured test/dev launch; without YOLOMUX_PORT, retain the override.
if [[ -n "${YOLOMUX_PORT:-}" ]]; then
  background_owner_primary_port="$primary_port"
else
  background_owner_primary_port="${YOLOMUX_BACKGROUND_OWNER_PRIMARY_PORT:-$primary_port}"
fi
default_port="$primary_port"
host="${YOLOMUX_HOST:-0.0.0.0}"
log_dir="${YOLOMUX_LOG_DIR:-/tmp}"
restart_lock_base="${TMPDIR:-/tmp}"
dev_mode="auto"
print_command=0
check_assets=0
ports=()
python_bin="${PYTHON:-python3}"
server_shell="${SHELL:-$(command -v bash)}"

usage() {
  cat <<'EOF'
Usage: boot.sh [--print-command|--check-assets] [--host HOST] [--log-dir DIR] [--dev|--no-dev] [--port PORT] [PORT ...]

Restart this checkout's YOLOmux server. YOLOMUX_PORT selects the primary port; otherwise it defaults to 8880 on macOS and 7110 on Linux. Non-primary ports use --dev by default.

Examples:
  ./boot.sh
  ./boot.sh <dev-port>
  ./boot.sh --port <port-a> --port <port-b>
EOF
}

die() {
  printf 'error: %s\n' "$*" >&2
  exit 2
}

add_port() {
  local port="$1"
  if [[ ! "$port" =~ ^[0-9]+$ ]]; then
    die "invalid port: $port"
  fi
  ports+=("$port")
}

while [[ "$#" -gt 0 ]]; do
  case "$1" in
    --help|-h)
      usage
      exit 0
      ;;
    --print-command)
      print_command=1
      shift
      ;;
    --check-assets)
      check_assets=1
      shift
      ;;
    --host)
      [[ "$#" -ge 2 ]] || die "--host requires a value"
      host="$2"
      shift 2
      ;;
    --log-dir)
      [[ "$#" -ge 2 ]] || die "--log-dir requires a value"
      log_dir="$2"
      shift 2
      ;;
    --port)
      [[ "$#" -ge 2 ]] || die "--port requires a value"
      add_port "$2"
      shift 2
      ;;
    --dev)
      dev_mode="always"
      shift
      ;;
    --no-dev)
      dev_mode="never"
      shift
      ;;
    --)
      shift
      while [[ "$#" -gt 0 ]]; do
        add_port "$1"
        shift
      done
      ;;
    -*)
      die "unknown option: $1"
      ;;
    *)
      add_port "$1"
      shift
      ;;
  esac
done

if [[ "${#ports[@]}" -eq 0 ]]; then
  add_port "$default_port"
fi

path_entries=()
for path_entry in "${HOME}/.local/bin" "${HOME}/.local/node-v22.11.0-linux-x64/bin"; do
  [[ -d "$path_entry" ]] && path_entries+=("$path_entry")
done
IFS=: read -r -a inherited_path_entries <<< "${PATH:-}"
for path_entry in "${inherited_path_entries[@]}"; do
  [[ -d "$path_entry" ]] && path_entries+=("$path_entry")
done
PATH="$(IFS=:; printf '%s' "${path_entries[*]}")"
unset path_entries inherited_path_entries path_entry
export PATH
export TERM="${TERM:-xterm-256color}"
export PYTHONUNBUFFERED=1
export MALLOC_ARENA_MAX="${MALLOC_ARENA_MAX:-2}"

# YO!agent's Claude backend runs `claude` non-interactively. On macOS, the `claude`
# binary authenticates only via ANTHROPIC_API_KEY (or a Keychain login) and does NOT
# read primaryApiKey from ~/.claude.json the way the Linux build does, so export the
# stored primaryApiKey as ANTHROPIC_API_KEY when it is not already set. Exported (not
# passed on argv) so the key never appears in `ps`. Linux is excluded: its `claude`
# build already reads primaryApiKey from ~/.claude.json directly, and forcing the env
# var here collides with a claude.ai (OAuth) login stored in the same file.
# TODO: verify this is still accurate on the current macOS `claude` release.
if [[ "$(uname -s)" == "Darwin" && -z "${ANTHROPIC_API_KEY:-}" && -r "${HOME}/.claude.json" ]]; then
  ANTHROPIC_API_KEY="$("$python_bin" -c 'import json, os
try:
    print(json.load(open(os.path.expanduser("~/.claude.json"))).get("primaryApiKey") or "")
except Exception:
    print("")' 2>/dev/null || true)"
  [[ -n "$ANTHROPIC_API_KEY" ]] && export ANTHROPIC_API_KEY
fi

extra_env=()
extra_env+=("YOLOMUX_BACKGROUND_OWNER_PRIMARY_PORT=${background_owner_primary_port}")
extra_env+=("$(yolomux_default_server_optin)")
if [[ -n "${YOLOMUX_TEST_AUTH_BYPASS:-}" ]]; then
  extra_env+=("YOLOMUX_TEST_AUTH_BYPASS=${YOLOMUX_TEST_AUTH_BYPASS}")
fi

use_dev_mode() {
  local port="$1"
  case "$dev_mode" in
    always) return 0 ;;
    never) return 1 ;;
    auto) [[ "$port" != "$primary_port" ]] ;;
    *) die "invalid dev mode: $dev_mode" ;;
  esac
}

server_args=()
build_server_args() {
  local port="$1"
  # Every non-default instance receives an independent state family, so its
  # background owner must be itself rather than this launcher's primary port.
  extra_env[0]="YOLOMUX_BACKGROUND_OWNER_PRIMARY_PORT=${port}"
  server_args=(--host "$host" --port "$port" --dang --self-signed)
  if use_dev_mode "$port"; then
    server_args+=(--dev)
  fi
}

log_path_for() {
  local port="$1"
  printf '%s/yolomux-%s.log' "${log_dir%/}" "$port"
}

print_launch_command() {
  local port="$1"
  local log_path
  local isolation_exports
  isolation_exports="$("$python_bin" "$repo_root/tools/instance_isolation.py" --port "$port")" || die "port $port launch refused by instance-isolation preflight"
  printf '%s\n' "$isolation_exports"
  log_path="$(log_path_for "$port")"
  build_server_args "$port"
  if [[ "$(uname -s)" == "Darwin" ]]; then
    local launcher socket_name session_name
    launcher="$(yolomux_macos_server_launcher)"
    socket_name="$(yolomux_macos_server_tmux_socket)"
    session_name="$(yolomux_macos_server_tmux_session "$port")"
    printf 'launchctl bootout %q 2>/dev/null || true\n' "$(yolomux_macos_launch_target "$port")"
    printf 'tmux -L %q kill-session -t %q 2>/dev/null || true\n' "$socket_name" "=$session_name"
    printf 'tmux -L %q new-session -d -s %q -c %q /bin/bash -c %q bash %q %q %q %q %q %q %q' \
      "$socket_name" "$session_name" "$repo_root" "$launcher" "$repo_root" "$PATH" "$server_shell" "$python_bin" "$repo_root/yolomux.py" "$background_owner_primary_port" "$log_path"
    for item in "${server_args[@]}"; do
      printf ' %q' "$item"
    done
    printf '\n'
    return
  fi
  printf 'PATH=%s\n' "$PATH"
  printf 'cd %q\n' "$repo_root"
  if supports_setsid_f; then
    print_detach_prefix
    printf 'bash -c %q > /dev/null 2>&1 < /dev/null & disown\n' "$(shell_command_for "$log_path")"
  else
    print_python_detach_command "$log_path"
  fi
}

supports_setsid_f() {
  command -v setsid >/dev/null 2>&1 && setsid -f true >/dev/null 2>&1
}

shell_command_for() {
  local log_path="$1"
  # A server started from inside a tmux client inherits TMUX and would then operate on
  # that client socket instead of the user's shared default server. A deliberate custom
  # socket still travels through YOLOMUX_TMUX_SOCKET below.
  printf 'cd %q && exec env TMUX= TMUX_PANE= TERM=%q PYTHONUNBUFFERED=%q MALLOC_ARENA_MAX=%q PATH=%q' "$repo_root" "$TERM" "$PYTHONUNBUFFERED" "$MALLOC_ARENA_MAX" "$PATH"
  for item in "${extra_env[@]}"; do
    local key="${item%%=*}"
    local value="${item#*=}"
    printf ' %s=%q' "$key" "$value"
  done
  printf ' %q %q' "$python_bin" "${repo_root}/yolomux.py"
  for item in "${server_args[@]}"; do
    printf ' %q' "$item"
  done
  printf ' >> %q 2>&1 < /dev/null' "$log_path"
}

python_detach_code='
import os
import subprocess
import sys

repo_root = sys.argv[1]
log_path = sys.argv[2]
separator = sys.argv.index("--")
env = os.environ.copy()
env.pop("TMUX", None)
env.pop("TMUX_PANE", None)
for item in sys.argv[3:separator]:
    key, _, value = item.partition("=")
    if key:
        env[key] = value
cmd = sys.argv[separator + 1:]
with open(log_path, "ab", buffering=0) as log:
    subprocess.Popen(
        cmd,
        cwd=repo_root,
        stdin=subprocess.DEVNULL,
        stdout=log,
        stderr=subprocess.STDOUT,
        env=env,
        start_new_session=True,
    )
'

python_detach_args=()
build_python_detach_args() {
  local log_path="$1"
  python_detach_args=(
    "$python_bin"
    -c "$python_detach_code"
    "$repo_root"
    "$log_path"
    "TERM=$TERM"
    "PYTHONUNBUFFERED=$PYTHONUNBUFFERED"
    "MALLOC_ARENA_MAX=$MALLOC_ARENA_MAX"
    "PATH=$PATH"
  )
  for item in "${extra_env[@]}"; do
    python_detach_args+=("$item")
  done
  python_detach_args+=(
    --
    "$python_bin"
    "${repo_root}/yolomux.py"
  )
  for item in "${server_args[@]}"; do
    python_detach_args+=("$item")
  done
}

print_python_detach_command() {
  local log_path="$1"
  local item
  build_python_detach_args "$log_path"
  printf 'nohup'
  for item in "${python_detach_args[@]}"; do
    printf ' %q' "$item"
  done
  printf ' > /dev/null 2>&1 < /dev/null & disown\n'
}

print_detach_prefix() {
  if supports_setsid_f; then
    printf 'nohup setsid -f '
  else
    printf 'nohup '
  fi
}

# Delegates to the one shared scanner in startup_common.sh (sourced above), so
# boot.sh and the supported launcher never carry two copies of this logic.
port_listener_pids() {
  # The listener census walks /proc; it needs no external scanner. A failure here is one of the
  # typed classes it reports: this port's listening inode had no visible owner, several visible
  # owners, a fatal read, or the walk exceeded its time budget. Its stderr names which.
  yolomux_port_listener_pids "$1" || die "listener census could not identify a unique owner for port $1"
}

wait_for_pid_exit() {
  local pid="$1"
  local max_attempts="${2:-8}"
  local attempt
  for ((attempt = 0; attempt < max_attempts; attempt++)); do
    if ! kill -0 "$pid" 2>/dev/null; then
      return 0
    fi
    sleep 1
  done
  return 1
}

wait_for_port_free() {
  local port="$1"
  local max_attempts="${2:-8}"
  local attempt pids
  for ((attempt = 0; attempt < max_attempts; attempt++)); do
    pids="$(port_listener_pids "$port")" || return 2
    if [[ -z "$pids" ]]; then
      return 0
    fi
    sleep 1
  done
  return 1
}

stop_port_listener() {
  local port="$1"
  local existing_pids=()
  local pid pids wait_status
  pids="$(port_listener_pids "$port")" || return 2
  while IFS= read -r pid; do
    if [[ -n "$pid" ]]; then
      existing_pids+=("$pid")
    fi
  done <<< "$pids"
  if [[ "${#existing_pids[@]}" -eq 0 ]]; then
    return
  fi
  kill "${existing_pids[@]}"
  if wait_for_port_free "$port" 8; then
    return
  else
    wait_status="$?"
  fi
  if [[ "$wait_status" -eq 2 ]]; then
    return 2
  fi

  existing_pids=()
  pids="$(port_listener_pids "$port")" || return 2
  while IFS= read -r pid; do
    if [[ -n "$pid" ]]; then
      existing_pids+=("$pid")
    fi
  done <<< "$pids"
  if [[ "${#existing_pids[@]}" -gt 0 ]]; then
    printf 'port %s listener still alive after SIGTERM; sending SIGKILL to pid(s): %s\n' "$port" "${existing_pids[*]}" >&2
    kill -KILL "${existing_pids[@]}" 2>/dev/null || true
  fi
  if wait_for_port_free "$port" 4; then
    return
  else
    wait_status="$?"
  fi
  if [[ "$wait_status" -eq 2 ]]; then
    return 2
  fi
  pids="$(port_listener_pids "$port")" || return 2
  printf 'port %s still has listener pid(s) after stop: %s\n' "$port" "${pids//$'\n'/ }" >&2
  return 1
}

port_restart_lock_dir() {
  local port="$1"
  printf '%s/yolomux-restart-%s.lock' "$restart_lock_base" "$port"
}

acquire_port_restart_lock() {
  local port="$1"
  local lock_dir
  local owner_pid
  lock_dir="$(port_restart_lock_dir "$port")"
  if mkdir "$lock_dir" 2>/dev/null; then
    printf '%s\n' "$$" > "$lock_dir/pid"
    return 0
  fi
  owner_pid="$(cat "$lock_dir/pid" 2>/dev/null || true)"
  if [[ "$owner_pid" =~ ^[0-9]+$ ]] && ! kill -0 "$owner_pid" 2>/dev/null; then
    rm -f "$lock_dir/pid"
    rmdir "$lock_dir" 2>/dev/null || true
    if mkdir "$lock_dir" 2>/dev/null; then
      printf '%s\n' "$$" > "$lock_dir/pid"
      return 0
    fi
  fi
  die "a YOLOmux restart for port $port is already in progress"
}

release_port_restart_lock() {
  local port="$1"
  local lock_dir
  lock_dir="$(port_restart_lock_dir "$port")"
  rm -f "$lock_dir/pid"
  rmdir "$lock_dir" 2>/dev/null || true
}

# Probe the public liveness route, never an authenticated one. These probes run before any
# operator cookie exists, so polling a protected route made every restart log one server ERROR
# per probe (authentication_required), which then failed release soaks that require zero server
# log errors. /healthz is registered PUBLIC and answers 200 from the HTTP listener alone, so 200
# is the only acceptable code here: a 401 now means the auth boundary changed, not that the
# server is up.
wait_for_port() {
  local port="$1"
  local code
  local attempt
  for ((attempt = 0; attempt < 20; attempt++)); do
    code="$(curl -sk -o /dev/null -w '%{http_code}' "https://localhost:${port}/healthz" 2>/dev/null || true)"
    if [[ "$code" == "200" ]]; then
      printf 'port %s ready: /healthz -> %s\n' "$port" "$code"
      return 0
    fi
    sleep 1
  done
  printf 'port %s did not become ready: /healthz -> %s\n' "$port" "${code:-curl failed}" >&2
  return 1
}

verify_port_stable() {
  local port="$1"
  local code
  local pids
  local attempt
  for ((attempt = 0; attempt < 4; attempt++)); do
    sleep 1
    pids="$(port_listener_pids "$port" | tr '\n' ' ')"
    code="$(curl -sk -o /dev/null -w '%{http_code}' "https://localhost:${port}/healthz" 2>/dev/null || true)"
    if [[ -z "$pids" || "$code" != "200" ]]; then
      printf 'port %s became unstable after readiness: listener=%s /healthz -> %s\n' "$port" "${pids:-none}" "${code:-curl failed}" >&2
      return 1
    fi
  done
}

launch_server() {
  local log_path="$1"
  local shell_command
  if supports_setsid_f; then
    shell_command="$(shell_command_for "$log_path")"
    nohup setsid -f bash -c "$shell_command" > /dev/null 2>&1 < /dev/null &
  else
    build_python_detach_args "$log_path"
    nohup "${python_detach_args[@]}" > /dev/null 2>&1 < /dev/null &
  fi
  disown 2>/dev/null || true
}

# Single owner of the log-sink writability precondition, called from both the
# pre-ramp preflight and the in-lock repeat inside restart_port.
ensure_log_sink_writable() {
  local log_path="$1"
  mkdir -p "$log_dir" && : >> "$log_path"
}

# Prove every requested port's log sink resolves and is writable BEFORE the
# startup lock and the slow-ramp load gate. This check is cheap, deterministic
# and idempotent (mkdir -p plus an append); the load gate is expensive and
# host-dependent, so running the gate first lets host load mask an unwritable
# log directory behind a "system load did not recover" timeout. It runs before
# any existing listener is stopped, so a bad log sink can never cost the
# operator a running server.
preflight_log_sinks() {
  local port log_path
  for port in "${ports[@]}"; do
    log_path="$(log_path_for "$port")"
    ensure_log_sink_writable "$log_path" || die "log path is not writable: $log_path"
  done
}

restart_port() {
  local port="$1"
  local log_path
  if ! yolomux_validate_instance_isolation "$repo_root" "$python_bin" "$port"; then
    die "port $port launch refused by instance-isolation preflight"
  fi
  log_path="$(log_path_for "$port")"
  acquire_port_restart_lock "$port"
  # Repeat under the restart lock: the load gate between the preflight and here
  # can block for minutes, and the sink can be removed or made read-only in that
  # window. Still before stop_port_listener, so the listener survives either way.
  if ! ensure_log_sink_writable "$log_path"; then
    release_port_restart_lock "$port"
    die "log path is not writable: $log_path"
  fi
  build_server_args "$port"

  if [[ "$(uname -s)" == "Darwin" ]]; then
    yolomux_bootout_macos_server "$port"
  fi
  stop_port_listener "$port"

  # Fail closed: a wedged previous owner (alive but not listening, so the
  # listener kill above never reached it) or identity-verified stale children
  # of a dead owner must be resolved before another launch can stack on top.
  if ! "$python_bin" -m yolomux_lib.local_services.preflight --port "$port"; then
    release_port_restart_lock "$port"
    die "port $port launch preflight refused (wedged owner or stale tracked children; see message above)"
  fi

  printf '\n[%s] boot.sh launching port %s from %s\n' "$(date '+%Y-%m-%d %H:%M:%S %z')" "$port" "$repo_root" >> "$log_path"
  if [[ "$(uname -s)" == "Darwin" ]]; then
    yolomux_submit_macos_server "$repo_root" "$python_bin" "$server_shell" "$PATH" "$port" "$log_path" "$background_owner_primary_port" "${server_args[@]}"
  else
    (
      cd "$repo_root"
      launch_server "$log_path"
    )
  fi
  printf 'restarted port %s from %s; log: %s\n' "$port" "$repo_root" "$log_path"
  wait_for_port "$port"
  verify_port_stable "$port"
  release_port_restart_lock "$port"
}

ensure_xterm_assets() {
  local asset
  for asset in xterm.js xterm.css xterm-addon-unicode11.js; do
    [[ -s "$repo_root/static/vendor/$asset" ]] || die "tracked xterm vendor asset is missing: static/vendor/$asset"
  done
}

if [[ "$check_assets" -eq 1 ]]; then
  ensure_xterm_assets
  exit 0
fi

log_dir="$("$python_bin" "$repo_root/tools/instance_isolation.py" resolve-product-path YOLOMUX_LOG_DIR "$log_dir")" \
  || die "log directory refused by product-root policy"
restart_lock_base="$("$python_bin" "$repo_root/tools/instance_isolation.py" resolve-product-path TMPDIR "$restart_lock_base")" \
  || die "restart lock directory refused by product-root policy"
if ! yolomux_validate_root_environment "$repo_root" "$python_bin"; then
  die "startup root validation failed before listener mutation"
fi

if [[ "$print_command" -eq 1 ]]; then
  for port in "${ports[@]}"; do
    print_launch_command "$port"
  done
  exit 0
fi

preflight_log_sinks
yolomux_acquire_start_lock || die "startup lock unavailable"
trap yolomux_release_start_lock EXIT
yolomux_wait_for_system_capacity "$python_bin"
ensure_xterm_assets

for port in "${ports[@]}"; do
  yolomux_wait_for_system_capacity "$python_bin"
  restart_port "$port"
done
