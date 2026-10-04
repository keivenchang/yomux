#!/usr/bin/env bash
set -euo pipefail
binary="$1"
test -x "$binary"
output="${TEST_TMPDIR:?}/fake-llm-invalid-port.txt"
if "$binary" --port 70000 >"$output" 2>&1; then
  echo "fake LLM accepted an out-of-range port" >&2
  exit 1
fi
if ! grep -Fxq 'invalid port' "$output"; then
  cat "$output" >&2
  echo "fake LLM did not report the invalid-port contract" >&2
  exit 1
fi
