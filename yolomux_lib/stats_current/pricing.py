# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Exact current usage-cost projection backed by the shared pricing catalog."""

from __future__ import annotations

import threading
import time
from collections import OrderedDict
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from types import MappingProxyType
from typing import Protocol

from ..observability.pricing_catalog import PricingCatalog, PricingCatalogError, ResolvedRate
from .protocol import MAX_SAFE_INTEGER
from .storage import UsageAtom
from .usage import CACHE_ROLES, DIRECTIONS, MODALITIES, UNITS


DEFAULT_MAX_RATE_DIMENSIONS = 4_096
DEFAULT_REVISION_CHECK_SECONDS = 300.0
MICRO_USD_PER_USD = Decimal(1_000_000)
DEFAULT_PRICING_PROFILE = "default"
SUBSCRIPTION_PRICING_PROFILE = "subscription"


class PricingProjectionError(ValueError):
    """The shared catalog cannot produce a safe current cost projection."""


@dataclass(frozen=True, slots=True)
class PricingEvidence:
    catalog_model: str
    rate_usd: str
    rate_scale: int
    effective_from: str
    source_kind: str
    source_url: str
    catalog_revision: int


@dataclass(frozen=True, slots=True)
class UsagePriceProjection:
    micro_usd: int | None
    api_list_micro_usd: int | None
    evidence: PricingEvidence | None

    @property
    def priced(self) -> bool:
        return (
            self.micro_usd is not None
            and self.api_list_micro_usd is not None
            and self.evidence is not None
        )


class RateCatalog(Protocol):
    def status(self) -> dict[str, object]: ...

    def resolve_rate(
        self,
        *,
        provider: str,
        model: str,
        direction: str,
        modality: str = "text",
        cache_role: str = "none",
        unit: str = "tokens",
        profile: str = "default",
        service_tier: str = "default",
        timestamp: str = "9999-12-31T23:59:59Z",
    ) -> ResolvedRate | None: ...


@dataclass(frozen=True, slots=True)
class _RateDimension:
    provider: str
    model: str
    direction: str
    modality: str
    cache_role: str
    unit: str
    profile: str
    service_tier: str


def _catalog_provider(provider: str, model: str) -> tuple[str, str]:
    """Map routed provider identities to the reviewed catalog provider."""
    route = provider.strip().lower()
    catalog_model = model.strip()
    lowered_model = catalog_model.lower()
    if lowered_model.startswith("switchyard/openai/"):
        return "openai", catalog_model.split("/", 2)[2]
    if route == "inferencehub" and lowered_model.startswith("openai/"):
        while catalog_model.lower().startswith("openai/"):
            catalog_model = catalog_model.split("/", 1)[1]
        return "openai", catalog_model
    if route == "switchyard" and "/" in catalog_model:
        routed_provider, catalog_model = catalog_model.split("/", 1)
        route = routed_provider
    if route == "switchyard":
        route = "openai"
    if route == "openai":
        while catalog_model.lower().startswith("openai/"):
            catalog_model = catalog_model.split("/", 1)[1]
    return route, catalog_model


@dataclass(slots=True)
class _RateWindow:
    start: str | None
    end: str | None
    rate: ResolvedRate | None

    def contains(self, timestamp: str) -> bool:
        if self.rate is None:
            return self.end is not None and timestamp <= self.end
        return (
            self.start is not None
            and self.start <= timestamp
            and (self.end is None or timestamp < self.end)
        )


class UsagePriceProjector:
    """Resolve exact integer micro-USD once, then reuse effective-rate windows.

    A successful lookup proves that its rate applies from ``effective_from``
    through the queried timestamp. A missing lookup proves no rate applies up
    through that timestamp. Those windows let periodic full reconciliation
    become cache-only after the cold chronological pass without guessing rates.
    """

    def __init__(
        self,
        catalog: RateCatalog | None = None,
        *,
        max_rate_dimensions: int = DEFAULT_MAX_RATE_DIMENSIONS,
        revision_check_seconds: float = DEFAULT_REVISION_CHECK_SECONDS,
        monotonic: Callable[[], float] = time.monotonic,
    ):
        if isinstance(max_rate_dimensions, bool) or not isinstance(max_rate_dimensions, int) or max_rate_dimensions < 1:
            raise ValueError("max_rate_dimensions must be a positive integer")
        if revision_check_seconds <= 0:
            raise ValueError("revision_check_seconds must be positive")
        self.catalog: RateCatalog = catalog if catalog is not None else PricingCatalog()
        self.max_rate_dimensions = max_rate_dimensions
        self.revision_check_seconds = float(revision_check_seconds)
        self.monotonic = monotonic
        self._lock = threading.RLock()
        self._catalog_revision: int | None = None
        self._next_revision_check = float("-inf")
        self._windows: OrderedDict[_RateDimension, list[_RateWindow]] = OrderedDict()

    def __call__(self, atom: UsageAtom) -> UsagePriceProjection:
        """Project the cost of one atom that `usage.normalize_usage_atom` already produced."""

        _require_canonical(atom)
        timestamp = _iso_timestamp(atom.observed_at)
        dimension = _dimension(atom)
        catalog_provider, catalog_model = _catalog_provider(dimension.provider, dimension.model)
        dimension = _RateDimension(
            catalog_provider, catalog_model, dimension.direction, dimension.modality,
            dimension.cache_role, dimension.unit, dimension.profile, dimension.service_tier,
        )
        with self._lock:
            self._refresh_revision_if_due()
            api_list_rate = self._rate(_api_list_dimension(dimension), timestamp)
            if api_list_rate is None:
                return UsagePriceProjection(None, None, None)
            if dimension.profile == SUBSCRIPTION_PRICING_PROFILE:
                marginal_micro_usd = 0
                evidence_rate = api_list_rate
            else:
                marginal_rate = (
                    api_list_rate
                    if dimension.profile == DEFAULT_PRICING_PROFILE
                    else self._rate(dimension, timestamp)
                )
                if marginal_rate is None:
                    return UsagePriceProjection(None, None, None)
                marginal_micro_usd = _micro_usd(atom.payload["quantity"], marginal_rate)
                evidence_rate = marginal_rate
            api_list_micro_usd = _micro_usd(atom.payload["quantity"], api_list_rate)
            public = evidence_rate.public_payload()
            return UsagePriceProjection(
                marginal_micro_usd,
                api_list_micro_usd,
                PricingEvidence(
                    catalog_model=str(public["model"]),
                    rate_usd=str(public["usd"]),
                    rate_scale=int(public["scale"]),
                    effective_from=str(public["effective_from"]),
                    source_kind=str(public["source_kind"]),
                    source_url=str(public["source_url"]),
                    catalog_revision=int(public["catalog_revision"]),
                ),
            )

    def _rate(self, dimension: _RateDimension, timestamp: str) -> ResolvedRate | None:
        found, rate = self._cached_rate(dimension, timestamp)
        if not found:
            rate = self._resolve(dimension, timestamp)
            self._remember(dimension, timestamp, rate)
        return rate

    def _refresh_revision_if_due(self) -> None:
        now = self.monotonic()
        if now < self._next_revision_check:
            return
        try:
            status = self.catalog.status()
        except PricingCatalogError as error:
            raise PricingProjectionError("pricing catalog status failed") from error
        revision = status.get("catalog_revision")
        if isinstance(revision, bool) or not isinstance(revision, int) or revision < 1:
            raise PricingProjectionError("pricing catalog has no valid revision")
        if self._catalog_revision is not None and revision != self._catalog_revision:
            self._windows.clear()
        self._catalog_revision = revision
        self._next_revision_check = now + self.revision_check_seconds

    def _cached_rate(
        self, dimension: _RateDimension, timestamp: str,
    ) -> tuple[bool, ResolvedRate | None]:
        windows = self._windows.get(dimension)
        if windows is None:
            return False, None
        self._windows.move_to_end(dimension)
        for window in windows:
            if window.contains(timestamp):
                return True, window.rate
        return False, None

    def _resolve(self, dimension: _RateDimension, timestamp: str) -> ResolvedRate | None:
        try:
            return self.catalog.resolve_rate(
                provider=dimension.provider,
                model=dimension.model,
                direction=dimension.direction,
                modality=dimension.modality,
                cache_role=dimension.cache_role,
                unit=dimension.unit,
                profile=dimension.profile,
                service_tier=dimension.service_tier,
                timestamp=timestamp,
            )
        except PricingCatalogError as error:
            raise PricingProjectionError("pricing catalog lookup failed") from error

    def _remember(
        self, dimension: _RateDimension, timestamp: str, rate: ResolvedRate | None,
    ) -> None:
        windows = self._windows.setdefault(dimension, [])
        start = None if rate is None else rate.effective_from
        end = timestamp if rate is None else rate.effective_until
        for window in windows:
            if window.start == start and window.rate == rate:
                if rate is None:
                    window.end = max(window.end or timestamp, timestamp)
                self._windows.move_to_end(dimension)
                return
        windows.append(_RateWindow(start, end, rate))
        windows.sort(key=lambda window: window.start or "", reverse=True)
        self._windows.move_to_end(dimension)
        while len(self._windows) > self.max_rate_dimensions:
            self._windows.popitem(last=False)


def _require_canonical(atom: UsageAtom) -> None:
    """Refuse an atom that has not been through `usage.normalize_usage_atom`.

    Every stored atom is normalized once by the materializer before projection, so
    normalizing again here re-validated every string of every atom on every build for
    an answer that cannot differ. Dropping that second pass makes "already canonical"
    a precondition, and an unchecked precondition is a silently wrong price: an
    upper-case or unstripped enum simply misses the catalog and returns an unpriced
    projection rather than raising. This never rewrites a value -- it only refuses to
    guess, so it cannot become a second normalizing path.
    """

    if not isinstance(atom, UsageAtom):
        raise PricingProjectionError("price projection requires a storage.UsageAtom")
    if (
        atom.direction not in DIRECTIONS
        or atom.modality not in MODALITIES
        or atom.cache_role not in CACHE_ROLES
        or atom.unit not in UNITS
    ):
        raise PricingProjectionError(
            "price projection requires an atom normalized by usage.normalize_usage_atom"
        )
    if isinstance(atom.observed_at, bool) or not isinstance(atom.observed_at, (int, float)):
        raise PricingProjectionError("price projection requires a numeric observed_at")
    # Concrete types on purpose: `isinstance(payload, Mapping)` runs the abc
    # metaclass check, which measured 66% of this guard's own cost. A canonical
    # payload is always the MappingProxyType that normalize_usage_atom returns,
    # and a stored one is the dict the decoder produced.
    payload = atom.payload
    if not isinstance(payload, (dict, MappingProxyType)) or "quantity" not in payload:
        raise PricingProjectionError("price projection requires a payload with a quantity")
    quantity = payload["quantity"]
    if isinstance(quantity, bool) or not isinstance(quantity, (int, float)):
        raise PricingProjectionError("price projection requires a numeric payload.quantity")


def _dimension(atom: UsageAtom) -> _RateDimension:
    payload: Mapping[str, object] = atom.payload
    return _RateDimension(
        str(payload["provider"]),
        str(payload["model"]),
        atom.direction,
        atom.modality,
        atom.cache_role,
        atom.unit,
        str(payload.get("pricing_profile") or DEFAULT_PRICING_PROFILE),
        str(payload.get("service_tier") or "default"),
    )


def _api_list_dimension(dimension: _RateDimension) -> _RateDimension:
    if dimension.profile == DEFAULT_PRICING_PROFILE:
        return dimension
    return _RateDimension(
        dimension.provider,
        dimension.model,
        dimension.direction,
        dimension.modality,
        dimension.cache_role,
        dimension.unit,
        DEFAULT_PRICING_PROFILE,
        dimension.service_tier,
    )


def _iso_timestamp(observed_at: float) -> str:
    return datetime.fromtimestamp(observed_at, timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _micro_usd(quantity: object, rate: ResolvedRate) -> int:
    if rate.scale <= 0 or not rate.usd.is_finite() or rate.usd < 0:
        raise PricingProjectionError("pricing catalog produced an invalid rate")
    amount = Decimal(str(quantity)) * rate.usd * MICRO_USD_PER_USD / Decimal(rate.scale)
    if not amount.is_finite() or amount < 0:
        raise PricingProjectionError("pricing catalog produced an invalid cost")
    rounded = int(amount.to_integral_value(rounding=ROUND_HALF_UP))
    if rounded > MAX_SAFE_INTEGER:
        raise PricingProjectionError("projected cost exceeds the JSON safe integer range")
    return rounded


__all__ = (
    "PricingEvidence",
    "PricingProjectionError",
    "SUBSCRIPTION_PRICING_PROFILE",
    "UsagePriceProjection",
    "UsagePriceProjector",
)
