# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Shared OpenCode and transcript usage collector owned by statsd."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from .. import session_files
from ..settings import usage_pricing_profile
from . import collectors, opencode, storage, usage
from .transcripts import StatsCurrentTranscriptUsageScanner


TokenRowsProvider = Callable[[opencode.OpenCodeProcessInventory], list[Mapping[str, object]]]
InventoryProvider = Callable[[], tuple[opencode.OpenCodeProcessInventory, list[str]]]
UsageReader = Callable[..., opencode.OpenCodeReadResult]
SettingsProvider = Callable[[], Mapping[str, object]]
SourceIdentityProvider = Callable[[], str]
BackfillStatusSink = Callable[[Mapping[str, object]], None]


def token_key(row: Mapping[str, object], fallback_index: int) -> str:
    """Build the stable physical agent-window key used by chart attribution."""

    session = str(row.get("session") or "").strip()
    window = row.get("window_index")
    if not isinstance(window, int):
        window = str(row.get("window") or row.get("window_label") or row.get("label") or "").strip()
    pane_target = str(row.get("pane_target") or row.get("pane") or "").strip()
    kind = str(row.get("kind") or "").strip().lower()
    key = "|".join(part for part in (session, str(window).strip(), pane_target, kind) if part)
    return key or f"agent-{fallback_index}"


def rows_from_opencode_inventory(
    inventory: opencode.OpenCodeProcessInventory,
) -> list[dict[str, object]]:
    return [
        {
            "key": opencode.agent_token_key_for_process(
                pid=item.observation.pid,
                tmux_session=item.observation.tmux_session,
            ),
            "session": item.observation.tmux_session,
            "pane_target": item.observation.pane_target,
            "kind": "opencode",
            "agent_session_id": item.session_id,
            "cwd": item.observation.directory or "",
            "started_at": item.observation.started_at,
        }
        for item in inventory.sessions
    ]


class AgentTokenCollector:
    """Collect and validate all token facts before one statsd store append."""

    def __init__(
        self,
        *,
        scanner: StatsCurrentTranscriptUsageScanner,
        cursors: opencode.OpenCodeCursorStore,
        database: Path,
        inventory_provider: InventoryProvider,
        rows_provider: TokenRowsProvider | None = None,
        settings_provider: SettingsProvider,
        source_identity_provider: SourceIdentityProvider,
        read_usage: UsageReader = opencode.read_usage,
        backfill_status_sink: BackfillStatusSink | None = None,
    ) -> None:
        self.scanner = scanner
        self.cursors = cursors
        self.database = Path(database)
        self.inventory_provider = inventory_provider
        self.rows_provider = rows_provider
        self.settings_provider = settings_provider
        self.source_identity_provider = source_identity_provider
        self.read_usage = read_usage
        self.backfill_status_sink = backfill_status_sink

    def collect(self, attempt: Any) -> collectors.CollectorFacts:
        inventory, inventory_errors = self.inventory_provider()
        rows = (
            [dict(row) for row in self.rows_provider(inventory)]
            if self.rows_provider is not None
            else rows_from_opencode_inventory(inventory)
        )
        unavailable_spans = self._inventory_unavailable(attempt, inventory, inventory_errors)
        if not rows and (inventory.unavailable or inventory_errors):
            return collectors.collector_unavailable(
                family="agent_tokens",
                source_id="opencode-process-inventory",
                epoch_id=attempt.epoch_id,
                epoch_started_at=attempt.epoch_started_at,
                observed_at=attempt.scheduled_at,
                cadence_seconds=attempt.cadence_seconds,
                owner_generation=attempt.owner_generation,
                reason="opencode-inventory-unavailable",
            )

        cursor_fence = self.cursors.reset_for_database(self.database)
        if cursor_fence is not None:
            unavailable_spans.extend(self._unavailable(
                attempt,
                opencode.source_id_for_agent("cursor-state"),
                "cursor-state-fence",
                f"opencode-{cursor_fence.reason}",
            ))
            return collectors.usage_scan_success(
                (), (), None,
                epoch_id=attempt.epoch_id,
                epoch_started_at=attempt.epoch_started_at,
                observed_at=attempt.scheduled_at,
                cadence_seconds=attempt.cadence_seconds,
                owner_generation=attempt.owner_generation,
                source_id=self.source_identity_provider(),
                unavailable_spans=unavailable_spans,
            )
        scan = self.scanner.scan(rows)
        state = self.cursors.state()
        cursor_values = state.values if isinstance(state, opencode.OpenCodeCursorState) else {}
        cursor_epochs = (
            state.epochs
            if isinstance(state, opencode.OpenCodeCursorState) and state.epochs is not None
            else {}
        )
        cursor_sequences = (
            state.sequences
            if isinstance(state, opencode.OpenCodeCursorState) and state.sequences is not None
            else {}
        )
        cursor_presence = (
            state.presence
            if isinstance(state, opencode.OpenCodeCursorState) and state.presence is not None
            else {}
        )
        cursor_event_revisions = (
            state.event_revisions
            if isinstance(state, opencode.OpenCodeCursorState) and state.event_revisions is not None
            else {}
        )
        cursor_unavailable = state if isinstance(state, opencode.OpenCodeUnavailable) else None
        proposed_cursor_values = dict(cursor_values)
        proposed_cursor_epochs = dict(cursor_epochs)
        proposed_cursor_sequences = dict(cursor_sequences)
        proposed_cursor_presence = dict(cursor_presence)
        proposed_cursor_event_revisions = dict(cursor_event_revisions)
        atoms: list[storage.UsageAtom] = []
        tombstones = [
            usage.legacy_fork_usage_tombstone_from_source(vars(item.atom))
            for item in scan.tombstones
        ]
        opencode_atoms: list[tuple[str, session_files.TranscriptUsageAtom]] = []
        opencode_coverage: list[storage.CoverageEpoch] = []
        opencode_results: dict[str, opencode.OpenCodeReadResult] = {}
        opencode_claims: dict[str, set[str]] = {}

        for row_index, row in enumerate(rows):
            if str(row.get("kind") or "").lower() != "opencode":
                continue
            key = str(row.get("key") or token_key(row, row_index))
            session_id = str(row.get("agent_session_id") or row.get("session_id") or "").strip() or None
            directory = str(row.get("cwd") or row.get("path") or "").strip() or None
            started_at = row.get("started_at")
            if isinstance(started_at, bool) or not isinstance(started_at, (int, float)) or started_at <= 0:
                started_at = None
            result = self.read_usage(
                database=self.database,
                session_id=session_id,
                directory=directory,
                started_at=started_at,
                now=attempt.scheduled_at,
                known_event_revisions=proposed_cursor_event_revisions,
                incremental=True,
            )
            opencode_results[key] = result
            if isinstance(result, opencode.OpenCodeReadSuccess):
                opencode_claims.setdefault(result.session.session_id, set()).add(key)

        conflicting_keys = {
            key
            for claimants in opencode_claims.values()
            if len(claimants) > 1
            for key in claimants
        }
        for row_index, row in enumerate(rows):
            if str(row.get("kind") or "").lower() != "opencode":
                continue
            key = str(row.get("key") or token_key(row, row_index))
            session_id = str(row.get("agent_session_id") or row.get("session_id") or "").strip() or None
            directory = str(row.get("cwd") or row.get("path") or "").strip() or None
            source_id = opencode.source_id_for_selector(
                session_id=session_id,
                directory=directory,
                agent_key=key,
            )
            if cursor_unavailable is not None:
                unavailable_spans.extend(self._unavailable(
                    attempt, source_id, key, f"opencode-{cursor_unavailable.reason}",
                ))
                continue
            result = opencode_results[key]
            if isinstance(result, opencode.OpenCodeAmbiguousSession):
                unavailable_spans.extend(self._unavailable(attempt, source_id, key, f"opencode-{result.reason}"))
                continue
            if not isinstance(result, opencode.OpenCodeReadSuccess):
                unavailable_spans.extend(self._unavailable(attempt, source_id, key, f"opencode-{result.reason}"))
                continue
            if key in conflicting_keys:
                unavailable_spans.extend(self._unavailable(
                    attempt, source_id, key, "opencode-session-claimed-by-multiple-agents",
                ))
                continue
            source_id = opencode.source_id_for_session(result.session.session_id)
            omits_dimensions = any(
                component.dimension not in opencode._ATOM_DIMENSIONS
                for component in result.components
            )
            opencode_coverage.append(storage.CoverageEpoch(
                "agent_tokens",
                source_id,
                f"{attempt.epoch_id}:opencode:{result.session.session_id}",
                attempt.epoch_started_at,
                attempt.scheduled_at + attempt.cadence_seconds,
                attempt.cadence_seconds,
                attempt.owner_generation,
            ))
            for component in result.components:
                if component.dimension not in opencode._ATOM_DIMENSIONS or component.tokens <= 0:
                    continue
                cursor_key = opencode.cursor_key(component.session_id, component.dimension)
                if not component.source_revision:
                    previous = proposed_cursor_values.get(cursor_key)
                    if previous is not None and component.tokens < previous:
                        proposed_cursor_epochs[cursor_key] = proposed_cursor_epochs.get(cursor_key, 0) + 1
                        proposed_cursor_sequences[cursor_key] = 0
                        proposed_cursor_values[cursor_key] = component.tokens
                        continue
                    quantity = component.tokens - previous if previous is not None else component.tokens
                    proposed_cursor_values[cursor_key] = component.tokens
                    if quantity <= 0:
                        continue
                    proposed_cursor_sequences[cursor_key] = proposed_cursor_sequences.get(cursor_key, 0) + 1
                    event_id = opencode.delta_event_id(
                        component.session_id,
                        component.dimension,
                        proposed_cursor_epochs.get(cursor_key, 0),
                        proposed_cursor_sequences[cursor_key],
                    )
                else:
                    quantity = component.tokens
                    event_id = component.event_id
                if component.source_revision:
                    previous_revision = proposed_cursor_event_revisions.get(event_id)
                    if previous_revision == component.source_revision:
                        continue
                    if previous_revision is not None and previous_revision != component.source_revision:
                        unavailable_spans.extend(self._unavailable(
                            attempt, source_id, "revision", "opencode-source-revision-changed",
                        ))
                        continue
                    proposed_cursor_event_revisions[event_id] = component.source_revision
                direction, cache_role = {
                    "input": ("input", "none"),
                    "cache_read": ("input", "read"),
                    "output": ("output", "none"),
                }[component.dimension]
                opencode_atoms.append((
                    key,
                    session_files.TranscriptUsageAtom(
                        source=f"opencode:{component.session_id}",
                        timestamp=component.observed_at,
                        event_id=event_id,
                        provider=component.provider,
                        model=component.model,
                        model_evidence=component.model_evidence,
                        effort="unknown",
                        direction=direction,
                        modality="text",
                        cache_role=cache_role,
                        unit="tokens",
                        quantity=float(quantity),
                        root_thread_id=component.session_id,
                        agent_thread_id=component.session_id,
                        endpoint="opencode",
                        telemetry_complete=component.telemetry_complete and not omits_dimensions,
                    ),
                ))

        rejection_reasons: dict[str, int] = {}
        settings = self.settings_provider()
        transcript_atoms_accepted = 0
        for item in scan.items:
            fields = dict(vars(item.atom))
            fields["tmux_key"] = item.tmux_key
            fields["agent_kind"] = item.agent_kind
            self._apply_profile(fields, settings, item.agent_kind, item.atom.timestamp)
            try:
                atoms.append(usage.usage_atom_from_source(fields))
                transcript_atoms_accepted += 1
            except usage.UsageValidationError as error:
                reason = str(error)[:160] or "usage_validation_error"
                rejection_reasons[reason] = rejection_reasons.get(reason, 0) + 1
        for key, atom in opencode_atoms:
            fields = dict(vars(atom))
            fields["tmux_key"] = key
            fields["agent_kind"] = "opencode"
            fields["agent_id"] = key
            self._apply_profile(fields, settings, "opencode", atom.timestamp)
            try:
                atoms.append(usage.usage_atom_from_source(fields))
            except usage.UsageValidationError as error:
                reason = str(error)[:160] or "usage_validation_error"
                rejection_reasons[reason] = rejection_reasons.get(reason, 0) + 1
        if self.backfill_status_sink is not None:
            self.backfill_status_sink(
                self.scanner.usage_atom_backfill_status_for_scan(
                    scan,
                    atoms_accepted=transcript_atoms_accepted,
                    rejection_reasons=rejection_reasons,
                )
            )

        cursor_prepared = False
        cursor_changed = (
            proposed_cursor_values != cursor_values
            or proposed_cursor_epochs != cursor_epochs
            or proposed_cursor_sequences != cursor_sequences
            or proposed_cursor_presence != cursor_presence
            or proposed_cursor_event_revisions != cursor_event_revisions
        )
        if cursor_changed and cursor_unavailable is None:
            try:
                self.cursors.prepare(
                    proposed_cursor_values,
                    proposed_cursor_epochs,
                    proposed_cursor_sequences,
                    expected_values=cursor_values,
                    expected_epochs=cursor_epochs,
                    expected_sequences=cursor_sequences,
                    presence=proposed_cursor_presence,
                    expected_presence=cursor_presence,
                    event_revisions=proposed_cursor_event_revisions,
                    expected_event_revisions=cursor_event_revisions,
                )
                cursor_prepared = True
            except (OSError, ValueError) as error:
                unavailable_spans.extend(self._unavailable(
                    attempt, opencode.source_id_for_agent("cursor-state"), "cursor-state", f"opencode-{error}",
                ))
                atoms = []
                opencode_coverage = []

        transcript_committed = False
        cursor_committed = not cursor_prepared

        def commit_receipt() -> None:
            nonlocal transcript_committed, cursor_committed
            if not transcript_committed:
                self.scanner.commit(scan.receipt_id)
                transcript_committed = True
            if cursor_prepared and not cursor_committed:
                self.cursors.commit()
                cursor_committed = True

        def rollback_receipt() -> None:
            if not transcript_committed:
                try:
                    self.scanner.rollback(scan.receipt_id)
                finally:
                    if cursor_prepared and not cursor_committed:
                        self.cursors.rollback()
            elif cursor_prepared and not cursor_committed:
                self.cursors.rollback()

        return collectors.usage_scan_success(
            atoms,
            tombstones,
            collectors.CollectorReceipt(commit_receipt, rollback_receipt),
            epoch_id=attempt.epoch_id,
            epoch_started_at=attempt.epoch_started_at,
            observed_at=attempt.scheduled_at,
            cadence_seconds=attempt.cadence_seconds,
            owner_generation=attempt.owner_generation,
            source_id=self.source_identity_provider(),
            unavailable_spans=unavailable_spans,
            additional_coverage=opencode_coverage,
            budget_exhausted_follow_up=scan.budget_exhausted,
        )

    @staticmethod
    def _apply_profile(
        fields: dict[str, object],
        settings: Mapping[str, object],
        execution_source: str,
        observed_at: float,
    ) -> None:
        if fields.get("pricing_profile", "default") == "default":
            fields["pricing_profile"] = usage_pricing_profile(
                dict(settings),
                provider=str(fields.get("provider") or ""),
                execution_source=execution_source,
                endpoint=str(fields.get("endpoint") or ""),
                observed_at=observed_at,
            )

    @staticmethod
    def _unavailable(
        attempt: Any,
        source_id: str,
        key: str,
        reason: str,
    ) -> tuple[storage.UnavailableSpan, ...]:
        return collectors.collector_unavailable(
            family="agent_tokens",
            source_id=source_id,
            epoch_id=f"{attempt.epoch_id}:opencode:{key}",
            epoch_started_at=attempt.epoch_started_at,
            observed_at=attempt.scheduled_at,
            cadence_seconds=attempt.cadence_seconds,
            owner_generation=attempt.owner_generation,
            reason=reason[:160],
        ).unavailable_spans

    def _inventory_unavailable(
        self,
        attempt: Any,
        inventory: opencode.OpenCodeProcessInventory,
        errors: list[str],
    ) -> list[storage.UnavailableSpan]:
        spans: list[storage.UnavailableSpan] = []
        for item in inventory.unavailable:
            spans.extend(self._unavailable(
                attempt,
                opencode.source_id_for_agent(f"process:{item.observation.pid}"),
                f"process:{item.observation.pid}",
                f"opencode-{item.reason or 'session-identity-unavailable'}",
            ))
        if self.rows_provider is None:
            for error in errors:
                spans.extend(self._unavailable(
                    attempt,
                    opencode.source_id_for_agent("process-inventory"),
                    "process-inventory",
                    f"opencode-{error}",
                ))
        return spans
