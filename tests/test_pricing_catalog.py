# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

from decimal import Decimal

import pytest

from yomux_lib.observability import pricing_catalog as pricing


def catalog_payload():
    return {
        "schema_version": 1,
        "catalog_revision": 200,
        "models": [{
            "provider": "openai", "model": "fixture-preview", "aliases": ["fixture-preview"],
            "source": {"url": "https://openai.com/pricing"},
            "rates": [{
                "direction": "input", "modality": "text", "cache_role": "none",
                "unit": "tokens", "scale": 1000000, "usd": "2",
                "effective_from": "2020-01-01T00:00:00Z",
            }],
        }],
    }


def test_prefixed_exact_price_is_not_overwritten_by_failed_family_lookup(tmp_path):
    catalog = pricing.PricingCatalog(tmp_path / "pricing")
    catalog.set_override(catalog_payload())
    with catalog._transaction() as connection:
        payload = pricing.validate_catalog(catalog_payload())
        catalog._import_catalog(connection, payload, kind="official")
    rate = catalog.resolve_rate(provider="openai", model="openai/fixture-preview", direction="input")
    assert rate is not None
    assert rate.usd == Decimal("2")
    assert rate.source_kind == "official"


@pytest.mark.parametrize("url", ["https://openai.com:invalid/pricing", "https://[broken/pricing"])
def test_malformed_source_url_is_not_exposed(url):
    assert pricing.safe_source_url(url) == ""


@pytest.mark.parametrize("field", ["schema_version", "catalog_revision", "scale"])
def test_catalog_rejects_boolean_integer_fields(field):
    payload = catalog_payload()
    target = payload["models"][0]["rates"][0] if field == "scale" else payload
    target[field] = True
    with pytest.raises(pricing.PricingCatalogValidationError):
        pricing.validate_catalog(payload)
