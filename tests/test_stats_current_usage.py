# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Focused tests for the canonical current usage-atom owner."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from yolomux_lib.stats_current.storage import UsageAtom
from yolomux_lib.stats_current import usage


def _atom(**updates) -> UsageAtom:
    values = {
        "event_id": "event-1",
        "direction": "input",
        "modality": "text",
        "cache_role": "none",
        "unit": "tokens",
        "observed_at": 10,
        "payload": {
            "quantity": 25,
            "provider": "openai",
            "model": "gpt-5",
            "agent_id": "agent-a",
            "telemetry_complete": True,
        },
    }
    values.update(updates)
    return UsageAtom(**values)


def test_normalized_atom_is_immutable_and_keeps_only_source_facts():
    atom = usage.normalize_usage_atom(_atom(
        event_id=" event-1 ", direction="INPUT",
        payload={
            "quantity": 25,
            "provider": " openai ",
            "model": " gpt-5 ",
            "agent_id": " agent-a ",
            "telemetry_complete": True,
            "pricing_profile": " default ",
            "service_tier": " flex ",
            "effort": " high ",
            "model_evidence": " turn_context.payload.model ",
            "execution_source": " codex ",
            "thread_id": " thread-a ",
        },
    ))
    assert atom.event_id == "event-1"
    assert atom.direction == "input"
    assert atom.observed_at == 10.0
    assert dict(atom.payload) == {
        "quantity": 25.0,
        "provider": "openai",
        "model": "gpt-5",
        "agent_id": "agent-a",
        "telemetry_complete": True,
        "effort": "high",
        "execution_source": "codex",
        "model_evidence": "turn_context.payload.model",
        "pricing_profile": "default",
        "service_tier": "flex",
        "thread_id": "thread-a",
    }
    with pytest.raises(TypeError):
        atom.payload["quantity"] = 30


@pytest.mark.parametrize(
    ("direction", "modality", "cache_role", "unit"),
    [
        ("input", "text", "none", "tokens"),
        ("input", "text", "read", "tokens"),
        ("input", "text", "write_5m", "tokens"),
        ("input", "text", "write_1h", "tokens"),
        ("output", "text", "none", "tokens"),
        ("input", "image", "none", "tokens"),
        ("output", "image", "none", "tokens"),
        ("output", "image", "none", "requests"),
    ],
)
def test_current_claude_codex_and_image_dimensions_validate(
    direction, modality, cache_role, unit,
):
    atom = usage.normalize_usage_atom(_atom(
        direction=direction, modality=modality, cache_role=cache_role, unit=unit,
    ))
    assert (atom.direction, atom.modality, atom.cache_role, atom.unit) == (
        direction, modality, cache_role, unit,
    )


def test_zero_quantity_is_an_explicit_valid_source_fact():
    atom = usage.normalize_usage_atom(_atom(payload={
        "quantity": 0,
        "provider": "anthropic",
        "model": "claude",
        "agent_id": "agent-a",
        "telemetry_complete": False,
    }))
    assert atom.payload["quantity"] == 0
    assert atom.payload["telemetry_complete"] is False


@pytest.mark.parametrize(
    ("updates", "message"),
    [
        ({"event_id": ""}, "event_id must be a non-empty string"),
        ({"event_id": "bad\nidentity"}, "event_id contains control characters"),
        ({"event_id": "x" * (usage.MAX_EVENT_ID_BYTES + 1)}, "event_id exceeds"),
        ({"observed_at": -1}, "observed_at must be"),
        ({"observed_at": float("nan")}, "observed_at must be"),
        ({"observed_at": 10**400}, "observed_at must be"),
        ({"direction": "both"}, "direction must be one of"),
        ({"modality": "video"}, "modality must be one of"),
        ({"cache_role": "cached"}, "cache_role must be one of"),
        ({"direction": "output", "cache_role": "read"}, "output usage cannot carry"),
        ({"unit": "dollars"}, "unit must be one of"),
        ({"payload": []}, "payload must be an object"),
        ({"payload": {"quantity": 1}}, "payload is missing fields"),
        ({"payload": {
            "quantity": -1, "provider": "openai", "model": "gpt",
            "agent_id": "a", "telemetry_complete": True,
        }}, "payload.quantity must be"),
        ({"payload": {
            "quantity": 1, "provider": "", "model": "gpt",
            "agent_id": "a", "telemetry_complete": True,
        }}, "payload.provider must be"),
        ({"payload": {
            "quantity": 1, "provider": "openai", "model": "gpt\ninvalid",
            "agent_id": "a", "telemetry_complete": True,
        }}, "payload.model contains control characters"),
        ({"payload": {
            "quantity": 1, "provider": "openai", "model": "gpt",
            "agent_id": "agent\x7finvalid", "telemetry_complete": True,
        }}, "payload.agent_id contains control characters"),
        ({"payload": {
            "quantity": 1, "provider": "openai", "model": "gpt",
            "agent_id": "a", "telemetry_complete": 1,
        }}, "telemetry_complete must be a boolean"),
        ({"payload": {
            "quantity": 1, "provider": "openai", "model": "gpt",
            "agent_id": "a", "telemetry_complete": True, "thread_id": "",
        }}, "payload.thread_id must be a non-empty string"),
    ],
)
def test_invalid_columns_and_payloads_fail_at_one_boundary(updates, message):
    with pytest.raises(usage.UsageValidationError, match=message):
        usage.normalize_usage_atom(_atom(**updates))


@pytest.mark.parametrize(
    "field",
    ["micro_usd", "cost_summary", "agent_token_total", "model_token_total"],
)
def test_derived_values_cannot_enter_the_source_atom(field):
    payload = dict(_atom().payload)
    payload[field] = 1
    with pytest.raises(usage.UsageValidationError, match="unknown fields"):
        usage.normalize_usage_atom(_atom(payload=payload))


def test_non_string_payload_keys_are_rejected_before_sorting():
    payload = dict(_atom().payload)
    payload[1] = "bad"
    with pytest.raises(usage.UsageValidationError, match="fields must be strings"):
        usage.normalize_usage_atom(_atom(payload=payload))


def test_structured_source_atom_maps_attribution_without_rendered_text():
    atom = usage.usage_atom_from_source({
        "event_id": "response-1",
        "timestamp": 12,
        "direction": "output",
        "modality": "text",
        "cache_role": "none",
        "unit": "tokens",
        "quantity": 25,
        "provider": "openai",
        "model": "gpt-5.6",
        "tmux_key": "yo8881|0|codex",
        "agent_kind": "codex",
        "agent_thread_id": "thread-1",
        "pricing_profile": "default",
        "service_tier": "flex",
        "effort": "high",
        "model_evidence": "scan_state.resumed_model",
        "telemetry_complete": True,
    })

    assert atom.observed_at == 12
    assert dict(atom.payload) == {
        "quantity": 25,
        "provider": "openai",
        "model": "gpt-5.6",
        "agent_id": "yo8881|0|codex",
        "telemetry_complete": True,
        "pricing_profile": "default",
        "service_tier": "flex",
        "effort": "high",
        "execution_source": "codex",
        "model_evidence": "scan_state.resumed_model",
        "thread_id": "thread-1",
    }


def test_owner_is_small_and_has_no_old_or_derived_pipeline_dependency():
    source = Path(usage.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module is not None
    }
    assert imported == {"__future__", "collections.abc", "types", "storage"}
    assert len(source.splitlines()) < 220
    for forbidden in (
        "micro_usd", "cost_summary", "agent_token_rates", "tokens_per_agent_total",
        "token_stream", "legacy_history",
    ):
        assert forbidden not in source
