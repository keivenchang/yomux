#!/usr/bin/env bash

# Shared, source-only startup guards for boot.sh and yolo-dev-start.

# A server deliberately watches the shared default tmux server; test and ad hoc
# processes must not inherit that authority merely by omitting a socket target.
yolomux_default_server_optin() {
  printf '%s' 'YOLOMUX_TMUX_ALLOW_DEFAULT_SERVER=1'
}

# Route startup through the same fail-closed Python census as runtime probes.
yolomux_port_listener_pids() {
  local port="$1"
  local listener_python="${YOLOMUX_PYTHON:-${python_bin:-${PYTHON:-python3}}}"
  local startup_repo_root
  startup_repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)" || return 2
  (cd -- "$startup_repo_root" && "$listener_python" -m yolomux_lib.infra.listener_census "$port")
}

# Require EXACTLY one listener on a port and print its pid. The managed owner
# probe binds its ownership assertion to this unique pid; more than one (or none)
# is a hard failure, never a silent pick.
yolomux_unique_listener_pid() {
  local port="$1" pids count
  pids="$(yolomux_port_listener_pids "$port")" || return 2
  count="$(printf '%s\n' "$pids" | grep -c '[0-9]')"
  if [ "$count" -ne 1 ]; then
    printf 'port %s must have exactly one listener; found %s\n' "$port" "$count" >&2
    return 1
  fi
  printf '%s\n' "$pids" | grep '[0-9]'
}

yolomux_validate_instance_isolation() {
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

yolomux_validate_root_environment() {
  local requested_repo_root="${1:-${repo_root:-}}"
  local requested_python_bin="${2:-${python_bin:-python3}}"
  if [[ -z "$requested_repo_root" ]]; then
    requested_repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
  fi
  "$requested_python_bin" "$requested_repo_root/tools/instance_isolation.py" validate-roots >/dev/null
}

yolomux_start_lock_path() {
  if [[ -n "${YOLOMUX_ROOT:-}" ]]; then
    printf '%s' "${YOLOMUX_ROOT%/}/cache/start.lock"
    return
  fi
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
  if ! yolomux_validate_root_environment; then
    printf 'ERROR: startup root validation failed before lock acquisition\n' >&2
    return 1
  fi
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
import math
import os
import platform
import time

detected_cpus = max(1, os.cpu_count() or 1)
try:
    schedulable_cpus = max(1, len(os.sched_getaffinity(0)))
except (AttributeError, OSError):
    schedulable_cpus = detected_cpus
cpus = min(schedulable_cpus, 8) if platform.system() == "Darwin" else schedulable_cpus
load1, load5, _load15 = os.getloadavg()


def cpu_idle_fraction() -> float:
    """Sample actual CPU idle time instead of treating blocked I/O tasks as CPU demand."""
    if platform.system() != "Linux":
        return 1.0

    def sample() -> tuple[int, int]:
        fields = open("/proc/stat", encoding="utf-8").readline().split()[1:]
        values = [int(value) for value in fields]
        total = sum(values)
        idle = values[3] + (values[4] if len(values) > 4 else 0)
        return total, idle

    total_before, idle_before = sample()
    time.sleep(0.25)
    total_after, idle_after = sample()
    total_delta = total_after - total_before
    return (idle_after - idle_before) / total_delta if total_delta > 0 else 1.0


def defender_d_state_tasks() -> int:
    """Count Defender tasks that inflate Linux load while they are blocked in the kernel."""
    if platform.system() != "Linux":
        return 0
    defender_names = {"sensecm", "senseimdscollector", "msmpeng", "mdatp"}
    count = 0
    for entry in os.scandir("/proc"):
        if not entry.name.isdigit():
            continue
        try:
            status = open(f"/proc/{entry.name}/status", encoding="utf-8").read().splitlines()
        except OSError:
            continue
        fields = dict(line.split(":", 1) for line in status if ":" in line)
        name = fields.get("Name", "").strip().lower()
        state = fields.get("State", "").lstrip()[:1]
        if name in defender_names and state == "D":
            count += 1
    return count


idle_fraction = cpu_idle_fraction()
defender_d_tasks = defender_d_state_tasks()
try:
    requested_discount = float(os.environ.get("YOLOMUX_START_LOAD_DISCOUNT_CORES", "0"))
except ValueError:
    requested_discount = -1.0
if not math.isfinite(requested_discount) or requested_discount < 0:
    print("invalid YOLOMUX_START_LOAD_DISCOUNT_CORES", flush=True)
    raise SystemExit(2)
discount = min(float(cpus), requested_discount)
effective_load1 = max(0.0, load1 - discount)
effective_load5 = max(0.0, load5 - discount)
# Microsoft Defender can leave tasks in uninterruptible I/O sleep. Linux counts those tasks in
# load average even while the host has substantial CPU headroom, so they must not prevent a
# developer-only YOLOmux restart. Keep a real CPU-saturation floor: this exemption is not a
# blanket override for a machine that is actually busy.
defender_load_ignored = defender_d_tasks > 0
# The one-minute average can remain elevated after short-lived parallel work has
# ended. Keep it as a burst guard at one full CPU per schedulable CPU; the
# five-minute ceiling and, on Linux, the measured idle floor still reject sustained pressure.
load_ok = defender_load_ignored or (effective_load1 <= cpus and effective_load5 <= cpus * 2.0)
cpu_ok = idle_fraction >= 0.10
ok = load_ok and cpu_ok
print(
    f"load1={load1:.2f} effective={effective_load1:.2f}/{cpus:.2f} "
    f"load5={load5:.2f} effective={effective_load5:.2f}/{cpus * 2.0:.2f} "
    f"discount={discount:.2f} cpu_idle={idle_fraction:.2%} defender_d_tasks={defender_d_tasks} "
    f"defender_load_ignored={defender_load_ignored} cpu_budget={cpus}"
)
raise SystemExit(0 if ok else 1)
PY
}

yolomux_report_inotify_capacity() {
  local python_bin="$1"
  local startup_repo_root
  startup_repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)" || return 2
  "$python_bin" - "$startup_repo_root" <<'PY'
import os
import platform
import sys

if platform.system() != "Linux":
    raise SystemExit(0)
os.chdir(sys.argv[1])
from yolomux_lib.infra.inotify_capacity import inotify_capacity_verdict

verdict = inotify_capacity_verdict()
if verdict.admitted:
    print(
        "startup inotify capacity: admitted "
        f"({verdict.free_instances} free instances)",
        flush=True,
    )
    raise SystemExit(0)
print("WARNING: startup inotify capacity is below the YOLOmux gate floor", flush=True)
print(verdict.refusal_text(), flush=True)
raise SystemExit(1)
PY
}

yolomux_wait_for_system_capacity() {
  local python_bin="$1"
  local max_wait="${2:-${YOLOMUX_START_LOAD_WAIT_SECONDS:-300}}"
  local deadline=$((SECONDS + max_wait))
  local snapshot=""
  # This is advisory for ordinary dev startup: production can use polling, while the heavy gate
  # needs the full uid-wide watcher reservation and refuses to run below it.
  yolomux_report_inotify_capacity "$python_bin" || true
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

yolomux_macos_server_tmux_socket() {
  printf '%s' "${YOLOMUX_MACOS_SERVER_TMUX_SOCKET:-yolomux-services}"
}

yolomux_macos_server_tmux_session() {
  local port="$1"
  printf 'yolomux-%s' "$port"
}

yolomux_bootout_macos_server() {
  local port="$1"
  local target socket_name session_name
  target="$(yolomux_macos_launch_target "$port")"
  if launchctl print "$target" >/dev/null 2>&1; then
    launchctl bootout "$target"
  fi
  socket_name="$(yolomux_macos_server_tmux_socket)"
  session_name="$(yolomux_macos_server_tmux_session "$port")"
  if command -v tmux >/dev/null 2>&1 && tmux -L "$socket_name" has-session -t "=$session_name" 2>/dev/null; then
    tmux -L "$socket_name" kill-session -t "=$session_name"
  fi
}

yolomux_macos_server_launcher() {
  # The plan JSON is a positional argument, not inherited state: a long-lived
  # tmux server cannot substitute a stale root or stale plan-file path. Both the
  # direct boot path and supported multi-row launcher use this same exec path.
  printf '%s' 'repo=$1; launch_path=$2; shell_bin=$3; python_bin=$4; script=$5; primary_port=$6; log_path=$7; plan_json=$8; shift 8; cd "$repo" && export PATH="$launch_path" SHELL="$shell_bin" PYTHONUNBUFFERED=1 TERM=xterm-256color MALLOC_ARENA_MAX=2 '"$(yolomux_default_server_optin)"' && unset TMUX TMUX_PANE; if [ -n "$primary_port" ]; then set -- env YOLOMUX_BACKGROUND_OWNER_PRIMARY_PORT="$primary_port" "$python_bin" -u "$script" "$@"; else set -- "$python_bin" -u "$script" "$@"; fi; exec "$python_bin" "$repo/tools/instance_isolation.py" exec --plan-json "$plan_json" -- "$@" >> "$log_path" 2>&1'
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
  local launcher socket_name session_name plan_json
  launcher="$(yolomux_macos_server_launcher)"
  socket_name="$(yolomux_macos_server_tmux_socket)"
  session_name="$(yolomux_macos_server_tmux_session "$port")"
  if [[ -n "${YOLOMUX_ROW_PLAN_FILE:-}" ]]; then
    plan_json="$(< "$YOLOMUX_ROW_PLAN_FILE")"
  else
    plan_json="$("$python_bin" "$repo_root/tools/instance_isolation.py" plan-direct --port "$port")" || return
  fi
  tmux -L "$socket_name" new-session -d -s "$session_name" -c "$repo_root" \
    /bin/bash -c "$launcher" bash "$repo_root" "$launch_path" "$shell_bin" "$python_bin" "$repo_root/yolomux.py" "$primary_port" "$log_path" "$plan_json" "$@"
}
