import json
import threading as threading_module
import time
from http import HTTPStatus
from unittest.mock import DEFAULT, Mock

import pytest

from yolomux_lib import atomic_file
import yolomux_lib.observability.queued_delivery as queued_delivery_module
from yolomux_lib.infra import jobd as jobd_module
from yolomux_lib.app import TmuxWebtermApp
from yolomux_lib.observability.queued_delivery import QueuedDeliveryCompactionOwner
from yolomux_lib.observability.queued_delivery import QueuedDeliveryLedger
from yolomux_lib.observability.queued_delivery import compact_queued_delivery_journal
from yolomux_lib.infra.state_services import JobdOperationFlight


def accept_queued_operation(ledger, suffix, **overrides):
    options = {
        "request_id": f"r-{suffix}",
        "route": "GET /api/fs/list",
        "deadline_at": time.time() + 30,
        "progress": {"phase": "waiting_for_product"},
        "producer": {"service": "jobd", "job_id": f"job-{suffix}"},
    }
    options.update(overrides)
    return ledger.accept_operation(**options)


def test_queued_operation_ledger_appends_acceptance_and_terminal_without_snapshot_rewrite(monkeypatch, tmp_path):
    path = tmp_path / "operations.json"
    ledger = QueuedDeliveryLedger(state_path=path)
    monkeypatch.setattr("yolomux_lib.observability.queued_delivery.atomic_write_text", lambda *_args, **_kwargs: pytest.fail("request path must not rewrite the full ledger"))

    receipt = accept_queued_operation(
        ledger,
        "append",
        deadline_at=10.0,
        kind="filesystem_operation",
        context={"path": "/repo"},
    )
    operation_id = receipt["operation"]["id"]
    result = {"state": "ready", "request": {"id": "r-append"}, "data": {"entries": []}}
    terminal = ledger.terminalize_operation(operation_id, result, HTTPStatus.OK)

    recovered = QueuedDeliveryLedger(state_path=path)
    assert terminal["status"] == HTTPStatus.OK
    assert recovered.operation_replay_event(operation_id) == terminal
    assert recovered.operation_status(operation_id) == (result, HTTPStatus.OK)
    assert len(path.read_text(encoding="utf-8").splitlines()) == 2


def test_queued_operation_ledger_loads_legacy_snapshot(tmp_path):
    path = tmp_path / "operations.json"
    result = {"state": "ready", "request": {"id": "r-legacy"}, "data": {"entries": []}}
    event = {"operation": {"id": "op-legacy", "cursor": {"epoch": "legacy", "seq": 1}}, "result": result}
    path.write_text(json.dumps({
        "version": 1,
        "epoch": "legacy",
        "operations": [{
            "id": "op-legacy",
            "state": "ready",
            "created_at": time.time(),
            "terminal_at": time.time(),
            "terminal_event": event,
            "http_status": int(HTTPStatus.OK),
        }],
    }), encoding="utf-8")

    ledger = QueuedDeliveryLedger(state_path=path)

    assert ledger.operation_replay_event("op-legacy") == event
    assert ledger.operation_status("op-legacy") == (result, HTTPStatus.OK)


def test_queued_operation_ledger_ignores_truncated_trailing_journal_record(tmp_path):
    path = tmp_path / "operations.json"
    ledger = QueuedDeliveryLedger(state_path=path)
    receipt = accept_queued_operation(ledger, "truncated", deadline_at=10.0)
    operation_id = receipt["operation"]["id"]
    with path.open("a", encoding="utf-8") as handle:
        handle.write('{"version":2,"type":"operation"')

    recovered = QueuedDeliveryLedger(state_path=path)

    assert recovered.operation_status(operation_id) == (receipt, HTTPStatus.ACCEPTED)


def test_queued_operation_ledger_compacts_only_when_called_out_of_band(tmp_path):
    path = tmp_path / "operations.json"
    ledger = QueuedDeliveryLedger(state_path=path)
    receipt = accept_queued_operation(ledger, "compact", deadline_at=10.0)
    operation_id = receipt["operation"]["id"]

    ledger.compact_operations()
    ledger.terminalize_operation(operation_id, {"state": "ready"}, HTTPStatus.OK)

    assert len(path.read_text(encoding="utf-8").splitlines()) == 2
    assert QueuedDeliveryLedger(state_path=path).operation_status(operation_id) == ({"state": "ready"}, HTTPStatus.OK)


def test_stale_flight_registration_cannot_overwrite_a_completed_producer(monkeypatch, tmp_path):
    path = tmp_path / "operations.json"
    ledger = QueuedDeliveryLedger(state_path=path)
    queued_producer = {
        "service": "jobd",
        "chain": [{"stage": "requested", "job_id": "job-race", "state": "queued"}],
    }
    receipt = accept_queued_operation(ledger, "producer-race", producer=queued_producer)
    operation_id = receipt["operation"]["id"]
    flight = JobdOperationFlight(
        lane="bulk",
        key="job-race|replace=0",
        deadline_at=time.time() + 30,
        producer=queued_producer,
    )
    registration_blocked = threading_module.Event()
    release_registration = threading_module.Event()
    original_update = ledger.update_operation_producer

    def delayed_update(*args, **kwargs):
        registration_blocked.set()
        assert release_registration.wait(timeout=5)
        return original_update(*args, **kwargs)

    monkeypatch.setattr(ledger, "update_operation_producer", delayed_update)

    def register():
        producer, producer_generation = flight.register_operation(operation_id)
        ledger.update_operation_producer(
            operation_id,
            producer,
            producer_generation=producer_generation,
        )

    registration = threading_module.Thread(target=register)
    registration.start()
    assert registration_blocked.wait(timeout=1)
    producer, operation_ids, producer_generation = flight.finish_current_producer("completed")
    assert ledger.update_operation_producers(
        operation_ids,
        producer,
        producer_generation=producer_generation,
    ) == (operation_id,)
    release_registration.set()
    registration.join(timeout=2)

    assert not registration.is_alive()
    recovered = QueuedDeliveryLedger(state_path=path)._operations[operation_id]
    assert recovered["producer"]["chain"][-1]["state"] == "completed"
    assert recovered["receipt"]["operation"]["progress"]["producer_state"] == "completed"


def test_multi_participant_producer_transition_is_one_atomic_journal_record(monkeypatch, tmp_path):
    path = tmp_path / "operations.json"; ledger = QueuedDeliveryLedger(state_path=path)
    queued_producer = {"service": "jobd", "chain": [{"stage": "requested", "job_id": "job-batch", "state": "queued"}]}
    receipts = [accept_queued_operation(ledger, suffix, producer=queued_producer) for suffix in ("producer-batch-a", "producer-batch-b")]
    operation_ids = tuple(receipt["operation"]["id"] for receipt in receipts)
    completed_producer = {"service": "jobd", "chain": [{"stage": "requested", "job_id": "job-batch", "state": "completed"}]}
    original_append = queued_delivery_module.append_fsync_text; operation_transition_appends = 0

    def fail_transition(target, text, mode=None):
        nonlocal operation_transition_appends
        payload = json.loads(text)
        if payload.get("type") == "operations": raise OSError("disk full")
        record = payload.get("record")
        if payload.get("type") == "operation" and isinstance(record, dict) and record.get("id") in operation_ids:
            operation_transition_appends += 1
            if operation_transition_appends == 2: raise OSError("disk full")
        original_append(target, text, mode=mode)

    monkeypatch.setattr(queued_delivery_module, "append_fsync_text", fail_transition)
    with pytest.raises(OSError, match="disk full"):
        ledger.update_operation_producers(operation_ids, completed_producer)
    assert all(ledger._operations[operation_id]["producer"]["chain"][-1]["state"] == "queued" for operation_id in operation_ids)
    assert all(QueuedDeliveryLedger(state_path=path)._operations[operation_id]["producer"]["chain"][-1]["state"] == "queued" for operation_id in operation_ids)
    monkeypatch.setattr(queued_delivery_module, "append_fsync_text", original_append)
    before_lines = len(path.read_text(encoding="utf-8").splitlines()); assert ledger.update_operation_producers(operation_ids, completed_producer) == operation_ids
    lines = path.read_text(encoding="utf-8").splitlines(); assert len(lines) == before_lines + 1; assert json.loads(lines[-1])["type"] == "operations"
    assert all(QueuedDeliveryLedger(state_path=path)._operations[operation_id]["producer"]["chain"][-1]["state"] == "completed" for operation_id in operation_ids)

@pytest.mark.parametrize("receipt_exposed_before_terminal", [False, True])
def test_queued_operation_terminal_remains_exact_until_delivery_ack_then_bounds_replay(
    tmp_path,
    receipt_exposed_before_terminal,
):
    path = tmp_path / "operations.json"
    ledger = QueuedDeliveryLedger(state_path=path)
    receipt = accept_queued_operation(ledger, "bounded-replay", route="GET /api/session-files")
    operation_id = receipt["operation"]["id"]
    if receipt_exposed_before_terminal:
        ledger.observe_http_response(receipt, HTTPStatus.ACCEPTED)
    exact_result = {
        "state": "ready",
        "request": {"id": "r-bounded-replay"},
        "data": {"blob": "x" * (512 * 1024)},
    }

    terminal = ledger.terminalize_operation(operation_id, exact_result, HTTPStatus.OK)
    assert terminal["result"] == exact_result
    assert ledger.operation_replay_event(operation_id) == terminal

    if not receipt_exposed_before_terminal:
        ledger.observe_http_response(receipt, HTTPStatus.ACCEPTED)

    replay, status = ledger.operation_status(operation_id)
    assert status == HTTPStatus.OK
    assert replay == exact_result

    assert ledger.acknowledge_operation_delivery(operation_id, terminal["operation"]["cursor"]) is True
    replay, status = ledger.operation_status(operation_id)
    assert status == HTTPStatus.GONE
    assert replay["state"] == "failed"
    assert replay["request"] == receipt["request"]
    assert replay["error"]["code"] == "operation_replay_evicted"
    recovered = QueuedDeliveryLedger(state_path=path)
    assert recovered.operation_status(operation_id) == (replay, HTTPStatus.GONE)


def test_operation_terminal_batch_ack_is_exact_idempotent_and_bounds_once(tmp_path):
    path = tmp_path / "operations.json"
    ledger = QueuedDeliveryLedger(state_path=path)
    terminals = []
    for suffix in ("a", "b"):
        receipt = accept_queued_operation(ledger, f"batch-{suffix}", route="GET /api/fs/watch-diff")
        terminals.append(ledger.terminalize_operation(
            receipt["operation"]["id"],
            {"state": "ready", "request": receipt["request"], "data": {"blob": suffix * (512 * 1024)}},
            HTTPStatus.OK,
        ))

    stale = {
        "id": terminals[0]["operation"]["id"],
        "cursor": {**terminals[0]["operation"]["cursor"], "seq": 99},
    }
    exact = [
        {"id": terminal["operation"]["id"], "cursor": terminal["operation"]["cursor"]}
        for terminal in terminals
    ]

    assert ledger.acknowledge_operation_deliveries([stale]) == []
    assert ledger.operation_replay_event(stale["id"])["result"]["data"]["blob"].startswith("a")
    assert ledger.acknowledge_operation_deliveries(exact) == [item["id"] for item in exact]
    assert ledger.acknowledge_operation_deliveries(exact) == [item["id"] for item in exact]
    for item in exact:
        replay, status = ledger.operation_status(item["id"])
        assert status == HTTPStatus.GONE
        assert replay["error"]["code"] == "operation_replay_evicted"


def test_operation_ack_appends_one_durable_v3_record_before_live_mutation(monkeypatch, tmp_path):
    path = tmp_path / "operations.json"
    ledger = QueuedDeliveryLedger(state_path=path)
    receipt = accept_queued_operation(ledger, "ack-journal", route="GET /api/fs/read")
    operation_id = receipt["operation"]["id"]
    terminal = ledger.terminalize_operation(
        operation_id,
        {"state": "ready", "request": receipt["request"], "data": {"blob": "x" * (512 * 1024)}},
        HTTPStatus.OK,
    )
    before_size = path.stat().st_size
    original_append = queued_delivery_module.append_fsync_text
    persisted_before_mutation = []

    def observed_append(target, text, mode=None):
        persisted_before_mutation.append(ledger._operations[operation_id]["delivery_acknowledged"] is False)
        original_append(target, text, mode=mode)

    monkeypatch.setattr(queued_delivery_module, "append_fsync_text", observed_append)
    monkeypatch.setattr(queued_delivery_module, "atomic_write_text", lambda *_args, **_kwargs: pytest.fail("ack request must not rewrite the ledger"))

    exact = [{"id": operation_id, "cursor": terminal["operation"]["cursor"]}]
    assert ledger.acknowledge_operation_deliveries(exact) == [operation_id]
    after_first_ack = path.stat().st_size
    assert persisted_before_mutation == [True]
    assert after_first_ack - before_size < 1024
    entry = json.loads(path.read_text(encoding="utf-8").splitlines()[-1])
    assert entry == {
        "version": 3,
        "type": "ack",
        "epoch": terminal["operation"]["cursor"]["epoch"],
        "acks": exact,
    }
    assert ledger.acknowledge_operation_deliveries(exact) == [operation_id]
    assert path.stat().st_size == after_first_ack


def test_operation_terminal_and_ack_append_failures_keep_live_transitions_retryable(monkeypatch, tmp_path):
    path = tmp_path / "operations.json"; ledger = QueuedDeliveryLedger(state_path=path)
    receipt = accept_queued_operation(ledger, "ack-failure", route="GET /api/fs/read"); operation_id = receipt["operation"]["id"]; original_write = atomic_file.os.write; writes = [0]
    def partial_then_fail_once(descriptor, data):
        writes[0] += 1
        if writes[0] == 2: raise OSError("write interrupted")
        return original_write(descriptor, data[: max(1, len(data) // 2)] if writes[0] == 1 else data)
    monkeypatch.setattr(atomic_file.os, "write", partial_then_fail_once); monkeypatch.setattr(queued_delivery_module, "QUEUED_OPERATION_TERMINAL_RETRY_SECONDS", 0.0)
    terminal = ledger.terminalize_operation(operation_id, {"state": "ready"}, HTTPStatus.OK); assert QueuedDeliveryLedger(state_path=path).operation_status(operation_id) == ({"state": "ready"}, HTTPStatus.OK)
    exact = [{"id": operation_id, "cursor": terminal["operation"]["cursor"]}]
    retrying_append = Mock(wraps=queued_delivery_module.append_fsync_text, side_effect=[OSError("disk full"), DEFAULT]); monkeypatch.setattr(queued_delivery_module, "append_fsync_text", retrying_append)
    with pytest.raises(OSError, match="disk full"):
        ledger.acknowledge_operation_deliveries(exact)
    assert ledger.acknowledge_operation_deliveries(exact) == [operation_id]; assert QueuedDeliveryLedger(state_path=path)._operations[operation_id]["delivery_acknowledged"] is True; assert all(json.loads(line) for line in path.read_text(encoding="utf-8").splitlines())


def test_terminalization_follower_takes_over_failure_and_rollback_failure_is_not_retried(monkeypatch, tmp_path):
    path = tmp_path / "takeover.json"; ledger = QueuedDeliveryLedger(state_path=path); operation_id = accept_queued_operation(ledger, "takeover")["operation"]["id"]
    original_append = queued_delivery_module.append_fsync_text; started = threading_module.Event(); release = threading_module.Event(); attempts = [0]; results = []; errors = []
    def fail_first_owner(target, text, mode=None):
        attempts[0] += 1
        if attempts[0] == 1: started.set(); assert release.wait(2); raise OSError("disk full")
        return original_append(target, text, mode=mode)
    monkeypatch.setattr(queued_delivery_module, "append_fsync_text", fail_first_owner); monkeypatch.setattr(queued_delivery_module, "QUEUED_OPERATION_TERMINAL_RETRY_BUDGET_SECONDS", 0.0)
    def terminalize_owner():
        try: results.append(ledger.terminalize_operation(operation_id, {"state": "ready"}, HTTPStatus.OK))
        except OSError as error: errors.append(error)
    owner = threading_module.Thread(target=terminalize_owner); follower = threading_module.Thread(target=terminalize_owner); owner.start(); assert started.wait(2); follower.start(); release.set(); owner.join(2); follower.join(2)
    assert len(errors) == 1; assert len(results) == 1 and results[0] is not None; assert ledger.operation_status(operation_id) == ({"state": "ready"}, HTTPStatus.OK)
    bad_path = tmp_path / "rollback.json"; bad = QueuedDeliveryLedger(state_path=bad_path); bad_id = accept_queued_operation(bad, "rollback")["operation"]["id"]; writes = [0]; real_write = atomic_file.os.write
    def partial_then_fail(descriptor, data):
        writes[0] += 1
        if writes[0] == 1: return real_write(descriptor, data[: max(1, len(data) // 2)])
        raise OSError("write interrupted")
    monkeypatch.setattr(queued_delivery_module, "append_fsync_text", original_append); monkeypatch.setattr(atomic_file.os, "write", partial_then_fail); monkeypatch.setattr(atomic_file.os, "ftruncate", lambda *_args: (_ for _ in ()).throw(OSError("truncate failed")))
    with pytest.raises(atomic_file.AppendRollbackError): bad.terminalize_operation(bad_id, {"state": "ready"}, HTTPStatus.OK)
    assert writes == [2]; assert bad.operation_status(bad_id)[1] == HTTPStatus.ACCEPTED


def test_v3_ack_recovery_bounds_replay_and_ignores_only_a_truncated_final_record(tmp_path):
    path = tmp_path / "operations.json"
    ledger = QueuedDeliveryLedger(state_path=path)
    receipt = accept_queued_operation(ledger, "v3-recovery", route="GET /api/fs/read")
    operation_id = receipt["operation"]["id"]
    terminal = ledger.terminalize_operation(
        operation_id,
        {"state": "ready", "request": receipt["request"], "data": {"blob": "z" * (512 * 1024)}},
        HTTPStatus.OK,
    )
    before_ack = path.read_text(encoding="utf-8")
    exact = [{"id": operation_id, "cursor": terminal["operation"]["cursor"]}]
    ledger.acknowledge_operation_deliveries(exact)
    after_ack = path.read_text(encoding="utf-8")

    recovered = QueuedDeliveryLedger(state_path=path)
    replay, status = recovered.operation_status(operation_id)
    assert status == HTTPStatus.GONE
    assert replay["error"]["code"] == "operation_replay_evicted"

    path.write_text(before_ack + after_ack[len(before_ack):].rstrip("\n")[:-8], encoding="utf-8")
    truncated = QueuedDeliveryLedger(state_path=path)
    replay, status = truncated.operation_status(operation_id)
    assert status == HTTPStatus.OK
    assert replay["data"]["blob"].startswith("z")


def test_out_of_band_compaction_holds_file_lock_before_read_and_preserves_racing_append(monkeypatch, tmp_path):
    path = tmp_path / "operations.json"
    ledger = QueuedDeliveryLedger(state_path=path)
    receipt = accept_queued_operation(ledger, "compact-race")
    operation_id = receipt["operation"]["id"]
    compactor_has_lock = threading_module.Event()
    release_compactor = threading_module.Event()
    original_write = queued_delivery_module.atomic_write_text

    def blocked_write(target, text, mode=None):
        compactor_has_lock.set()
        assert release_compactor.wait(2)
        original_write(target, text, mode=mode)

    monkeypatch.setattr(queued_delivery_module, "atomic_write_text", blocked_write)
    compact_thread = threading_module.Thread(target=compact_queued_delivery_journal, args=(path,))
    compact_thread.start()
    assert compactor_has_lock.wait(2)

    terminal_result = []
    terminal_thread = threading_module.Thread(
        target=lambda: terminal_result.append(ledger.terminalize_operation(operation_id, {"state": "ready"}, HTTPStatus.OK)),
    )
    terminal_thread.start()
    assert terminal_thread.is_alive()
    release_compactor.set()
    compact_thread.join(2)
    terminal_thread.join(2)

    assert not compact_thread.is_alive()
    assert not terminal_thread.is_alive()
    assert terminal_result[0] is not None
    assert QueuedDeliveryLedger(state_path=path).operation_status(operation_id) == ({"state": "ready"}, HTTPStatus.OK)


def test_operation_compaction_watermark_keeps_ack_appended_after_submission_due(monkeypatch, tmp_path):
    now = [100.0]
    monkeypatch.setattr(queued_delivery_module, "QUEUED_OPERATION_COMPACT_RECORDS", 3)
    path = tmp_path / "operations.json"
    ledger = QueuedDeliveryLedger(state_path=path, compaction_clock=lambda: now[0])

    def terminal(suffix):
        receipt = accept_queued_operation(ledger, f"watermark-{suffix}")
        event = ledger.terminalize_operation(receipt["operation"]["id"], {"state": "ready"}, HTTPStatus.OK)
        ledger.acknowledge_operation_delivery(receipt["operation"]["id"], event["operation"]["cursor"])

    terminal("a")
    submitted = ledger.operation_compaction_request()
    assert submitted["due_at"] == now[0]
    terminal("b")

    ledger.note_operation_compaction_succeeded(submitted)

    remaining = ledger.operation_compaction_request()
    assert remaining is not None
    assert remaining["ack_generation"] > submitted["ack_generation"]
    assert remaining["tail_records"] > 0


def test_compaction_owner_submits_one_fresh_maintenance_job_and_clears_due(monkeypatch, tmp_path):
    monkeypatch.setattr(queued_delivery_module, "QUEUED_OPERATION_COMPACT_RECORDS", 3)
    path = tmp_path / "operations.json"
    ledger = QueuedDeliveryLedger(state_path=path)
    submissions = []
    submitted = threading_module.Event()

    def submit(state_path, coalesce_key):
        submissions.append((state_path, coalesce_key))
        submitted.set()
        return {"ok": True, "job": {"job_id": "compact-1", "status": "completed"}}

    owner = QueuedDeliveryCompactionOwner(
        ledger,
        submit,
        lambda _job_id: pytest.fail("completed receipt must not be polled"),
    )
    try:
        receipt = accept_queued_operation(ledger, "owner")
        event = ledger.terminalize_operation(receipt["operation"]["id"], {"state": "ready"}, HTTPStatus.OK)
        ledger.acknowledge_operation_delivery(receipt["operation"]["id"], event["operation"]["cursor"])
        assert submitted.wait(2)
        worker = owner._worker
        if worker is not None:
            worker.join(2)
        assert ledger.operation_compaction_request() is None
        assert len(submissions) == 1
        assert submissions[0][0] == path
        assert submissions[0][1].startswith("operation-ledger-compact:")
    finally:
        owner.stop()


def test_jobd_registered_compactor_replays_current_file_under_the_worker_lock(tmp_path):
    path = tmp_path / "operations.json"
    ledger = QueuedDeliveryLedger(state_path=path)
    receipt = accept_queued_operation(ledger, "jobd-compact")
    operation_id = receipt["operation"]["id"]
    terminal = ledger.terminalize_operation(operation_id, {"state": "ready"}, HTTPStatus.OK)
    ledger.acknowledge_operation_delivery(operation_id, terminal["operation"]["cursor"])

    result = json.loads(jobd_module.run_registered_task(
        "queued_delivery_compact",
        json.dumps({"state_path": str(path)}).encode("utf-8"),
    ))

    assert result["operations"] == 1
    assert len(path.read_text(encoding="utf-8").splitlines()) == 1
    assert QueuedDeliveryLedger(state_path=path)._operations[operation_id]["delivery_acknowledged"] is True


def test_app_submits_operation_compaction_as_fresh_maintenance_receipt(tmp_path):
    calls = []

    class JobClient:
        def produce(self, *args, **kwargs):
            calls.append((args, kwargs))
            return {"ok": True, "job": {"job_id": "compact", "status": "queued"}}, b""

    webapp = object.__new__(TmuxWebtermApp)
    webapp.job_client = JobClient()
    path = tmp_path / "operations.json"

    response = webapp.submit_queued_delivery_compaction(path, "operation-ledger-compact:test")

    assert response["ok"] is True
    assert calls == [(('queued_delivery_compact', {"state_path": str(path)}), {
        "priority": "maintenance",
        "generation": 1,
        "coalesce_key": "operation-ledger-compact:test",
        "delivery": "receipt",
        "fresh_only": True,
    })]


def test_operation_ack_batch_persists_one_record_for_sixty_four_exact_transitions(tmp_path):
    path = tmp_path / "operations.json"
    ledger = QueuedDeliveryLedger(state_path=path)
    exact = []
    for index in range(64):
        receipt = accept_queued_operation(ledger, f"batch-64-{index}")
        operation_id = receipt["operation"]["id"]
        terminal = ledger.terminalize_operation(operation_id, {"state": "ready"}, HTTPStatus.OK)
        exact.append({"id": operation_id, "cursor": terminal["operation"]["cursor"]})
    before_lines = len(path.read_text(encoding="utf-8").splitlines())

    assert ledger.acknowledge_operation_deliveries(exact) == [item["id"] for item in exact]

    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == before_lines + 1
    assert json.loads(lines[-1])["acks"] == exact
