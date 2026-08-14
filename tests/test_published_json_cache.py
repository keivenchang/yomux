# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Exact record/manifest bytes and publication trace for PublishedJsonCache."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from yolomux_lib.infra.published_json_cache import DeterministicJsonCodec
from yolomux_lib.infra.published_json_cache import PublishedJsonCache
from yolomux_lib.infra.published_json_cache import PublishedJsonFreshness


def payload_signature(payload):
    text = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class Validator:
    def payload(self, record, signature):
        if record.get("version") != 2 or record.get("signature") != signature:
            return None
        payload = record.get("payload")
        return payload if isinstance(payload, dict) else None


class Freshness:
    def evaluate(self, record, manifest, payload, source_generation, *, now, max_age_seconds):
        del payload
        current = record.get("source_generation") == source_generation
        if not current:
            stored_at = float(record.get("stored_at", 0.0))
        elif isinstance(manifest, dict) and manifest.get("payload_signature") == record.get("payload_signature"):
            stored_at = float(manifest.get("stored_at", record.get("stored_at", 0.0)))
        else:
            stored_at = float(record.get("stored_at", 0.0))
        age = max(0.0, now - stored_at)
        return PublishedJsonFreshness(current, current and (max_age_seconds is None or age <= max_age_seconds), age, stored_at)


class RecordManifest:
    def payload_signature(self, payload):
        return payload_signature(payload)

    def payload_changed(self, existing, signature, signature_value, source_generation):
        if not isinstance(existing, dict) or existing.get("version") != 2 or existing.get("signature") != signature:
            return True
        return existing.get("payload_signature") != signature_value or existing.get("source_generation") != source_generation

    def record(self, signature, payload, signature_value, source_generation, stored_at):
        return {"version": 2, "signature": signature, "source_generation": source_generation, "stored_at": stored_at, "payload_signature": signature_value, "payload": payload}

    def manifest(self, signature, signature_value, source_generation, stored_at, payload_changed):
        return {"version": 2, "signature": signature, "source_generation": source_generation, "stored_at": stored_at, "payload_signature": signature_value, "payload_changed": payload_changed, "refresh_status": "ready"}


class PostWrite:
    def __init__(self, trace):
        self.trace = trace

    def after_write(self, result, signature, source_generation):
        self.trace.append(("post", result.record_written, signature, source_generation))


def cache(tmp_path, times):
    trace = []
    codec = DeterministicJsonCodec()

    def writer(path, text, mode):
        trace.append(("write", path.name, text, mode))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    owner = PublishedJsonCache(
        codec=codec,
        validator=Validator(),
        freshness=Freshness(),
        record_manifest=RecordManifest(),
        post_write=PostWrite(trace),
        clock=lambda: next(times),
        writer=writer,
    )
    return owner, trace, tmp_path / "abc.json"


def test_record_then_manifest_then_post_write_bytes_and_write_amplification(tmp_path):
    owner, trace, path = cache(tmp_path, iter((100.0, 101.0)))
    payload = {"files": [{"path": "same.py"}], "errors": []}
    signature_value = payload_signature(payload)

    first = owner.write(path, "abc", payload, "source-1")
    first_record = path.read_bytes()
    second = owner.write(path, "abc", payload, "source-1")

    expected_record = json.dumps({"version": 2, "signature": "abc", "source_generation": "source-1", "stored_at": 100.0, "payload_signature": signature_value, "payload": payload}, sort_keys=True, separators=(",", ":")).encode()
    assert first_record == expected_record
    assert path.read_bytes() == expected_record
    assert first.record_written is True
    assert second.record_written is False
    assert [item[:2] for item in trace] == [
        ("write", "abc.json"), ("write", "abc.manifest.json"), ("post", True),
        ("write", "abc.manifest.json"), ("post", False),
    ]
    manifest = json.loads((tmp_path / "abc.manifest.json").read_text())
    assert manifest == {"version": 2, "signature": "abc", "source_generation": "source-1", "stored_at": 101.0, "payload_signature": signature_value, "payload_changed": False, "refresh_status": "ready"}


def test_read_keeps_current_stale_and_source_mismatch_policy_in_strategy(tmp_path):
    owner, _trace, path = cache(tmp_path, iter((100.0, 110.0, 110.0, 110.0)))
    payload = {"files": []}
    owner.write(path, "abc", payload, "source-1")

    current = owner.read(path, "abc", "source-1", max_age_seconds=20.0, allow_stale=False)
    stale = owner.read(path, "abc", "source-1", max_age_seconds=5.0, allow_stale=True)
    mismatch = owner.read(path, "abc", "source-2", max_age_seconds=20.0, allow_stale=True)

    assert current is not None and current.freshness.fresh is True
    assert stale is not None and stale.freshness.fresh is False
    assert mismatch is not None and mismatch.freshness.current is False
