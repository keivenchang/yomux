# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Durable, offline-first API-list-price catalog.

This module is deliberately independent of statsd: it owns only reconstructible
price metadata and exposes deterministic effective-dated rate lookup.  A future
stats projection can consume :meth:`PricingCatalog.resolve_rate` without taking
ownership of catalog writes or provider fetching.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import threading
import time
import urllib.parse
from contextlib import contextmanager
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any
from typing import Callable
from typing import Iterator

from ..atomic_file import file_lock
from ..common import MODEL_PRICING_CACHE_DIR
from ..infra.filesystem_preflight import preflight_mutable_roots
from ..infra.host_partition import host_partitioned_state_dir


PRICING_SCHEMA_VERSION = 1
SEED_FILENAME = "model_pricing.json"
PRICING_STALE_SECONDS = 30 * 24 * 60 * 60
SOURCE_PRIORITY = {"seed": 1, "inferred": 2, "official": 3, "override": 4}
OFFICIAL_SOURCE_HOSTS = frozenset({"platform.openai.com", "developers.openai.com", "openai.com", "www.anthropic.com", "anthropic.com", "platform.claude.com", "ai.google.dev", "cloud.google.com", "inference-backend.internal"})
CORROBORATION_SOURCE_HOSTS = frozenset({"openrouter.ai", "raw.githubusercontent.com", "github.com"})


class PricingCatalogError(RuntimeError):
    pass


class PricingCatalogValidationError(PricingCatalogError):
    pass


def default_pricing_cache_dir(cache_dir: Path | None = None) -> Path:
    """Return the host-private root for reconstructible model-pricing WAL data."""
    return host_partitioned_state_dir(cache_dir or MODEL_PRICING_CACHE_DIR) / "model-pricing"


@dataclass(frozen=True)
class PricingPaths:
    root: Path
    database: Path
    source_cache: Path
    lock: Path

    @classmethod
    def from_root(cls, root: Path | None = None) -> "PricingPaths":
        directory = Path(root or default_pricing_cache_dir()).expanduser()
        database = directory / "pricing.sqlite3"
        return cls(directory, database, directory / "sources", directory / "catalog.lock")


@dataclass(frozen=True)
class ResolvedRate:
    provider: str
    model: str
    alias: str
    direction: str
    modality: str
    cache_role: str
    unit: str
    scale: int
    usd: Decimal
    effective_from: str
    source_kind: str
    source_url: str
    catalog_revision: int
    effective_until: str | None = None

    def public_payload(self) -> dict[str, Any]:
        """JSON-safe rate evidence for stats/projection and the source popover."""
        return {
            "provider": self.provider,
            "model": self.model,
            "alias": self.alias,
            "direction": self.direction,
            "modality": self.modality,
            "cache_role": self.cache_role,
            "unit": self.unit,
            "scale": self.scale,
            "usd": format(self.usd, "f"),
            "effective_from": self.effective_from,
            "source_kind": self.source_kind,
            "source_url": safe_source_url(self.source_url),
            "catalog_revision": self.catalog_revision,
        }


@dataclass(frozen=True)
class EstimatedRateBand:
    """Comparable low/high rates for an unpriced usage dimension."""

    minimum: ResolvedRate
    maximum: ResolvedRate


def safe_source_url(value: object) -> str:
    """Return only a reviewed absolute HTTPS provider/corroboration link.

    Rate evidence is passed to browser popovers, so this boundary prevents a
    local override or malformed provider record from becoming an unsafe link.
    """
    url = str(value or "").strip()
    try:
        parsed = urllib.parse.urlsplit(url)
        allowed = parsed.scheme == "https" and not parsed.username and not parsed.password and parsed.port in (None, 443)
    except ValueError:
        return ""
    if not allowed:
        return ""
    return url if (parsed.hostname or "").lower() in OFFICIAL_SOURCE_HOSTS | CORROBORATION_SOURCE_HOSTS else ""


def _inferred_family_aliases(model: str) -> tuple[str, ...]:
    """Return only conservative, provider-visible family aliases.

    Date/build suffixes and the Sol/Terra/Luna deployment suffixes are common
    telemetry identities, but are not separate price evidence.  No arbitrary
    prefix or edit-distance matching is permitted.
    """
    candidates: list[str] = []
    dated = re.sub(r"-(?:20\d{2}[-.]?\d{2}[-.]?\d{2}|latest|preview)$", "", model, flags=re.IGNORECASE)
    if dated != model:
        candidates.append(dated)
    variant = re.sub(r"-(?:sol|terra|luna)(?:-(?:latest|preview|20\d{2}[-.]?\d{2}[-.]?\d{2}))?$", "", model, flags=re.IGNORECASE)
    if variant != model and variant not in candidates:
        candidates.append(variant)
    return tuple(candidates)


def _switchyard_equivalent_model(model: str) -> str | None:
    value = str(model or "").strip()
    if not value.startswith("switchyard/"):
        return None
    equivalent = value.rsplit("/", 1)[-1]
    return equivalent if equivalent else None


def _canonical_text(value: object, field: str) -> str:
    result = str(value or "").strip()
    if not result:
        raise PricingCatalogValidationError(f"missing {field}")
    return result


def _decimal_text(value: object, field: str = "usd") -> str:
    try:
        amount = Decimal(str(value))
    except Exception as exc:
        raise PricingCatalogValidationError(f"invalid {field}") from exc
    if not amount.is_finite() or amount < 0:
        raise PricingCatalogValidationError(f"invalid {field}")
    return format(amount, "f")


def validate_catalog(payload: object) -> dict[str, Any]:
    """Validate the packaged/remote normalized JSON without a runtime dependency."""
    if not isinstance(payload, dict):
        raise PricingCatalogValidationError("catalog must be an object")
    if type(payload.get("schema_version")) is not int or payload["schema_version"] != PRICING_SCHEMA_VERSION:
        raise PricingCatalogValidationError("unsupported catalog schema")
    revision = payload.get("catalog_revision")
    if type(revision) is not int or revision < 1:
        raise PricingCatalogValidationError("invalid catalog_revision")
    models = payload.get("models")
    if not isinstance(models, list):
        raise PricingCatalogValidationError("models must be an array")
    aliases: set[tuple[str, str]] = set()
    normalized_models: list[dict[str, Any]] = []
    for raw_model in models:
        if not isinstance(raw_model, dict):
            raise PricingCatalogValidationError("model must be an object")
        provider = _canonical_text(raw_model.get("provider"), "provider")
        model = _canonical_text(raw_model.get("model"), "model")
        model_aliases = raw_model.get("aliases")
        rates = raw_model.get("rates")
        if not isinstance(model_aliases, list) or not model_aliases or not isinstance(rates, list) or not rates:
            raise PricingCatalogValidationError("model aliases and rates are required")
        checked_aliases: list[str] = []
        for alias in model_aliases:
            clean = _canonical_text(alias, "alias")
            key = (provider, clean)
            if key in aliases:
                raise PricingCatalogValidationError(f"duplicate alias {provider}/{clean}")
            aliases.add(key)
            checked_aliases.append(clean)
        source = raw_model.get("source") if isinstance(raw_model.get("source"), dict) else {}
        source_url = str(source.get("url") or "")
        if source_url and urllib.parse.urlsplit(source_url).scheme != "https":
            raise PricingCatalogValidationError("source URL must use https")
        checked_rates: list[dict[str, Any]] = []
        for raw_rate in rates:
            if not isinstance(raw_rate, dict):
                raise PricingCatalogValidationError("rate must be an object")
            scale = raw_rate.get("scale")
            if type(scale) is not int or scale <= 0:
                raise PricingCatalogValidationError("rate scale must be a positive integer")
            checked_rates.append({
                "direction": _canonical_text(raw_rate.get("direction"), "direction"),
                "modality": _canonical_text(raw_rate.get("modality"), "modality"),
                "cache_role": _canonical_text(raw_rate.get("cache_role"), "cache_role"),
                "unit": _canonical_text(raw_rate.get("unit"), "unit"),
                "scale": scale,
                "usd": _decimal_text(raw_rate.get("usd")),
                "effective_from": _canonical_text(raw_rate.get("effective_from"), "effective_from"),
                "profile": str(raw_rate.get("profile") or "default"),
                "service_tier": str(raw_rate.get("service_tier") or "default"),
            })
        normalized_models.append({"provider": provider, "model": model, "aliases": checked_aliases, "rates": checked_rates, "source": source})
    return {"schema_version": PRICING_SCHEMA_VERSION, "catalog_revision": revision, "generated_at": str(payload.get("generated_at") or ""), "models": normalized_models}


def load_packaged_seed() -> dict[str, Any]:
    try:
        data = Path(__file__).resolve().parents[2] / "assets" / "runtime"
        seed = validate_catalog(json.loads(data.joinpath(SEED_FILENAME).read_text(encoding="utf-8")))
        internal_path = data.joinpath("internal_model_pricing.json")
        if not internal_path.is_file():
            return seed
        internal = validate_catalog(json.loads(internal_path.read_text(encoding="utf-8")))
        internal_luna = next((model for model in internal["models"] if model["provider"] == "openai" and model["model"] == "gpt-5.6-luna"), None)
        merged = [model for model in seed["models"] if not (model["provider"] == "openai" and model["model"] == "gpt-5.6-luna")]
        if internal_luna is not None:
            merged.append(internal_luna)
        seen = {(model["provider"], alias) for model in merged for alias in model["aliases"]}
        for model in internal["models"]:
            if model["provider"] == "openai" and model["model"] == "gpt-5.6-luna":
                continue
            if any((model["provider"], alias) in seen for alias in model["aliases"]):
                continue
            merged.append(model)
            seen.update((model["provider"], alias) for alias in model["aliases"])
        return validate_catalog({"schema_version": PRICING_SCHEMA_VERSION, "catalog_revision": max(seed["catalog_revision"], internal["catalog_revision"]), "generated_at": internal["generated_at"], "models": merged})
    except (OSError, json.JSONDecodeError, PricingCatalogValidationError) as exc:
        raise PricingCatalogValidationError(f"invalid packaged pricing seed: {exc}") from exc


class PricingCatalog:
    """Single durable owner for seed import, effective-dated rates, and refresh audit."""

    def __init__(self, root: Path | None = None, *, clock: Callable[[], float] = time.time):
        self.paths = PricingPaths.from_root(root)
        self.clock = clock
        self._lock = threading.RLock()
        self._opened = False

    def _connect(self) -> sqlite3.Connection:
        preflight_mutable_roots(wal_databases=[self.paths.database])
        self.paths.root.mkdir(parents=True, exist_ok=True)
        self.paths.root.chmod(0o700)
        connection = sqlite3.connect(self.paths.database, timeout=15.0, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 15000")
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        return connection

    def _create_schema(self, connection: sqlite3.Connection) -> None:
        connection.executescript("""
            CREATE TABLE IF NOT EXISTS catalog_revisions (revision INTEGER NOT NULL, kind TEXT NOT NULL, activated_at REAL NOT NULL, seed_revision INTEGER NOT NULL DEFAULT 0, status TEXT NOT NULL, digest TEXT NOT NULL, PRIMARY KEY(revision, kind));
            CREATE TABLE IF NOT EXISTS model_aliases (provider TEXT NOT NULL, alias TEXT NOT NULL, model TEXT NOT NULL, source_kind TEXT NOT NULL, revision INTEGER NOT NULL, PRIMARY KEY (provider, alias, source_kind, revision));
            CREATE TABLE IF NOT EXISTS price_rates (id INTEGER PRIMARY KEY, provider TEXT NOT NULL, model TEXT NOT NULL, direction TEXT NOT NULL, modality TEXT NOT NULL, cache_role TEXT NOT NULL, unit TEXT NOT NULL, profile TEXT NOT NULL, service_tier TEXT NOT NULL, scale INTEGER NOT NULL, usd TEXT NOT NULL, effective_from TEXT NOT NULL, source_kind TEXT NOT NULL, source_url TEXT NOT NULL, revision INTEGER NOT NULL, active INTEGER NOT NULL DEFAULT 1);
            CREATE INDEX IF NOT EXISTS price_rates_lookup ON price_rates(provider, model, direction, modality, cache_role, unit, profile, service_tier, effective_from, active);
            CREATE TABLE IF NOT EXISTS source_checks (url TEXT PRIMARY KEY, etag TEXT NOT NULL DEFAULT '', last_modified TEXT NOT NULL DEFAULT '', digest TEXT NOT NULL DEFAULT '', checked_at REAL NOT NULL DEFAULT 0, parser_version TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT '');
            CREATE TABLE IF NOT EXISTS refresh_runs (id INTEGER PRIMARY KEY, started_at REAL NOT NULL, finished_at REAL, status TEXT NOT NULL, detail TEXT NOT NULL DEFAULT '');
            CREATE TABLE IF NOT EXISTS overrides (provider TEXT NOT NULL, alias TEXT NOT NULL, payload TEXT NOT NULL, updated_at REAL NOT NULL, PRIMARY KEY(provider, alias));
        """)
        connection.execute(f"PRAGMA user_version = {PRICING_SCHEMA_VERSION}")

    def _quarantine_corrupt_database(self) -> None:
        if not self.paths.database.exists():
            return
        target = self.paths.database.with_name(f"{self.paths.database.name}.corrupt-{int(self.clock())}")
        os.replace(self.paths.database, target)

    def open(self) -> None:
        if self._opened:
            return
        with self._lock, file_lock(self.paths.lock, dir_mode=0o700):
            if self._opened:
                return
            try:
                connection = self._connect()
                try:
                    version = int(connection.execute("PRAGMA user_version").fetchone()[0])
                    if version > PRICING_SCHEMA_VERSION:
                        raise PricingCatalogError("pricing database is newer than this YOmux")
                    self._create_schema(connection)
                finally:
                    connection.close()
            except sqlite3.DatabaseError:
                self._quarantine_corrupt_database()
                connection = self._connect()
                try:
                    self._create_schema(connection)
                finally:
                    connection.close()
            # Seed reconciliation is intentionally performed under this same
            # cross-process lock.  Calling _transaction() here would acquire
            # a second flock for the same file descriptor family and can
            # deadlock on macOS.
            seed = load_packaged_seed()
            seed_revision = int(seed["catalog_revision"])
            connection = self._connect()
            try:
                connection.execute("BEGIN IMMEDIATE")
                existing = connection.execute("SELECT MAX(revision) FROM catalog_revisions WHERE kind = 'seed'").fetchone()[0]
                if existing is None or int(existing) < seed_revision:
                    self._import_catalog(connection, {**seed, "catalog_revision": seed_revision}, kind="seed", status="seed-only")
                connection.commit()
            except Exception:
                connection.rollback()
                raise
            finally:
                connection.close()
            self._opened = True

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        self.open()
        with self._lock, file_lock(self.paths.lock, dir_mode=0o700):
            connection = self._connect()
            try:
                connection.execute("BEGIN IMMEDIATE")
                yield connection
                connection.commit()
            except Exception:
                connection.rollback()
                raise
            finally:
                connection.close()

    @staticmethod
    def _digest(catalog: dict[str, Any]) -> str:
        return hashlib.sha256(json.dumps(catalog, sort_keys=True, separators=(",", ":")).encode()).hexdigest()

    def _import_catalog(self, connection: sqlite3.Connection, catalog: dict[str, Any], *, kind: str, status: str = "active") -> None:
        revision = int(catalog["catalog_revision"])
        source_kind = kind
        connection.execute("INSERT OR REPLACE INTO catalog_revisions(revision, kind, activated_at, seed_revision, status, digest) VALUES (?, ?, ?, ?, ?, ?)", (revision, kind, self.clock(), revision if kind == "seed" else 0, status, self._digest(catalog)))
        connection.execute("DELETE FROM model_aliases WHERE revision = ? AND source_kind = ?", (revision, source_kind))
        connection.execute("DELETE FROM price_rates WHERE revision = ? AND source_kind = ?", (revision, source_kind))
        for model in catalog["models"]:
            source = model["source"] if isinstance(model.get("source"), dict) else {}
            source_url = str(source.get("url") or "")
            for alias in model["aliases"]:
                connection.execute("INSERT INTO model_aliases(provider, alias, model, source_kind, revision) VALUES (?, ?, ?, ?, ?)", (model["provider"], alias, model["model"], source_kind, revision))
            for rate in model["rates"]:
                connection.execute("INSERT INTO price_rates(provider, model, direction, modality, cache_role, unit, profile, service_tier, scale, usd, effective_from, source_kind, source_url, revision, active) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)", (model["provider"], model["model"], rate["direction"], rate["modality"], rate["cache_role"], rate["unit"], rate["profile"], rate["service_tier"], rate["scale"], rate["usd"], rate["effective_from"], source_kind, source_url, revision))

    def reconcile_seed(self) -> None:
        seed = load_packaged_seed()
        # Do not downgrade or erase a verified remote catalog; seed rows are a
        # fallback source and are imported only when their revision is new.
        with self._transaction() as connection:
            existing = connection.execute("SELECT MAX(revision) FROM catalog_revisions WHERE kind = 'seed'").fetchone()[0]
            if existing is None or int(existing) < int(seed["catalog_revision"]):
                self._import_catalog(connection, seed, kind="seed", status="seed-only")

    def status(self) -> dict[str, Any]:
        self.open()
        with self._transaction() as connection:
            return self._status_from_connection(connection)

    def _status_from_connection(self, connection: sqlite3.Connection) -> dict[str, Any]:
        seed_revision = connection.execute("SELECT MAX(revision) FROM catalog_revisions WHERE kind = 'seed'").fetchone()[0] or 0
        official_revision = connection.execute("SELECT MAX(revision) FROM catalog_revisions WHERE kind = 'official' AND status = 'active'").fetchone()[0] or 0
        latest_run = connection.execute("SELECT status FROM refresh_runs ORDER BY id DESC LIMIT 1").fetchone()
        latest_failed = latest_run is not None and str(latest_run["status"]) == "failed"
        latest_check = connection.execute("SELECT MAX(checked_at) FROM source_checks WHERE status = 'accepted'").fetchone()[0] or 0
        state = "seed-only"
        if official_revision:
            state = "review-needed" if latest_failed else ("stale" if float(latest_check) and float(latest_check) < self.clock() - PRICING_STALE_SECONDS else "fresh")
        return {
            "state": state,
            "seed_revision": int(seed_revision),
            "catalog_revision": int(official_revision or seed_revision),
            "last_checked_at": float(latest_check),
            "database": str(self.paths.database),
        }


    def public_payload(self) -> dict[str, Any]:
        """Public metadata only: links/evidence, never provider response bodies."""
        self.open()
        with self._transaction() as connection:
            sources = connection.execute("SELECT DISTINCT source_kind, source_url, revision FROM price_rates WHERE active = 1 ORDER BY source_kind, source_url, revision").fetchall()
            return {
                "status": self._status_from_connection(connection),
                "sources": [
                    {"kind": str(row["source_kind"]), "url": safe_source_url(row["source_url"]), "revision": int(row["revision"])}
                    for row in sources
                    if safe_source_url(row["source_url"])
                ],
            }

    def resolve_rate(self, *, provider: str, model: str, direction: str, modality: str = "text", cache_role: str = "none", unit: str = "tokens", profile: str = "default", service_tier: str = "default", timestamp: str = "9999-12-31T23:59:59Z") -> ResolvedRate | None:
        self.open()
        with self._transaction() as connection:
            def find(alias: str, kinds: tuple[str, ...]) -> sqlite3.Row | None:
                placeholders = ",".join("?" for _ in kinds)
                alias_row = connection.execute(
                    f"SELECT model FROM model_aliases WHERE provider = ? AND alias = ? AND source_kind IN ({placeholders}) "
                    "ORDER BY CASE source_kind WHEN 'override' THEN 4 WHEN 'official' THEN 3 ELSE 1 END DESC, revision DESC LIMIT 1",
                    (provider, alias, *kinds),
                ).fetchone()
                if alias_row is None:
                    return None
                return connection.execute(
                    f"SELECT * FROM price_rates WHERE provider = ? AND model = ? AND direction = ? AND modality = ? AND cache_role = ? AND unit = ? AND profile = ? AND service_tier = ? AND effective_from <= ? AND active = 1 AND source_kind IN ({placeholders}) "
                    "ORDER BY CASE source_kind WHEN 'override' THEN 4 WHEN 'official' THEN 3 ELSE 1 END DESC, effective_from DESC, revision DESC LIMIT 1",
                    (provider, alias_row["model"], direction, modality, cache_role, unit, profile, service_tier, timestamp, *kinds),
                ).fetchone()

            # Explicit identities always win.  Historical official rows remain
            # eligible so a partial upstream refresh cannot erase coverage.
            row = find(model, ("override", "official"))
            source_kind = str(row["source_kind"]) if row is not None else ""
            if row is None:
                equivalent_model = _switchyard_equivalent_model(model)
                if str(provider).strip().lower() == "inferencehub" and equivalent_model:
                    candidates = connection.execute(
                        "SELECT * FROM price_rates WHERE direction = ? AND modality = ? AND cache_role = ? AND unit = ? AND profile = ? AND service_tier = ? AND effective_from <= ? AND active = 1 AND (model = ? OR model LIKE ?) ORDER BY effective_from DESC, revision DESC",
                        (direction, modality, cache_role, unit, profile, service_tier, timestamp, equivalent_model, f"%/{equivalent_model}"),
                    ).fetchall()
                    latest: dict[tuple[str, str], sqlite3.Row] = {}
                    for candidate in candidates:
                        if Decimal(str(candidate["usd"])) <= 0:
                            continue
                        latest.setdefault((str(candidate["provider"]), str(candidate["model"])), candidate)
                    if latest:
                        rates = [Decimal(str(candidate["usd"])) for candidate in latest.values()]
                        first = next(iter(latest.values()))
                        return ResolvedRate(
                            provider,
                            equivalent_model,
                            model,
                            direction,
                            modality,
                            cache_role,
                            unit,
                            int(first["scale"]),
                            sum(rates, Decimal(0)) / Decimal(len(rates)),
                            str(first["effective_from"]),
                            "inferred",
                            str(first["source_url"]),
                            int(first["revision"]),
                        )
                if row is None and provider == "openai" and model.startswith("openai/"):
                    row = find(model.split("/", 1)[1], ("official", "seed"))
                    source_kind = str(row["source_kind"]) if row is not None else ""
                # Only reviewed, deterministic family forms are inferred.  The
                # evidence is visibly labelled rather than masquerading as an
                # exact provider match.
                for family in _inferred_family_aliases(model):
                    if row is not None:
                        break
                    row = find(family, ("official",))
                    if row is not None:
                        source_kind = "inferred"
                        break
            if row is None:
                row = find(model, ("seed",))
                source_kind = "seed" if row is not None else ""
            if row is None:
                return None
            selected_priority = SOURCE_PRIORITY[str(row["source_kind"])]
            boundary_kinds = tuple(
                kind
                for kind, priority in SOURCE_PRIORITY.items()
                if priority >= selected_priority and kind != "inferred"
            )
            boundary_placeholders = ",".join("?" for _kind in boundary_kinds)
            effective_until_row = connection.execute(
                "SELECT MIN(effective_from) FROM price_rates WHERE provider = ? AND model = ? "
                "AND direction = ? AND modality = ? AND cache_role = ? AND unit = ? "
                "AND profile = ? AND service_tier = ? AND effective_from > ? AND active = 1 "
                f"AND source_kind IN ({boundary_placeholders})",
                (
                    provider,
                    row["model"],
                    direction,
                    modality,
                    cache_role,
                    unit,
                    profile,
                    service_tier,
                    row["effective_from"],
                    *boundary_kinds,
                ),
            ).fetchone()
            effective_until = (
                None
                if effective_until_row is None or effective_until_row[0] is None
                else str(effective_until_row[0])
            )
            return ResolvedRate(
                provider,
                str(row["model"]),
                model,
                direction,
                modality,
                cache_role,
                unit,
                int(row["scale"]),
                Decimal(str(row["usd"])),
                str(row["effective_from"]),
                source_kind,
                str(row["source_url"]),
                int(row["revision"]),
                effective_until,
            )

    def estimate_rate_band(self, *, provider: str = "unknown", direction: str, modality: str = "text", cache_role: str = "none", unit: str = "tokens", profile: str = "default", service_tier: str = "default", timestamp: str = "9999-12-31T23:59:59Z") -> EstimatedRateBand | None:
        """Return a defensible low/high comparable rate band for unknown models.

        This intentionally does not guess a model identity. It compares active
        catalog rows from the known provider with the same billable dimension tuple and picks the
        cheapest and most expensive current rates in that class. If no exact
        profile/service-tier match exists, it falls back to the same
        direction/modality/cache-role/unit class across profiles and tiers.
        Provider-unknown telemetry may compare across providers; a known
        provider never inherits another provider's unrelated ceiling.
        """
        self.open()
        normalized_provider = str(provider or "unknown").strip().lower() or "unknown"
        provider_clause = " AND provider = ?" if normalized_provider != "unknown" else ""
        with self._transaction() as connection:
            rows = connection.execute(
                "SELECT * FROM price_rates WHERE direction = ? AND modality = ? AND cache_role = ? AND unit = ? AND profile = ? AND service_tier = ? AND effective_from <= ? AND active = 1" + provider_clause,
                (direction, modality, cache_role, unit, profile, service_tier, timestamp, *([normalized_provider] if provider_clause else [])),
            ).fetchall()
            if not rows:
                rows = connection.execute(
                    "SELECT * FROM price_rates WHERE direction = ? AND modality = ? AND cache_role = ? AND unit = ? AND effective_from <= ? AND active = 1" + provider_clause,
                    (direction, modality, cache_role, unit, timestamp, *([normalized_provider] if provider_clause else [])),
                ).fetchall()
            rates = [
                ResolvedRate(
                    str(row["provider"]), str(row["model"]), str(row["model"]), str(row["direction"]), str(row["modality"]),
                    str(row["cache_role"]), str(row["unit"]), int(row["scale"]), Decimal(str(row["usd"])),
                    str(row["effective_from"]), str(row["source_kind"]), str(row["source_url"]), int(row["revision"]),
                )
                for row in rows
            ]
            if not rates:
                return None
            key = lambda rate: rate.usd / Decimal(rate.scale)
            return EstimatedRateBand(min(rates, key=key), max(rates, key=key))

    def set_override(self, catalog: dict[str, Any]) -> None:
        """Install an explicit local override, retaining its immutable rate rows."""
        catalog = validate_catalog(catalog)
        with self._transaction() as connection:
            self._import_catalog(connection, catalog, kind="override")
            for model in catalog["models"]:
                for alias in model["aliases"]:
                    connection.execute(
                        "INSERT INTO overrides(provider, alias, payload, updated_at) VALUES (?, ?, ?, ?) ON CONFLICT(provider, alias) DO UPDATE SET payload=excluded.payload, updated_at=excluded.updated_at",
                        (model["provider"], alias, json.dumps(model, sort_keys=True, separators=(",", ":")), self.clock()),
                    )
