# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

import json

import pytest

from yomux_lib.stats_current import identity, migration, storage


@pytest.mark.parametrize("escaped", [r'"\ud800"', r'"\udfff"'])
def test_invalid_unicode_keeps_typed_storage_rejection_and_legacy_recovery(escaped):
    value = json.loads(escaped)
    with pytest.raises(identity.IdentityValidationError):
        identity.identity_text(value, "event_id")
    with pytest.raises(storage.StorageValidationError):
        storage._validate_text(value, "event_id")
    normalized, changed = identity.legacy_identity(value, "event_id")
    assert changed and normalized.startswith("retired-event_id:")
    assert identity.legacy_identity(value, "event_id") == (normalized, True)
    recovered = migration._Recovered()
    assert migration._canonicalize_legacy_identity(recovered, value, "event_id") == normalized
    assert len(recovered.issues) == 1
    assert recovered.issues[0].kind == "identity_canonicalized"


def test_valid_unicode_identity_remains_unchanged():
    value = json.loads(r'"\ud83d\ude00"')
    assert identity.identity_text(value, "event_id") == value
    assert identity.legacy_identity(value, "event_id") == (value, False)
