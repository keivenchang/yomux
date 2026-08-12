"""A leaked search-progress notifier must not survive its owning test.

`app.TmuxWebtermApp.__init__` wires `file_index.set_search_progress_notifier(self.publish_search_progress)`
alongside five other background producers. The autouse `isolated_file_index_background_hooks` fixture is
the one owner that clears those producers between tests; before this regression it cleared the other
five but not the search-progress notifier. A test that built an App therefore leaked a notifier bound
to that torn-down App, and a later test's background crawl -- or a coalescing daemon `Timer` scheduled
by the prior crawl -- fired `notify_search_progress`, forwarding onto `write_shared_background_client_event`,
which `mkdir`/`chmod`s the host-state `background-owner` dir. Under `-n16` load that surfaced as
`PermissionError: [Errno 13]` on an unrelated worker's host-state directory.

These tests exercise the fixture's reset (`conftest.reset_file_index_background_hooks`) directly, so the
regression is deterministic: red before the notifier + coalescing clears are added to the helper, green
after.
"""

from pathlib import Path

import conftest

from yolomux_lib import file_index


def test_reset_clears_leaked_search_progress_notifier_and_coalescing():
    calls: list[dict] = []
    file_index.set_search_progress_notifier(lambda frame: calls.append(frame))

    # A first emit fires immediately; a second within the coalescing window parks a pending frame and
    # schedules a trailing daemon Timer -- both are state a prior test would leak into the next one.
    file_index.notify_search_progress(Path("/leaked/root"), 1, 1, None)
    file_index.notify_search_progress(Path("/leaked/root"), 1, 2, None)
    assert len(calls) == 1
    assert file_index._SEARCH_PROGRESS_PENDING
    assert file_index._SEARCH_PROGRESS_TIMERS

    # The fixture's owner reset must clear the notifier AND cancel/drop the coalescing state, so no
    # leaked frame or timer can reach the shared bus after the owning test is gone.
    conftest.reset_file_index_background_hooks()

    assert file_index._SEARCH_PROGRESS_NOTIFIER is None
    assert file_index._SEARCH_PROGRESS_TIMERS == {}
    assert file_index._SEARCH_PROGRESS_PENDING == {}


def test_leaked_late_notify_cannot_write_or_chmod_a_foreign_host_state_dir(tmp_path):
    # Stand in for another test's host-state dir. The real leaked notifier would mkdir+chmod+write here
    # (write_shared_background_client_event -> file_lock(..., dir_mode=0o700)); make it unwritable so any
    # such touch raises exactly the cross-test PermissionError we are closing.
    foreign_dir = tmp_path / "hosts" / "deadbeef" / "background-owner"
    foreign_dir.mkdir(parents=True)
    foreign_target = foreign_dir / "client-events.json"

    touched: list[dict] = []

    def leaked_notifier(frame):
        foreign_dir.chmod(0o700)
        foreign_target.write_text("leaked")
        touched.append(frame)

    file_index.set_search_progress_notifier(leaked_notifier)
    foreign_dir.chmod(0o000)

    # The owner reset removes the leaked notifier before any late crawl can fire it.
    conftest.reset_file_index_background_hooks()

    # A late/leaked notify from the torn-down context is now inert: it never reaches the notifier, so it
    # cannot mkdir, chmod, or write the foreign host-state dir.
    file_index.notify_search_progress(Path("/leaked/root"), 2, 3, None)

    # Restore traversal before inspecting the tree (a mode-0 parent would fail the stat itself).
    foreign_dir.chmod(0o700)
    assert touched == []
    assert not foreign_target.exists()
