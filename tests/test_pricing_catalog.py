import json
import threading
import gzip
from types import SimpleNamespace

import pytest

from yolomux_lib.pricing_catalog import PricingCatalog
from yolomux_lib.pricing_catalog import PricingCatalogValidationError
from yolomux_lib.pricing_catalog import PricingSourceAdapter
from yolomux_lib.pricing_catalog import PricingRefreshCoordinator
from yolomux_lib.pricing_catalog import default_pricing_source_adapters
from yolomux_lib.pricing_catalog import load_packaged_seed
from yolomux_lib.pricing_catalog import parse_litellm_prices
from yolomux_lib.pricing_catalog import parse_openrouter_prices
from yolomux_lib.pricing_catalog import parse_provider_pricing_table
from yolomux_lib.pricing_catalog import safe_source_url
from yolomux_lib.pricing_catalog import validate_catalog
from yolomux_lib import pricing_catalog


def test_fresh_catalog_imports_packaged_seed_offline(tmp_path):
    catalog = PricingCatalog(tmp_path / "cache")
    catalog.open()

    status = catalog.status()
    assert status["state"] == "seed-only"
    assert status["catalog_revision"] == load_packaged_seed()["catalog_revision"]
    assert (tmp_path / "cache" / "pricing.sqlite3").exists()
    rate = catalog.resolve_rate(provider="openai", model="gpt-4.1", direction="input")
    assert rate is not None
    assert str(rate.usd) == "2.00"
    assert rate.source_kind == "seed"


def test_fresh_catalog_prices_current_models_with_provenance_and_effective_dates(tmp_path):
    catalog = PricingCatalog(tmp_path)
    # Availability dates, not the seed's checked-at date, must price retained
    # history from before this YOLOmux release.
    sol = catalog.resolve_rate(provider="openai", model="gpt-5.6-sol", direction="output", timestamp="2026-07-11T00:00:00Z")
    terra = catalog.resolve_rate(provider="openai", model="gpt-5.6-terra", direction="input", timestamp="2026-07-11T00:00:00Z")
    sonnet_now = catalog.resolve_rate(provider="anthropic", model="claude-sonnet-5", direction="output", timestamp="2026-08-31T23:59:59Z")
    sonnet_later = catalog.resolve_rate(provider="anthropic", model="claude-sonnet-5", direction="output", timestamp="2026-09-01T00:00:00Z")
    fable = catalog.resolve_rate(provider="anthropic", model="claude-fable-5", direction="output", timestamp="2026-07-11T00:00:00Z")

    assert sol is not None and str(sol.usd) == "30.00" and sol.source_url.startswith("https://developers.openai.com/")
    assert terra is not None and str(terra.usd) == "2.50" and terra.effective_from == "2026-07-09T00:00:00Z"
    assert sonnet_now is not None and str(sonnet_now.usd) == "10.00"
    assert sonnet_later is not None and str(sonnet_later.usd) == "15.00"
    assert fable is not None and str(fable.usd) == "50.00"


def test_catalog_requires_exact_alias_and_effective_date(tmp_path):
    catalog = PricingCatalog(tmp_path)
    assert catalog.resolve_rate(provider="openai", model="gpt-4", direction="input") is None
    assert catalog.resolve_rate(provider="openai", model="gpt-4.1", direction="input", timestamp="2024-12-31T00:00:00Z") is None


def test_catalog_estimates_unknown_model_rate_band_from_comparable_active_rates(tmp_path):
    catalog = PricingCatalog(tmp_path)
    catalog.set_override({
        "schema_version": 1,
        "catalog_revision": 3,
        "models": [
            {"provider": "openai", "model": "cheap-output", "aliases": ["cheap-output"], "rates": [{"direction": "output", "modality": "synthetic", "cache_role": "none", "unit": "widgets", "scale": 1_000_000, "usd": "1.25", "effective_from": "2026-01-01T00:00:00Z", "profile": "default", "service_tier": "default"}], "source": {"url": "https://platform.openai.com/docs/pricing", "kind": "override"}},
            {"provider": "openai", "model": "expensive-output", "aliases": ["expensive-output"], "rates": [{"direction": "output", "modality": "synthetic", "cache_role": "none", "unit": "widgets", "scale": 1_000_000, "usd": "9.50", "effective_from": "2026-01-01T00:00:00Z", "profile": "default", "service_tier": "default"}], "source": {"url": "https://platform.openai.com/docs/pricing", "kind": "override"}},
            {"provider": "anthropic", "model": "unrelated-output", "aliases": ["unrelated-output"], "rates": [{"direction": "output", "modality": "synthetic", "cache_role": "none", "unit": "widgets", "scale": 1_000_000, "usd": "99.00", "effective_from": "2026-01-01T00:00:00Z", "profile": "default", "service_tier": "default"}], "source": {"url": "https://docs.anthropic.com/pricing", "kind": "override"}},
        ],
    })

    band = catalog.estimate_rate_band(provider="openai", direction="output", modality="synthetic", cache_role="none", unit="widgets", profile="batch", service_tier="flex")

    assert band is not None
    assert str(band.minimum.usd) == "1.25"
    assert band.minimum.model == "cheap-output"
    assert str(band.maximum.usd) == "9.50"
    assert band.maximum.model == "expensive-output"
    cross_provider = catalog.estimate_rate_band(provider="unknown", direction="output", modality="synthetic", cache_role="none", unit="widgets")
    assert cross_provider is not None and str(cross_provider.maximum.usd) == "99.00"
    assert catalog.estimate_rate_band(provider="google", direction="output", modality="synthetic", cache_role="none", unit="widgets") is None
    assert catalog.estimate_rate_band(provider="openai", direction="input", modality="synthetic", cache_role="none", unit="widgets") is None


def test_seed_validation_rejects_duplicate_alias_and_float_price():
    seed = load_packaged_seed()
    seed["models"].append({**seed["models"][0], "model": "other"})
    with pytest.raises(PricingCatalogValidationError, match="duplicate alias"):
        validate_catalog(seed)

    seed = load_packaged_seed()
    seed["models"][0]["rates"][0]["usd"] = "nan"
    with pytest.raises(PricingCatalogValidationError, match="invalid usd"):
        validate_catalog(seed)


def test_corrupt_database_is_quarantined_and_rebootstrapped(tmp_path):
    path = tmp_path / "cache"
    path.mkdir()
    (path / "pricing.sqlite3").write_text("not sqlite", encoding="utf-8")
    catalog = PricingCatalog(path, clock=lambda: 1234.0)
    catalog.open()
    assert (path / "pricing.sqlite3.corrupt-1234").exists()
    assert catalog.status()["state"] == "seed-only"


def test_concurrent_open_imports_seed_once_and_resolves(tmp_path):
    results = []
    errors = []

    def worker():
        try:
            catalog = PricingCatalog(tmp_path / "cache")
            results.append(catalog.resolve_rate(provider="openai", model="gpt-4.1", direction="output"))
        except Exception as exc:  # pragma: no cover - asserted below
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == []
    assert len(results) == 4
    assert all(item and item.source_kind == "seed" for item in results)


def test_equal_seed_revision_reconciliation_is_idempotent(tmp_path):
    catalog = PricingCatalog(tmp_path / "cache")
    catalog.open()
    with catalog._transaction() as connection:
        before = connection.execute("SELECT COUNT(*) FROM price_rates WHERE source_kind = 'seed'").fetchone()[0]
    catalog.reconcile_seed()
    with catalog._transaction() as connection:
        after = connection.execute("SELECT COUNT(*) FROM price_rates WHERE source_kind = 'seed'").fetchone()[0]

    assert after == before
    assert catalog.status()["catalog_revision"] == load_packaged_seed()["catalog_revision"]


def test_deleted_pricing_database_rebootstraps_from_packaged_seed(tmp_path):
    root = tmp_path / "cache"
    first = PricingCatalog(root)
    assert first.resolve_rate(provider="openai", model="gpt-4.1", direction="input") is not None
    (root / "pricing.sqlite3").unlink()

    restored = PricingCatalog(root)
    rate = restored.resolve_rate(provider="openai", model="gpt-4.1", direction="input")

    assert rate is not None and rate.source_kind == "seed"
    assert restored.status()["state"] == "seed-only"


def test_refresh_is_transactional_and_preserves_old_catalog_on_parser_failure(tmp_path):
    catalog = PricingCatalog(tmp_path)
    old = catalog.resolve_rate(provider="openai", model="gpt-4.1", direction="input")
    assert old is not None

    adapter = PricingSourceAdapter("openai", "https://platform.openai.com/docs/pricing", "official", lambda body: json.loads(body.decode()))
    with pytest.raises(Exception):
        catalog.refresh([adapter], fetch=lambda *_args: (200, {}, b"not json"))
    retained = catalog.resolve_rate(provider="openai", model="gpt-4.1", direction="input")
    assert retained == old


def test_refresh_uses_etag_and_activates_normalized_official_catalog(tmp_path):
    catalog = PricingCatalog(tmp_path)
    seed = load_packaged_seed()
    seed["catalog_revision"] = 2
    seed["models"][0]["rates"][0]["usd"] = "3.00"
    body = json.dumps(seed).encode()
    adapter = PricingSourceAdapter("openai", "https://platform.openai.com/docs/pricing", "official", lambda value: json.loads(value.decode()))
    result = catalog.refresh([adapter], fetch=lambda *_args: (200, {"etag": "new-etag"}, body))
    assert result == {"ok": True, "status": "updated", "catalog_revision": 3}
    rate = catalog.resolve_rate(provider="openai", model="gpt-4.1", direction="input")
    assert rate is not None and str(rate.usd) == "3.00" and rate.source_kind == "official"
    assert catalog.source_check(adapter.url)["etag"] == "new-etag"


def test_refresh_conditional_304_preserves_digest_and_sends_validators(tmp_path):
    now = [100.0]
    catalog = PricingCatalog(tmp_path, clock=lambda: now[0])
    payload = load_packaged_seed()
    payload["catalog_revision"] = 2
    adapter = PricingSourceAdapter("openai", "https://platform.openai.com/docs/pricing", "official", lambda value: json.loads(value.decode()))
    assert catalog.refresh([adapter], fetch=lambda *_args: (200, {"etag": "v1", "last-modified": "yesterday"}, json.dumps(payload).encode()))["status"] == "updated"
    before = catalog.source_check(adapter.url)
    now[0] = 116.0  # outside the cross-server single-flight window
    seen_headers = []

    def not_modified(_url, headers, _timeout):
        seen_headers.append(headers)
        return 304, {"etag": "v1"}, b""

    assert catalog.refresh([adapter], fetch=not_modified)["status"] == "unchanged"
    after = catalog.source_check(adapter.url)
    assert seen_headers == [{"If-None-Match": "v1", "If-Modified-Since": "yesterday"}]
    assert after["digest"] == before["digest"] and after["etag"] == "v1"


def test_refresh_merges_all_official_provider_pages_and_rejects_exact_disagreement(tmp_path):
    now = [100.0]
    catalog = PricingCatalog(tmp_path, clock=lambda: now[0])

    def payload(provider, model, usd):
        return {"schema_version": 1, "catalog_revision": 2, "models": [{"provider": provider, "model": model, "aliases": [model], "rates": [{"direction": "input", "modality": "text", "cache_role": "none", "unit": "tokens", "scale": 1_000_000, "usd": usd, "effective_from": "2025-01-01T00:00:00Z"}], "source": {"url": "https://platform.openai.com/docs/pricing", "kind": "official"}}]}

    openai = PricingSourceAdapter("openai", "https://platform.openai.com/docs/pricing", "official", lambda value: json.loads(value.decode()))
    anthropic = PricingSourceAdapter("anthropic", "https://www.anthropic.com/pricing", "official", lambda value: json.loads(value.decode()))
    bodies = {openai.url: payload("openai", "gpt-fixture", "2.00"), anthropic.url: payload("anthropic", "claude-fixture", "3.00")}
    assert catalog.refresh([openai, anthropic], fetch=lambda url, *_args: (200, {}, json.dumps(bodies[url]).encode()))["status"] == "updated"
    assert catalog.resolve_rate(provider="openai", model="gpt-fixture", direction="input").source_kind == "official"
    assert catalog.resolve_rate(provider="anthropic", model="claude-fixture", direction="input").source_kind == "official"

    cross_reference = PricingSourceAdapter("cross", "https://openrouter.ai/api/v1/models", "corroboration", lambda _value: payload("openai", "gpt-fixture", "9.00"))
    now[0] = 116.0
    with pytest.raises(Exception, match="disagreement"):
        catalog.refresh([openai, cross_reference], fetch=lambda url, *_args: (200, {}, json.dumps(bodies[openai.url]).encode()))
    assert str(catalog.resolve_rate(provider="openai", model="gpt-fixture", direction="input").usd) == "2.00"


def test_refresh_carries_forward_official_model_omitted_by_partial_revision(tmp_path, caplog):
    now = [100.0]
    catalog = PricingCatalog(tmp_path, clock=lambda: now[0])

    def payload(model):
        return {"schema_version": 1, "catalog_revision": 1, "models": [{"provider": "openai", "model": model, "aliases": [model], "rates": [{"direction": "input", "modality": "text", "cache_role": "none", "unit": "tokens", "scale": 1_000_000, "usd": "2.00", "effective_from": "2025-01-01T00:00:00Z"}], "source": {"url": "https://platform.openai.com/docs/pricing", "kind": "official"}}]}

    adapter = PricingSourceAdapter("openai", "https://platform.openai.com/docs/pricing", "official", lambda value: json.loads(value.decode()))
    assert catalog.refresh([adapter], fetch=lambda *_args: (200, {}, json.dumps(payload("gpt-removed")).encode()))["status"] == "updated"
    assert catalog.resolve_rate(provider="openai", model="gpt-removed", direction="input") is not None
    with catalog._transaction() as connection:
        coverage_before = connection.execute("SELECT COUNT(DISTINCT provider || '/' || alias) FROM model_aliases WHERE source_kind = 'official'").fetchone()[0]
    now[0] = 116.0
    result = catalog.refresh([adapter], fetch=lambda *_args: (200, {}, json.dumps(payload("gpt-replacement")).encode()))
    assert result["status"] == "updated"
    assert result["carried_forward_models"] == ["openai/gpt-removed"]
    retained = catalog.resolve_rate(provider="openai", model="gpt-removed", direction="input")
    assert retained is not None and retained.catalog_revision == 3
    assert catalog.resolve_rate(provider="openai", model="gpt-replacement", direction="input") is not None
    with catalog._transaction() as connection:
        coverage_after = connection.execute("SELECT COUNT(DISTINCT provider || '/' || alias) FROM model_aliases WHERE source_kind = 'official'").fetchone()[0]
    assert coverage_after >= coverage_before
    assert "retaining prior official evidence" in caplog.text


def test_provider_table_parser_is_fail_closed_on_reordered_or_missing_columns():
    page = b"<table><tr><th>Model</th><th>Input</th><th>Cached input</th><th>Output</th></tr><tr><td>test-1</td><td>$2.00</td><td>$0.50</td><td>$8.00</td></tr></table>"
    catalog = parse_provider_pricing_table(page, provider="openai", source_url="https://platform.openai.com/docs/pricing")
    assert catalog["models"][0]["model"] == "test-1"
    assert {rate["cache_role"] for rate in catalog["models"][0]["rates"]} == {"none", "read"}
    with pytest.raises(PricingCatalogValidationError, match="columns changed"):
        parse_provider_pricing_table(b"<table><tr><th>Model</th><th>Input</th></tr></table>", provider="openai", source_url="https://platform.openai.com/docs/pricing")


def test_provider_table_parser_preserves_explicit_profile_and_service_tier_candidates():
    page = b"<table><tr><th>Model</th><th>Pricing profile</th><th>Service tier</th><th>Input</th><th>Cached input</th><th>Output</th></tr><tr><td>test-tiered</td><td>Batch</td><td>Flex</td><td>$1.00</td><td>$0.10</td><td>$4.00</td></tr></table>"
    catalog = parse_provider_pricing_table(page, provider="openai", source_url="https://platform.openai.com/docs/pricing")
    assert {(rate["profile"], rate["service_tier"]) for rate in catalog["models"][0]["rates"]} == {("batch", "flex")}


def test_provider_table_parser_accepts_reviewed_openai_context_and_cache_write_columns():
    page = b"""<table>
      <tr><th>Model</th><th>Input</th><th>Cached input</th><th>Cache writes</th><th>Output</th><th>Input</th><th>Cached input</th><th>Cache writes</th><th>Output</th></tr>
      <tr><td>gpt-5.6-sol</td><td>$5.00</td><td>$0.50</td><td>$6.25</td><td>$30.00</td><td>$10.00</td><td>$1.00</td><td>$12.50</td><td>$45.00</td></tr>
      <tr><td>gpt-optional</td><td>$1.00</td><td>-</td><td>-</td><td>$6.00</td><td>-</td><td>-</td><td>-</td><td>-</td></tr>
    </table>"""
    catalog = parse_provider_pricing_table(page, provider="openai", source_url="https://developers.openai.com/api/docs/pricing")
    sol = next(model for model in catalog["models"] if model["model"] == "gpt-5.6-sol")
    assert {(rate["cache_role"], rate["usd"]) for rate in sol["rates"]} == {("none", "5.00"), ("none", "30.00"), ("read", "0.50"), ("write_5m", "6.25")}
    assert len(next(model for model in catalog["models"] if model["model"] == "gpt-optional")["rates"]) == 2


def test_provider_table_parser_accepts_reviewed_anthropic_mtok_columns_and_slug_alias():
    page = b"""<table>
      <tr><th>Model</th><th>Base Input Tokens</th><th>5m Cache Writes</th><th>1h Cache Writes</th><th>Cache Hits &amp; Refreshes</th><th>Output Tokens</th></tr>
      <tr><td>Claude Opus 4.8</td><td>$5 / MTok</td><td>$6.25 / MTok</td><td>$10 / MTok</td><td>$0.50 / MTok</td><td>$25 / MTok</td></tr>
    </table>"""
    catalog = parse_provider_pricing_table(page, provider="anthropic", source_url="https://platform.claude.com/docs/en/about-claude/pricing")
    model = catalog["models"][0]
    assert "claude-opus-4-8" in model["aliases"]
    assert {(rate["cache_role"], rate["usd"]) for rate in model["rates"]} == {("none", "5"), ("none", "25"), ("read", "0.50"), ("write_5m", "6.25"), ("write_1h", "10")}


def test_bounded_refresh_fetch_rejects_non_allowlisted_redirectable_urls_without_network():
    for url in ("http://platform.openai.com/docs/pricing", "https://127.0.0.1/pricing", "https://platform.openai.com@evil.example/pricing"):
        with pytest.raises(Exception):
            pricing_catalog._bounded_https_fetch(url)


def test_bounded_refresh_fetch_rejects_oversized_and_invalid_compressed_bodies(monkeypatch):
    class Response:
        status = 200

        def __init__(self, body, headers):
            self._body = body
            self.headers = headers

        def read(self, _limit):
            return self._body

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    monkeypatch.setattr(pricing_catalog.urllib.request, "build_opener", lambda *_args: SimpleNamespace(open=lambda *_args, **_kwargs: Response(b"x" * (pricing_catalog.MAX_SOURCE_BYTES + 1), {})))
    with pytest.raises(Exception, match="exceeds limit"):
        pricing_catalog._bounded_https_fetch("https://platform.openai.com/docs/pricing")

    monkeypatch.setattr(pricing_catalog.urllib.request, "build_opener", lambda *_args: SimpleNamespace(open=lambda *_args, **_kwargs: Response(b"not-gzip", {"Content-Encoding": "gzip"})))
    with pytest.raises(Exception, match="invalid compressed"):
        pricing_catalog._bounded_https_fetch("https://platform.openai.com/docs/pricing")

    huge = gzip.compress(b"x" * (pricing_catalog.MAX_SOURCE_BYTES + 1))
    monkeypatch.setattr(pricing_catalog.urllib.request, "build_opener", lambda *_args: SimpleNamespace(open=lambda *_args, **_kwargs: Response(huge, {"Content-Encoding": "gzip"})))
    with pytest.raises(Exception, match="decompressed.*exceeds limit"):
        pricing_catalog._bounded_https_fetch("https://platform.openai.com/docs/pricing")


def test_bounded_refresh_fetch_rejects_redirect_and_timeout_without_following_them(monkeypatch):
    class Response:
        status = 302
        headers = {"Location": "https://evil.example/pricing"}

        def read(self, _limit):
            return b""

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    monkeypatch.setattr(pricing_catalog.urllib.request, "build_opener", lambda *_args: SimpleNamespace(open=lambda *_args, **_kwargs: Response()))
    with pytest.raises(Exception, match="HTTP 302"):
        pricing_catalog._bounded_https_fetch("https://platform.openai.com/docs/pricing")
    monkeypatch.setattr(pricing_catalog.urllib.request, "build_opener", lambda *_args: SimpleNamespace(open=lambda *_args, **_kwargs: (_ for _ in ()).throw(TimeoutError("fixture timeout"))))
    with pytest.raises(Exception, match="unavailable"):
        pricing_catalog._bounded_https_fetch("https://platform.openai.com/docs/pricing")


def test_json_parsers_keep_machine_and_corroboration_provenance_distinct():
    openrouter = parse_openrouter_prices(json.dumps({"data": [{"id": "openai/gpt-test", "pricing": {"prompt": "0.000002", "completion": "0.000008"}}]}).encode())
    assert openrouter["models"][0]["source"]["kind"] == "corroboration"
    assert openrouter["models"][0]["rates"][0]["usd"] == "2.000000"
    litellm = parse_litellm_prices(json.dumps({"gpt-test": {"litellm_provider": "openai", "input_cost_per_token": 0.000002, "output_cost_per_token": 0.000008}}).encode())
    assert litellm["models"][0]["provider"] == "openai"
    assert litellm["models"][0]["source"]["kind"] == "machine"
    assert {item.name for item in default_pricing_source_adapters()} == {"openai", "anthropic", "google", "openrouter", "litellm"}


def test_machine_json_is_structured_only_and_fills_without_overriding_provider_page(tmp_path):
    with pytest.raises(PricingCatalogValidationError, match="no usable models"):
        parse_litellm_prices(b"{}")
    offline = PricingCatalog(tmp_path / "offline")
    before = offline.resolve_rate(provider="openai", model="gpt-4.1", direction="input")
    broken = PricingSourceAdapter("machine", "https://raw.githubusercontent.com/BerriAI/litellm/main/model_prices_and_context_window.json", "machine", parse_litellm_prices)
    with pytest.raises(Exception, match="no usable models"):
        offline.refresh([broken], fetch=lambda *_args: (200, {}, b"{}"))
    assert offline.resolve_rate(provider="openai", model="gpt-4.1", direction="input") == before

    def normalized(models):
        return {"schema_version": 1, "catalog_revision": 1, "models": models}

    def model(name, usd, url):
        return {"provider": "openai", "model": name, "aliases": [name], "rates": [{"direction": "input", "modality": "text", "cache_role": "none", "unit": "tokens", "scale": 1_000_000, "usd": usd, "effective_from": "2026-01-01T00:00:00Z"}], "source": {"url": url, "kind": "official"}}

    provider_url = "https://developers.openai.com/api/docs/pricing"
    machine_url = "https://raw.githubusercontent.com/BerriAI/litellm/main/model_prices_and_context_window.json"
    provider = PricingSourceAdapter("provider", provider_url, "official", lambda body: json.loads(body))
    machine = PricingSourceAdapter("machine", machine_url, "machine", lambda body: json.loads(body))
    bodies = {
        provider_url: normalized([model("gpt-layered", "2.00", provider_url)]),
        machine_url: normalized([model("gpt-layered", "9.00", machine_url), model("gpt-bulk-only", "3.00", machine_url)]),
    }
    catalog = PricingCatalog(tmp_path)
    catalog.refresh([provider, machine], fetch=lambda url, *_args: (200, {}, json.dumps(bodies[url]).encode()))

    assert str(catalog.resolve_rate(provider="openai", model="gpt-layered", direction="input").usd) == "2.00"
    assert str(catalog.resolve_rate(provider="openai", model="gpt-bulk-only", direction="input").usd) == "3.00"


def test_conservative_family_inference_is_labeled_and_ranked_between_official_and_seed(tmp_path):
    catalog = PricingCatalog(tmp_path)
    payload = {"schema_version": 1, "catalog_revision": 9, "models": [{"provider": "openai", "model": "gpt-5.6", "aliases": ["gpt-5.6"], "rates": [{"direction": "input", "modality": "text", "cache_role": "none", "unit": "tokens", "scale": 1_000_000, "usd": "4.00", "effective_from": "2026-01-01T00:00:00Z"}], "source": {"url": "https://developers.openai.com/api/docs/pricing", "kind": "official"}}]}
    adapter = PricingSourceAdapter("openai", "https://developers.openai.com/api/docs/pricing", "official", lambda _body: payload)
    catalog.refresh([adapter], fetch=lambda *_args: (200, {}, b"fixture"))

    inferred = catalog.resolve_rate(provider="openai", model="gpt-5.6-sol-preview", direction="input")
    exact_seed_displaced = catalog.resolve_rate(provider="openai", model="gpt-5.6-sol", direction="input")
    assert inferred is not None and inferred.source_kind == "inferred" and str(inferred.usd) == "4.00"
    assert exact_seed_displaced is not None and exact_seed_displaced.source_kind == "inferred"
    assert catalog.resolve_rate(provider="openai", model="gpt-5.6-unrelated", direction="input") is None


def test_refresh_coalesces_cross_server_equivalent_followup(tmp_path):
    catalog = PricingCatalog(tmp_path, clock=lambda: 100.0)
    payload = load_packaged_seed()
    payload["catalog_revision"] = 3
    adapter = PricingSourceAdapter("openai", "https://platform.openai.com/docs/pricing", "official", lambda value: json.loads(value.decode()))
    calls = []

    def fetch(*_args):
        calls.append(1)
        return 200, {}, json.dumps(payload).encode()

    assert catalog.refresh([adapter], fetch=fetch)["status"] == "updated"
    assert catalog.refresh([adapter], fetch=fetch)["status"] == "coalesced"
    assert calls == [1]


def test_refresh_activates_valid_official_source_when_another_source_fails(tmp_path):
    catalog = PricingCatalog(tmp_path)
    payload = load_packaged_seed()
    payload["catalog_revision"] = 2
    good = PricingSourceAdapter("openai", "https://developers.openai.com/api/docs/pricing", "official", lambda value: json.loads(value.decode()))
    stale = PricingSourceAdapter("google", "https://ai.google.dev/gemini-api/docs/pricing", "official", lambda _value: (_ for _ in ()).throw(PricingCatalogValidationError("columns changed")))

    result = catalog.refresh([good, stale], fetch=lambda url, *_args: (200, {}, json.dumps(payload).encode() if "openai" in url else b"changed"))

    assert result["status"] == "updated"
    assert result["source_failures"] == [{"name": "google", "error": "columns changed"}]
    assert catalog.resolve_rate(provider="openai", model="gpt-4.1", direction="input").source_kind == "official"


def test_successful_refresh_clears_review_needed_state_from_an_older_failed_run(tmp_path):
    catalog = PricingCatalog(tmp_path)
    broken = PricingSourceAdapter("openai", "https://developers.openai.com/api/docs/pricing", "official", lambda _value: (_ for _ in ()).throw(PricingCatalogValidationError("old failure")))
    with pytest.raises(Exception, match="old failure"):
        catalog.refresh([broken], fetch=lambda *_args: (200, {}, b"changed"))
    assert catalog.status()["state"] == "seed-only"

    payload = load_packaged_seed()
    payload["catalog_revision"] = 2
    valid = PricingSourceAdapter("openai", "https://developers.openai.com/api/docs/pricing", "official", lambda value: json.loads(value.decode()))
    catalog.refresh([valid], fetch=lambda *_args: (200, {}, json.dumps(payload).encode()))

    assert catalog.status()["state"] == "fresh"


def test_refresh_coordinator_returns_immediately_and_coalesces_in_process(tmp_path):
    catalog = PricingCatalog(tmp_path)
    coordinator = PricingRefreshCoordinator(catalog, adapters=())
    first = coordinator.start()
    second = coordinator.start()
    assert first["status"] == "running"
    assert second["coalesced"] is True
    coordinator._thread.join(timeout=2)  # the empty fixture has no network work
    assert coordinator.status()["status"] == "done"


def test_periodic_refresh_schedules_stale_startup_but_defers_fresh_catalog():
    class FakeCatalog:
        def __init__(self, due):
            self.due = due

        def refresh_due(self):
            return self.due

        def refresh(self, _adapters):
            return {"ok": True, "status": "unchanged"}

    class FakeTimer:
        created = []

        def __init__(self, delay, callback):
            self.delay, self.callback, self.daemon = delay, callback, False
            self.cancelled = False
            self.created.append(self)

        def start(self):
            pass

        def cancel(self):
            self.cancelled = True

    FakeTimer.created = []
    stale = PricingRefreshCoordinator(FakeCatalog(True), adapters=(), clock=lambda: 100.0, random_source=lambda: 0.0, timer_factory=FakeTimer)
    stale.start_periodic()
    assert FakeTimer.created[-1].delay == 0
    stale.stop_periodic()

    fresh = PricingRefreshCoordinator(FakeCatalog(False), adapters=(), clock=lambda: 100.0, random_source=lambda: 0.0, timer_factory=FakeTimer)
    fresh.start_periodic()
    assert FakeTimer.created[-1].delay == pricing_catalog.PRICING_REFRESH_INTERVAL_SECONDS
    fresh.stop_periodic()


def test_periodic_refresh_failure_publishes_and_uses_exponential_backoff():
    class BrokenCatalog:
        def refresh_due(self):
            return True

        def refresh(self, _adapters):
            raise RuntimeError("fixture unavailable")

    class FakeTimer:
        created = []

        def __init__(self, delay, callback):
            self.delay, self.callback, self.daemon = delay, callback, False
            self.created.append(self)

        def start(self):
            pass

        def cancel(self):
            pass

    FakeTimer.created = []
    published = []
    coordinator = PricingRefreshCoordinator(BrokenCatalog(), adapters=(), publish=lambda name, payload: published.append((name, payload)), clock=lambda: 200.0, random_source=lambda: 0.0, timer_factory=FakeTimer)
    coordinator.start_periodic()
    FakeTimer.created[-1].callback()
    coordinator._thread.join(timeout=2)

    assert coordinator.status()["backoff_seconds"] == pricing_catalog.PRICING_REFRESH_BACKOFF_INITIAL_SECONDS
    assert FakeTimer.created[-1].delay == pricing_catalog.PRICING_REFRESH_BACKOFF_INITIAL_SECONDS
    assert published[-1][0] == "pricing_catalog_changed"


def test_explicit_override_wins_over_official_and_survives_seed_upgrade(monkeypatch, tmp_path):
    catalog = PricingCatalog(tmp_path)
    seed = load_packaged_seed()
    override = json.loads(json.dumps(seed))
    override["models"][0]["rates"][0]["usd"] = "9.00"
    catalog.set_override(override)
    assert str(catalog.resolve_rate(provider="openai", model="gpt-4.1", direction="input").usd) == "9.00"

    upgraded_seed = json.loads(json.dumps(seed))
    upgraded_seed["catalog_revision"] = 2
    upgraded_seed["models"][0]["rates"][0]["usd"] = "3.00"
    monkeypatch.setattr("yolomux_lib.pricing_catalog.load_packaged_seed", lambda: upgraded_seed)
    catalog.reconcile_seed()
    selected = catalog.resolve_rate(provider="openai", model="gpt-4.1", direction="input")
    assert selected is not None and str(selected.usd) == "9.00" and selected.source_kind == "override"


def test_seed_downgrade_does_not_replace_newer_official_catalog(monkeypatch, tmp_path):
    catalog = PricingCatalog(tmp_path)
    official = load_packaged_seed()
    official["catalog_revision"] = 3
    official["models"][0]["rates"][0]["usd"] = "4.00"
    adapter = PricingSourceAdapter("openai", "https://platform.openai.com/docs/pricing", "official", lambda value: json.loads(value.decode()))
    catalog.refresh([adapter], fetch=lambda *_args: (200, {}, json.dumps(official).encode()))
    monkeypatch.setattr("yolomux_lib.pricing_catalog.load_packaged_seed", load_packaged_seed)
    catalog.reconcile_seed()
    selected = catalog.resolve_rate(provider="openai", model="gpt-4.1", direction="input")
    assert selected is not None and str(selected.usd) == "4.00" and selected.source_kind == "official"
    assert catalog.status()["catalog_revision"] == 3


def test_public_rate_and_catalog_payload_expose_only_safe_source_evidence(tmp_path):
    catalog = PricingCatalog(tmp_path)
    rate = catalog.resolve_rate(provider="openai", model="gpt-4.1", direction="input")
    assert rate is not None
    evidence = rate.public_payload()
    assert evidence["effective_from"] == "2025-01-01T00:00:00Z"
    assert evidence["source_url"] == "https://developers.openai.com/api/docs/pricing"
    public = catalog.public_payload()
    assert public["status"]["state"] == "seed-only"
    assert public["sources"] == [
        {"kind": "seed", "url": "https://developers.openai.com/api/docs/pricing", "revision": 2},
        {"kind": "seed", "url": "https://platform.claude.com/docs/en/about-claude/pricing", "revision": 2},
    ]
    assert safe_source_url("javascript:alert(1)") == ""
    assert safe_source_url("https://example.invalid/pricing") == ""
