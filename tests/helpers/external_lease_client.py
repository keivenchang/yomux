# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""One stand-in external client for every daemon's client-lease fence.

``runtime.acquire_client_lease`` is the single owner of the lease table for
batchd, watchd, statsd, statusd, approvald and the search indexer, so the
self-connection exclusion it enforces is one rule, not six. A harness that calls
``service.handle({"action": "lease", "client_pid": os.getpid()})`` in-process IS
the daemon, and the fence correctly refuses it. Production is not shaped like
that: the caller is the web server and the daemon is a separate process, so the
fence sees a different pid whose process-start identity it can verify.

Modelling that with a real child process is the honest repair. Renaming the
caller's pid or relaxing the fence would turn a harness mistake into a product
hole, which is why the negative control below is asserted next to every use.
"""

from __future__ import annotations

import contextlib
import os
import subprocess
import sys
import time
from collections.abc import Callable
from collections.abc import Iterator
from typing import Any

from yolomux_lib.local_services import runtime


# A process that does nothing but stay alive until its stdin is closed. It needs
# no YOLOmux code: the only thing the fence reads about it is its pid and the
# process-start identity the kernel publishes for it.
EXTERNAL_CLIENT_SOURCE = "import sys\nsys.stdin.read()\n"


@contextlib.contextmanager
def external_lease_client() -> Iterator[int]:
    """Yield the pid of a real, separate process standing in for a daemon's caller."""

    process = subprocess.Popen(
        [sys.executable, "-c", EXTERNAL_CLIENT_SOURCE],
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline and not runtime.process_start_identity(process.pid):
            time.sleep(0.01)
        assert runtime.process_start_identity(process.pid), (
            "the stand-in external client never published a verifiable process-start identity"
        )
        assert process.pid != os.getpid()
        yield process.pid
    finally:
        process.kill()
        process.wait(timeout=5.0)


def assert_self_lease_is_refused(
    lease: Callable[[int], dict[str, Any]],
    lease_count: Callable[[], int],
) -> None:
    """NEGATIVE CONTROL: a true self-connection may never buy a lease.

    Asserted next to every harness that hands a daemon an external pid, so the
    external stand-in can never quietly become a way around the fence: if the
    self-connection exclusion regressed, the external lease beside it would
    still pass and nothing else in that file would notice.

    ``lease`` performs one lease request for the given client pid and returns
    the daemon's response dict; ``lease_count`` reports the daemon's current
    lease-table size. Both are supplied by the caller because the six daemons
    spell their request entry point differently while sharing one fence.
    """

    leases_before = lease_count()

    response = lease(os.getpid())

    assert response["ok"] is False, "a service granted itself the lease that keeps it alive"
    assert response["error"] == "a service may not lease itself"
    assert response["diagnostic"] == {"reason": "self_connection", "pid": os.getpid()}
    assert lease_count() == leases_before, "a refused self-lease still touched the lease table"


def assert_daemon_refuses_a_self_lease(daemon: Any) -> None:
    """NEGATIVE CONTROL for the daemons that spell one lease request the same way.

    batchd's broker, its status service and the interaction-lease harness all reach
    the fence through ``handle({"action": "lease", "client_pid": pid})`` and all
    keep the table on ``.leases``, so binding that shape was already copied per
    test file. It is bound ONCE here instead: watchd and statsd wrap the same
    request differently and keep using ``assert_self_lease_is_refused`` directly,
    which is the general entry point this one is built on -- not a second control.
    """

    assert_self_lease_is_refused(
        lambda client_pid: daemon.handle({"action": "lease", "client_pid": client_pid})[0],
        lambda: len(daemon.leases),
    )
