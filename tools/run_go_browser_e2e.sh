#!/usr/bin/env bash
set -eu
script="$2"
source_root="$(cd -- "$(dirname -- "$script")/.." && pwd)"
YOLOMUX_E2E_SOURCE_ROOT="$source_root" exec python3 "$script" "$1"
