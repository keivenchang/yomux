#!/bin/bash
set -u
cd /home/keivenc/dev/yolomux.e3-readiness
for label in 1x-a 1x-b 1x-c; do
  echo "=== $label start PT=$(TZ=America/Los_Angeles date '+%F %T %Z') mono=$(python3 -c 'import time;print(time.clock_gettime(time.CLOCK_MONOTONIC))') load=$(cut -d' ' -f1-3 /proc/loadavg)"
  python3 tools/statsd_readiness_probe.py run \
    --master /tmp/yolomux-e3-readiness-10/masters/1x.sqlite3 \
    --root /tmp/yolomux-e3-readiness-10/root-1x \
    --label "$label" --out "/tmp/yolomux-e3-readiness-10/results/$label.json" \
    --ready-timeout 600 --snapshot-calls 300 --append-calls 300 > "/tmp/yolomux-e3-readiness-10/$label.log" 2>&1
  echo "=== $label rc=$? end PT=$(TZ=America/Los_Angeles date '+%F %T %Z') mono=$(python3 -c 'import time;print(time.clock_gettime(time.CLOCK_MONOTONIC))') load=$(cut -d' ' -f1-3 /proc/loadavg)"
  du -sm /tmp/yolomux-e3-readiness-10
done
echo "ALL DONE"
