# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Current-only catalog pricing projection contracts."""

import hashlib
import json
from contextlib import nullcontext
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from yolomux_lib.pricing_catalog import PricingCatalog, ResolvedRate
from yolomux_lib.stats_current import materializer, pricing, storage, usage
from yolomux_lib.stats_current import service as service_module
from tests.cross_layer_matrix import observed_fixture_atoms


def _atom(
    *, event_id="usage-1", model="gpt-5.6-sol", quantity=1_000_000,
    observed_at=None, pricing_profile="default",
):
    timestamp = _observed_at() if observed_at is None else observed_at
    return storage.UsageAtom(event_id, "output", "text", "none", "tokens", timestamp, {
        "quantity": quantity,
        "provider": "openai",
        "model": model,
        "agent_id": "agent",
        "pricing_profile": pricing_profile,
        "telemetry_complete": True,
    })


def _observed_at():
    return datetime(2026, 7, 11, tzinfo=timezone.utc).timestamp()


def _snapshot(*atoms):
    return storage.StoreSnapshot(
        storage.SchemaMetadata(5, 23, 1, 1), (), (), atoms, (), (),
    )


def _build(snapshot, resolver, cache_generation):
    observed_until = _observed_at() + 1
    return materializer.build_generation(
        snapshot,
        source_generation=1,
        cache_generation=cache_generation,
        generated_at=observed_until,
        observed_until=observed_until,
        price_resolver=resolver,
    )


def _cost(generation):
    bucket = next(
        bucket
        for bucket in generation.layer(1).buckets
        if bucket.start == int(_observed_at())
    )
    return next(item.value for item in bucket.series if item.name == "cost_micro_usd")


def _api_list_cost(generation):
    bucket = next(
        bucket
        for bucket in generation.layer(1).buckets
        if bucket.start == int(_observed_at())
    )
    return next(
        item.value for item in bucket.series
        if item.name == "api_list_cost_micro_usd"
    )


def _total_cost(generation):
    return sum(
        item.value
        for bucket in generation.layer(1).buckets
        for item in bucket.series
        if item.name == "cost_micro_usd"
    )


def test_every_model_discovered_from_observed_transcript_usage_is_priced(tmp_path):
    atoms = observed_fixture_atoms()
    assert atoms, "the observed transcript corpus must emit billable usage"
    resolver = pricing.UsagePriceProjector(PricingCatalog(tmp_path / "pricing"))

    unpriced = sorted({
        (atom.payload["provider"], atom.payload["model"], atom.direction, atom.cache_role)
        for atom in atoms
        if not resolver(atom).priced
    })

    assert unpriced == []


class CountingCatalog:
    def __init__(self):
        self.status_calls = 0
        self.resolve_calls = 0

    def status(self):
        self.status_calls += 1
        return {"catalog_revision": 7}

    def resolve_rate(self, **_fields):
        self.resolve_calls += 1
        return ResolvedRate(
            "openai", "gpt-5.6-sol", "gpt-5.6-sol", "output", "text",
            "none", "tokens", 1_000_000, Decimal("30.00"),
            "2026-07-09T00:00:00Z", "seed", "", 7,
        )


class ProfileCatalog(CountingCatalog):
    def __init__(self):
        super().__init__()
        self.profiles = []

    def resolve_rate(self, **fields):
        self.resolve_calls += 1
        profile = fields["profile"]
        self.profiles.append(profile)
        return ResolvedRate(
            "openai", "gpt-5.6-sol", "gpt-5.6-sol", "output", "text",
            "none", "tokens", 1_000_000,
            Decimal("15.00" if profile == "batch" else "30.00"),
            "2026-07-09T00:00:00Z", "seed", "", 7,
        )


def test_seed_priced_model_projects_exact_nonzero_integer_micro_usd(tmp_path):
    resolver = pricing.UsagePriceProjector(PricingCatalog(tmp_path / "pricing"))

    projection = resolver(_atom())

    assert projection.micro_usd == 30_000_000
    assert projection.api_list_micro_usd == 30_000_000
    assert isinstance(projection.micro_usd, int)
    assert projection.evidence is not None
    assert projection.evidence.catalog_model == "gpt-5.6-sol"
    assert projection.evidence.catalog_revision == 5
    assert projection.evidence.rate_usd == "30.00"
    assert projection.evidence.rate_scale == 1_000_000
    assert projection.evidence.source_kind == "seed"
    assert projection.evidence.source_url.startswith("https://developers.openai.com/")
    assert _cost(_build(_snapshot(_atom()), resolver, 1)) == 30_000_000
    assert _api_list_cost(_build(_snapshot(_atom()), resolver, 2)) == 30_000_000


def test_switchyard_provider_uses_the_catalog_model_rate(tmp_path):
    resolver = pricing.UsagePriceProjector(PricingCatalog(tmp_path / "pricing"))
    atom = _atom()
    atom = storage.UsageAtom(
        atom.event_id, atom.direction, atom.modality, atom.cache_role, atom.unit,
        atom.observed_at, {**atom.payload, "provider": "switchyard"},
    )

    projection = resolver(usage.normalize_usage_atom(atom))

    assert projection.priced is True
    assert projection.micro_usd == 30_000_000
    assert projection.api_list_micro_usd == 30_000_000


def test_switchyard_routed_openai_model_uses_the_catalog_model_rate(tmp_path):
    resolver = pricing.UsagePriceProjector(PricingCatalog(tmp_path / "pricing"))
    atom = _atom()
    atom = storage.UsageAtom(
        atom.event_id, atom.direction, atom.modality, atom.cache_role, atom.unit,
        datetime(2026, 8, 3, tzinfo=timezone.utc).timestamp(),
        {**atom.payload, "provider": "switchyard", "model": "openai/gpt-5.6-luna", "quantity": 480_000},
    )

    projection = resolver(usage.normalize_usage_atom(atom))

    assert projection.priced is True
    assert projection.micro_usd == 422_784
    assert projection.api_list_micro_usd == 422_784


def test_inferencehub_switchyard_openai_model_uses_the_catalog_model_rate(tmp_path):
    resolver = pricing.UsagePriceProjector(PricingCatalog(tmp_path / "pricing"))
    atom = _atom()
    atom = storage.UsageAtom(
        atom.event_id, atom.direction, atom.modality, atom.cache_role, atom.unit,
        datetime(2026, 8, 3, tzinfo=timezone.utc).timestamp(),
        {**atom.payload, "provider": "inferencehub", "model": "switchyard/openai/gpt-5.6-luna", "quantity": 480_000},
    )

    projection = resolver(usage.normalize_usage_atom(atom))

    assert projection.priced is True
    assert projection.micro_usd == 422_784
    assert projection.api_list_micro_usd == 422_784


def test_inferencehub_openai_identity_with_repeated_provider_prefix_is_priced(tmp_path):
    resolver = pricing.UsagePriceProjector(PricingCatalog(tmp_path / "pricing"))
    atom = _atom()
    atom = storage.UsageAtom(
        atom.event_id, atom.direction, atom.modality, atom.cache_role, atom.unit,
        atom.observed_at,
        {**atom.payload, "provider": "inferencehub", "model": "openai/openai/gpt-5.6-luna", "quantity": 480_000},
    )

    projection = resolver(usage.normalize_usage_atom(atom))

    assert projection.priced is True
    assert projection.micro_usd == 422_784


def test_subscription_profile_is_zero_marginal_with_api_list_counterfactual(tmp_path):
    resolver = pricing.UsagePriceProjector(PricingCatalog(tmp_path / "pricing"))
    atom = _atom(pricing_profile="subscription")

    projection = resolver(atom)
    generation = _build(_snapshot(atom), resolver, 1)
    bucket = next(
        item for item in generation.layer(1).buckets
        if item.start == int(_observed_at())
    )
    report = materializer.build_cost_report(
        materializer.slice_generation(generation, 300, 1),
    )

    assert projection.micro_usd == 0
    assert projection.api_list_micro_usd == 30_000_000
    assert projection.priced is True
    assert projection.evidence is not None
    assert _cost(generation) == 0
    assert _api_list_cost(generation) == 30_000_000
    assert bucket.cost_detail.priced.tokens == 1_000_000
    assert bucket.cost_detail.evidence[0].pricing_profile == "subscription"
    assert bucket.cost_detail.evidence[0].micro_usd == 0
    assert bucket.cost_detail.evidence[0].api_list_micro_usd == 30_000_000
    assert report["total_tokens"] == 1_000_000
    assert report["total_micro_usd"] == 0
    assert report["total_api_list_micro_usd"] == 30_000_000
    assert report["priced"] == {"atoms": 1, "tokens": 1_000_000}
    assert report["evidence"][0]["pricing_profile"] == "subscription"
    assert report["evidence"][0]["micro_usd"] == 0
    assert report["evidence"][0]["api_list_micro_usd"] == 30_000_000


def test_nondefault_catalog_profile_keeps_its_marginal_rate_evidence():
    catalog = ProfileCatalog()
    resolver = pricing.UsagePriceProjector(
        catalog, revision_check_seconds=60, monotonic=lambda: 10.0,
    )

    projection = resolver(_atom(pricing_profile="batch"))

    assert projection.micro_usd == 15_000_000
    assert projection.api_list_micro_usd == 30_000_000
    assert projection.evidence is not None
    assert projection.evidence.rate_usd == "15.00"
    assert catalog.profiles == ["default", "batch"]


def test_unknown_model_remains_unpriced_instead_of_using_a_guess(tmp_path):
    resolver = pricing.UsagePriceProjector(PricingCatalog(tmp_path / "pricing"))

    projection = resolver(_atom(model="not-a-catalog-model"))

    assert projection.micro_usd is None
    assert projection.api_list_micro_usd is None
    assert projection.evidence is None


def test_known_sub_micro_usd_cost_rounds_honestly_to_zero(tmp_path):
    resolver = pricing.UsagePriceProjector(PricingCatalog(tmp_path / "pricing"))
    atom = storage.UsageAtom(
        "tiny", "input", "text", "read", "tokens", _observed_at(), {
            "quantity": 1,
            "provider": "openai",
            "model": "gpt-5.6-terra",
            "agent_id": "agent",
            "telemetry_complete": True,
        },
    )

    projection = resolver(atom)

    assert projection.micro_usd == 0
    assert projection.api_list_micro_usd == 0
    assert projection.evidence is not None


def test_warm_full_reconciliation_reuses_effective_rate_window():
    catalog = CountingCatalog()
    resolver = pricing.UsagePriceProjector(
        catalog, revision_check_seconds=60, monotonic=lambda: 10.0,
    )
    snapshot = _snapshot(
        _atom(),
        _atom(event_id="usage-2", observed_at=_observed_at() + 1),
    )

    first = _build(snapshot, resolver, 1)
    second = _build(snapshot, resolver, 2)

    assert _total_cost(first) == _total_cost(second) == 60_000_000
    assert catalog.status_calls == 1
    assert catalog.resolve_calls == 1


def test_effective_rate_boundaries_are_cached_without_crossing_price_changes():
    class EffectiveCatalog(CountingCatalog):
        def resolve_rate(self, **fields):
            self.resolve_calls += 1
            before_change = fields["timestamp"] < "2026-07-12T00:00:00Z"
            return ResolvedRate(
                "openai",
                "gpt-5.6-sol",
                "gpt-5.6-sol",
                "output",
                "text",
                "none",
                "tokens",
                1_000_000,
                Decimal("30.00" if before_change else "40.00"),
                "2026-07-09T00:00:00Z" if before_change else "2026-07-12T00:00:00Z",
                "seed",
                "",
                7,
                "2026-07-12T00:00:00Z" if before_change else None,
            )

    catalog = EffectiveCatalog()
    resolver = pricing.UsagePriceProjector(
        catalog,
        revision_check_seconds=60,
        monotonic=lambda: 10.0,
    )
    before = _observed_at()
    after = datetime(2026, 7, 13, tzinfo=timezone.utc).timestamp()

    projections = (
        resolver(_atom(event_id="before-1", observed_at=before)),
        resolver(_atom(event_id="before-2", observed_at=before + 1)),
        resolver(_atom(event_id="after-1", observed_at=after)),
        resolver(_atom(event_id="after-2", observed_at=after + 1)),
    )

    assert [item.micro_usd for item in projections] == [
        30_000_000,
        30_000_000,
        40_000_000,
        40_000_000,
    ]
    assert catalog.resolve_calls == 2


def test_rate_dimension_cache_is_bounded_and_evicts_least_recently_used():
    catalog = CountingCatalog()
    resolver = pricing.UsagePriceProjector(
        catalog,
        max_rate_dimensions=1,
        revision_check_seconds=60,
        monotonic=lambda: 10.0,
    )

    resolver(_atom(model="model-a"))
    resolver(_atom(model="model-b"))
    resolver(_atom(model="model-a"))

    assert catalog.resolve_calls == 3


def test_current_service_default_projects_seed_priced_usage(tmp_path, monkeypatch):
    catalog_root = tmp_path / "pricing"
    monkeypatch.setattr(pricing, "PricingCatalog", lambda: PricingCatalog(catalog_root))
    service = service_module.StatsCurrentService(
        tmp_path / "statsd.sock",
        tmp_path / storage.DATABASE_FILENAME,
        clock=lambda: _observed_at() + 1,
    )
    reader = type("SnapshotReader", (), {
        "pinned_snapshot": lambda self, **_kwargs: nullcontext(
            lambda: _snapshot(_atom())
        ),
    })()

    service._build_once(reader, True, frozenset())

    assert isinstance(service.price_resolver, pricing.UsagePriceProjector)
    assert service._cache is not None
    assert _cost(service._cache.generation) == 30_000_000


# --- the materializer -> pricing seam --------------------------------------------------------
# `materializer._build` normalizes every stored usage atom once before projecting it, so the
# projector receives an atom that is already canonical. It used to renormalize that atom a
# second time, which repeated the whole validation for every atom on every build.


def _count_canonical_text_validations(monkeypatch):
    """Record every string the usage owner validates, however it is reached."""

    names: list[str] = []
    original = usage._text

    def counting(value, name, **options):
        names.append(name)
        return original(value, name, **options)

    monkeypatch.setattr(usage, "_text", counting)
    return names


def test_a_build_normalizes_each_usage_atom_exactly_once(tmp_path, monkeypatch):
    """Two passes over the same atom is duplicate work, not a second opinion."""

    resolver = pricing.UsagePriceProjector(PricingCatalog(tmp_path / "pricing"))
    atoms = tuple(_atom(event_id=f"usage-{index}") for index in range(4))

    validations = _count_canonical_text_validations(monkeypatch)
    usage.normalize_usage_atom(atoms[0])
    per_atom = len(validations)
    assert per_atom > 0, "the counter never observed a normalization"

    validations.clear()
    _build(_snapshot(*atoms), resolver, 1)

    assert len(validations) == per_atom * len(atoms)


def test_price_projection_refuses_an_atom_nobody_normalized(tmp_path):
    """An un-normalized enum must raise, not miss the catalog and read as merely unpriced."""

    resolver = pricing.UsagePriceProjector(PricingCatalog(tmp_path / "pricing"))
    canonical = usage.normalize_usage_atom(_atom())
    assert resolver(canonical).micro_usd == 30_000_000

    for field, value in (
        ("direction", "OUTPUT"), ("modality", " text"),
        ("cache_role", "NONE"), ("unit", "Tokens"),
    ):
        raw = storage.UsageAtom(**{
            "event_id": "usage-1", "direction": "output", "modality": "text",
            "cache_role": "none", "unit": "tokens", "observed_at": _observed_at(),
            "payload": dict(canonical.payload), **{field: value},
        })
        with pytest.raises(pricing.PricingProjectionError):
            resolver(raw)


def test_served_cost_report_is_byte_identical_for_a_pinned_usage_snapshot(tmp_path):
    """Dropping the duplicate normalization must not move one price, dimension or rounding."""

    resolver = pricing.UsagePriceProjector(PricingCatalog(tmp_path / "pricing"))
    atoms = (
        _atom(event_id="usage-1"),
        _atom(event_id="usage-2", quantity=1500),
        _atom(event_id="usage-3", pricing_profile="subscription"),
    )

    report = materializer.build_cost_report(_build(_snapshot(*atoms), resolver, 1).layer(1))
    served = json.dumps(report, sort_keys=True, separators=(",", ":")).encode()

    assert report["catalog_revision"] == 5
    assert report["dimensions"]["output"] == {
        "api_list_micro_usd": 60_045_000, "micro_usd": 30_045_000, "tokens": 2_001_500,
    }
    assert len(served) == 3652
    assert hashlib.sha256(served).hexdigest() == (
        "ba863dc767dee10a496074ffefa1e3dc7ad8e40897561fa2393271e0ee8b5ec4"
    )
