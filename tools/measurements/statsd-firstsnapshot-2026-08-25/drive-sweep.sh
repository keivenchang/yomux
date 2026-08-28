#!/bin/bash
set -u
cd /home/keivenc/dev/yolomux.e3-readiness
for n in 1 2 3 4 5 6 7 8; do
  echo "=== sw$n start PT=$(TZ=America/Los_Angeles date '+%F %T %Z') mono=$(python3 -c 'import time;print(time.clock_gettime(time.CLOCK_MONOTONIC))') load=$(cut -d' ' -f1-3 /proc/loadavg) disk_mb=$(du -sm /tmp/yolomux-e3-readiness-10 | cut -f1)"
  python3 tools/statsd_readiness_probe.py sweep \
    --master /tmp/yolomux-e3-readiness-10/masters/1x.sqlite3 \
    --root /tmp/yolomux-e3-readiness-10/root-sw --label "sweep-$n" \
    --out "/tmp/yolomux-e3-readiness-10/results/sweep-$n.json" \
    --sweep-seconds 30 --gap-ms 25 > "/tmp/yolomux-e3-readiness-10/sweep-$n.log" 2>&1
  echo "=== sw$n rc=$? end PT=$(TZ=America/Los_Angeles date '+%F %T %Z') mono=$(python3 -c 'import time;print(time.clock_gettime(time.CLOCK_MONOTONIC))') load=$(cut -d' ' -f1-3 /proc/loadavg)"
  cat "/tmp/yolomux-e3-readiness-10/sweep-$n.log"
done
echo "ALL DONE"
