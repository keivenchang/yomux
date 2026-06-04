#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Kill any running instances
pkill -f "python3 -m yolomux" 2>/dev/null || true
sleep 1

# Common flags
FLAGS="--host 0.0.0.0 --self-signed --dangerously-yolo"

launch() {
    local port=$1
    local session=$2
    setsid nohup env TERM=xterm-256color PYTHONUNBUFFERED=1 \
        python3 -m yolomux --port "$port" --sessions "$session" $FLAGS \
        > "/tmp/yolomux-${port}.log" 2>&1 < /dev/null &
    echo "started port $port -> session $session (pid $!)"
}

cd "$SCRIPT_DIR"
launch 7777 1
launch 7778 2
launch 7779 3

sleep 2
ss -tlnp | grep -E "7777|7778|7779" | awk '{print $4, $6}'
