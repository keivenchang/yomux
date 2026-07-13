# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Durable, offline-first API-list-price catalog.

This module is deliberately independent of statsd: it owns only reconstructible
price metadata and exposes deterministic effective-dated rate lookup.  A future
stats projection can consume :meth:`PricingCatalog.resolve_rate` without taking
ownership of catalog writes or provider fetching.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import os
import re
import sqlite3
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from importlib.resources import files
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from typing import Callable
from typing import Iterator

from .atomic_file import file_lock
from .common import MODEL_PRICING_CACHE_DIR
from .common import MODEL_PRICING_DATABASE_PATH


PRICING_SCHEMA_VERSION = 1
SEED_FILENAME = "model_pricing.json"
MAX_SOURCE_BYTES = 1_000_000
REFRESH_TIMEOUT_SECONDS = 10.0
PRICING_STALE_SECONDS = 30 * 24 * 60 * 60
SOURCE_PRIORITY = {"seed": 1, "official": 2, "override": 3}
OFFICIAL_SOURCE_HOSTS = frozenset({"platform.openai.com", "developers.openai.com", "openai.com", "www.anthropic.com", "anthropic.com", "platform.claude.com", "ai.google.dev", "cloud.google.com"})
CORROBORATION_SOURCE_HOSTS = frozenset({"openrouter.ai", "raw.githubusercontent.com", "github.com"})


class PricingCatalogError(RuntimeError):
    pass


class PricingCatalogValidationError(PricingCatalogError):
    pass


class PricingRefreshError(PricingCatalogError):
    pass


@dataclass(frozen=True)
class PricingPaths:
    root: Path
    database: Path
    source_cache: Path
    lock: Path

    @classmethod
    def from_root(cls, root: Path | None = None) -> "PricingPaths":
        directory = Path(root or MODEL_PRICING_CACHE_DIR).expanduser()
        database = MODEL_PRICING_DATABASE_PATH if root is None else directory / "pricing.sqlite3"
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
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != "https" or parsed.username or parsed.password or parsed.port not in (None, 443):
        return ""
    return url if (parsed.hostname or "").lower() in OFFICIAL_SOURCE_HOSTS | CORROBORATION_SOURCE_HOSTS else ""


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
    if payload.get("schema_version") != PRICING_SCHEMA_VERSION:
        raise PricingCatalogValidationError("unsupported catalog schema")
    revision = payload.get("catalog_revision")
    if not isinstance(revision, int) or revision < 1:
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
            if not isinstance(scale, int) or scale <= 0:
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
        raw = files("yolomux_lib").joinpath("data", SEED_FILENAME).read_text(encoding="utf-8")
        return validate_catalog(json.loads(raw))
    except (OSError, json.JSONDecodeError, PricingCatalogValidationError) as exc:
        raise PricingCatalogValidationError(f"invalid packaged pricing seed: {exc}") from exc


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str) -> None:
        return None


def _bounded_https_fetch(url: str, headers: dict[str, str] | None = None, timeout: float = REFRESH_TIMEOUT_SECONDS) -> tuple[int, dict[str, str], bytes]:
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != "https" or parsed.username or parsed.password or parsed.port not in (None, 443):
        raise PricingRefreshError("pricing source must be a plain HTTPS URL")
    host = (parsed.hostname or "").lower()
    if host not in OFFICIAL_SOURCE_HOSTS | CORROBORATION_SOURCE_HOSTS:
        raise PricingRefreshError("pricing source host is not allowlisted")
    request = urllib.request.Request(url, headers={"User-Agent": "YOLOmux pricing refresh", "Accept": "application/json,text/html;q=0.9", **(headers or {})})
    opener = urllib.request.build_opener(_NoRedirect)
    try:
        response = opener.open(request, timeout=timeout)
    except urllib.error.HTTPError as exc:
        if exc.code == 304:
            return 304, {str(k).lower(): str(v) for k, v in exc.headers.items()}, b""
        raise PricingRefreshError(f"pricing source HTTP {exc.code}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise PricingRefreshError(f"pricing source unavailable: {exc}") from exc
    with response:
        raw_status = getattr(response, "status", None)
        status = int(raw_status if raw_status is not None else response.getcode())
        headers_out = {str(k).lower(): str(v) for k, v in response.headers.items()}
        if status < 200 or status >= 300:
            raise PricingRefreshError(f"pricing source HTTP {status}")
        payload = response.read(MAX_SOURCE_BYTES + 1)
    if len(payload) > MAX_SOURCE_BYTES:
        raise PricingRefreshError("pricing source body exceeds limit")
    encoding = headers_out.get("content-encoding", "").lower()
    try:
        if encoding == "gzip":
            payload = gzip.decompress(payload)
        elif encoding == "deflate":
            payload = zlib.decompress(payload)
    except (OSError, zlib.error) as exc:
        raise PricingRefreshError("invalid compressed pricing source") from exc
    if len(payload) > MAX_SOURCE_BYTES:
        raise PricingRefreshError("decompressed pricing source exceeds limit")
    return status, headers_out, payload


class PricingCatalog:
    """Single durable owner for seed import, effective-dated rates, and refresh audit."""

    def __init__(self, root: Path | None = None, *, clock: Callable[[], float] = time.time):
        self.paths = PricingPaths.from_root(root)
        self.clock = clock
        self._lock = threading.RLock()
        self._opened = False

    def _connect(self) -> sqlite3.Connection:
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
                        raise PricingCatalogError("pricing database is newer than this YOLOmux")
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
            connection = self._connect()
            try:
                connection.execute("BEGIN IMMEDIATE")
                existing = connection.execute("SELECT MAX(revision) FROM catalog_revisions WHERE kind = 'seed'").fetchone()[0]
                if existing is None or int(existing) < int(seed["catalog_revision"]):
                    self._import_catalog(connection, seed, kind="seed", status="seed-only")
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
        return {"state": state, "seed_revision": int(seed_revision), "catalog_revision": int(official_revision or seed_revision), "database": str(self.paths.database)}

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
            # Exact aliases only; never prefix/fuzzy match a model identity.
            # Official rows are immutable history, but only the newest
            # accepted official revision is active.  Without this predicate a
            # model removed from a refreshed provider page would continue to
            # resolve from an old official revision indefinitely.
            alias_row = connection.execute("SELECT model, source_kind, revision FROM model_aliases WHERE provider = ? AND alias = ? AND (source_kind != 'official' OR revision = (SELECT MAX(revision) FROM catalog_revisions WHERE kind = 'official' AND status = 'active')) ORDER BY CASE source_kind WHEN 'override' THEN 3 WHEN 'official' THEN 2 ELSE 1 END DESC, revision DESC LIMIT 1", (provider, model)).fetchone()
            if alias_row is None:
                return None
            row = connection.execute("SELECT * FROM price_rates WHERE provider = ? AND model = ? AND direction = ? AND modality = ? AND cache_role = ? AND unit = ? AND profile = ? AND service_tier = ? AND effective_from <= ? AND active = 1 AND (source_kind != 'official' OR revision = (SELECT MAX(revision) FROM catalog_revisions WHERE kind = 'official' AND status = 'active')) ORDER BY CASE source_kind WHEN 'override' THEN 3 WHEN 'official' THEN 2 ELSE 1 END DESC, effective_from DESC, revision DESC LIMIT 1", (provider, alias_row["model"], direction, modality, cache_role, unit, profile, service_tier, timestamp)).fetchone()
            if row is None:
                return None
            return ResolvedRate(provider, str(row["model"]), model, direction, modality, cache_role, unit, int(row["scale"]), Decimal(str(row["usd"])), str(row["effective_from"]), str(row["source_kind"]), str(row["source_url"]), int(row["revision"]))

    def estimate_rate_band(self, *, direction: str, modality: str = "text", cache_role: str = "none", unit: str = "tokens", profile: str = "default", service_tier: str = "default", timestamp: str = "9999-12-31T23:59:59Z") -> EstimatedRateBand | None:
        """Return a defensible low/high comparable rate band for unknown models.

        This intentionally does not guess a model identity. It compares active
        catalog rows with the same billable dimension tuple and picks the
        cheapest and most expensive current rates in that class. If no exact
        profile/service-tier match exists, it falls back to the same
        direction/modality/cache-role/unit class across profiles and tiers.
        """
        self.open()
        with self._transaction() as connection:
            rows = connection.execute(
                "SELECT * FROM price_rates WHERE direction = ? AND modality = ? AND cache_role = ? AND unit = ? AND profile = ? AND service_tier = ? AND effective_from <= ? AND active = 1 AND (source_kind != 'official' OR revision = (SELECT MAX(revision) FROM catalog_revisions WHERE kind = 'official' AND status = 'active'))",
                (direction, modality, cache_role, unit, profile, service_tier, timestamp),
            ).fetchall()
            if not rows:
                rows = connection.execute(
                    "SELECT * FROM price_rates WHERE direction = ? AND modality = ? AND cache_role = ? AND unit = ? AND effective_from <= ? AND active = 1 AND (source_kind != 'official' OR revision = (SELECT MAX(revision) FROM catalog_revisions WHERE kind = 'official' AND status = 'active'))",
                    (direction, modality, cache_role, unit, timestamp),
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

    def refresh(self, adapters: list["PricingSourceAdapter"], *, fetch: Callable[[str, dict[str, str] | None, float], tuple[int, dict[str, str], bytes]] = _bounded_https_fetch) -> dict[str, Any]:
        """Serialize provider crawls across servers, not just DB commits."""
        self.open()
        refresh_lock = self.paths.root / "refresh.lock"
        with file_lock(refresh_lock, dir_mode=0o700):
            # A second web server that pressed Refresh while the first crawl
            # was in flight observes its completed revision instead of
            # immediately issuing a duplicate provider crawl.
            with self._transaction() as connection:
                latest = connection.execute("SELECT finished_at, status, detail FROM refresh_runs WHERE status IN ('updated', 'unchanged') ORDER BY id DESC LIMIT 1").fetchone()
            if latest is not None and float(latest["finished_at"] or 0) >= self.clock() - 15:
                try:
                    detail = json.loads(str(latest["detail"]))
                except json.JSONDecodeError:
                    detail = {}
                return {"ok": True, "status": "coalesced", "catalog_revision": int(detail.get("catalog_revision") or self.status()["catalog_revision"])}
            return self._refresh_locked(adapters, fetch=fetch)

    def _refresh_locked(self, adapters: list["PricingSourceAdapter"], *, fetch: Callable[[str, dict[str, str] | None, float], tuple[int, dict[str, str], bytes]]) -> dict[str, Any]:
        """Run a reviewed explicit refresh.  Failure never mutates active rows."""
        self.open()
        with self._transaction() as connection:
            started = self.clock()
            run_id = connection.execute("INSERT INTO refresh_runs(started_at, status) VALUES (?, 'running')", (started,)).lastrowid
        try:
            catalogs: list[tuple[PricingSourceAdapter, dict[str, Any], dict[str, str], bytes]] = []
            failures: list[dict[str, str]] = []
            for adapter in adapters:
                try:
                    state = self.source_check(adapter.url)
                    headers = {"If-None-Match": state.get("etag", ""), "If-Modified-Since": state.get("last_modified", "")}
                    status, response_headers, body = fetch(adapter.url, headers, REFRESH_TIMEOUT_SECONDS)
                    if status == 304:
                        self.record_source_not_modified(adapter.url, response_headers, adapter.parser_version)
                        continue
                    catalog = adapter.parse(body)
                    catalogs.append((adapter, validate_catalog(catalog), response_headers, body))
                except Exception as exc:
                    failures.append({"name": adapter.name, "error": str(exc)[:500]})
            if not catalogs:
                if failures:
                    raise PricingRefreshError(failures[0]["error"])
                result = {"ok": True, "status": "unchanged", "catalog_revision": self.status()["catalog_revision"]}
            else:
                # Only normalized, internally self-consistent official adapters are
                # activated.  A refresh is a provider set, not a winner-takes-all
                # page: selecting one page here would discard the other providers.
                # Cross-reference adapters remain discovery/audit input and can
                # veto an exact conflicting component instead of changing a rate.
                official = [item for adapter, item, _headers, _body in catalogs if adapter.kind == "official"]
                if not official:
                    raise PricingRefreshError("no accepted official pricing catalog")
                corroboration = [item for adapter, item, _headers, _body in catalogs if adapter.kind == "corroboration"]
                if any(_catalogs_disagree(candidate, corroboration) for candidate in official):
                    raise PricingRefreshError("pricing source disagreement requires review")
                chosen = _merge_official_catalogs(official, current_revision=self.status()["catalog_revision"])
                with self._transaction() as connection:
                    self._import_catalog(connection, chosen, kind="official")
                    for adapter, catalog, response_headers, body in catalogs:
                        # Commit source evidence only with the matching accepted
                        # catalog transaction; parser/network failure leaves both
                        # active pricing and audit state untouched.
                        self._record_source_check(connection, adapter.url, response_headers, body, adapter.parser_version, "accepted")
                result = {"ok": True, "status": "updated", "catalog_revision": int(chosen["catalog_revision"])}
                if failures:
                    result["source_failures"] = failures
            with self._transaction() as connection:
                connection.execute("UPDATE refresh_runs SET finished_at = ?, status = ?, detail = ? WHERE id = ?", (self.clock(), result["status"], json.dumps(result, sort_keys=True), run_id))
            return result
        except Exception as exc:
            with self._transaction() as connection:
                connection.execute("UPDATE refresh_runs SET finished_at = ?, status = 'failed', detail = ? WHERE id = ?", (self.clock(), str(exc)[:1000], run_id))
            raise

    def source_check(self, url: str) -> dict[str, str]:
        self.open()
        with self._transaction() as connection:
            row = connection.execute("SELECT etag, last_modified, digest, status FROM source_checks WHERE url = ?", (url,)).fetchone()
            return dict(row) if row else {}

    def record_source_check(self, url: str, headers: dict[str, str], body: bytes, parser_version: str, status: str) -> None:
        with self._transaction() as connection:
            self._record_source_check(connection, url, headers, body, parser_version, status)

    def _record_source_check(self, connection: sqlite3.Connection, url: str, headers: dict[str, str], body: bytes, parser_version: str, status: str, *, digest: str | None = None) -> None:
        digest = hashlib.sha256(body).hexdigest() if digest is None else digest
        connection.execute("INSERT INTO source_checks(url, etag, last_modified, digest, checked_at, parser_version, status) VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT(url) DO UPDATE SET etag=CASE WHEN excluded.etag != '' THEN excluded.etag ELSE source_checks.etag END, last_modified=CASE WHEN excluded.last_modified != '' THEN excluded.last_modified ELSE source_checks.last_modified END, digest=CASE WHEN excluded.digest != '' THEN excluded.digest ELSE source_checks.digest END, checked_at=excluded.checked_at, parser_version=excluded.parser_version, status=excluded.status", (url, str(headers.get("etag") or ""), str(headers.get("last-modified") or ""), digest, self.clock(), parser_version, status))

    def record_source_not_modified(self, url: str, headers: dict[str, str], parser_version: str) -> None:
        with self._transaction() as connection:
            self._record_source_check(connection, url, headers, b"", parser_version, "accepted", digest="")


@dataclass(frozen=True)
class PricingSourceAdapter:
    """Reviewed adapter contract; parsers return normalized catalog JSON only."""
    name: str
    url: str
    kind: str
    parser: Callable[[bytes], dict[str, Any]]
    parser_version: str = "1"

    def parse(self, body: bytes) -> dict[str, Any]:
        if self.kind not in {"official", "corroboration"}:
            raise PricingRefreshError("unknown pricing source kind")
        return self.parser(body)


class _PricingTableParser(HTMLParser):
    """Small fail-closed table extractor for reviewed provider fixtures.

    We deliberately do not attempt to scrape arbitrary prose.  A provider
    layout change therefore produces a review-needed refresh failure rather
    than silently activating guessed rates.
    """

    def __init__(self) -> None:
        super().__init__()
        self.rows: list[list[str]] = []
        self._row: list[str] | None = None
        self._cell: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        del attrs
        if tag == "tr":
            self._row = []
        elif tag in {"td", "th"} and self._row is not None:
            self._cell = []

    def handle_data(self, data: str) -> None:
        if self._cell is not None:
            self._cell.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag in {"td", "th"} and self._cell is not None and self._row is not None:
            self._row.append(" ".join("".join(self._cell).split()))
            self._cell = None
        elif tag == "tr" and self._row is not None:
            if self._row:
                self.rows.append(self._row)
            self._row = None


def _usd_per_million(value: str) -> str:
    cleaned = value.strip().replace("$", "").replace(",", "")
    # Provider pages present a per-million label.  Reject token/character
    # variants rather than treating a visually similar table as compatible.
    if not cleaned or any(marker in cleaned.lower() for marker in ("free", "contact", "-", "n/a")):
        raise PricingCatalogValidationError("missing concrete USD rate")
    lowered = cleaned.lower()
    for suffix in ("/ mtok", "per mtok"):
        if lowered.endswith(suffix):
            cleaned = cleaned[:len(cleaned) - len(suffix)].strip()
            break
    return _decimal_text(cleaned)


def _optional_usd_per_million(value: str) -> str | None:
    cleaned = value.strip().lower()
    if not cleaned or cleaned in {"-", "—", "n/a", "not available"} or "free" in cleaned or "contact" in cleaned:
        return None
    return _usd_per_million(value)


def _provider_model_aliases(provider: str, model: str) -> list[str]:
    aliases = [model]
    if provider == "anthropic":
        base = model.split("(", 1)[0].strip().lower()
        slug = "-".join(part for part in re.split(r"[^a-z0-9]+", base) if part)
        if slug and slug not in aliases:
            aliases.append(slug)
    return aliases


def parse_provider_pricing_table(body: bytes, *, provider: str, source_url: str, catalog_revision: int = 1) -> dict[str, Any]:
    """Parse a reviewed ``Model | Input | Cached input | Output`` HTML table.

    This is shared by OpenAI, Anthropic, and Google fixture adapters; their
    public pages remain separate allowlisted sources.  Cache-write durations
    are intentionally not inferred from an input/cache-read table.
    """
    parser = _PricingTableParser()
    try:
        parser.feed(body.decode("utf-8"))
    except UnicodeDecodeError as exc:
        raise PricingCatalogValidationError("pricing page is not UTF-8") from exc
    header_index = next((index for index, row in enumerate(parser.rows) if row and row[0].strip().lower() == "model"), None)
    if header_index is None:
        raise PricingCatalogValidationError("reviewed pricing table header missing")
    headers = [item.strip().lower() for item in parser.rows[header_index]]

    def first_header(*names: str) -> int | None:
        return next((headers.index(name) for name in names if name in headers), None)

    input_index = first_header("input", "base input tokens")
    output_index = first_header("output", "output tokens")
    if input_index is None or output_index is None:
        raise PricingCatalogValidationError("reviewed pricing table columns changed")
    cached_index = first_header("cached input", "cache hits & refreshes")
    cache_write_index = first_header("cache writes", "5m cache writes")
    cache_write_1h_index = first_header("1h cache writes")
    profile_index = headers.index("pricing profile") if "pricing profile" in headers else (headers.index("profile") if "profile" in headers else None)
    tier_index = headers.index("service tier") if "service tier" in headers else None
    models: list[dict[str, Any]] = []
    for row in parser.rows[header_index + 1:]:
        if row and row[0].strip().lower() == "model":
            break
        if len(row) != len(headers) or not row[0].strip():
            continue
        model = row[0].strip()
        profile = row[profile_index].strip().lower() if profile_index is not None and row[profile_index].strip() else "default"
        service_tier = row[tier_index].strip().lower() if tier_index is not None and row[tier_index].strip() else "default"
        input_usd = _optional_usd_per_million(row[input_index])
        output_usd = _optional_usd_per_million(row[output_index])
        if input_usd is None or output_usd is None:
            continue

        def rate(direction: str, cache_role: str, usd: str) -> dict[str, Any]:
            return {"direction": direction, "modality": "text", "cache_role": cache_role, "unit": "tokens", "scale": 1_000_000, "usd": usd, "effective_from": "1970-01-01T00:00:00Z", "profile": profile, "service_tier": service_tier}

        rates = [rate("input", "none", input_usd), rate("output", "none", output_usd)]
        for index, cache_role in ((cached_index, "read"), (cache_write_index, "write_5m"), (cache_write_1h_index, "write_1h")):
            usd = _optional_usd_per_million(row[index]) if index is not None else None
            if usd is not None:
                rates.append(rate("input", cache_role, usd))
        models.append({"provider": provider, "model": model, "aliases": _provider_model_aliases(provider, model), "rates": rates, "source": {"url": source_url, "kind": "official"}})
    if not models:
        raise PricingCatalogValidationError("reviewed pricing table has no models")
    return validate_catalog({"schema_version": 1, "catalog_revision": catalog_revision, "models": models})


def parse_openrouter_prices(body: bytes) -> dict[str, Any]:
    """Normalize OpenRouter's documented JSON price-per-token fields for corroboration only."""
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PricingCatalogValidationError("invalid OpenRouter price JSON") from exc
    entries = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(entries, list):
        raise PricingCatalogValidationError("OpenRouter data array missing")
    models: list[dict[str, Any]] = []
    for entry in entries:
        if not isinstance(entry, dict) or not isinstance(entry.get("id"), str) or not isinstance(entry.get("pricing"), dict):
            continue
        pricing = entry["pricing"]
        rates: list[dict[str, Any]] = []
        for field, direction, cache_role in (("prompt", "input", "none"), ("completion", "output", "none"), ("input_cache_read", "input", "read"), ("input_cache_write", "input", "write")):
            value = pricing.get(field)
            if value in (None, ""):
                continue
            # OpenRouter JSON reports USD/token; retain Decimal precision while
            # normalizing into the catalog's per-million-token rate shape.
            rates.append({"direction": direction, "modality": "text", "cache_role": cache_role, "unit": "tokens", "scale": 1_000_000, "usd": format(Decimal(_decimal_text(value)) * Decimal(1_000_000), "f"), "effective_from": "1970-01-01T00:00:00Z"})
        if rates:
            model = entry["id"]
            models.append({"provider": "openrouter", "model": model, "aliases": [model], "rates": rates, "source": {"url": "https://openrouter.ai/api/v1/models", "kind": "corroboration"}})
    return validate_catalog({"schema_version": 1, "catalog_revision": 1, "models": models})


def parse_litellm_prices(body: bytes) -> dict[str, Any]:
    """Normalize LiteLLM's model-price map for corroboration/discovery only."""
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PricingCatalogValidationError("invalid LiteLLM price JSON") from exc
    if not isinstance(payload, dict):
        raise PricingCatalogValidationError("LiteLLM price map missing")
    models: list[dict[str, Any]] = []
    for model, details in payload.items():
        if not isinstance(model, str) or not isinstance(details, dict):
            continue
        rates: list[dict[str, Any]] = []
        for field, direction, cache_role in (("input_cost_per_token", "input", "none"), ("output_cost_per_token", "output", "none"), ("cache_read_input_token_cost", "input", "read"), ("cache_creation_input_token_cost", "input", "write")):
            value = details.get(field)
            if value in (None, ""):
                continue
            rates.append({"direction": direction, "modality": "text", "cache_role": cache_role, "unit": "tokens", "scale": 1_000_000, "usd": format(Decimal(_decimal_text(value)) * Decimal(1_000_000), "f"), "effective_from": "1970-01-01T00:00:00Z"})
        if rates:
            provider = str(details.get("litellm_provider") or "litellm")
            models.append({"provider": provider, "model": model, "aliases": [model], "rates": rates, "source": {"url": "https://raw.githubusercontent.com/BerriAI/litellm/main/model_prices_and_context_window.json", "kind": "corroboration"}})
    return validate_catalog({"schema_version": 1, "catalog_revision": 1, "models": models})


def _merge_official_catalogs(catalogs: list[dict[str, Any]], *, current_revision: int) -> dict[str, Any]:
    """Combine accepted provider pages into one immutable active revision.

    Provider pages do not share a revision clock.  The local catalog revision
    therefore advances monotonically whenever a successful provider set is
    installed, while each parsed model keeps its own source evidence.
    """
    models = [model for catalog in catalogs for model in catalog["models"]]
    maximum = max(int(catalog["catalog_revision"]) for catalog in catalogs)
    revision = max(maximum, int(current_revision) + 1)
    return validate_catalog({"schema_version": PRICING_SCHEMA_VERSION, "catalog_revision": revision, "models": models})


def _catalogs_disagree(official: dict[str, Any], corroboration: list[dict[str, Any]]) -> bool:
    """Fail closed on an exact, comparable corroboration mismatch.

    Non-overlapping aliases are ordinary discovery input.  Only an identical
    provider/model/component with a different normalized rate blocks automatic
    activation; that avoids inferring equivalence across reseller aliases.
    """
    authoritative: dict[tuple[str, str, str, str, str, str, str, str], str] = {}
    for model in official["models"]:
        for rate in model["rates"]:
            key = (model["provider"], model["model"], rate["direction"], rate["modality"], rate["cache_role"], rate["unit"], rate["profile"], rate["service_tier"])
            authoritative[key] = rate["usd"]
    for catalog in corroboration:
        for model in catalog["models"]:
            for rate in model["rates"]:
                key = (model["provider"], model["model"], rate["direction"], rate["modality"], rate["cache_role"], rate["unit"], rate["profile"], rate["service_tier"])
                if key in authoritative and authoritative[key] != rate["usd"]:
                    return True
    return False


def default_pricing_source_adapters() -> tuple[PricingSourceAdapter, ...]:
    """Reviewed registrations.  Official pages are authoritative; the JSON
    feeds are retained strictly as corroboration and model discovery input."""
    return (
        PricingSourceAdapter("openai", "https://developers.openai.com/api/docs/pricing", "official", lambda body: parse_provider_pricing_table(body, provider="openai", source_url="https://developers.openai.com/api/docs/pricing")),
        PricingSourceAdapter("anthropic", "https://platform.claude.com/docs/en/about-claude/pricing", "official", lambda body: parse_provider_pricing_table(body, provider="anthropic", source_url="https://platform.claude.com/docs/en/about-claude/pricing")),
        PricingSourceAdapter("google", "https://ai.google.dev/gemini-api/docs/pricing", "official", lambda body: parse_provider_pricing_table(body, provider="google", source_url="https://ai.google.dev/gemini-api/docs/pricing")),
        PricingSourceAdapter("openrouter", "https://openrouter.ai/api/v1/models", "corroboration", parse_openrouter_prices),
        PricingSourceAdapter("litellm", "https://raw.githubusercontent.com/BerriAI/litellm/main/model_prices_and_context_window.json", "corroboration", parse_litellm_prices),
    )


class PricingRefreshCoordinator:
    """Nonblocking in-process single-flight wrapper for an explicit Refresh.

    Cross-server serialization remains the catalog's durable file lock; a web
    handler merely asks this coordinator to launch the bounded worker and can
    return progress immediately.
    """

    def __init__(self, catalog: PricingCatalog, *, adapters: tuple[PricingSourceAdapter, ...] | None = None, publish: Callable[[str, dict[str, Any]], None] | None = None):
        self.catalog = catalog
        self.adapters = default_pricing_source_adapters() if adapters is None else adapters
        self.publish = publish or (lambda _name, _payload: None)
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._state: dict[str, Any] = {"status": "idle"}

    def status(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._state)

    def start(self) -> dict[str, Any]:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return {"ok": True, "coalesced": True, **self._state}
            self._state = {"status": "running", "started_at": time.time()}
            self._thread = threading.Thread(target=self._run, name="yolomux-pricing-refresh", daemon=True)
            self._thread.start()
            return {"ok": True, "coalesced": False, **self._state}

    def _run(self) -> None:
        try:
            result = self.catalog.refresh(list(self.adapters))
            with self._lock:
                self._state = {"status": "done", "finished_at": time.time(), "refresh_status": result.get("status", ""), **{key: value for key, value in result.items() if key != "status"}}
                state = dict(self._state)
            self.publish("pricing_catalog_changed", state)
        except Exception as exc:
            with self._lock:
                self._state = {"status": "failed", "finished_at": time.time(), "error": str(exc)}
