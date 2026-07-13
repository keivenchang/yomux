#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
source "$repo_root/tools/startup_common.sh"
if [[ "$(uname -s)" == "Darwin" ]]; then
  platform_default_port=8880
else
  platform_default_port=7770
fi
primary_port="${YOLOMUX_PORT:-$platform_default_port}"
background_owner_primary_port="${YOLOMUX_BACKGROUND_OWNER_PRIMARY_PORT:-$primary_port}"
default_port="$primary_port"
host="${YOLOMUX_HOST:-0.0.0.0}"
log_dir="${YOLOMUX_LOG_DIR:-/tmp}"
dev_mode="auto"
print_command=0
ports=()
python_bin="${PYTHON:-python3}"
server_shell="${SHELL:-$(command -v bash)}"

usage() {
  cat <<'EOF'
Usage: boot.sh [--print-command] [--host HOST] [--log-dir DIR] [--dev|--no-dev] [--port PORT] [PORT ...]

Restart this checkout's YOLOmux server. YOLOMUX_PORT selects the primary port; otherwise it defaults to 8880 on macOS and 7770 on Linux. Non-primary ports use --dev by default.

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
  log_path="$(log_path_for "$port")"
  build_server_args "$port"
  if [[ "$(uname -s)" == "Darwin" ]]; then
    printf 'launchctl bootout %q 2>/dev/null || true\n' "$(yolomux_macos_launch_target "$port")"
    printf 'launchctl submit -l %q -- /usr/bin/env YOLOMUX_BACKGROUND_OWNER_PRIMARY_PORT=%q MALLOC_ARENA_MAX=2 TMUX= TMUX_PANE= PATH=%q %q -u %q' \
      "local.yolomux.$port" "$background_owner_primary_port" "$PATH" "$python_bin" "$repo_root/yolomux.py"
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
    printf ' %q' "$item"
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

port_listener_pids() {
  local port="$1"
  if command -v ss >/dev/null 2>&1; then
    ss -ltnp "sport = :${port}" 2>/dev/null | sed -n 's/.*pid=\([0-9][0-9]*\).*/\1/p' | sort -u
    return
  fi
  if command -v lsof >/dev/null 2>&1; then
    lsof -nP -iTCP:"$port" -sTCP:LISTEN -t 2>/dev/null | sort -u
    return
  fi
  die "need ss or lsof to find the listener for port $port"
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
  local attempt
  for ((attempt = 0; attempt < max_attempts; attempt++)); do
    if [[ -z "$(port_listener_pids "$port")" ]]; then
      return 0
    fi
    sleep 1
  done
  return 1
}

stop_port_listener() {
  local port="$1"
  local existing_pids=()
  local pid
  while IFS= read -r pid; do
    if [[ -n "$pid" ]]; then
      existing_pids+=("$pid")
    fi
  done < <(port_listener_pids "$port")
  if [[ "${#existing_pids[@]}" -eq 0 ]]; then
    return
  fi
  kill "${existing_pids[@]}"
  if wait_for_port_free "$port" 8; then
    return
  fi

  existing_pids=()
  while IFS= read -r pid; do
    if [[ -n "$pid" ]]; then
      existing_pids+=("$pid")
    fi
  done < <(port_listener_pids "$port")
  if [[ "${#existing_pids[@]}" -gt 0 ]]; then
    printf 'port %s listener still alive after SIGTERM; sending SIGKILL to pid(s): %s\n' "$port" "${existing_pids[*]}" >&2
    kill -KILL "${existing_pids[@]}" 2>/dev/null || true
  fi
  if ! wait_for_port_free "$port" 4; then
    printf 'port %s still has listener pid(s) after stop: %s\n' "$port" "$(port_listener_pids "$port" | tr '\n' ' ')" >&2
    return 1
  fi
}

port_restart_lock_dir() {
  local port="$1"
  printf '%s/yolomux-restart-%s.lock' "${TMPDIR:-/tmp}" "$port"
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

wait_for_port() {
  local port="$1"
  local code
  local attempt
  for ((attempt = 0; attempt < 20; attempt++)); do
    code="$(curl -sk -o /dev/null -w '%{http_code}' "https://localhost:${port}/api/ping" 2>/dev/null || true)"
    if [[ "$code" == "200" || "$code" == "401" ]]; then
      printf 'port %s ready: /api/ping -> %s\n' "$port" "$code"
      return 0
    fi
    sleep 1
  done
  printf 'port %s did not become ready: /api/ping -> %s\n' "$port" "${code:-curl failed}" >&2
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
    code="$(curl -sk -o /dev/null -w '%{http_code}' "https://localhost:${port}/api/ping" 2>/dev/null || true)"
    if [[ -z "$pids" || ! "$code" =~ ^(200|401)$ ]]; then
      printf 'port %s became unstable after readiness: listener=%s /api/ping -> %s\n' "$port" "${pids:-none}" "${code:-curl failed}" >&2
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

restart_port() {
  local port="$1"
  local log_path
  acquire_port_restart_lock "$port"
  log_path="$(log_path_for "$port")"
  build_server_args "$port"

  if [[ "$(uname -s)" == "Darwin" ]]; then
    yolomux_bootout_macos_server "$port"
  fi
  stop_port_listener "$port"

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
  local xterm_js="$repo_root/node_modules/@xterm/xterm/lib/xterm.js"
  local xterm_css="$repo_root/node_modules/@xterm/xterm/css/xterm.css"
  local unicode_addon="$repo_root/node_modules/@xterm/addon-unicode11/lib/addon-unicode11.js"
  local packaged_js="$repo_root/static/xterm.js"
  local packaged_css="$repo_root/static/xterm.css"
  local packaged_addon="$repo_root/static/xterm-addon-unicode11.js"
  # yolomux serves /static/{xterm.js,xterm.css,xterm-addon-unicode11.js} by resolving @xterm/*
  # from node_modules (see yolomux_lib/common.py XTERM_ASSET_ROOTS). Require the complete runtime
  # set. VDI boxes have no compatible npm, so they use the pinned UMD files in static/ instead.
  [[ ( -f "$xterm_js" && -f "$xterm_css" && -f "$unicode_addon" ) \
    || ( -f "$packaged_js" && -f "$packaged_css" && -f "$packaged_addon" ) ]] && return 0
  if command -v npm >/dev/null 2>&1; then
    printf 'boot.sh: installing web-terminal assets (npm install) in %s ...\n' "$repo_root" >&2
    ( cd "$repo_root" && npm install --no-audit --no-fund --silent ) || true
    [[ -f "$xterm_js" && -f "$xterm_css" && -f "$unicode_addon" ]] && return 0
  fi
  if ! command -v curl >/dev/null 2>&1; then
    printf 'warn: curl unavailable and xterm runtime assets are missing; terminals cannot attach\n' >&2
    return 0
  fi
  printf 'boot.sh: downloading static xterm assets for this host ...\n' >&2
  mkdir -p "$repo_root/static"
  fetch_xterm_asset() {
    local url="$1"
    local destination="$2"
    local temporary="${destination}.$$"
    rm -f "$temporary"
    curl -fsSL --connect-timeout 10 --retry 2 "$url" -o "$temporary" \
      && [[ -s "$temporary" ]] \
      && mv -f "$temporary" "$destination"
  }
  fetch_xterm_asset https://cdn.jsdelivr.net/npm/@xterm/xterm@6.0.0/lib/xterm.js "$packaged_js" \
    && fetch_xterm_asset https://cdn.jsdelivr.net/npm/@xterm/xterm@6.0.0/css/xterm.css "$packaged_css" \
    && fetch_xterm_asset https://cdn.jsdelivr.net/npm/@xterm/addon-unicode11@0.9.0/lib/addon-unicode11.js "$packaged_addon" \
    || printf 'warn: static xterm asset download failed; terminals cannot attach\n' >&2
}

if [[ "$print_command" -eq 1 ]]; then
  for port in "${ports[@]}"; do
    print_launch_command "$port"
  done
  exit 0
fi

yolomux_acquire_start_lock || die "startup lock unavailable"
trap yolomux_release_start_lock EXIT
yolomux_wait_for_system_capacity "$python_bin"
ensure_xterm_assets

for port in "${ports[@]}"; do
  yolomux_wait_for_system_capacity "$python_bin"
  restart_port "$port"
done
