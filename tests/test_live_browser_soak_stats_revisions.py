# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Hidden YO!stats soak revision-scope contracts."""

from yomux_lib import live_browser_soak as soak


def stats_evidence(
    delivery_sequence: int,
    accepted_delta_sequence: int,
    cache_generation: int,
    source_generation: int,
    delta_revision: int,
    *,
    stream_epoch: int,
    last_delivery_kind: str,
) -> dict[str, object]:
    return {
        "paintedGenerationKey": "",
        "stream": {
            "streamEpoch": stream_epoch,
            "deliverySequence": delivery_sequence,
            "acceptedDeltaSequence": accepted_delta_sequence,
            "sourceGeneration": source_generation,
            "cacheGeneration": cache_generation,
            "deltaRevision": delta_revision,
            "rangeSeconds": 300,
            "resolutionSeconds": 1,
            "lastDeliveryKind": last_delivery_kind,
        },
    }


def test_snapshot_repair_accepts_revision_reset_after_cache_scope_advance():
    previous = stats_evidence(1743, 51, 1743, 1743, 51, stream_epoch=2, last_delivery_kind="delta")
    current = stats_evidence(1768, 51, 1768, 1768, 0, stream_epoch=5, last_delivery_kind="ready")

    _validated, integrity = soak.classify_hidden_stats_stream(current, previous)

    assert integrity == []


def test_revision_reset_is_rejected_within_the_same_cache_scope():
    previous = stats_evidence(1743, 51, 1743, 1743, 51, stream_epoch=2, last_delivery_kind="delta")
    current = stats_evidence(1768, 51, 1743, 1743, 0, stream_epoch=5, last_delivery_kind="ready")

    _validated, integrity = soak.classify_hidden_stats_stream(current, previous)

    assert integrity == ["hidden YO!stats deltaRevision regressed within cache generation"]
