#!/usr/bin/env bash

# Shared, source-only startup guards for boot.sh and yomux-dev-start.

source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)/product_environment.sh"

# Managed/operator launches deliberately watch the shared default tmux server.
# Test and ad hoc processes can still pass a private socket directly when they
# need isolation, but operator launchers must not inherit one accidentally.
yomux_default_server_optin() {
  printf '%s' "$(product_env_name TMUX_ALLOW_DEFAULT_SERVER)=1"
}

yomux_use_machine_tmux() {
  unset TMUX TMUX_PANE
  product_env_assign TMUX_SOCKET= TMUX_ALLOW_DEFAULT_SERVER=1
}

# Native executable selection is shared by operator and managed launches.
yomux_go_binary() {
  local checkout="$1" configured candidate
  configured="$(product_env_print GO_BINARY "")"
  if [[ -n "$configured" ]]; then
    [[ -f "$configured" && -x "$configured" ]] || { printf 'ERROR: Go binary is missing or unexecutable: %s\n' "$configured" >&2; return 2; }
    printf '%s' "$configured"
    return
  fi
  for candidate in "$checkout/bazel-bin/cmd/yomux-go/yomux-go_/yomux-go" "$checkout/bazel-bin/yomux-go" "$checkout/yomux-go"; do
    if [[ -f "$candidate" && -x "$candidate" ]]; then printf '%s' "$candidate"; return; fi
  done
  printf 'ERROR: Go binary is unavailable in %s; run tools/build_go.sh\n' "$checkout" >&2
  return 2
}

yomux_validate_backend() {
  local name value
  for name in "${PRODUCT_ENV_CANONICAL_PREFIX}BACKEND" "${PRODUCT_ENV_LEGACY_PREFIXES[0]}BACKEND"; do
    value="${!name-}"
    case "$value" in ""|go|auto) ;; *) printf 'ERROR: %s=%s is unsupported; the Python server is retired\n' "$name" "$value" >&2; return 2 ;; esac
  done
}

yomux_port_listener_pids() {
  local checkout binary
  checkout="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)" || return
  binary="$(yomux_go_binary "$checkout")" || return
  "$binary" --operator listeners --port "$1"
}

yomux_unique_listener_pid() {
  local pids
  pids="$(yomux_port_listener_pids "$1")" || return
  [[ "$pids" =~ ^[0-9]+$ ]] || { printf 'ERROR: expected one listener on port %s\n' "$1" >&2; return 1; }
  printf '%s' "$pids"
}

yomux_validate_instance_isolation() {
  local checkout="$1" binary
  binary="$(yomux_go_binary "$checkout")" || return
  if [[ -n "$(product_env_print ROW_PLAN_FILE "")" ]]; then
    "$binary" --operator validate-roots --root "$checkout" --plan-file "$(product_env_print ROW_PLAN_FILE)"
  else
    "$binary" --operator plan-direct --root "$checkout" --port "$3" >/dev/null
  fi
}

yomux_validate_root_environment() {
  local checkout="${1:-${repo_root:-}}" binary
  [[ -n "$checkout" ]] || checkout="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
  binary="$(yomux_go_binary "$checkout")" || return
  "$binary" --operator validate-roots --root "$checkout"
}

yomux_start_lock_path() {
  yomux_normalize_environment
  if [[ -n "$(product_env_print ROOT "")" ]]; then
    local lock_root
    product_env ROOT lock_root
    printf '%s' "${lock_root%/}/cache/start.lock"
    return
  fi
  printf '%s' "$(product_env_print START_LOCK_DIR "${XDG_CACHE_HOME:-$HOME/.cache}/yomux/start.lock")"
}

yomux_release_start_lock() {
  if [[ "${start_lock_acquired:-0}" -eq 1 ]]; then
    local lock_dir owner_pid
    lock_dir="$(yomux_start_lock_path)"
    owner_pid="$(cat "$lock_dir/pid" 2>/dev/null || true)"
    if [[ "$owner_pid" != "$BASHPID" ]]; then
      start_lock_acquired=0
      return 0
    fi
    rm -f "$lock_dir/pid"
    rmdir "$lock_dir" 2>/dev/null || true
    start_lock_acquired=0
  fi
}

yomux_acquire_start_lock() {
  yomux_normalize_environment
  local lock_dir owner_pid inherited_path inherited_pid
  inherited_path="$(product_env_print INHERITED_START_LOCK_PATH "")"
  inherited_pid="$(product_env_print INHERITED_START_LOCK_PID "")"
  # Consume the capability here; terminal and worker descendants never inherit it.
  product_env_unset INHERITED_START_LOCK_PATH
  product_env_unset INHERITED_START_LOCK_PID
  if ! yomux_validate_root_environment; then
    printf 'ERROR: startup root validation failed before lock acquisition\n' >&2
    return 1
  fi
  lock_dir="$(yomux_start_lock_path)"
  mkdir -p "$(dirname "$lock_dir")"
  owner_pid=""
  if mkdir "$lock_dir" 2>/dev/null; then
    printf '%s\n' "$BASHPID" > "$lock_dir/pid"
    start_lock_acquired=1
    return 0
  fi
  owner_pid="$(cat "$lock_dir/pid" 2>/dev/null || true)"
  # A cutover can source boot.sh in the shell that already holds this guard.
  # An inherited marker in another process never grants ownership.
  if [[ "${start_lock_acquired:-0}" -eq 1 && "$owner_pid" == "$BASHPID" ]]; then
    return 0
  fi
  # A direct child may borrow its live launcher's exact guard, never release it.
  if [[ "$inherited_path" == "$lock_dir" && "$inherited_pid" =~ ^[0-9]+$ && "$inherited_pid" == "$PPID" && "$owner_pid" == "$inherited_pid" ]] && kill -0 "$inherited_pid" 2>/dev/null; then
    start_lock_acquired=0
    return 0
  fi
  if [[ "$owner_pid" =~ ^[0-9]+$ ]] && ! kill -0 "$owner_pid" 2>/dev/null; then
    rm -f "$lock_dir/pid"
    rmdir "$lock_dir" 2>/dev/null || true
    if mkdir "$lock_dir" 2>/dev/null; then
      printf '%s\n' "$BASHPID" > "$lock_dir/pid"
      start_lock_acquired=1
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

yomux_macos_server_tmux() {
  env -u TMUX -u TMUX_PANE tmux "$@"
}

yomux_macos_server_tmux_session() {
  local port="$1"
  printf 'yolomux-%s' "$port"
}

yomux_bootout_macos_server() {
  local port="$1"
  local target session_name
  target="$(yomux_macos_launch_target "$port")"
  if launchctl print "$target" >/dev/null 2>&1; then
    launchctl bootout "$target"
  fi
  session_name="$(yomux_macos_server_tmux_session "$port")"
  if command -v tmux >/dev/null 2>&1 && yomux_macos_server_tmux has-session -t "=$session_name" 2>/dev/null; then
    yomux_macos_server_tmux kill-session -t "=$session_name"
  fi
}

# Capture launch controls in the caller before a retained tmux daemon can replace them.
# NUL-delimited assignments preserve spaces and newlines in explicit operator paths.
yomux_server_launch_environment() {
  local backend="$1" go_binary="$2"
  yomux_normalize_environment
  local assignments=("$(yomux_default_server_optin)" "$(product_env_name TMUX_SOCKET)=")
  if [[ "$backend" == "go" ]]; then
    if [[ -n "$go_binary" ]]; then
      assignments+=("$(product_env_name GO_BINARY)=$go_binary")
    fi
    assignments+=("$(product_env_name GO_START_REASON)=$(product_env_print GO_START_REASON "demand")")
    assignments+=("$(product_env_name CLAUDE_TRANSCRIPT_ROOT)=$(product_env_print CLAUDE_TRANSCRIPT_ROOT "$HOME/.claude/projects")")
    assignments+=("$(product_env_name CODEX_TRANSCRIPT_ROOT)=$(product_env_print CODEX_TRANSCRIPT_ROOT "$HOME/.codex/sessions")")
    assignments+=("$(product_env_name OPENCODE_DATABASE)=$(product_env_print OPENCODE_DATABASE "${XDG_DATA_HOME:-$HOME/.local/share}/opencode/opencode.db")")
  fi
  if [[ -n "$(product_env_print TEST_AUTH_BYPASS "")" ]]; then
    assignments+=("$(product_env_name TEST_AUTH_BYPASS)=$(product_env_print TEST_AUTH_BYPASS)")
  fi
  printf '%s\0' "${assignments[@]}"
}

yomux_macos_server_launcher() {
  # The plan JSON is a positional argument, not inherited state: a long-lived
  # tmux server cannot substitute a stale root or stale plan-file path. Both the
  # direct boot path and supported multi-row launcher use this same exec path.
  printf '%s' 'repo=$1; launch_path=$2; shell_bin=$3; native_binary=$4; script=$5; log_path=$6; plan_json=$7; env_count=$8; shift 8; cd "$repo" || exit 2; export PATH="$launch_path" SHELL="$shell_bin" TERM=xterm-256color MALLOC_ARENA_MAX=2 || exit 2; unset TMUX TMUX_PANE; source "$repo/tools/product_environment.sh" || exit 2; for env_suffix in GO_BINARY GO_START_REASON CLAUDE_TRANSCRIPT_ROOT CODEX_TRANSCRIPT_ROOT OPENCODE_DATABASE TEST_AUTH_BYPASS TMUX_SOCKET TMUX_ALLOW_DEFAULT_SERVER; do product_env_unset "$env_suffix"; done; export '"$(yomux_default_server_optin)"' || exit 2; case "$env_count" in ""|*[!0-9]*) exit 2 ;; esac; for ((env_index=0; env_index<env_count; env_index++)); do assignment=$1; shift; case "$assignment" in *=*) ;; *) exit 2 ;; esac; env_name=${assignment%%=*}; if [[ ! "$env_name" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]]; then exit 2; fi; export "$assignment" || exit 2; done; source "$repo/tools/startup_common.sh" || exit 2; yomux_normalize_environment; exec "$native_binary" --operator exec --root "$repo" --plan-json "$plan_json" -- "$native_binary" --launch --launch-plan-json "$plan_json" --root "$repo" "$@" >> "$log_path" 2>&1'
}

yomux_submit_macos_server() {
  yomux_normalize_environment
  local repo_root="$1"
  local native_binary="$2"
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
    done < <(yomux_server_launch_environment go "$(product_env_print GO_BINARY "")")
    env_count="${#launch_environment[@]}"
    set -- "${launch_environment[@]}" "$@"
  else
    printf 'invalid macOS launch environment count: %s\n' "${7:-}" >&2
    return 2
  fi
  local launcher session_name plan_json
  launcher="$(yomux_macos_server_launcher)"
  session_name="$(yomux_macos_server_tmux_session "$port")"
  if [[ -n "$(product_env_print ROW_PLAN_FILE "")" ]]; then
    plan_json="$(< "$(product_env_print ROW_PLAN_FILE)")"
  else
    plan_json="$("$native_binary" --operator plan-direct --root "$repo_root" --port "$port")" || return
  fi
  yomux_macos_server_tmux new-session -d -s "$session_name" -c "$repo_root" \
    /bin/bash -c "$launcher" bash "$repo_root" "$launch_path" "$shell_bin" "$native_binary" "$native_binary" "$log_path" "$plan_json" "$env_count" "$@"
}
