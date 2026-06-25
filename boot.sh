#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
default_port="${YOLOMUX_PORT:-7777}"
host="${YOLOMUX_HOST:-0.0.0.0}"
log_dir="${YOLOMUX_LOG_DIR:-/tmp}"
dev_mode="auto"
print_command=0
ports=()
python_bin="${PYTHON:-python3}"

usage() {
  cat <<'EOF'
Usage: boot.sh [--print-command] [--host HOST] [--log-dir DIR] [--dev|--no-dev] [--port PORT] [PORT ...]

Restart this checkout's YOLOmux server. Defaults to port 7777. Non-7777 ports use --dev by default.

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

export PATH="${HOME}/.local/bin:${HOME}/.local/node-v22.11.0-linux-x64/bin:${PATH:-}"
export TERM="${TERM:-xterm-256color}"
export PYTHONUNBUFFERED=1

extra_env=()
if [[ -n "${YOLOMUX_TEST_AUTH_BYPASS:-}" ]]; then
  extra_env+=("YOLOMUX_TEST_AUTH_BYPASS=${YOLOMUX_TEST_AUTH_BYPASS}")
fi

use_dev_mode() {
  local port="$1"
  case "$dev_mode" in
    always) return 0 ;;
    never) return 1 ;;
    auto) [[ "$port" != "7777" ]] ;;
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
  printf 'cd %q && exec env TERM=%q PYTHONUNBUFFERED=%q PATH=%q' "$repo_root" "$TERM" "$PYTHONUNBUFFERED" "$PATH"
  for item in "${extra_env[@]}"; do
    printf ' %q' "$item"
  done
  printf ' %q %q' "$python_bin" "${repo_root}/yolomux.py"
  for item in "${server_args[@]}"; do
    printf ' %q' "$item"
  done
  printf ' > %q 2>&1 < /dev/null' "$log_path"
}

python_detach_code='
import os
import subprocess
import sys

repo_root = sys.argv[1]
log_path = sys.argv[2]
separator = sys.argv.index("--")
env = os.environ.copy()
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
  local attempt
  for ((attempt = 0; attempt < 8; attempt++)); do
    if ! kill -0 "$pid" 2>/dev/null; then
      return
    fi
    sleep 1
  done
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
  for pid in "${existing_pids[@]}"; do
    wait_for_pid_exit "$pid"
  done
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
  log_path="$(log_path_for "$port")"
  build_server_args "$port"

  stop_port_listener "$port"

  (
    cd "$repo_root"
    launch_server "$log_path"
  )
  printf 'restarted port %s from %s; log: %s\n' "$port" "$repo_root" "$log_path"
  wait_for_port "$port"
}

if [[ "$print_command" -eq 1 ]]; then
  for port in "${ports[@]}"; do
    print_launch_command "$port"
  done
  exit 0
fi

for port in "${ports[@]}"; do
  restart_port "$port"
done
