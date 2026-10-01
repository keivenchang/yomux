"""Completion-reservation doubles shared by app contract tests."""

from types import SimpleNamespace


class StubOperationReservation:
    """Stand-in completion reservation handle with exactly-once release for test doubles."""

    def __init__(self, on_release=None):
        self._on_release = on_release
        self._released = False

    def release(self):
        if self._released:
            return
        self._released = True
        if self._on_release is not None:
            self._on_release()

    @property
    def released(self):
        return self._released


def reservation_must_not_release():
    raise AssertionError("an accepted operation owns its completion reservation")


def isolate_batchd_fs_batch_lease(webapp):
    webapp.batchd_fs_batch_lease = SimpleNamespace(acquire=lambda: True, release=lambda: None)


def replace_job_client_for_fs_batch(webapp, client):
    webapp.job_client = client
    isolate_batchd_fs_batch_lease(webapp)
