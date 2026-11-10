# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

import pytest

from yomux_lib.stats_current import browser_family


@pytest.mark.parametrize("surrogate", ["\ud800", "\udfff"])
@pytest.mark.parametrize("field,prefix", [("endpoint", "/"), ("request_id", "r-"), ("journey_id", ""), ("message", "")])
def test_browser_observation_rejects_invalid_unicode_with_typed_error(field, prefix, surrogate):
    payload = {"kind": "api", field: prefix + surrogate}
    if field == "message":
        payload.update(kind="warning", signature="test", source="/")
    with pytest.raises(browser_family.BrowserPayloadError):
        browser_family.validate_payload(payload)


@pytest.mark.parametrize("value", [10 ** 1000, -10 ** 1000, float("inf"), float("nan"), 100.5, True], ids=["large-positive", "large-negative", "infinity", "nan", "fraction", "boolean"])
def test_browser_status_rejects_out_of_range_numbers_with_typed_error(value):
    with pytest.raises(browser_family.BrowserPayloadError):
        browser_family.validate_payload({"kind": "api", "status": value})


def test_browser_observation_accepts_unicode_and_integer_boundaries():
    payload = {"kind": "warning", "signature": "test", "source": "/é", "request_id": "r-😀", "message": "é😀\ntext", "line": 10_000_000, "status": 100.0}
    browser_family.validate_payload(payload)
    assert browser_family.sanitize_retained_payload(payload)["message"] == payload["message"]
