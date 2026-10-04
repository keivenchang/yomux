#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
set -euo pipefail

if [ "${1:-}" != --private-devpts ]; then
  # Bazel 9.2's hermetic MountDev omits devpts. Create it in another private
  # user/mount namespace without changing the non-root runner's numeric UID.
  exec unshare --user --map-current-user --mount --keep-caps -- \
    /bin/bash "$0" --private-devpts "$@"
fi
shift
mkdir -p /dev/pts
mount -t devpts -o newinstance,ptmxmode=0666,mode=0600 devpts /dev/pts
ln -s pts/ptmx /dev/ptmx
# Mounting needs namespace-local capabilities; the test itself receives none.
exec setpriv --inh-caps=-all --ambient-caps=-all --bounding-set=-all -- "$@"
