#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
source "$repo_root/tools/startup_common.sh"
primary_port="${YOLOMUX_PORT:-${YOLOMUX_DEFAULT_PORT:-}}"
default_port="$primary_port"
host="${YOLOMUX_HOST:-0.0.0.0}"
log_dir="${YOLOMUX_LOG_DIR:-/tmp}"
restart_lock_base="${TMPDIR:-/tmp}"
dev_mode="auto"
print_command=0
check_assets=0
ignore_load=0
force_start=0
launched_pid=""
ports=()
python_bin="${PYTHON:-python3}"
server_shell="${SHELL:-$(command -v bash)}"

usage() {
  cat <<'EOF'
Usage: boot.sh [--print-command|--check-assets] [--ignore-load] [--force] [--host HOST] [--log-dir DIR] [--dev|--no-dev] [--port PORT] [PORT ...]

Restart this checkout's YOLOmux server. YOLOMUX_PORT or an explicit port argument selects the primary port; a no-argument launch requires YOLOMUX_DEFAULT_PORT. Non-primary ports use --dev by default.

Examples:
  ./boot.sh
  ./boot.sh <dev-port>
  ./boot.sh --force <port>
  ./boot.sh --ignore-load <dev-port>
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
    --ignore-load)
      ignore_load=1
      shift
      ;;
    --force)
      force_start=1
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
  if [[ "$check_assets" -eq 1 ]]; then
    add_port "${primary_port:-7110}"
  else
    [[ -n "$primary_port" ]] || die "no port selected; set YOLOMUX_DEFAULT_PORT or pass an explicit port"
    add_port "$default_port"
  fi
elif [[ -z "$primary_port" ]]; then
  primary_port="${ports[0]}"
  default_port="$primary_port"
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
  server_args=(--host "$host" --port "$port" --dang --self-signed)
  if [[ "$force_start" -eq 1 ]]; then
    server_args+=(--force)
  fi
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
      "$socket_name" "$session_name" "$repo_root" "$launcher" "$repo_root" "$PATH" "$server_shell" "$python_bin" "$repo_root/yolomux.py" "$log_path"
    for item in "${server_args[@]}"; do
      printf ' %q' "$item"
    done
    printf '\n'
    return
  fi
  printf 'PATH=%s\n' "$PATH"
  printf 'cd %q\n' "$repo_root"
  printf 'nohup bash -c %q > /dev/null 2>&1 < /dev/null & disown\n' "$(shell_command_for "$log_path")"
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

port_restart_lock_dir() {
  local port="$1"
  printf '%s/yolomux-restart-%s.lock' "$restart_lock_base" "$port"
}

acquire_port_restart_lock() {
  local port="$1"
  local lock_dir
  local owner_pid
  lock_dir="$(port_restart_lock_dir "$port")"
  mkdir -p "$restart_lock_base" || die "cannot create YOLOmux restart-lock directory: $restart_lock_base"
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
listener_pid_is_forbidden() {
  local listener_pid="$1"
  local forbidden_pids="$2"
  local forbidden_pid
  for forbidden_pid in $forbidden_pids; do
    if [[ "$forbidden_pid" == "$listener_pid" ]]; then
      return 0
    fi
  done
  return 1
}

launch_process_is_alive() {
  local launch_pid="$1"
  local process_state
  kill -0 "$launch_pid" 2>/dev/null || return 1
  process_state="$(ps -p "$launch_pid" -o stat= 2>/dev/null || true)"
  [[ -n "$process_state" && "$process_state" != Z* ]]
}

wait_for_port() {
  local port="$1"
  local forbidden_pids="${2:-}"
  local launch_pid="${3:-}"
  local log_path="${4:-}"
  local code
  local listener_pid
  local attempt
  for ((attempt = 0; attempt < 20; attempt++)); do
    if [[ -n "$launch_pid" ]] && ! launch_process_is_alive "$launch_pid"; then
      printf 'port %s launch process %s exited before readiness; see log: %s\n' "$port" "$launch_pid" "${log_path:-unavailable}" >&2
      return 1
    fi
    code="$(curl -sk -o /dev/null -w '%{http_code}' "https://localhost:${port}/healthz" 2>/dev/null || true)"
    if [[ "$code" == "200" ]]; then
      listener_pid="$(port_listener_pids "$port")"
      if [[ -n "$listener_pid" && "$listener_pid" != *$'\n'* && "$listener_pid" != *[!0-9]* ]]; then
        if listener_pid_is_forbidden "$listener_pid" "$forbidden_pids"; then
          :
        else
          printf 'port %s ready: listener=%s /healthz -> %s\n' "$port" "$listener_pid" "$code"
          return 0
        fi
      fi
    fi
    sleep 1
  done
  printf 'port %s did not become ready: listener=%s /healthz -> %s\n' "$port" "${listener_pid:-none}" "${code:-curl failed}" >&2
  return 1
}

verify_port_stable() {
  local port="$1"
  local forbidden_pids="${2:-}"
  local code
  local pids
  local stable_pid=""
  local attempt
  for ((attempt = 0; attempt < 4; attempt++)); do
    sleep 1
    pids="$(port_listener_pids "$port")"
    code="$(curl -sk -o /dev/null -w '%{http_code}' "https://localhost:${port}/healthz" 2>/dev/null || true)"
    if [[ -z "$pids" || "$pids" == *$'\n'* || "$pids" == *[!0-9]* || "$code" != "200" ]]; then
      printf 'port %s became unstable after readiness: listener=%s /healthz -> %s\n' "$port" "${pids:-none}" "${code:-curl failed}" >&2
      return 1
    fi
    if listener_pid_is_forbidden "$pids" "$forbidden_pids"; then
      printf 'port %s became unstable after readiness: replacement listener was not established: %s\n' "$port" "$pids" >&2
      return 1
    fi
    if [[ -n "$stable_pid" && "$stable_pid" != "$pids" ]]; then
      printf 'port %s changed listener after readiness: %s -> %s\n' "$port" "$stable_pid" "$pids" >&2
      return 1
    fi
    stable_pid="$pids"
  done
}

launch_server() {
  local log_path="$1"
  local shell_command
  shell_command="$(shell_command_for "$log_path")"
  nohup bash -c "$shell_command" > /dev/null 2>&1 < /dev/null &
  launched_pid="$!"
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
  local previous_listener_pids
  if ! yolomux_validate_instance_isolation "$repo_root" "$python_bin" "$port"; then
    die "port $port launch refused by instance-isolation preflight"
  fi
  log_path="$(log_path_for "$port")"
  acquire_port_restart_lock "$port"
  previous_listener_pids="$(port_listener_pids "$port")"
  # Repeat under the restart lock: the load gate between the preflight and here
  # can block for minutes, and the sink can be removed or made read-only in that
  # window. This remains before any replacement action.
  if ! ensure_log_sink_writable "$log_path"; then
    release_port_restart_lock "$port"
    die "log path is not writable: $log_path"
  fi
  if [[ -n "$previous_listener_pids" && "$force_start" -ne 1 ]]; then
    release_port_restart_lock "$port"
    die "port $port already has listener(s) $previous_listener_pids; use --force to replace the existing YOLOmux instance"
  fi
  build_server_args "$port"
  launched_pid=""

  if [[ "$force_start" -eq 1 && "$(uname -s)" == "Darwin" ]]; then
    # The Python launch path performs the one identity-checked replacement.
    # Only remove the launchd wrapper here; a second force pass could kill a
    # different instance after the first replacement has already completed.
    yolomux_bootout_macos_server "$port"
  fi

  printf '\n[%s] boot.sh launching port %s from %s\n' "$(date '+%Y-%m-%d %H:%M:%S %z')" "$port" "$repo_root" >> "$log_path"
  if [[ "$(uname -s)" == "Darwin" ]]; then
    yolomux_submit_macos_server "$repo_root" "$python_bin" "$server_shell" "$PATH" "$port" "$log_path" "${server_args[@]}"
  else
    launch_server "$log_path"
  fi
  printf 'restarted port %s from %s; log: %s\n' "$port" "$repo_root" "$log_path"
  if ! wait_for_port "$port" "$previous_listener_pids" "$launched_pid" "$log_path"; then
    release_port_restart_lock "$port"
    return 1
  fi
  if ! verify_port_stable "$port" "$previous_listener_pids"; then
    release_port_restart_lock "$port"
    return 1
  fi
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
if [[ "$ignore_load" -eq 1 ]]; then
  printf 'WARNING: --ignore-load requested; skipping only the startup CPU/load capacity wait\n' >&2
else
  yolomux_wait_for_system_capacity "$python_bin"
fi
ensure_xterm_assets

for port in "${ports[@]}"; do
  if [[ "$ignore_load" -ne 1 ]]; then
    yolomux_wait_for_system_capacity "$python_bin"
  fi
  restart_port "$port"
done
