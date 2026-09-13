#!/usr/bin/env bash
set -eu
binary="$1"
"$binary" -test.run 'TestHealthAndCapabilities|TestWriteAndReadMaskedFrame' -test.count=1
