"""Job-product polling contracts retained under tests.test_app node IDs."""

import pytest

from yolomux_lib import app as app_module


def assert_wait_for_batchd_product_uses_shared_bounded_cadence_until_ready(monkeypatch):
    clock = [100.0]
    sleeps = []
    responses = iter([
        ({"ok": True, "state": "pending", "generation": 1}, None),
        ({"ok": True, "state": "ready", "generation": 2}, b"ready"),
    ])

    class Client:
        def product(self, key, timeout=0.5):
            assert key == "product-key"
            assert timeout == app_module.BATCHD_PRODUCT_RPC_TIMEOUT_SECONDS
            return next(responses)

    def fake_sleep(seconds):
        sleeps.append(seconds)
        clock[0] += seconds

    monkeypatch.setattr(app_module.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(app_module.time, "sleep", fake_sleep)

    meta, body, state = app_module.wait_for_batchd_product(Client(), "product-key", 2, 20.0)

    assert (meta["generation"], body, state) == (2, b"ready", "ready")
    assert sleeps == [app_module.BATCHD_PRODUCT_POLL_INITIAL_SECONDS]


def assert_wait_for_batchd_product_caps_its_final_sleep_at_deadline(monkeypatch):
    clock = [100.0]
    sleeps = []

    class Client:
        def product(self, key, timeout=0.5):
            assert timeout == pytest.approx(0.3 - sum(sleeps))
            return {"ok": True, "state": "pending", "generation": 1}, None

    def fake_sleep(seconds):
        sleeps.append(seconds)
        clock[0] += seconds

    monkeypatch.setattr(app_module.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(app_module.time, "sleep", fake_sleep)
    monkeypatch.setattr(app_module, "BATCHD_PRODUCT_POLL_INITIAL_SECONDS", 0.25)

    meta, body, state = app_module.wait_for_batchd_product(Client(), "product-key", 2, 0.3)

    assert (meta, body, state) == (None, None, "pending")
    assert sleeps == [0.25, pytest.approx(0.05)]
    assert sum(sleeps) == pytest.approx(0.3)


def assert_wait_for_batchd_product_backs_off_to_a_bounded_broker_cadence(monkeypatch):
    clock = [100.0]
    sleeps = []

    class Client:
        def product(self, key, timeout=0.5):
            assert timeout == app_module.BATCHD_PRODUCT_RPC_TIMEOUT_SECONDS
            return ({"ok": True, "state": "ready", "generation": 2}, b"ready") if len(sleeps) == 3 else ({"ok": True, "state": "pending", "generation": 1}, None)

    def fake_sleep(seconds):
        sleeps.append(seconds)
        clock[0] += seconds

    monkeypatch.setattr(app_module.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(app_module.time, "sleep", fake_sleep)
    meta, body, state = app_module.wait_for_batchd_product(Client(), "product-key", 2, 20.0)
    assert (meta["generation"], body, state) == (2, b"ready", "ready")
    assert sleeps == [0.25, 0.5, 1.0]


def assert_wait_for_batchd_product_retries_busy_within_the_existing_budget(monkeypatch):
    clock = [100.0]
    sleeps = []
    responses = iter([
        ({"ok": False, "error": "service busy", "capacity_rejected": True}, b""),
        ({"ok": True, "state": "ready", "generation": 2}, b"ready"),
    ])

    class Client:
        def product(self, key, timeout=0.5):
            assert key == "product-key"
            assert timeout == app_module.BATCHD_PRODUCT_RPC_TIMEOUT_SECONDS
            return next(responses)

    def fake_sleep(seconds):
        sleeps.append(seconds)
        clock[0] += seconds

    monkeypatch.setattr(app_module.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(app_module.time, "sleep", fake_sleep)

    meta, body, state = app_module.wait_for_batchd_product(Client(), "product-key", 2, 20.0)

    assert (meta["generation"], body, state) == (2, b"ready", "ready")
    assert sleeps == [app_module.BATCHD_PRODUCT_POLL_INITIAL_SECONDS]


def assert_wait_for_batchd_product_keeps_broker_failure_distinct():
    class Client:
        def product(self, key, timeout=0.5):
            return {"ok": False}, None

    with pytest.raises(app_module.BatchedProductRpcUnavailable, match="rpc unavailable"):
        app_module.wait_for_batchd_product(Client(), "product-key", 2, 20.0)


def assert_wait_for_batchd_product_caps_rpc_at_outer_deadline(monkeypatch):
    clock = [100.0]
    rpc_timeouts = []

    class Client:
        def product(self, key, timeout=0.5):
            assert key == "product-key"
            rpc_timeouts.append(timeout)
            clock[0] += timeout
            return {"ok": False, "error": "service busy", "capacity_rejected": True}, b""

    monkeypatch.setattr(app_module.time, "monotonic", lambda: clock[0])

    result = app_module.wait_for_batchd_product(Client(), "product-key", 2, 0.14)

    assert result == (None, None, "busy")
    assert rpc_timeouts == [pytest.approx(0.14)]
