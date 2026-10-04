#!/usr/bin/env bash

# Shared, source-only startup guards for boot.sh and yomux-dev-start.

yomux_normalize_environment() {
  local variable_name legacy_name new_name used_legacy=0
  while IFS= read -r variable_name; do
    case "$variable_name" in
      YOLOMUX_*)
        legacy_name="$variable_name"
        new_name="YOMUX_${legacy_name#YOLOMUX_}"
        used_legacy=1
        ;;
      YOMUX_*)
        new_name="$variable_name"
        legacy_name="YOLOMUX_${new_name#YOMUX_}"
        ;;
      *) continue ;;
    esac
    if [[ "${!new_name+x}" ]]; then
      printf -v "$legacy_name" '%s' "${!new_name}"
      export "$legacy_name"
    else
      printf -v "$new_name" '%s' "${!legacy_name}"
      export "$new_name"
    fi
  done < <(compgen -e)
  if [[ "$used_legacy" -eq 1 && "${YOMUX_ENVIRONMENT_NORMALIZED:-}" != "1" ]]; then
    printf 'WARNING: YOLOMUX_* environment names are deprecated; use YOMUX_*.\n' >&2
  fi
  YOMUX_ENVIRONMENT_NORMALIZED=1
  export YOMUX_ENVIRONMENT_NORMALIZED
}

# Managed/operator launches deliberately watch the shared default tmux server.
# Test and ad hoc processes can still pass a private socket directly when they
# need isolation, but operator launchers must not inherit one accidentally.
yomux_default_server_optin() {
  printf '%s' 'YOLOMUX_TMUX_ALLOW_DEFAULT_SERVER=1'
}

yomux_use_machine_tmux() {
  unset TMUX TMUX_PANE
  export YOMUX_TMUX_SOCKET=
  export YOLOMUX_TMUX_SOCKET=
  export YOMUX_TMUX_ALLOW_DEFAULT_SERVER=1
  export YOLOMUX_TMUX_ALLOW_DEFAULT_SERVER=1
}

# Route startup through the same fail-closed Python census as runtime probes.
yomux_port_listener_pids() {
  local port="$1"
  local listener_python="${YOLOMUX_PYTHON:-${python_bin:-${PYTHON:-python3}}}"
  local startup_repo_root
  startup_repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)" || return 2
  (cd -- "$startup_repo_root" && "$listener_python" -m yomux_lib.infra.listener_census "$port")
}

# Require EXACTLY one listener on a port and print its pid. The managed owner
# probe binds its ownership assertion to this unique pid; more than one (or none)
# is a hard failure, never a silent pick.
yomux_unique_listener_pid() {
  local port="$1" pids count
  pids="$(yomux_port_listener_pids "$port")" || return 2
  count="$(printf '%s\n' "$pids" | grep -c '[0-9]')"
  if [ "$count" -ne 1 ]; then
    printf 'port %s must have exactly one listener; found %s\n' "$port" "$count" >&2
    return 1
  fi
  printf '%s\n' "$pids" | grep '[0-9]'
}

yomux_validate_instance_isolation() {
  local repo_root="$1"
  local python_bin="$2"
  local port="$3"
  if [[ -n "${YOLOMUX_ROW_PLAN_FILE:-}" ]]; then
    "$python_bin" "$repo_root/tools/instance_isolation.py" exec --plan-file "$YOLOMUX_ROW_PLAN_FILE" -- \
      "$python_bin" "$repo_root/tools/instance_isolation.py" validate-roots >/dev/null
    return
  fi
  "$python_bin" "$repo_root/tools/instance_isolation.py" plan-direct --port "$port" >/dev/null
}

yomux_validate_root_environment() {
  local requested_repo_root="${1:-${repo_root:-}}"
  local requested_python_bin="${2:-${python_bin:-python3}}"
  if [[ -z "$requested_repo_root" ]]; then
    requested_repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
  fi
  "$requested_python_bin" "$requested_repo_root/tools/instance_isolation.py" validate-roots >/dev/null
}

yomux_start_lock_path() {
  if [[ -n "${YOLOMUX_ROOT:-}" ]]; then
    printf '%s' "${YOLOMUX_ROOT%/}/cache/start.lock"
    return
  fi
  printf '%s' "${YOLOMUX_START_LOCK_DIR:-${XDG_CACHE_HOME:-$HOME/.cache}/yomux/start.lock}"
}

yomux_release_start_lock() {
  if [[ "${YOLOMUX_START_LOCK_ACQUIRED:-0}" -eq 1 ]]; then
    local lock_dir owner_pid
    lock_dir="$(yomux_start_lock_path)"
    owner_pid="$(cat "$lock_dir/pid" 2>/dev/null || true)"
    if [[ "$owner_pid" != "$BASHPID" ]]; then
      YOLOMUX_START_LOCK_ACQUIRED=0
      return 0
    fi
    rm -f "$lock_dir/pid"
    rmdir "$lock_dir" 2>/dev/null || true
    YOLOMUX_START_LOCK_ACQUIRED=0
  fi
}

yomux_acquire_start_lock() {
  local lock_dir owner_pid
  if ! yomux_validate_root_environment; then
    printf 'ERROR: startup root validation failed before lock acquisition\n' >&2
    return 1
  fi
  lock_dir="$(yomux_start_lock_path)"
  mkdir -p "$(dirname "$lock_dir")"
  owner_pid=""
  if mkdir "$lock_dir" 2>/dev/null; then
    printf '%s\n' "$BASHPID" > "$lock_dir/pid"
    YOLOMUX_START_LOCK_ACQUIRED=1
    return 0
  fi
  owner_pid="$(cat "$lock_dir/pid" 2>/dev/null || true)"
  # A cutover can source boot.sh in the shell that already holds this guard.
  # An inherited marker in another process never grants ownership.
  if [[ "${YOLOMUX_START_LOCK_ACQUIRED:-0}" -eq 1 && "$owner_pid" == "$BASHPID" ]]; then
    return 0
  fi
  if [[ "$owner_pid" =~ ^[0-9]+$ ]] && ! kill -0 "$owner_pid" 2>/dev/null; then
    rm -f "$lock_dir/pid"
    rmdir "$lock_dir" 2>/dev/null || true
    if mkdir "$lock_dir" 2>/dev/null; then
      printf '%s\n' "$BASHPID" > "$lock_dir/pid"
      YOLOMUX_START_LOCK_ACQUIRED=1
      return 0
    fi
  fi
  printf 'ERROR: another YOmux stack start is already in progress%s\n' "${owner_pid:+ (pid $owner_pid)}" >&2
  return 1
}

yomux_wait_for_system_capacity() {
  printf 'ramp  startup CPU/memory admission disabled\n'
}

# The managed launcher still calls this compatibility entry point during preflight.
# Startup is intentionally permissive; this must not inspect or wait on host load.
yomux_system_load_snapshot() {
  printf 'startup CPU/memory admission disabled\n'
}

yomux_macos_launch_target() {
  local port="$1"
  printf 'gui/%s/local.yolomux.%s' "$(id -u)" "$port"
}

yomux_macos_server_tmux_socket() {
  printf '%s' "${YOLOMUX_MACOS_SERVER_TMUX_SOCKET:-yolomux-services}"
}

yomux_macos_server_tmux_session() {
  local port="$1"
  printf 'yolomux-%s' "$port"
}

yomux_bootout_macos_server() {
  local port="$1"
  local target socket_name session_name
  target="$(yomux_macos_launch_target "$port")"
  if launchctl print "$target" >/dev/null 2>&1; then
    launchctl bootout "$target"
  fi
  socket_name="$(yomux_macos_server_tmux_socket)"
  session_name="$(yomux_macos_server_tmux_session "$port")"
  if command -v tmux >/dev/null 2>&1 && tmux -L "$socket_name" has-session -t "=$session_name" 2>/dev/null; then
    tmux -L "$socket_name" kill-session -t "=$session_name"
  fi
}

# Capture launch controls in the caller before a retained tmux daemon can replace them.
# NUL-delimited assignments preserve spaces and newlines in explicit operator paths.
yomux_server_launch_environment() {
  local backend="$1" go_binary="$2"
  yomux_normalize_environment
  local assignments=("$(yomux_default_server_optin)" "YOLOMUX_TMUX_SOCKET=")
  if [[ "$backend" == "go" ]]; then
    if [[ -n "$go_binary" ]]; then
      assignments+=("YOLOMUX_GO_BINARY=$go_binary")
    fi
    assignments+=("YOLOMUX_GO_START_REASON=${YOLOMUX_GO_START_REASON:-demand}")
    assignments+=("YOLOMUX_CLAUDE_TRANSCRIPT_ROOT=${YOLOMUX_CLAUDE_TRANSCRIPT_ROOT:-$HOME/.claude/projects}")
    assignments+=("YOLOMUX_CODEX_TRANSCRIPT_ROOT=${YOLOMUX_CODEX_TRANSCRIPT_ROOT:-$HOME/.codex/sessions}")
    assignments+=("YOLOMUX_OPENCODE_DATABASE=${YOLOMUX_OPENCODE_DATABASE:-${XDG_DATA_HOME:-$HOME/.local/share}/opencode/opencode.db}")
  fi
  if [[ -n "${YOLOMUX_TEST_AUTH_BYPASS:-}" ]]; then
    assignments+=("YOLOMUX_TEST_AUTH_BYPASS=${YOLOMUX_TEST_AUTH_BYPASS}")
  fi
  printf '%s\0' "${assignments[@]}"
}

yomux_macos_server_launcher() {
  # The plan JSON is a positional argument, not inherited state: a long-lived
  # tmux server cannot substitute a stale root or stale plan-file path. Both the
  # direct boot path and supported multi-row launcher use this same exec path.
  printf '%s' 'repo=$1; launch_path=$2; shell_bin=$3; python_bin=$4; script=$5; log_path=$6; plan_json=$7; env_count=$8; shift 8; cd "$repo" || exit 2; export PATH="$launch_path" SHELL="$shell_bin" PYTHONUNBUFFERED=1 TERM=xterm-256color MALLOC_ARENA_MAX=2 || exit 2; unset TMUX TMUX_PANE; for env_suffix in GO_BINARY GO_START_REASON CLAUDE_TRANSCRIPT_ROOT CODEX_TRANSCRIPT_ROOT OPENCODE_DATABASE TEST_AUTH_BYPASS TMUX_SOCKET TMUX_ALLOW_DEFAULT_SERVER; do unset "YOMUX_$env_suffix" "YOLOMUX_$env_suffix"; done; export '"$(yomux_default_server_optin)"' || exit 2; case "$env_count" in ""|*[!0-9]*) exit 2 ;; esac; for ((env_index=0; env_index<env_count; env_index++)); do assignment=$1; shift; case "$assignment" in *=*) ;; *) exit 2 ;; esac; env_name=${assignment%%=*}; if [[ ! "$env_name" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]]; then exit 2; fi; export "$assignment" || exit 2; done; source "$repo/tools/startup_common.sh" || exit 2; yomux_normalize_environment; exec "$python_bin" "$repo/tools/instance_isolation.py" exec --plan-json "$plan_json" -- "$python_bin" -u "$script" "$@" >> "$log_path" 2>&1'
}

yomux_submit_macos_server() {
  local repo_root="$1"
  local python_bin="$2"
  local shell_bin="$3"
  local launch_path="$4"
  local port="$5"
  local log_path="$6"
  local env_count assignment
  if [[ "${7:-}" =~ ^[0-9]+$ ]]; then
    env_count="$7"
    shift 7
  elif [[ "$#" -eq 6 || "${7:-}" == --* ]]; then
    # Older managed callers pass server options immediately after the six launch fields.
    shift 6
    local launch_environment=()
    while IFS= read -r -d '' assignment; do
      launch_environment+=("$assignment")
    done < <(yomux_server_launch_environment go "${YOMUX_GO_BINARY-${YOLOMUX_GO_BINARY:-}}")
    env_count="${#launch_environment[@]}"
    set -- "${launch_environment[@]}" "$@"
  else
    printf 'invalid macOS launch environment count: %s\n' "${7:-}" >&2
    return 2
  fi
  local launcher socket_name session_name plan_json
  launcher="$(yomux_macos_server_launcher)"
  socket_name="$(yomux_macos_server_tmux_socket)"
  session_name="$(yomux_macos_server_tmux_session "$port")"
  if [[ -n "${YOLOMUX_ROW_PLAN_FILE:-}" ]]; then
    plan_json="$(< "$YOLOMUX_ROW_PLAN_FILE")"
  else
    plan_json="$("$python_bin" "$repo_root/tools/instance_isolation.py" plan-direct --port "$port")" || return
  fi
  tmux -L "$socket_name" new-session -d -s "$session_name" -c "$repo_root" \
    /bin/bash -c "$launcher" bash "$repo_root" "$launch_path" "$shell_bin" "$python_bin" "$repo_root/yomux.py" "$log_path" "$plan_json" "$env_count" "$@"
}
