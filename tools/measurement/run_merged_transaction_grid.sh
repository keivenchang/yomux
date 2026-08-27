#!/usr/bin/env bash
# Run the shared-transaction append-cost grid SERIALLY and retain every artifact.
#
# Twelve arms: {per_family, merged} x {1 s, 5 s, 10 s} across two store topologies
# (a realistically sized schema-8 copy, and a fresh empty store). Each arm runs in its own
# process so its /proc/self/io deltas are its own, and arms never overlap: a resource
# baseline may be measuring this host and these arms write real bytes.
#
# Usage: run_shared_transaction_grid.sh <out-dir> <realistic-source-db> [seconds]
set -euo pipefail

OUT_DIR="${1:?out dir required}"
SOURCE_DB="${2:?realistic source database required}"
SECONDS_PER_ARM="${3:-3600}"
# "cover" publishes a ring whose head spans the whole append window so every commit also
# writes the invalidation ledger, which is what live ingest does. "nocover" leaves the ring
# head where it is, so appends fall outside every published slot and the ledger stays idle.
RING_MODE="${4:-nocover}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HARNESS="$HERE/merged_transaction_append_cost.py"

mkdir -p "$OUT_DIR/raw" "$OUT_DIR/db"
LEDGER="$OUT_DIR/arm-ledger.tsv"
printf 'arm\ttopology\tmerge\tinterval\tpt_start\tmono_start\tpt_end\tmono_end\tscratch_kb\n' > "$LEDGER"

run_arm() {
  local name="$1" topology="$2" merge="$3" interval="$4"
  local merged_flag=""
  [ "$merge" = "merged" ] && merged_flag="--merged"
  local cover_flag=""
  [ "$RING_MODE" = "cover" ] && cover_flag="--ring-cover"
  local db_dir="$OUT_DIR/db/$name"
  rm -rf "$db_dir"; mkdir -p "$db_dir"

  local pt_start mono_start
  pt_start="$(TZ=America/Los_Angeles date '+%Y-%m-%d %H:%M:%S %Z')"
  mono_start="$(python3 -c 'import time; print(f"{time.monotonic():.6f}")')"

  python3 "$HARNESS" \
    --db "$db_dir/stats-v8.sqlite3" \
    --out "$OUT_DIR/raw/$name.json" \
    --label "$name" --tag "$name" \
    --topology "$topology" ${SOURCE_DB:+--source "$SOURCE_DB"} \
    --shape real --interval "$interval" --seconds "$SECONDS_PER_ARM" $merged_flag $cover_flag

  local pt_end mono_end scratch_kb
  pt_end="$(TZ=America/Los_Angeles date '+%Y-%m-%d %H:%M:%S %Z')"
  mono_end="$(python3 -c 'import time; print(f"{time.monotonic():.6f}")')"
  scratch_kb="$(du -sk "$OUT_DIR" | cut -f1)"
  printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
    "$name" "$topology" "$merge" "$interval" \
    "$pt_start" "$mono_start" "$pt_end" "$mono_end" "$scratch_kb" >> "$LEDGER"

  # Reclaim the arm's scratch database immediately: twelve realistic copies would otherwise
  # hold several GB on a host already at 84% used. The JSON record retains every measurement.
  rm -rf "$db_dir"
}

for topology in copy fresh; do
  for merge in per_family merged; do
    for interval in 1 5 10; do
      run_arm "${topology}-${merge}-i${interval}" "$topology" "$merge" "$interval"
    done
  done
done

echo "--- ledger ---"
cat "$LEDGER"
