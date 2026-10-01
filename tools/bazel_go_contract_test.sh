#!/usr/bin/env bash
set -eu
binary="$1"
test -x "$binary"
"$binary" --help >/dev/null
"$binary" --version | grep -q 'yomux-go'
