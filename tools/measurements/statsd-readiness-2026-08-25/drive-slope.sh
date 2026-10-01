#!/bin/bash
set -u
cd /home/keivenc/dev/yolomux.e3-readiness
run() {
  local label=$1 master=$2 root=$3
  echo "=== $label start PT=$(TZ=America/Los_Angeles date '+%F %T %Z') mono=$(python3 -c 'import time;print(time.clock_gettime(time.CLOCK_MONOTONIC))') load=$(cut -d' ' -f1-3 /proc/loadavg) disk_mb=$(du -sm /tmp/yolomux-e3-readiness-10 | cut -f1)"
  python3 tools/statsd_readiness_probe.py run --master "$master" --root "$root" \
    --label "$label" --out "/tmp/yolomux-e3-readiness-10/results/$label.json" \
    --ready-timeout 900 --snapshot-calls 300 --append-calls 300 > "/tmp/yolomux-e3-readiness-10/$label.log" 2>&1
  echo "=== $label rc=$? end PT=$(TZ=America/Los_Angeles date '+%F %T %Z') mono=$(python3 -c 'import time;print(time.clock_gettime(time.CLOCK_MONOTONIC))') load=$(cut -d' ' -f1-3 /proc/loadavg) disk_mb=$(du -sm /tmp/yolomux-e3-readiness-10 | cut -f1)"
}
for n in a b c; do run "ck-$n" /tmp/yolomux-e3-readiness-10/masters/1x-checkpointed.sqlite3 /tmp/yolomux-e3-readiness-10/root-ck; done
for n in a b c; do run "2x-$n" /tmp/yolomux-e3-readiness-10/masters/2x.sqlite3 /tmp/yolomux-e3-readiness-10/root-2x; done
echo "ALL DONE"
