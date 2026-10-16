#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
source "$repo_root/tools/startup_common.sh"
yomux_normalize_environment
primary_port="$(product_env_print PORT "$(product_env_print DEFAULT_PORT "")")"
default_port="$primary_port"
host="$(product_env_print HOST "0.0.0.0")"
log_dir="$(product_env_print LOG_DIR "/tmp")"
restart_lock_base="${TMPDIR:-/tmp}"
dev_mode="auto"
backend="go"
cert_path=""
key_path=""
http_mode=0
print_command=0
check_assets=0
force_start=0
launched_pid=""
ports=()
python_bin="${PYTHON:-python3}"
server_shell="${SHELL:-$(command -v bash)}"

usage() {
  cat <<'EOF'
Usage: boot.sh [--print-command|--check-assets] [--ignore-load (legacy no-op)] [--force] [--golang|--deprecated-python|--backend go] [--http|--cert CERT --key KEY] [--host HOST] [--log-dir DIR] [--dev|--no-dev] [--port PORT] [PORT ...]

Restart this checkout's Go YOmux server. Use --deprecated-python to select the legacy Python backend. YOMUX_PORT or an explicit port argument selects the primary port; a no-argument launch requires YOMUX_DEFAULT_PORT. Non-primary ports use --dev by default.

Operator launches always use this Unix user's machine-wide tmux server, so every session visible to `tmux list-sessions` is eligible for discovery. The launcher clears inherited nested-tmux and private-socket routing; only isolated test fixtures should set YOMUX_TMUX_SOCKET explicitly.

Examples:
  ./boot.sh
  ./boot.sh <dev-port>
  ./boot.sh --force <port>
  ./boot.sh --deprecated-python <port>
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
    --ignore-load) shift ;; # Compatibility no-op; startup has no load-admission gate.
    --force)
      force_start=1
      shift
      ;;
    --golang)
      backend="go"
      shift
      ;;
    --deprecated-python)
      backend="python"
      shift
      ;;
    --backend)
      [[ "$#" -ge 2 ]] || die "--backend requires a value"
      case "$2" in
        go) backend="go" ;;
        python) die "the Python backend is deprecated for managed launches; use --deprecated-python" ;;
        *) die "invalid backend: $2 (expected go)" ;;
      esac
      shift 2
      ;;
    --host)
      [[ "$#" -ge 2 ]] || die "--host requires a value"
      host="$2"
      shift 2
      ;;
    --http)
      http_mode=1
      shift
      ;;
    --cert|--key)
      [[ "$#" -ge 2 ]] || die "$1 requires a path"
      if [[ "$1" == "--cert" ]]; then cert_path="$2"; else key_path="$2"; fi
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

if [[ -n "$cert_path" || -n "$key_path" ]]; then
  [[ "$http_mode" -eq 0 ]] || die "--http cannot be combined with --cert or --key"
  [[ -n "$cert_path" && -n "$key_path" ]] || die "--cert and --key must be supplied together"
  [[ -f "$cert_path" && -r "$cert_path" && -f "$key_path" && -r "$key_path" ]] || die "certificate and key must be readable files"
  # Detached launches change cwd to this checkout; retain the caller's paths.
  cert_path="$(cd -- "$(dirname -- "$cert_path")" && pwd -P)/$(basename -- "$cert_path")"
  key_path="$(cd -- "$(dirname -- "$key_path")" && pwd -P)/$(basename -- "$key_path")"
fi

if [[ "${#ports[@]}" -eq 0 ]]; then
  if [[ "$check_assets" -eq 1 ]]; then
    add_port "${primary_port:-7110}"
  else
    [[ -n "$primary_port" ]] || die "no port selected; set YOMUX_DEFAULT_PORT or pass an explicit port"
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
yomux_use_machine_tmux

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
go_binary=""
if [[ "$backend" == "go" ]]; then
  go_binary="$(product_env_print GO_BINARY "")"
  if [[ -z "$go_binary" ]]; then
    go_binary="$("$python_bin" "$repo_root/tools/go_binary.py" "$repo_root")"
  fi
fi
while IFS= read -r -d '' assignment; do
  extra_env+=("$assignment")
done < <(yomux_server_launch_environment "$backend" "$go_binary")

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
  server_args=(--host "$host" --port "$port" --dang)
  if [[ "$http_mode" -eq 1 ]]; then
    server_args+=(--http)
  elif [[ -n "$cert_path" ]]; then
    server_args+=(--cert "$cert_path" --key "$key_path")
  else
    server_args+=(--self-signed)
  fi
  if [[ "$force_start" -eq 1 ]]; then
    server_args+=(--force)
  fi
  if [[ "$backend" == "go" ]]; then
    server_args+=(--backend go)
  else
    server_args+=(--backend python)
  fi
  if [[ "$backend" == "python" ]] && use_dev_mode "$port"; then
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
    local launcher session_name plan_json env_count
    launcher="$(yomux_macos_server_launcher)"
    session_name="$(yomux_macos_server_tmux_session "$port")"
    plan_json="$("$python_bin" "$repo_root/tools/instance_isolation.py" plan-direct --port "$port")" || die "port $port launch refused by instance-isolation preflight"
    env_count="${#extra_env[@]}"
    printf 'launchctl bootout %q 2>/dev/null || true\n' "$(yomux_macos_launch_target "$port")"
    printf 'env -u TMUX -u TMUX_PANE tmux kill-session -t %q 2>/dev/null || true\n' "=$session_name"
    printf 'env -u TMUX -u TMUX_PANE tmux new-session -d -s %q -c %q /bin/bash -c %q bash %q %q %q %q %q %q %q %q' \
      "$session_name" "$repo_root" "$launcher" "$repo_root" "$PATH" "$server_shell" "$python_bin" "$repo_root/yomux.py" "$log_path" "$plan_json" "$env_count"
    for item in "${extra_env[@]}"; do
      printf ' %q' "$item"
    done
    for item in "${server_args[@]}"; do
      printf ' %q' "$item"
    done
    printf '\n'
    return
  fi
  printf 'PATH=%s\n' "$PATH"
  printf 'cd %q\n' "$repo_root"
  printf 'nohup setsid bash -c %q > /dev/null 2>&1 < /dev/null & disown\n' "$(shell_command_for "$log_path")"
}

shell_command_for() {
  local log_path="$1"
  # A server started from inside a tmux client inherits TMUX and would then operate on
  # that client socket instead of the user's shared default server. Managed launches
  # always clear YOMUX_TMUX_SOCKET below; isolated fixtures launch their own process
  # directly with a private socket instead of going through this operator path.
  printf 'cd %q && exec env TMUX= TMUX_PANE= TERM=%q PYTHONUNBUFFERED=%q MALLOC_ARENA_MAX=%q PATH=%q' "$repo_root" "$TERM" "$PYTHONUNBUFFERED" "$MALLOC_ARENA_MAX" "$PATH"
  for item in "${extra_env[@]}"; do
    local key="${item%%=*}"
    local value="${item#*=}"
    printf ' %s=%q' "$key" "$value"
  done
  printf ' %q %q' "$python_bin" "${repo_root}/yomux.py"
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
  yomux_port_listener_pids "$1" || die "listener census could not identify a unique owner for port $1"
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
  mkdir -p "$restart_lock_base" || die "cannot create YOmux restart-lock directory: $restart_lock_base"
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
  die "a YOmux restart for port $port is already in progress"
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

probe_healthz() {
  local port="$1"
  local scheme="https"
  if [[ "$http_mode" -eq 1 ]]; then
    scheme="http"
  fi
  curl -sk -o /dev/null -w '%{http_code}' "${scheme}://localhost:${port}/healthz" 2>/dev/null || true
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
    code="$(probe_healthz "$port")"
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
    code="$(probe_healthz "$port")"
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
  nohup setsid bash -c "$shell_command" > /dev/null 2>&1 < /dev/null &
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
# startup lock. This check is cheap, deterministic and idempotent (mkdir -p plus
# an append), and it runs before any existing listener is stopped so a bad log
# sink can never cost the operator a running server.
preflight_log_sinks() {
  local port log_path
  for port in "${ports[@]}"; do
    log_path="$(log_path_for "$port")"
    ensure_log_sink_writable "$log_path" || die "log path is not writable: $log_path"
  done
}

product_root_is_available() {
  local port="$1"
  (cd -- "$repo_root" && PYTHONDONTWRITEBYTECODE=1 "$python_bin" -c '
import os
import sys
from tools.instance_isolation import apply_early_instance_environment
from tools.instance_isolation import normalize_yomux_environment
from yomux_lib.infra.root_paths import resolve_yomux_roots, runtime_root
from yomux_lib.infra.instance_locks import instance_root_is_available
apply_early_instance_environment(["--port", sys.argv[1]])
values = normalize_yomux_environment(os.environ)
roots = resolve_yomux_roots(values, default_runtime_dir=runtime_root(environ=values))
raise SystemExit(0 if instance_root_is_available(roots) else 1)
' "$port")
}

restart_port() {
  local port="$1"
  local log_path
  local previous_listener_pids
  if ! yomux_validate_instance_isolation "$repo_root" "$python_bin" "$port"; then
    die "port $port launch refused by instance-isolation preflight"
  fi
  log_path="$(log_path_for "$port")"
  acquire_port_restart_lock "$port"
  previous_listener_pids="$(port_listener_pids "$port")"
  # Repeat under the restart lock in case the sink changed after preflight.
  if ! ensure_log_sink_writable "$log_path"; then
    release_port_restart_lock "$port"
    die "log path is not writable: $log_path"
  fi
  if [[ -n "$previous_listener_pids" && "$force_start" -ne 1 ]]; then
    release_port_restart_lock "$port"
    die "port $port already has listener(s) $previous_listener_pids; use --force to replace the existing YOmux instance"
  fi
  build_server_args "$port"
  launched_pid=""

  if [[ "$force_start" -eq 1 && "$(uname -s)" == "Darwin" ]]; then
    # The Python launch path performs the one identity-checked replacement.
    # Only remove the launchd wrapper here; a second force pass could kill a
    # different instance after the first replacement has already completed.
    yomux_bootout_macos_server "$port"
  fi

  printf '\n[%s] boot.sh launching port %s from %s\n' "$(date '+%Y-%m-%d %H:%M:%S %z')" "$port" "$repo_root" >> "$log_path"
  if [[ "$(uname -s)" == "Darwin" ]]; then
    yomux_submit_macos_server "$repo_root" "$python_bin" "$server_shell" "$PATH" "$port" "$log_path" "${#extra_env[@]}" "${extra_env[@]}" "${server_args[@]}"
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

log_dir="$("$python_bin" "$repo_root/tools/instance_isolation.py" resolve-product-path "$(product_env_name LOG_DIR)" "$log_dir")" \
  || die "log directory refused by product-root policy"
restart_lock_base="$("$python_bin" "$repo_root/tools/instance_isolation.py" resolve-product-path TMPDIR "$restart_lock_base")" \
  || die "restart lock directory refused by product-root policy"
if ! yomux_validate_root_environment "$repo_root" "$python_bin"; then
  die "startup root validation failed before listener mutation"
fi

if [[ "$print_command" -eq 1 ]]; then
  for port in "${ports[@]}"; do
    print_launch_command "$port"
  done
  exit 0
fi

if [[ "$force_start" -ne 1 ]]; then
  for port in "${ports[@]}"; do
    product_root_is_available "$port" || die "port $port launch refused: product root is already owned or unavailable"
  done
fi
preflight_log_sinks
yomux_acquire_start_lock || die "startup lock unavailable"
trap yomux_release_start_lock EXIT
ensure_xterm_assets

for port in "${ports[@]}"; do
  restart_port "$port"
done
