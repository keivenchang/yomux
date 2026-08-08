"""Terminal file-reference regressions at the browser-visible boundary."""

import pytest

from tests.gate_harness import gate_bundle_vm  # noqa: F401

pytestmark = pytest.mark.node_vm


def test_rejected_terminal_file_candidate_keeps_a_typed_visible_reason(gate_bundle_vm):
    """A 200 batch envelope must not turn an explicit failed file open into a silent miss."""
    result = gate_bundle_vm.execute(
        """
        return {
          target: await api.terminalFileReferenceTarget(
            '1',
            {type: 'file', path: '/fixture/rejected.py', text: '/fixture/rejected.py'},
            {fresh: true, user: true, reportRejection: true},
          ),
        };
        """,
        fs_batch_item={"ok": False, "status": 503, "error": "fixture file-info rejection"},
        flush_file_explorer_batch=True,
    )

    assert result.operation_error is None, result
    assert result.value == {
        "target": {
            "kind": "rejected",
            "label": "File could not be opened: fixture file-info rejection",
            "path": "/fixture/rejected.py",
            "reason": "fixture file-info rejection",
        },
    }, result
    assert len(result.fetches) == 1, result


def test_n5_explicit_rejected_result_after_http_200_is_counted_as_a_client_failure(gate_bundle_vm):
    """Three explicit rejected opens produce three client failures despite HTTP 200 envelopes."""
    result = gate_bundle_vm.execute(
        """
        const targets = [];
        for (const path of ['/fixture/a.py', '/fixture/b.py', '/fixture/c.py']) {
          const target = await api.terminalFileReferenceTarget('1', {type: 'file', path}, {fresh: true, user: true, reportRejection: true});
          targets.push(target && {kind: target.kind, path: target.path, reason: target.reason});
        }
        return {targets};
        """,
        fs_batch_item={"ok": False, "status": 503, "error": "fixture file-info rejection"},
        flush_file_explorer_batch=True,
    )
    assert result.operation_error is None, result
    assert result.value == {
        "targets": [
            {"kind": "rejected", "path": path, "reason": "fixture file-info rejection"}
            for path in ("/fixture/a.py", "/fixture/b.py", "/fixture/c.py")
        ]
    }, result
    api_events = [event for event in result.js_debug_events if event.get("type") == "api"]
    assert len(api_events) == 3 and all(event.get("status") == 200 for event in api_events), result
    assert len(result.fetches) == 3 and result.batch_flushes >= 1, result
    client_failures = [event for event in result.js_debug_events if event.get("type") == "client_failure"]
    assert [event.get("reason_code") for event in client_failures] == ["file_info_rejected"] * 3, result
    assert [event.get("path") for event in client_failures] == [
        "/fixture/a.py",
        "/fixture/b.py",
        "/fixture/c.py",
    ], result
    assert len(result.js_debug_errors) == 3, result


def test_n5_passive_terminal_file_guesses_are_not_client_failures(gate_bundle_vm):
    """A rejected speculative file-info probe is an expected miss rather than a client error."""

    result = gate_bundle_vm.execute(
        """
        const target = await api.terminalFileReferenceTarget(
          '1',
          {type: 'file', path: '/tmp/instruction-', text: '/tmp/instruction-'},
          {fresh: false, user: true},
        );
        return {target};
        """,
        fs_batch_item={"ok": False, "status": 404, "error": "path not found"},
        flush_file_explorer_batch=True,
    )
    assert result.operation_error is None, result
    assert result.value == {"target": None}, result
    api_events = [event for event in result.js_debug_events if event.get("type") == "api"]
    assert len(api_events) == 1 and api_events[0].get("status") == 200, result
    assert [event for event in result.js_debug_events if event.get("type") == "client_failure"] == [], result
    assert result.js_debug_errors == (), result
