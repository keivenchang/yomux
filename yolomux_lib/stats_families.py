"""The ONE declarative YO!stats family manifest.

Every per-family fact lives in this frozen table: canonical name (identical to
the coverage family written to ``stats_coverage_intervals``), the legacy alias
names an older server may still write into coverage payloads, the true sampler
cadence, storage fields, wire field group, delivery path, merge rule, the
client chart groups/series, and coverage companion families.

Consumers READ this table instead of hard-coding per-family special cases:

- ``yolomux_lib/local_services/stats_store.py`` derives ``SERVER_FIELDS``,
  ``STATS_COVERAGE_FAMILIES``, ``STATS_COVERAGE_LEGACY_CADENCE``, and
  ``empty_host_metrics``.
- ``yolomux_lib/statsd.py`` derives the coverage companion fan-out, the merge
  field groups of ``_merge_bucket``, the wire field groups of
  ``_record_from_bucket`` / ``_encoded_history``, and the LEGACY compact
  token-stream record keys (compat only; see below).
- The client mirror ``jsDebugStatsFamilyManifest``
  (static_src/js/yolomux/83_debug_panel.js) carries the same canonical names,
  aliases, cadences, and chart-group mapping; tests/yostats_performance.test.js
  pins both mirrors against each other and bans reintroduced inline per-family
  if/alias chains.

WHY: the recurring YO!stats bug class was a flag or special case on one family
silently affecting another. Latest incident (2026-07-14): ``host_metrics`` was
gated on the agent-token payload-slimming flags, blanking Server Load / System
memory / GPU at every range >= 4h. Wire field groups replace those cross-family
booleans (``include_agent_tokens``, ``merge_agent_details``,
``merge_cost_summary``):

- ``always``       rides EVERY history record. Structurally non-excludable:
                   ``normalized_field_groups`` re-adds always groups to any
                   caller-provided selection, so no future flag can strip an
                   unrelated family again.
- ``token_detail`` (token scalars, agent token rates, cost summary) ALSO rides
                   every history record — there is ONE history stream since
                   2026-07 (Phase 2); the client no longer requests a separate
                   compact token side-stream. Unlike ``always`` it stays
                   excludable for exactly two internal reasons: (a) the
                   response-path cost merge optimization accumulates cost
                   components once instead of per-bucket, and (b) the LEGACY
                   compat path keeps serving the old slimmed wire plus the
                   ``agent_token_history`` side payload to old clients that
                   still send ``token_resolution > 0`` (retire that path in a
                   later release; new clients never send token_* params).

12-24h token resolution (documented tradeoff): the durable retention tiers
hold only 600s buckets for data older than 12h, so token bars in the 12-24h
band render at 600s — exactly what the legacy compact stream really served
there (its requested 300s was a floor that ``effective_resolution =
max(requested, retained)`` clamped to the 600s tier; see
tests/test_statsd.py::test_token_detail_12_to_24h_band_serves_at_the_600s_tier_like_the_legacy_stream).
No per-family 300s retention was added: it would preserve nothing the old
wire actually delivered.

tests/test_stats_wire_parity.py pins the wire bytes this structure produces.
"""

from __future__ import annotations

from types import MappingProxyType
from typing import Any

WIRE_GROUP_ALWAYS = "always"
WIRE_GROUP_TOKEN_DETAIL = "token_detail"

FIELD_GROUP_SERVER_SCALARS = "server_scalars"
FIELD_GROUP_TOKEN_SCALARS = "token_scalars"
FIELD_GROUP_AGENT_TOKEN_RATES = "agent_token_rates"
FIELD_GROUP_COST_SUMMARY = "cost_summary"
FIELD_GROUP_HOST_METRICS = "host_metrics"

# The canonical host_metrics sub-document, in storage order, with each field's
# merge kind. ``stats_store.empty_host_metrics`` and the host-metrics merge in
# ``statsd._merge_bucket`` iterate THIS list; families below reference these
# names in their ``host_metric_fields``.
#   label        -> non-empty source overwrites target
#   sum          -> numeric accumulation
#   map_sum      -> keyed mapping whose items sum numeric fields and keep labels
#   service_load -> keyed mapping with total/samples/min/max per cpu and rss
HOST_METRIC_FIELDS = (
    ("cpu_label", "label"),
    ("system_memory_label", "label"),
    ("system_memory_used_total_bytes", "sum"),
    ("system_memory_capacity_total_bytes", "sum"),
    ("system_memory_count", "sum"),
    ("cpu_processes", "map_sum"),
    ("memory_processes", "map_sum"),
    ("gpu_util_processes", "map_sum"),
    ("gpu_memory_processes", "map_sum"),
    ("gpu_devices", "map_sum"),
    ("service_load", "service_load"),
)


def _family(**facts: Any) -> MappingProxyType:
    defaults: dict[str, Any] = {
        "name": "",
        # Coverage alias names an OLDER server may still write; the client tries
        # the canonical name first, then these. New code writes canonical only.
        "legacy_aliases": (),
        # True sampler cadence in seconds (agent_tokens samples at 10s while
        # watched and idle_cadence_seconds while idle).
        "cadence_seconds": 0,
        "idle_cadence_seconds": 0,
        # Cadence assumed for durable coverage intervals recorded before
        # cadence was persisted per interval.
        "legacy_coverage_cadence_seconds": 0,
        # Top-level numeric bucket fields owned by this family (sum-merged).
        "bucket_fields": (),
        # host_metrics subkeys owned by this family (must exist in
        # HOST_METRIC_FIELDS; merged by the host-metrics rule).
        "host_metric_fields": (),
        # Rich detail document owned by this family; its name doubles as its
        # field-group name ("agent_token_rates" / "cost_summary").
        "detail_field": "",
        "wire_group": WIRE_GROUP_ALWAYS,
        # Where the family's data reaches the client.
        "delivery": "history_records",
        # Client chart groups / series drawn from this family (mirrored in
        # jsDebugStatsFamilyManifest).
        "chart_groups": (),
        "series_keys": (),
        # Additional coverage families recorded whenever this family reports a
        # sample (cost rides the agent_tokens records; raw is the cpu-sampler
        # raw-tier marker).
        "coverage_companions": (),
    }
    unknown = set(facts) - set(defaults)
    if unknown:
        raise ValueError(f"unknown stats family facts: {sorted(unknown)}")
    return MappingProxyType({**defaults, **facts})


STATS_FAMILY_MANIFEST = (
    _family(
        name="raw",
        # The raw serve tier's coverage marker: recorded as a companion of cpu,
        # never sampled or charted on its own.
        cadence_seconds=1,
        legacy_coverage_cadence_seconds=1,
        delivery="coverage_only",
    ),
    _family(
        name="cpu",
        legacy_aliases=("server", "raw", "buckets"),
        cadence_seconds=1,
        legacy_coverage_cadence_seconds=1,
        bucket_fields=("cpu_total_percent", "cpu_count", "system_cpu_total_percent", "system_cpu_count"),
        host_metric_fields=("cpu_label", "cpu_processes"),
        chart_groups=("cpu",),
        series_keys=("systemCpu",),
        coverage_companions=("raw",),
    ),
    _family(
        name="service_load",
        cadence_seconds=10,
        legacy_coverage_cadence_seconds=10,
        host_metric_fields=("service_load",),
        delivery="host_metrics",
        # The serversLoad chart paints from host metrics without a coverage
        # overlay (pre-manifest behavior kept), so it maps to no chart group.
    ),
    _family(
        name="agent_status",
        legacy_aliases=("status",),
        cadence_seconds=10,
        legacy_coverage_cadence_seconds=10,
        bucket_fields=(
            "ask_agent_total",
            "run_agent_total",
            "transition_agent_total",
            "idle_agent_total",
            "active_agent_total",
            "inactive_agent_total",
            "agent_activity_samples",
        ),
        chart_groups=("activity",),
        series_keys=("askAgents", "workingAgents", "transitionAgents", "idleAgents"),
    ),
    _family(
        name="agent_tokens",
        legacy_aliases=("tokens",),
        cadence_seconds=10,
        idle_cadence_seconds=60,
        legacy_coverage_cadence_seconds=60,
        bucket_fields=("tokens_per_agent_total", "agent_token_samples"),
        detail_field="agent_token_rates",
        wire_group=WIRE_GROUP_TOKEN_DETAIL,
        chart_groups=("agentTokens",),
        series_keys=("tokensPerAgent",),
        coverage_companions=("cost",),
    ),
    _family(
        name="cost",
        legacy_aliases=("cost_atoms", "usage_atoms"),
        # Cost rides the agent_tokens records (same sampler, same stream).
        cadence_seconds=10,
        idle_cadence_seconds=60,
        legacy_coverage_cadence_seconds=60,
        detail_field="cost_summary",
        wire_group=WIRE_GROUP_TOKEN_DETAIL,
    ),
    _family(
        name="gpu",
        legacy_aliases=("gpu_metrics",),
        cadence_seconds=10,
        legacy_coverage_cadence_seconds=10,
        host_metric_fields=("gpu_util_processes", "gpu_memory_processes", "gpu_devices"),
        delivery="host_metrics",
        chart_groups=("gpuUtil", "gpuMemory"),
    ),
    _family(
        name="system_memory",
        legacy_aliases=("memory",),
        cadence_seconds=60,
        legacy_coverage_cadence_seconds=60,
        host_metric_fields=(
            "system_memory_label",
            "system_memory_used_total_bytes",
            "system_memory_capacity_total_bytes",
            "system_memory_count",
            "memory_processes",
        ),
        delivery="host_metrics",
        chart_groups=("memory",),
        series_keys=("systemMemory",),
    ),
)

STATS_FAMILY_BY_NAME = MappingProxyType({family["name"]: family for family in STATS_FAMILY_MANIFEST})

# Derived single-owner constants (order preserved from the manifest).
STATS_COVERAGE_FAMILY_NAMES = tuple(family["name"] for family in STATS_FAMILY_MANIFEST)
STATS_COVERAGE_LEGACY_CADENCE = MappingProxyType(
    {family["name"]: family["legacy_coverage_cadence_seconds"] for family in STATS_FAMILY_MANIFEST}
)
SERVER_BUCKET_FIELDS = tuple(field for family in STATS_FAMILY_MANIFEST for field in family["bucket_fields"])
TOKEN_DETAIL_BUCKET_FIELDS = tuple(
    field
    for family in STATS_FAMILY_MANIFEST
    if family["wire_group"] == WIRE_GROUP_TOKEN_DETAIL
    for field in family["bucket_fields"]
)
# LEGACY (retire in a later release): the compact token side-stream record
# projection — identity keys plus every token_detail field and detail document,
# in wire order. Served only when an OLD client still sends
# ``token_resolution > 0``; current clients receive token detail inline on
# every history record and never request this payload.
LEGACY_TOKEN_STREAM_RECORD_KEYS = (
    ("start", "duration", "sequence")
    + TOKEN_DETAIL_BUCKET_FIELDS
    + tuple(
        family["detail_field"]
        for family in STATS_FAMILY_MANIFEST
        if family["wire_group"] == WIRE_GROUP_TOKEN_DETAIL and family["detail_field"]
    )
)


def _derive_field_groups() -> MappingProxyType:
    groups: dict[str, dict[str, Any]] = {}

    def group(name: str, wire_group: str, detail_field: str = "") -> dict[str, Any]:
        entry = groups.setdefault(name, {"wire_group": wire_group, "bucket_fields": [], "detail_field": detail_field})
        if entry["wire_group"] != wire_group:
            raise ValueError(f"stats field group {name!r} declared with conflicting wire groups")
        return entry

    for family in STATS_FAMILY_MANIFEST:
        if family["bucket_fields"]:
            scalars = (
                FIELD_GROUP_SERVER_SCALARS
                if family["wire_group"] == WIRE_GROUP_ALWAYS
                else FIELD_GROUP_TOKEN_SCALARS
            )
            group(scalars, family["wire_group"])["bucket_fields"].extend(family["bucket_fields"])
        if family["detail_field"]:
            group(family["detail_field"], family["wire_group"], family["detail_field"])
        for field in family["host_metric_fields"]:
            if field not in {name for name, _kind in HOST_METRIC_FIELDS}:
                raise ValueError(f"stats family {family['name']!r} references unknown host metric field {field!r}")
            group(FIELD_GROUP_HOST_METRICS, WIRE_GROUP_ALWAYS, "host_metrics")
    return MappingProxyType(
        {
            name: MappingProxyType({**entry, "bucket_fields": tuple(entry["bucket_fields"])})
            for name, entry in groups.items()
        }
    )


STATS_FIELD_GROUPS = _derive_field_groups()
ALL_FIELD_GROUPS = frozenset(STATS_FIELD_GROUPS)
ALWAYS_FIELD_GROUPS = frozenset(
    name for name, entry in STATS_FIELD_GROUPS.items() if entry["wire_group"] == WIRE_GROUP_ALWAYS
)
TOKEN_DETAIL_FIELD_GROUPS = ALL_FIELD_GROUPS - ALWAYS_FIELD_GROUPS


def coverage_families_for(family_name: str) -> tuple[str, ...]:
    """Every coverage family recorded when ``family_name`` reports a sample."""
    entry = STATS_FAMILY_BY_NAME.get(family_name)
    if entry is None:
        return (family_name,)
    return (family_name, *entry["coverage_companions"])


def normalized_field_groups(field_groups: Any) -> frozenset[str]:
    """Validate a merge/encode field-group selection.

    ``always`` groups are structurally non-excludable: whatever a caller
    passes, they are re-added, so no flag derived from one family's needs can
    ever strip an unrelated always-wire family (the 2026-07-14 host-metrics
    incident) from a merge or a history payload.
    """
    groups = frozenset(field_groups)
    unknown = groups - ALL_FIELD_GROUPS
    if unknown:
        raise ValueError(f"unknown stats field groups: {sorted(unknown)}")
    return groups | ALWAYS_FIELD_GROUPS


def wire_field_groups(*, legacy_token_stream: bool = False) -> frozenset[str]:
    """The field groups a main history record carries on the wire.

    There is ONE history stream: every group — including ``token_detail`` —
    rides every record. The single exception is the LEGACY compat path
    (``legacy_token_stream=True``): an old client that still sends
    ``token_resolution > 0`` keeps receiving the pre-Phase-2 slimmed records
    plus the separate ``agent_token_history`` payload, byte-compatible with
    what it was built against. Retire with that path in a later release.
    """
    return ALWAYS_FIELD_GROUPS if legacy_token_stream else ALL_FIELD_GROUPS


def bucket_fields_for_groups(field_groups: Any) -> tuple[str, ...]:
    """The top-level numeric bucket fields of a group selection, in storage order."""
    groups = normalized_field_groups(field_groups)
    fields = {field for name in groups for field in STATS_FIELD_GROUPS[name]["bucket_fields"]}
    return tuple(field for field in SERVER_BUCKET_FIELDS if field in fields)
