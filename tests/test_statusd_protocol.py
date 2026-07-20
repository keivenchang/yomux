from __future__ import annotations

import json

import pytest

from yolomux_lib.statusd_protocol import StatusProtocolError
from yolomux_lib.statusd_protocol import STATUSD_PROTOCOL_VERSION
from yolomux_lib.statusd_protocol import stamped_request
from yolomux_lib.statusd_protocol import validate_inventory
from yolomux_lib.statusd_protocol import validate_request
from yolomux_lib.statusd_protocol import validate_snapshot


def test_statusd_snapshot_contract_accepts_public_preencoded_json():
    metadata = {
        "protocol_version": STATUSD_PROTOCOL_VERSION,
        "generation": 4,
        "status": 200,
        "stale": False,
        "built_at": 1.5,
    }

    snapshot = validate_snapshot(metadata, b'{"session_order":["1"],"sessions":{}}')

    assert snapshot.generation == 4
    assert snapshot.content_type == "application/json; charset=utf-8"
    assert stamped_request("wait_generation", after_generation=4, timeout_seconds=1.0)["protocol_version"] == STATUSD_PROTOCOL_VERSION


@pytest.mark.parametrize("payload", [
    {"action": "snapshot", "client_id": "private"},
    {"action": "snapshot", "protocol_version": STATUSD_PROTOCOL_VERSION + 1},
    {"action": "wait_generation", "after_generation": -1},
    {"action": "wait_generation", "timeout_seconds": 31},
    {"action": "unknown"},
])
def test_statusd_request_contract_rejects_private_or_malformed_input(payload):
    with pytest.raises(StatusProtocolError):
        validate_request(payload)


@pytest.mark.parametrize("metadata, body", [
    ({"protocol_version": STATUSD_PROTOCOL_VERSION + 1, "generation": 1, "status": 200, "built_at": 1}, b"{}"),
    ({"protocol_version": STATUSD_PROTOCOL_VERSION, "generation": -1, "status": 200, "built_at": 1}, b"{}"),
    ({"protocol_version": STATUSD_PROTOCOL_VERSION, "generation": 1, "status": 200, "built_at": 1}, b"not-json"),
    ({"protocol_version": STATUSD_PROTOCOL_VERSION, "generation": 1, "status": 200, "built_at": 1}, b'{"client_id":"private"}'),
])
def test_statusd_snapshot_contract_rejects_wrong_version_stale_generation_and_private_body(metadata, body):
    with pytest.raises(StatusProtocolError):
        validate_snapshot(metadata, body)


def test_statusd_inventory_contract_accepts_bounded_identifiers_with_source_signatures():
    metadata = {"protocol_version": STATUSD_PROTOCOL_VERSION, "inventory_generation": 3}
    body = json.dumps({
        "inventory_generation": 3,
        "roster": ["a"],
        "sessions": {"a": {"windows": 1, "panes": [{"target": "a:0.0", "cwd": "/repo"}], "source_signature": "abc123"}},
    }).encode("utf-8")

    decoded = validate_inventory(metadata, body)

    assert decoded["inventory_generation"] == 3
    assert decoded["sessions"]["a"]["source_signature"] == "abc123"


@pytest.mark.parametrize("metadata, body", [
    # wrong protocol version
    ({"protocol_version": STATUSD_PROTOCOL_VERSION + 1, "inventory_generation": 1}, json.dumps({"sessions": {"a": {"source_signature": "x"}}}).encode()),
    # negative generation
    ({"protocol_version": STATUSD_PROTOCOL_VERSION, "inventory_generation": -1}, json.dumps({"sessions": {"a": {"source_signature": "x"}}}).encode()),
    # heavy-enrichment field leaked into a session entry
    ({"protocol_version": STATUSD_PROTOCOL_VERSION, "inventory_generation": 1}, json.dumps({"sessions": {"a": {"source_signature": "x", "git": {}}}}).encode()),
    # transcript enrichment leaked
    ({"protocol_version": STATUSD_PROTOCOL_VERSION, "inventory_generation": 1}, json.dumps({"sessions": {"a": {"source_signature": "x", "transcript": "t"}}}).encode()),
    # missing per-session source_signature
    ({"protocol_version": STATUSD_PROTOCOL_VERSION, "inventory_generation": 1}, json.dumps({"sessions": {"a": {"windows": 1}}}).encode()),
    # browser-private field in a session entry
    ({"protocol_version": STATUSD_PROTOCOL_VERSION, "inventory_generation": 1}, json.dumps({"sessions": {"a": {"source_signature": "x", "client_id": "p"}}}).encode()),
    # sessions is not an object
    ({"protocol_version": STATUSD_PROTOCOL_VERSION, "inventory_generation": 1}, json.dumps({"sessions": []}).encode()),
])
def test_statusd_inventory_contract_rejects_wrong_version_heavy_and_unsigned_entries(metadata, body):
    with pytest.raises(StatusProtocolError):
        validate_inventory(metadata, body)
