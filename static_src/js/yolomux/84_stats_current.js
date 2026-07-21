// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
// This module is intentionally self-contained: tests and embedded consumers load only this namespace,
// so its escaping and UTF-8 validation helpers cannot depend on the app bundle's lexical core helpers.

(() => {
  'use strict';

  const SNAPSHOT_FIELDS = [
    'protocol_version', 'range_seconds', 'requested_resolution', 'resolution_seconds',
    'window_start', 'window_end', 'generated_at', 'source_generation', 'cache_generation',
    'rightmost_open', 'buckets', 'no_data', 'cost_report',
  ];
  const DELTA_FIELDS = [
    'protocol_version', 'range_seconds', 'resolution_seconds', 'source_generation',
    'base_cache_generation', 'cache_generation', 'revision', 'buckets', 'no_data', 'tombstones',
    'cost_report',
  ];
  const BUCKET_FIELDS = ['start', 'duration', 'series', 'source', 'open'];
  const SERIES_VALUE_FIELDS = ['value', 'source_count', 'first_timestamp', 'last_timestamp'];
  const SOURCE_FIELDS = ['first_timestamp', 'last_timestamp', 'count'];
  const NO_DATA_FIELDS = [
    'family', 'source_id', 'start', 'end', 'epoch', 'reason', 'source_cadence_seconds',
  ];
  const BUCKET_TOMBSTONE_FIELDS = ['kind', 'start', 'duration'];
  const NO_DATA_TOMBSTONE_FIELDS = ['kind', 'family', 'source_id', 'start', 'end', 'epoch'];
  const CAPABILITY_FIELDS = [
    'resolution_choices', 'max_buckets', 'min_buckets', 'max_live_cadence_seconds', 'ranges',
  ];
  const CAPABILITY_RANGE_FIELDS = [
    'range_seconds', 'auto_resolution_seconds', 'explicit_resolution_seconds', 'buckets',
  ];
  const CURRENT_RESOLUTIONS = Object.freeze([1, 10, 60, 300]);
  const CURRENT_STATS_WIRE_PROTOCOL_VERSION = 2;
  const CURRENT_COST_REPORT_SCHEMA_VERSION = 3;
  const CURRENT_COST_DIMENSIONS = Object.freeze(['input', 'cache_read', 'cache_write_5m', 'cache_write_1h', 'output', 'other']);
  const CURRENT_COST_REPORT_FIELDS = Object.freeze([
    'schema_version', 'total_micro_usd', 'total_api_list_micro_usd', 'total_tokens',
    'dimensions', 'priced', 'unpriced', 'models', 'agents', 'evidence', 'catalog_revision',
    'omissions', 'reasoning_available',
  ]);
  const CURRENT_COST_DIMENSION_FIELDS = Object.freeze(['tokens', 'micro_usd', 'api_list_micro_usd']);
  const CURRENT_COST_COVERAGE_FIELDS = Object.freeze(['atoms', 'tokens']);
  const CURRENT_COST_MODEL_FIELDS = Object.freeze([
    'key', 'provider', 'model', 'total_tokens', 'total_micro_usd',
    'total_api_list_micro_usd', 'dimensions', 'priced', 'unpriced',
  ]);
  const CURRENT_COST_AGENT_FIELDS = Object.freeze([
    'key', 'source', 'label', 'total_tokens', 'total_micro_usd', 'total_api_list_micro_usd',
    'dimensions', 'priced', 'unpriced',
  ]);
  const CURRENT_COST_EVIDENCE_FIELDS = Object.freeze([
    'key', 'provider', 'model', 'dimension', 'direction', 'modality', 'cache_role', 'unit',
    'pricing_profile', 'service_tier', 'catalog_model', 'rate_usd', 'rate_scale', 'effective_from',
    'source_kind', 'source_url', 'catalog_revision', 'tokens', 'micro_usd',
    'api_list_micro_usd', 'priced_atoms',
  ]);
  const CURRENT_COST_OMISSION_FIELDS = Object.freeze(['models', 'agents', 'evidence']);
  const CURRENT_COST_MAX_MODELS = 16;
  const CURRENT_COST_MAX_AGENTS = 16;
  const CURRENT_COST_MAX_EVIDENCE = 32;
  const CURRENT_STATS_PENDING_RETRY_MAX_SECONDS = 60;
  const SNAPSHOT_NOT_MODIFIED = Object.freeze({not_modified: true});
  const CURRENT_STATS_SVG_WIDTH = 600;
  const CURRENT_STATS_SVG_HEIGHT = 160;
  const CURRENT_STATS_PLOT_LEFT = 42;
  const CURRENT_STATS_PLOT_RIGHT = 12;
  const CURRENT_STATS_PLOT_TOP = 12;
  const CURRENT_STATS_PLOT_BOTTOM = 28;
  const CURRENT_STATS_ZOOM_THRESHOLD_PX = 8;
  let nextRendererId = 1;

  function createController(options) {
    const capabilities = normalizeCapabilities(options.capabilities);
    const clock = options.clock || {
      now: () => Date.now(),
      setTimeout: (callback, delay) => setTimeout(callback, delay),
      clearTimeout: timer => clearTimeout(timer),
    };
    const onGeneration = options.onGeneration || (() => {});
    const onRepairNeeded = options.onRepairNeeded || (() => {});
    const onRepairComplete = options.onRepairComplete || (() => {});
    const onViewport = options.onViewport || (() => {});
    const onTick = options.onTick || (() => null);
    const fetchSnapshot = options.fetchSnapshot || (async () => null);
    const repairBaseMs = positiveInteger(options.repairBaseMs ?? 500, 'repairBaseMs');
    const repairMaxMs = positiveInteger(options.repairMaxMs ?? 30_000, 'repairMaxMs');
    if (repairMaxMs < repairBaseMs) throw new Error('repairMaxMs must be at least repairBaseMs');
    const clientId = String(options.clientId || '').trim();
    if (!clientId || clientId.length > 128 || /[\x00-\x1f\x7f]/.test(clientId)) {
      throw new Error('clientId must be non-empty, bounded, and contain no controls');
    }

    let selection = normalizeSelection(capabilities, options.savedRange, options.savedResolution);
    let activeGeneration = null;
    let running = false;
    let visible = true;
    let zoomedStatic = false;
    let tickTimer = null;
    let tickBusy = false;
    let repairTimer = null;
    let repairBusy = false;
    let repairNeeded = false;
    let repairRequiresFullSnapshot = false;
    let repairDelayMs = repairBaseMs;
    let selectionSerial = 0;
    let presentationAnchorMs = null;
    let presentationAnchorEnd = null;
    let presentationWindowEnd = null;
    let zoomWindowStart = null;
    let zoomWindowEnd = null;
    let activeDeltaRevision = 0;

    function concreteResolution() {
      return selection.resolution === 'AUTO'
        ? selection.capability.auto_resolution_seconds
        : selection.resolution;
    }

    function buildRequest() {
      return Object.freeze({
        range_seconds: selection.range_seconds,
        resolution: selection.resolution,
        client_id: clientId,
        since_generation: repairRequiresFullSnapshot ? 0 : (activeGeneration?.cache_generation ?? 0),
      });
    }

    function buildDeltaRequest() {
      return Object.freeze({
        range_seconds: selection.range_seconds,
        resolution_seconds: concreteResolution(),
        client_id: clientId,
        after_cache_generation: activeGeneration?.cache_generation ?? 0,
        after_revision: activeDeltaRevision,
      });
    }

    function requestMatches(request, serial) {
      return serial === selectionSerial
        && request.range_seconds === selection.range_seconds
        && request.resolution === selection.resolution;
    }

    function acceptSnapshot(snapshot) {
      validateSnapshot(snapshot, selection, concreteResolution(), capabilities.max_buckets);
      if (activeGeneration && snapshot.cache_generation < activeGeneration.cache_generation) {
        throw new Error('snapshot generation is stale');
      }
      if (activeGeneration && snapshot.cache_generation === activeGeneration.cache_generation) {
        if (activeDeltaRevision > 0) {
          activeDeltaRevision = 0;
          publish(snapshot, true);
          clearRepair();
          return true;
        }
        if (!sameJson(snapshot, activeGeneration)) {
          throw new Error('snapshot generation is not immutable');
        }
        return false;
      }
      if (activeGeneration && snapshot.source_generation < activeGeneration.source_generation) {
        throw new Error('snapshot source generation regressed');
      }
      activeDeltaRevision = 0;
      publish(snapshot, true);
      clearRepair();
      return true;
    }

    function acceptDelta(delta) {
      if (!visible) {
        repairNeeded = true;
        return false;
      }
      if (!activeGeneration) {
        scheduleRepair();
        return false;
      }
      try {
        exactFields(delta, DELTA_FIELDS, 'delta');
        if (delta.protocol_version !== CURRENT_STATS_WIRE_PROTOCOL_VERSION) throw new Error('delta protocol is unsupported');
        generationNumber(delta.source_generation, 'source_generation');
        generationNumber(delta.base_cache_generation, 'base_cache_generation');
        generationNumber(delta.cache_generation, 'cache_generation');
        positiveInteger(delta.revision, 'revision');
        if (delta.cache_generation <= activeGeneration.cache_generation) return false;
        if (
          delta.range_seconds !== selection.range_seconds
          || delta.resolution_seconds !== concreteResolution()
          || delta.source_generation < activeGeneration.source_generation
          || delta.base_cache_generation !== activeGeneration.cache_generation
          || delta.cache_generation <= delta.base_cache_generation
          || (activeDeltaRevision > 0 && delta.revision !== activeDeltaRevision + 1)
        ) throw new Error('delta key, base, or revision does not continue the active stream');
        validateBuckets(
          delta.buckets,
          concreteResolution(),
          capabilities.max_buckets,
          false,
        );
        validateNoData(delta.no_data);
        validateCostReport(delta.cost_report);
        const removed = validateTombstones(
          delta.tombstones,
          concreteResolution(),
          capabilities.max_buckets * 2,
        );
        if (delta.buckets.length + delta.no_data.length + removed.size > capabilities.max_buckets * 2) {
          throw new Error('delta contains too many identities');
        }
        const bucketReplacements = new Map(delta.buckets.map(bucket => [bucketIdentityKey(bucket), bucket]));
        const noDataReplacements = new Map(delta.no_data.map(span => [noDataIdentityKey(span), span]));
        const replaced = new Set([...bucketReplacements.keys(), ...noDataReplacements.keys()]);
        if ([...removed.keys()].some(identity => replaced.has(identity))) {
          throw new Error('delta identity cannot be both replaced and removed');
        }
        if (!replaced.size && !removed.size) throw new Error('delta has no replacements or tombstones');

        const bucketsByIdentity = new Map(
          activeGeneration.buckets.map(bucket => [bucketIdentityKey(bucket), bucket]),
        );
        for (const [identity, tombstone] of removed) {
          if (tombstone.kind === 'bucket') bucketsByIdentity.delete(identity);
        }
        for (const [identity, bucket] of bucketReplacements) bucketsByIdentity.set(identity, bucket);
        const candidateEnd = Math.max(
          activeGeneration.window_end,
          ...delta.buckets.map(bucket => bucket.start + bucket.duration),
        );
        const candidateStart = candidateEnd - selection.range_seconds;
        const buckets = [...bucketsByIdentity.values()]
          .filter(bucket => bucket.start >= candidateStart && bucket.start + bucket.duration <= candidateEnd);
        buckets.sort((left, right) => left.start - right.start);
        validateBuckets(
          buckets,
          concreteResolution(),
          capabilities.max_buckets,
          true,
          candidateStart,
          candidateEnd,
        );
        const noDataByIdentity = new Map(
          activeGeneration.no_data.map(span => [noDataIdentityKey(span), span]),
        );
        for (const [identity, tombstone] of removed) {
          if (tombstone.kind === 'no_data') noDataByIdentity.delete(identity);
        }
        for (const [identity, span] of noDataReplacements) noDataByIdentity.set(identity, span);
        const gaps = [...noDataByIdentity.values()]
          .filter(gap => gap.end > candidateStart && gap.start < candidateEnd)
          .sort(compareNoData);
        validateNoData(gaps);
        activeDeltaRevision = delta.revision;
        publish({
          ...activeGeneration,
          source_generation: delta.source_generation,
          cache_generation: delta.cache_generation,
          window_start: candidateStart,
          window_end: candidateEnd,
          rightmost_open: buckets.at(-1)?.open === true,
          buckets,
          no_data: gaps,
          cost_report: delta.cost_report,
        }, false);
        clearRepair();
        return true;
      } catch (_error) {
        repairRequiresFullSnapshot = true;
        scheduleRepair();
        return false;
      }
    }

    function publish(value, authoritativeWindow) {
      const candidate = freezeJson(value);
      activeGeneration = candidate;
      if (!zoomedStatic || presentationWindowEnd === null) {
        const nextEnd = authoritativeWindow
          ? candidate.window_end
          : Math.max(presentationWindowEnd ?? candidate.window_end, candidate.window_end);
        anchorPresentation(nextEnd);
      }
      onGeneration(candidate);
    }

    function anchorPresentation(windowEnd) {
      const cadenceMs = liveCadenceSeconds() * 1000;
      presentationAnchorMs = Math.floor(clock.now() / cadenceMs) * cadenceMs;
      presentationAnchorEnd = windowEnd;
      presentationWindowEnd = windowEnd;
    }

    function updatePresentation(now) {
      if (!activeGeneration || presentationAnchorMs === null || presentationAnchorEnd === null) return;
      const cadenceSeconds = liveCadenceSeconds();
      const elapsedSteps = Math.floor(Math.max(0, now - presentationAnchorMs) / (cadenceSeconds * 1000));
      presentationWindowEnd = presentationAnchorEnd + elapsedSteps * cadenceSeconds;
    }

    function liveCadenceSeconds() {
      return Math.min(concreteResolution(), capabilities.max_live_cadence_seconds);
    }

    function axisContract() {
      const showSeconds = concreteResolution() === 1;
      return Object.freeze({
        smallest_unit: showSeconds ? 'second' : 'minute',
        show_seconds: showSeconds,
      });
    }

    function presentation() {
      if (!activeGeneration) return null;
      if (zoomedStatic && zoomWindowStart !== null && zoomWindowEnd !== null) {
        return Object.freeze({
          range_seconds: zoomWindowEnd - zoomWindowStart,
          resolution_seconds: concreteResolution(),
          source_generation: activeGeneration.source_generation,
          cache_generation: activeGeneration.cache_generation,
          delta_revision: activeDeltaRevision,
          window_start: zoomWindowStart,
          window_end: zoomWindowEnd,
          axis: axisContract(),
          buckets: activeGeneration.buckets,
          no_data: activeGeneration.no_data,
        });
      }
      const windowEnd = presentationWindowEnd ?? activeGeneration.window_end;
      return Object.freeze({
        range_seconds: selection.range_seconds,
        resolution_seconds: concreteResolution(),
        source_generation: activeGeneration.source_generation,
        cache_generation: activeGeneration.cache_generation,
        delta_revision: activeDeltaRevision,
        window_start: windowEnd - selection.range_seconds,
        window_end: windowEnd,
        axis: axisContract(),
        buckets: activeGeneration.buckets,
        no_data: activeGeneration.no_data,
      });
    }

    function projectSeries(seriesName, plotWidth) {
      if (typeof seriesName !== 'string' || !seriesName || !Number.isFinite(plotWidth) || plotWidth < 0) {
        throw new Error('seriesName and plotWidth are invalid');
      }
      const frame = presentation();
      if (!frame) return Object.freeze([]);
      const points = activeGeneration.buckets.flatMap(bucket => {
        if (!Object.prototype.hasOwnProperty.call(bucket.series, seriesName)) return [];
        return [Object.freeze({
          start: bucket.start,
          value: bucket.series[seriesName].value,
          x: (bucket.start - frame.window_start) / frame.range_seconds * plotWidth,
        })];
      });
      return Object.freeze(points);
    }

    function scheduleTick() {
      if (!running || !visible || zoomedStatic || tickTimer !== null) return;
      const cadence = liveCadenceSeconds() * 1000;
      const remainder = ((clock.now() % cadence) + cadence) % cadence;
      const delay = remainder === 0 ? cadence : cadence - remainder;
      tickTimer = clock.setTimeout(runTick, delay);
    }

    async function runTick() {
      tickTimer = null;
      if (!running || !visible || zoomedStatic) return;
      if (tickBusy || repairBusy) {
        scheduleTick();
        return;
      }
      tickBusy = true;
      const now = clock.now();
      updatePresentation(now);
      const request = buildRequest();
      const serial = selectionSerial;
      try {
        onViewport(presentation());
        // Fine ticks advance presentation and drain SSE work; they never call the
        // full-snapshot repair function. Coarse ticks may return one exact snapshot.
        const snapshot = await onTick(request, Object.freeze({
          now,
          snapshotDue: concreteResolution() >= 60,
        }));
        if (snapshot && requestMatches(request, serial) && running && visible && !zoomedStatic) {
          acceptSnapshot(snapshot);
        }
      } catch (_error) {
        scheduleRepair();
      } finally {
        tickBusy = false;
        scheduleTick();
      }
    }

    function scheduleRepair(immediate = false) {
      const firstRequest = !repairNeeded;
      repairNeeded = true;
      if (firstRequest) onRepairNeeded();
      if (!visible || zoomedStatic || repairBusy) return;
      if (repairTimer !== null) {
        if (!immediate) return;
        clock.clearTimeout(repairTimer);
      }
      repairTimer = clock.setTimeout(runRepair, immediate ? 0 : repairDelayMs);
    }

    async function runRepair() {
      repairTimer = null;
      if (!visible || zoomedStatic) return;
      if (repairBusy) return;
      if (tickBusy) {
        scheduleRepair();
        return;
      }
      repairBusy = true;
      repairNeeded = false;
      const request = buildRequest();
      const serial = selectionSerial;
      let succeeded = false;
      let pendingRetryMs = null;
      try {
        const snapshot = await fetchSnapshot(request);
        if (!requestMatches(request, serial) || !visible || zoomedStatic) return;
        if (snapshot === SNAPSHOT_NOT_MODIFIED && activeGeneration) {
          succeeded = true;
          onRepairComplete(activeGeneration);
        } else if (snapshot) {
          acceptSnapshot(snapshot);
          succeeded = true;
          onRepairComplete(activeGeneration);
        }
      } catch (error) {
        // The bounded delay below is the only retry owner.
        if (error?.pending === true && Number.isSafeInteger(error.retryAfterMs)) {
          pendingRetryMs = error.retryAfterMs;
        }
      } finally {
        repairBusy = false;
        if (succeeded) {
          repairDelayMs = repairBaseMs;
        } else {
          repairDelayMs = pendingRetryMs !== null
            ? pendingRetryMs
            : Math.min(repairMaxMs, repairDelayMs * 2);
          scheduleRepair(!requestMatches(request, serial));
        }
      }
    }

    function clearRepair() {
      if (repairTimer !== null) clock.clearTimeout(repairTimer);
      repairTimer = null;
      repairNeeded = false;
      repairRequiresFullSnapshot = false;
      repairDelayMs = repairBaseMs;
    }

    function select(rangeSeconds, resolution) {
      const nextSelection = normalizeSelection(capabilities, rangeSeconds, resolution);
      if (nextSelection.range_seconds === selection.range_seconds && nextSelection.resolution === selection.resolution) {
        return currentSelection();
      }
      selection = nextSelection;
      selectionSerial += 1;
      activeGeneration = null;
      presentationAnchorMs = null;
      presentationAnchorEnd = null;
      presentationWindowEnd = null;
      zoomWindowStart = null;
      zoomWindowEnd = null;
      zoomedStatic = false;
      activeDeltaRevision = 0;
      clearRepair();
      if (tickTimer !== null) clock.clearTimeout(tickTimer);
      tickTimer = null;
      scheduleTick();
      scheduleRepair(true);
      return currentSelection();
    }

    function currentSelection() {
      return Object.freeze({
        range_seconds: selection.range_seconds,
        resolution: selection.resolution,
        resolution_seconds: concreteResolution(),
      });
    }

    function setVisible(value) {
      const wasVisible = visible;
      visible = value === true;
      resetSchedulers();
      if (!wasVisible && visible) scheduleRepair(true);
    }

    function setZoomedStatic(value) {
      const wasZoomed = zoomedStatic;
      zoomedStatic = value === true;
      if (zoomedStatic && !wasZoomed) {
        const frame = presentation();
        zoomWindowStart = frame?.window_start ?? null;
        zoomWindowEnd = frame?.window_end ?? null;
      }
      if (!zoomedStatic) {
        zoomWindowStart = null;
        zoomWindowEnd = null;
      }
      if (wasZoomed && !zoomedStatic && activeGeneration) anchorPresentation(activeGeneration.window_end);
      resetSchedulers();
      if (!zoomedStatic && repairNeeded) scheduleRepair(true);
    }

    function setZoomWindow(start, end) {
      if (!activeGeneration || !Number.isFinite(start) || !Number.isFinite(end) || end <= start) {
        throw new Error('zoom window is invalid');
      }
      const liveFrame = presentation();
      const lower = activeGeneration.window_start;
      const upper = Math.max(activeGeneration.window_end, liveFrame?.window_end ?? activeGeneration.window_end);
      zoomWindowStart = Math.max(lower, start);
      zoomWindowEnd = Math.min(upper, end);
      if (zoomWindowEnd - zoomWindowStart < concreteResolution()) {
        throw new Error('zoom window is smaller than one exact bucket');
      }
      zoomedStatic = true;
      resetSchedulers();
      onViewport(presentation());
      return presentation();
    }

    function clearZoom() {
      setZoomedStatic(false);
      onViewport(presentation());
      return presentation();
    }

    function resetSchedulers() {
      if (repairTimer !== null) clock.clearTimeout(repairTimer);
      repairTimer = null;
      resetTickTimer();
      if (repairNeeded) scheduleRepair();
    }

    function resetTickTimer() {
      if (tickTimer !== null) clock.clearTimeout(tickTimer);
      tickTimer = null;
      scheduleTick();
    }

    return Object.freeze({
      acceptDelta,
      acceptSnapshot,
      axis: axisContract,
      buildDeltaRequest,
      buildRequest,
      capabilities: () => capabilities,
      generation: () => activeGeneration,
      deltaRequest: buildDeltaRequest,
      handleReconnect() {
        repairRequiresFullSnapshot = true;
        scheduleRepair(true);
      },
      presentation,
      projectSeries,
      select,
      selection: currentSelection,
      setZoomWindow,
      clearZoom,
      setVisible,
      setZoomedStatic,
      start() {
        if (running) return;
        running = true;
        scheduleTick();
        if (!activeGeneration) scheduleRepair(true);
      },
      stop() {
        running = false;
        resetTickTimer();
        clearRepair();
      },
    });
  }

  function createBrowserClient(options = {}) {
    if (!options || typeof options !== 'object' || Array.isArray(options)) {
      throw new Error('browser client options must be an object');
    }
    const fetchImpl = options.fetch || globalThis.fetch?.bind(globalThis);
    const EventSourceImpl = options.EventSource || globalThis.EventSource;
    if (typeof fetchImpl !== 'function') throw new Error('browser fetch is unavailable');
    if (typeof EventSourceImpl !== 'function') throw new Error('browser EventSource is unavailable');

    const controllerOptions = options.controllerOptions || {};
    const onState = options.onState || (() => {});
    const userOnGeneration = controllerOptions.onGeneration || (() => {});
    const userOnRepairNeeded = controllerOptions.onRepairNeeded || (() => {});
    const userOnRepairComplete = controllerOptions.onRepairComplete || (() => {});
    const clientId = String(options.clientId ?? controllerOptions.clientId ?? '').trim();
    let savedRange = options.savedRange ?? controllerOptions.savedRange;
    let savedResolution = options.savedResolution ?? controllerOptions.savedResolution;
    let capabilitiesPromise = null;
    let startPromise = null;
    let controller = null;
    let source = null;
    let streamEpoch = 0;
    let running = false;
    let visible = true;
    let readFenceRecovery = null;

    async function recoverReadFence(error) {
      if (error?.recoverableReadFence !== true) return;
      if (!readFenceRecovery) {
        readFenceRecovery = fetchJson(fetchImpl, '/api/stats-retry', false, {method: 'POST'})
          .catch(recoveryError => {
            // Preserve the original fence details for the visible state while
            // the controller's single bounded repair timer retries the read.
            error.recoveryError = recoveryError;
          })
          .finally(() => { readFenceRecovery = null; });
      }
      await readFenceRecovery;
    }

    function fetchCapabilities() {
      if (!capabilitiesPromise) {
        onState('loading');
        capabilitiesPromise = fetchJson(fetchImpl, '/api/stats-capabilities').catch(error => {
          onState(error.pending === true ? 'pending' : 'error', error);
          throw error;
        });
      }
      return capabilitiesPromise;
    }

    async function fetchSnapshot(request) {
      let value;
      try {
        value = await fetchJson(fetchImpl, exactUrl('/api/stats-snapshot', [
          ['range_seconds', request.range_seconds],
          ['resolution', request.resolution],
          ['client_id', request.client_id],
          ['since_generation', request.since_generation],
        ]), true);
      } catch (error) {
        if (error?.recoverableReadFence === true) await recoverReadFence(error);
        onState(error.pending === true ? 'pending' : 'error', error);
        throw error;
      }
      if (value === SNAPSHOT_NOT_MODIFIED && !controller?.generation()) {
        throw new Error('snapshot cannot be not-modified before an initial generation');
      }
      return value;
    }

    function closeStream() {
      streamEpoch += 1;
      const closing = source;
      source = null;
      if (closing) closing.close();
    }

    function routeStreamFailure(candidate, epoch) {
      if (source !== candidate || streamEpoch !== epoch) return;
      closeStream();
      controller.handleReconnect();
    }

    function openStream() {
      if (!running || !visible || source || !controller?.generation()) return;
      const request = controller.deltaRequest();
      const url = exactUrl('/api/stats-stream', [
        ['range_seconds', request.range_seconds],
        ['resolution_seconds', request.resolution_seconds],
        ['client_id', request.client_id],
        ['after_cache_generation', request.after_cache_generation],
        ['after_revision', request.after_revision],
      ]);
      const epoch = streamEpoch + 1;
      const candidate = new EventSourceImpl(url, {withCredentials: true});
      streamEpoch = epoch;
      source = candidate;
      candidate.addEventListener('delta', event => {
        if (source !== candidate || streamEpoch !== epoch) return;
        try {
          controller.acceptDelta(JSON.parse(event.data));
        } catch (_error) {
          routeStreamFailure(candidate, epoch);
        }
      });
      for (const eventName of ['repair', 'unavailable', 'error']) {
        candidate.addEventListener(eventName, () => routeStreamFailure(candidate, epoch));
      }
    }

    async function activate() {
      const capabilities = await fetchCapabilities();
      if (!controller) {
        controller = createController({
          ...controllerOptions,
          capabilities,
          clientId,
          savedRange,
          savedResolution,
          fetchSnapshot,
          onGeneration(generation) {
            onState('ready');
            userOnGeneration(generation);
            openStream();
          },
          onRepairNeeded() {
            closeStream();
            userOnRepairNeeded();
          },
          onRepairComplete(generation) {
            onState('ready');
            userOnRepairComplete(generation);
            openStream();
          },
        });
      }
      if (running) {
        controller.setVisible(visible);
        controller.start();
        openStream();
      }
      return controller;
    }

    function start() {
      if (running) return startPromise || Promise.resolve(controller);
      running = true;
      if (!startPromise) {
        startPromise = activate().catch(error => {
          running = false;
          throw error;
        }).finally(() => {
          startPromise = null;
        });
      }
      return startPromise;
    }

    return Object.freeze({
      controller: () => controller,
      select(rangeSeconds, resolution) {
        savedRange = rangeSeconds;
        savedResolution = resolution;
        if (!controller) return null;
        const result = controller.select(rangeSeconds, resolution);
        if (!controller.generation()) closeStream();
        if (!running) controller.stop();
        return result;
      },
      setVisible(value) {
        const nextVisible = value === true;
        if (visible === nextVisible) return;
        visible = nextVisible;
        if (!visible) closeStream();
        if (controller) {
          controller.setVisible(visible);
          if (!running) controller.stop();
        }
      },
      async retry() {
        onState('loading');
        await fetchJson(fetchImpl, '/api/stats-retry', false, {method: 'POST'}).catch(error => {
          onState('error', error);
          throw error;
        });
        closeStream();
        capabilitiesPromise = null;
        if (!controller) {
          running = true;
          return activate();
        }
        running = true;
        controller.setVisible(visible);
        controller.start();
        controller.handleReconnect();
        return controller;
      },
      start,
      stop() {
        running = false;
        closeStream();
        if (controller) controller.stop();
      },
    });
  }

  function mount(element, options = {}) {
    if (!element || typeof element.querySelector !== 'function' || typeof element.addEventListener !== 'function') {
      throw new Error('mount requires a DOM element');
    }
    const view = options.view || 'stats';
    if (!['stats', 'cost'].includes(view)) throw new Error('view must be stats or cost');
    const suppliedControllerOptions = options.controllerOptions || {};
    const suppliedOnGeneration = suppliedControllerOptions.onGeneration || (() => {});
    const suppliedOnViewport = suppliedControllerOptions.onViewport || (() => {});
    let client = null;
    let destroyed = false;
    const renderer = createCurrentRenderer(element, {
      view,
      onSelect(rangeSeconds, resolution) {
        if (!client || destroyed) return;
        const selection = client.select(rangeSeconds, resolution);
        if (selection) renderer.configure(client.controller().capabilities(), selection);
      },
      onZoom(start, end) {
        if (!client || destroyed) return;
        client.controller()?.setZoomWindow(start, end);
      },
      onResetZoom() {
        if (!client || destroyed) return;
        client.controller()?.clearZoom();
      },
    });
    client = createBrowserClient({
      fetch: options.fetch,
      EventSource: options.EventSource,
      clientId: options.clientId || currentStatsClientId(),
      savedRange: options.savedRange,
      savedResolution: options.savedResolution,
      onState(state) {
        renderer.setStatus(state);
      },
      controllerOptions: {
        ...suppliedControllerOptions,
        onGeneration(generation) {
          suppliedOnGeneration(generation);
          renderer.render(generation, client.controller().presentation());
        },
        onViewport(frame) {
          suppliedOnViewport(frame);
          const generation = client.controller().generation();
          if (generation) renderer.render(generation, frame);
        },
      },
    });

    const api = Object.freeze({
      async start() {
        if (destroyed) throw new Error('mounted stats view is destroyed');
        try {
          const controller = await client.start();
          renderer.configure(controller.capabilities(), controller.selection());
          if (controller.generation()) renderer.render(controller.generation(), controller.presentation());
          return api;
        } catch (error) {
          renderer.setStatus('error');
          throw error;
        }
      },
      stop() {
        if (!destroyed) client.stop();
      },
      setVisible(value) {
        if (!destroyed) client.setVisible(value === true);
      },
      destroy() {
        if (destroyed) return;
        destroyed = true;
        client.stop();
        renderer.destroy();
      },
    });
    return api;
  }

  function createCurrentRenderer(element, options) {
    const view = options.view;
    const rendererId = nextRendererId++;
    let capabilities = null;
    let selection = null;
    let latestGeneration = null;
    let latestFrame = null;
    let zoomed = false;
    let pointerDrag = null;
    let pinnedTooltip = null;
    let controlSignature = '';
    let statusSignature = '';
    let costModalOpen = false;
    let costModalSignature = '';
    const visibilityDefinitions = view === 'stats' ? currentStatsVisibilityDefinitions() : [];
    const visibleGroups = new Set(visibilityDefinitions.flatMap(definition => (
      definition.defaultVisible === false ? [] : definition.groups
    )));
    const ownerDocument = element.ownerDocument || globalThis.document || null;
    element.innerHTML = [
      `<section class="yo-stats-current yo-stats-current--${currentStatsEscape(view)}" data-stats-current-view="${currentStatsEscape(view)}">`,
      '<div class="yo-stats-current-controls" data-stats-current-controls></div>',
      '<div class="yo-stats-current-status" data-stats-current-status aria-live="polite"></div>',
      '<div class="yo-stats-current-content" data-stats-current-content></div>',
      '<div data-stats-current-modal-root></div>',
      '</section>',
    ].join('');
    const controlsElement = element.querySelector('[data-stats-current-controls]');
    const statusElement = element.querySelector('[data-stats-current-status]');
    const contentElement = element.querySelector('[data-stats-current-content]');
    const modalElement = element.querySelector('[data-stats-current-modal-root]');
    if (!controlsElement || !statusElement || !contentElement || !modalElement) {
      throw new Error('mount element could not create the current stats shell');
    }

    function paintControls() {
      if (!capabilities || !selection) return;
      const capability = capabilities.ranges.find(row => row.range_seconds === selection.range_seconds);
      if (!capability) throw new Error('renderer selection is absent from capabilities');
      const signature = JSON.stringify([
        selection.range_seconds, selection.resolution, capability.auto_resolution_seconds,
        capability.explicit_resolution_seconds, zoomed, [...visibleGroups].sort(compareUnicode),
      ]);
      if (signature === controlSignature) return;
      controlSignature = signature;
      const rangeOptions = capabilities.ranges.map(row => (
        `<option value="${row.range_seconds}"${row.range_seconds === selection.range_seconds ? ' selected' : ''}>${currentStatsEscape(currentStatsDuration(row.range_seconds))}</option>`
      )).join('');
      const resolutionOptions = [
        `<option value="AUTO"${selection.resolution === 'AUTO' ? ' selected' : ''}>AUTO (${capability.auto_resolution_seconds}s)</option>`,
        ...capability.explicit_resolution_seconds.map(resolution => (
          `<option value="${resolution}"${selection.resolution === resolution ? ' selected' : ''}>${resolution}s</option>`
        )),
      ].join('');
      controlsElement.innerHTML = [
        '<label class="yo-stats-current-control">Range ',
        `<select data-stats-current-range aria-label="Range">${rangeOptions}</select></label>`,
        '<label class="yo-stats-current-control">Resolution ',
        `<select data-stats-current-resolution aria-label="Resolution">${resolutionOptions}</select></label>`,
        `<span class="yo-stats-current-exact">Exact ${selection.resolution_seconds}s</span>`,
        `<button type="button" data-stats-current-zoom-reset${zoomed ? '' : ' hidden'}>Reset zoom</button>`,
        visibilityDefinitions.length ? `<div class="yo-stats-current-visibility" role="group" aria-label="Charts">${visibilityDefinitions.map(definition => {
          const pressed = definition.groups.every(group => visibleGroups.has(group));
          return `<button type="button" data-stats-current-visibility="${currentStatsEscape(definition.id)}" aria-pressed="${pressed}">${currentStatsEscape(definition.label)}</button>`;
        }).join('')}</div>` : '',
      ].join('');
    }

    function configure(nextCapabilities, nextSelection) {
      capabilities = nextCapabilities;
      selection = nextSelection;
      paintControls();
    }

    function setStatus(state, clearContent = false) {
      const normalized = ['loading', 'pending', 'error', 'ready'].includes(state) ? state : 'error';
      const message = {
        loading: 'Loading stats…',
        pending: 'Stats are still being prepared. Retrying when the server asks.',
        error: 'Could not load stats.',
        ready: '',
      }[normalized];
      const signature = `${normalized}\n${message}`;
      if (signature !== statusSignature) {
        statusSignature = signature;
        statusElement.innerHTML = message
          ? `<div class="yo-stats-current-state yo-stats-current-state--${normalized}" data-stats-current-state="${normalized}" role="status">${currentStatsEscape(message)}</div>`
          : '';
      }
      if (clearContent) {
        contentElement.innerHTML = '';
      }
    }

    function render(nextGeneration, frame = nextGeneration) {
      latestGeneration = nextGeneration;
      latestFrame = frame || nextGeneration;
      if (capabilities) {
        const requested = nextGeneration.requested_resolution;
        configure(capabilities, Object.freeze({
          range_seconds: nextGeneration.range_seconds,
          resolution: requested,
          resolution_seconds: nextGeneration.resolution_seconds,
        }));
      }
      contentElement.innerHTML = currentStatsSnapshotHtml(
        nextGeneration,
        frame || nextGeneration,
        view,
        rendererId,
        view === 'stats' ? visibleGroups : null,
      );
      if (view === 'cost' && costModalOpen) paintCostModal();
      setStatus('ready');
    }

    function paintCostModal(force = false) {
      if (view !== 'cost' || !latestGeneration || !costModalOpen) return;
      const signature = JSON.stringify([
        latestGeneration.cache_generation,
        latestGeneration.cost_report,
        latestGeneration.window_start,
        latestGeneration.window_end,
      ]);
      if (!force && signature === costModalSignature) return;
      const scrollTop = modalElement.querySelector?.('[data-stats-current-cost-modal-scroll]')?.scrollTop || 0;
      modalElement.innerHTML = currentCostModalHtml(latestGeneration.cost_report, latestGeneration, rendererId);
      costModalElementScroll(modalElement, scrollTop);
      costModalSignature = signature;
    }

    function openCostModal() {
      if (view !== 'cost' || !latestGeneration) return;
      costModalOpen = true;
      costModalSignature = '';
      paintCostModal(true);
      modalElement.querySelector?.('[data-stats-current-cost-modal-close]')?.focus?.();
    }

    function closeCostModal() {
      costModalOpen = false;
      costModalSignature = '';
      modalElement.innerHTML = '';
    }

    function chartPointerContext(event, svg = null) {
      const targetSvg = svg || event?.target?.closest?.('[data-stats-current-svg]');
      const chart = targetSvg?.closest?.('[data-stats-chart]');
      const bounds = targetSvg?.getBoundingClientRect?.();
      if (!targetSvg || !chart || !bounds || !(bounds.width > 0)) return null;
      const clientX = Number(event?.clientX);
      if (!Number.isFinite(clientX)) return null;
      const x = (clientX - bounds.left) / bounds.width * CURRENT_STATS_SVG_WIDTH;
      return {svg: targetSvg, chart, bounds, x};
    }

    function hideTooltip(tooltip = null) {
      const target = tooltip || pinnedTooltip;
      if (target) target.hidden = true;
      if (!tooltip || tooltip === pinnedTooltip) pinnedTooltip = null;
    }

    function nearestSeriesPoint(chart, x) {
      let nearest = null;
      for (const point of chart.querySelectorAll?.('[data-series-point]') || []) {
        const pointX = Number(point.getAttribute?.('cx'));
        if (!Number.isFinite(pointX)) continue;
        const distance = Math.abs(pointX - x);
        if (!nearest || distance < nearest.distance) nearest = {point, distance, x: pointX};
      }
      return nearest;
    }

    function showTooltip(context, pin = false) {
      const nearest = nearestSeriesPoint(context.chart, context.x);
      const tooltip = context.chart.querySelector?.('[data-stats-current-tooltip]');
      if (!nearest || !tooltip) return;
      const point = nearest.point;
      const name = String(point.dataset?.seriesPoint || '');
      const start = Number(point.dataset?.pointStart);
      const duration = Number(point.dataset?.pointDuration);
      const value = Number(point.dataset?.pointValue);
      const sourceCount = Number(point.dataset?.pointSourceCount);
      const firstTimestamp = Number(point.dataset?.pointFirstTimestamp);
      const lastTimestamp = Number(point.dataset?.pointLastTimestamp);
      const showSeconds = latestGeneration?.resolution_seconds === 1;
      const bucketEnd = start + duration;
      const parts = [
        currentStatsSeriesLabel(name),
        currentStatsMetric(value, context.chart.dataset?.statsChart || ''),
        `${currentStatsTime(start, showSeconds)}–${currentStatsTime(bucketEnd, showSeconds)}`,
      ];
      if (Number.isSafeInteger(sourceCount) && sourceCount >= 0) {
        parts.push(`${sourceCount} sample${sourceCount === 1 ? '' : 's'}`);
      }
      if (Number.isFinite(firstTimestamp) && Number.isFinite(lastTimestamp)) {
        const sourceTime = firstTimestamp === lastTimestamp
          ? currentStatsTime(firstTimestamp, true)
          : `${currentStatsTime(firstTimestamp, true)}–${currentStatsTime(lastTimestamp, true)}`;
        parts.push(`source ${sourceTime}`);
      }
      tooltip.textContent = parts.join(' · ');
      tooltip.style.left = `${Math.max(0, Math.min(100, nearest.x / CURRENT_STATS_SVG_WIDTH * 100))}%`;
      tooltip.hidden = false;
      if (pin) pinnedTooltip = tooltip;
    }

    function clearPointerDrag(event = null) {
      if (!pointerDrag) return;
      const selectionRect = pointerDrag.svg.querySelector?.('[data-stats-current-selection]');
      if (selectionRect) selectionRect.hidden = true;
      try { pointerDrag.svg.releasePointerCapture?.(pointerDrag.pointerId); } catch (_error) { /* capture is optional */ }
      pointerDrag = null;
      if (event?.pointerType === 'touch') event.preventDefault?.();
    }

    function onPointerDown(event) {
      if (event?.button !== undefined && event.button !== 0) return;
      const context = chartPointerContext(event);
      if (!context) return;
      hideTooltip();
      pointerDrag = {
        ...context,
        pointerId: event.pointerId,
        pointerType: event.pointerType,
        startClientX: Number(event.clientX),
        startX: context.x,
        currentX: context.x,
      };
      context.svg.setPointerCapture?.(event.pointerId);
      if (event.pointerType === 'touch') event.preventDefault?.();
    }

    function onPointerMove(event) {
      if (pointerDrag && event.pointerId === pointerDrag.pointerId) {
        const context = chartPointerContext(event, pointerDrag.svg);
        if (!context) return;
        pointerDrag.currentX = context.x;
        const start = Math.max(CURRENT_STATS_PLOT_LEFT, Math.min(CURRENT_STATS_SVG_WIDTH - CURRENT_STATS_PLOT_RIGHT, pointerDrag.startX));
        const end = Math.max(CURRENT_STATS_PLOT_LEFT, Math.min(CURRENT_STATS_SVG_WIDTH - CURRENT_STATS_PLOT_RIGHT, pointerDrag.currentX));
        const selectionRect = pointerDrag.svg.querySelector?.('[data-stats-current-selection]');
        if (selectionRect) {
          selectionRect.setAttribute('x', String(Math.min(start, end)));
          selectionRect.setAttribute('width', String(Math.abs(end - start)));
          selectionRect.hidden = false;
        }
        if (event.pointerType === 'touch') event.preventDefault?.();
        return;
      }
      if (event?.pointerType === 'mouse' && !pinnedTooltip) {
        const context = chartPointerContext(event);
        if (context) showTooltip(context);
      }
    }

    function onPointerUp(event) {
      if (!pointerDrag || event.pointerId !== pointerDrag.pointerId) return;
      const drag = pointerDrag;
      const context = chartPointerContext(event, drag.svg) || drag;
      const moved = Math.abs(Number(event.clientX) - drag.startClientX);
      if (moved >= CURRENT_STATS_ZOOM_THRESHOLD_PX && latestFrame) {
        const plotWidth = CURRENT_STATS_SVG_WIDTH - CURRENT_STATS_PLOT_LEFT - CURRENT_STATS_PLOT_RIGHT;
        const fraction = value => (
          Math.max(0, Math.min(1, (value - CURRENT_STATS_PLOT_LEFT) / plotWidth))
        );
        const first = Math.min(drag.startX, context.x);
        const last = Math.max(drag.startX, context.x);
        const start = latestFrame.window_start + fraction(first) * latestFrame.range_seconds;
        const end = latestFrame.window_start + fraction(last) * latestFrame.range_seconds;
        zoomed = true;
        controlSignature = '';
        paintControls();
        options.onZoom(start, end);
      } else if (event.pointerType === 'touch') {
        showTooltip(context, true);
      }
      clearPointerDrag(event);
    }

    function onPointerLeave(event) {
      if (!pointerDrag && event?.pointerType === 'mouse' && !pinnedTooltip) {
        hideTooltip(event.target?.closest?.('[data-stats-chart]')?.querySelector?.('[data-stats-current-tooltip]'));
      }
    }

    function onClick(event) {
      const target = event?.target;
      if (typeof openExternalLinkFromEvent === 'function' && openExternalLinkFromEvent(event, element)) return;
      const visibilityButton = target?.closest?.('[data-stats-current-visibility]');
      if (visibilityButton && view === 'stats') {
        const definition = visibilityDefinitions.find(item => item.id === visibilityButton.dataset.statsCurrentVisibility);
        if (!definition) return;
        const hide = definition.groups.every(group => visibleGroups.has(group));
        for (const group of definition.groups) {
          if (hide) visibleGroups.delete(group);
          else visibleGroups.add(group);
        }
        controlSignature = '';
        paintControls();
        if (latestGeneration) render(latestGeneration, latestFrame);
        return;
      }
      if (target?.closest?.('[data-stats-current-cost-more]')) {
        openCostModal();
        return;
      }
      if (
        target?.closest?.('[data-stats-current-cost-modal-close]')
        || target?.matches?.('[data-stats-current-cost-modal-backdrop]')
      ) {
        closeCostModal();
        return;
      }
      if (target?.closest?.('[data-stats-current-zoom-reset]')) {
        zoomed = false;
        controlSignature = '';
        paintControls();
        options.onResetZoom();
      }
    }

    function onKeyDown(event) {
      if (costModalOpen && event?.key === 'Escape') closeCostModal();
    }

    function onOutsidePointerDown(event) {
      if (!pinnedTooltip || element.contains?.(event.target)) return;
      hideTooltip();
    }

    function onChange(event) {
      const target = event?.target;
      if (!target || !target.dataset || !capabilities || !selection) return;
      if (Object.prototype.hasOwnProperty.call(target.dataset, 'statsCurrentRange')) {
        const rangeSeconds = Number(target.value);
        const capability = capabilities.ranges.find(row => row.range_seconds === rangeSeconds);
        if (!capability) return;
        selection = Object.freeze({
          range_seconds: rangeSeconds,
          resolution: 'AUTO',
          resolution_seconds: capability.auto_resolution_seconds,
        });
      } else if (Object.prototype.hasOwnProperty.call(target.dataset, 'statsCurrentResolution')) {
        const capability = capabilities.ranges.find(row => row.range_seconds === selection.range_seconds);
        const resolution = target.value === 'AUTO' ? 'AUTO' : Number(target.value);
        if (
          !capability
          || (resolution !== 'AUTO' && !capability.explicit_resolution_seconds.includes(resolution))
        ) return;
        selection = Object.freeze({
          range_seconds: selection.range_seconds,
          resolution,
          resolution_seconds: resolution === 'AUTO' ? capability.auto_resolution_seconds : resolution,
        });
      } else {
        return;
      }
      zoomed = false;
      paintControls();
      setStatus('loading', true);
      options.onSelect(selection.range_seconds, selection.resolution);
    }

    element.addEventListener('change', onChange);
    element.addEventListener('click', onClick);
    element.addEventListener('pointerdown', onPointerDown);
    element.addEventListener('pointermove', onPointerMove);
    element.addEventListener('pointerup', onPointerUp);
    element.addEventListener('pointercancel', clearPointerDrag);
    element.addEventListener('pointerleave', onPointerLeave);
    ownerDocument?.addEventListener?.('pointerdown', onOutsidePointerDown);
    ownerDocument?.addEventListener?.('keydown', onKeyDown);
    setStatus('loading');
    return Object.freeze({
      configure,
      destroy() {
        element.removeEventListener('change', onChange);
        element.removeEventListener('click', onClick);
        element.removeEventListener('pointerdown', onPointerDown);
        element.removeEventListener('pointermove', onPointerMove);
        element.removeEventListener('pointerup', onPointerUp);
        element.removeEventListener('pointercancel', clearPointerDrag);
        element.removeEventListener('pointerleave', onPointerLeave);
        ownerDocument?.removeEventListener?.('pointerdown', onOutsidePointerDown);
        ownerDocument?.removeEventListener?.('keydown', onKeyDown);
        element.innerHTML = '';
      },
      render,
      setStatus,
    });
  }

  function currentStatsSnapshotHtml(generation, frame, view, rendererId, visibleGroups = null) {
    const groups = currentStatsGroups(generation, view);
    const groupDefinitions = currentStatsGroupDefinitions(view);
    const tokenMaximum = currentStatsMaximum([
      groups.get('agent-tokens'), groups.get('model-output-tokens'),
    ]);
    const rendered = groupDefinitions.flatMap(definition => {
      if (visibleGroups && !visibleGroups.has(definition.id)) return [];
      const series = groups.get(definition.id) || new Map();
      const gaps = generation.no_data.filter(span => definition.families.includes(span.family));
      if (!series.size && !gaps.length) return [];
      const maximum = definition.sharedTokenScale
        ? tokenMaximum
        : currentStatsMaximum([series]);
      return [currentStatsChartHtml({
        definition, series, gaps, generation, frame, maximum, rendererId,
      })];
    });
    const charts = rendered.length
      ? `<div class="yo-stats-current-grid${view === 'cost' ? ' yo-stats-current-grid--compact' : ''}" data-stats-current-grid>${rendered.join('')}</div>`
      : `<div class="yo-stats-current-empty" data-stats-current-empty>${view === 'cost' ? 'No cost or usage data recorded.' : 'No stats data recorded.'}</div>`;
    return view === 'cost'
      ? `${currentCostSummaryHtml(generation.cost_report, generation)}${charts}`
      : charts;
  }

  function currentCostPriceHtml(marginalMicroUsd, apiListMicroUsd, {strong = false} = {}) {
    const marginal = currentStatsEscape(currentStatsMoney(marginalMicroUsd));
    const apiList = currentStatsEscape(currentStatsMoney(apiListMicroUsd));
    const amount = value => strong ? `<strong>${value}</strong>` : value;
    if (marginalMicroUsd === apiListMicroUsd) return amount(apiList);
    return `${amount(marginal)} marginal · ${amount(apiList)} list`;
  }

  function currentCostSummaryHtml(report, generation) {
    const breakdown = CURRENT_COST_DIMENSIONS.map(dimension => (
      `${currentCostDimensionLabel(dimension)}=${currentStatsMetric(report.dimensions[dimension].tokens, 'usage')}`
    )).join(', ');
    return [
      '<section class="yo-cost-current-summary" data-stats-current-cost-summary>',
      `<div class="yo-cost-current-summary-title"><strong>Cost Summary</strong><span>At API list prices · ${currentStatsEscape(currentStatsDateRange(generation))}</span></div>`,
      '<div class="yo-cost-current-summary-line">',
      `<span>Total: ${currentCostPriceHtml(report.total_micro_usd, report.total_api_list_micro_usd, {strong: true})}</span>`,
      `<span>Total tokens: <strong>${currentStatsEscape(currentStatsMetric(report.total_tokens, 'usage'))} tokens</strong> (${currentStatsEscape(breakdown)})</span>`,
      '<button type="button" class="preferences-inline-action" data-stats-current-cost-more>More Info</button>',
      '</div></section>',
    ].join('');
  }

  function currentCostModalHtml(report, generation, rendererId) {
    const titleId = `yo-cost-current-modal-title-${rendererId}`;
    const dimensions = CURRENT_COST_DIMENSIONS.map(dimension => {
      const value = report.dimensions[dimension];
      return `<tr><th scope="row">${currentStatsEscape(currentCostDimensionLabel(dimension))}</th><td>${currentStatsEscape(currentStatsMetric(value.tokens, 'usage'))}</td><td>${currentCostPriceHtml(value.micro_usd, value.api_list_micro_usd)}</td></tr>`;
    }).join('');
    const evidence = report.evidence.map(row => {
      const source = row.source_url
        ? `<a href="${currentStatsEscape(row.source_url)}" target="_blank" rel="noopener noreferrer">Pricing source</a>`
        : '<span>No public source link</span>';
      return [
        '<li class="yo-cost-current-evidence-row">',
        `<strong><span aria-hidden="true">✦</span> ${currentStatsEscape(row.provider)} · ${currentStatsEscape(row.model)}</strong>`,
        `<span>${currentStatsEscape(currentCostDimensionLabel(row.dimension))} · ${currentStatsEscape(currentStatsMetric(row.tokens, 'usage'))} tokens · ${currentCostPriceHtml(row.micro_usd, row.api_list_micro_usd)}</span>`,
        `<span>${currentStatsEscape(row.direction)} / ${currentStatsEscape(row.modality)} / ${currentStatsEscape(row.cache_role)} · ${currentStatsEscape(row.rate_usd)} per ${currentStatsEscape(String(row.rate_scale))} ${currentStatsEscape(row.unit)}</span>`,
        `<span>Profile ${currentStatsEscape(row.pricing_profile)} · tier ${currentStatsEscape(row.service_tier)} · effective ${currentStatsEscape(row.effective_from)} · ${source}</span>`,
        '</li>',
      ].join('');
    }).join('');
    const omissionTotal = CURRENT_COST_OMISSION_FIELDS.reduce((total, field) => total + report.omissions[field], 0);
    return [
      '<div class="app-modal-overlay yo-cost-current-modal-backdrop" data-stats-current-cost-modal-backdrop>',
      `<section class="yo-cost-current-modal" role="dialog" aria-modal="true" aria-labelledby="${titleId}">`,
      '<header class="yo-cost-current-modal-header">',
      `<h2 id="${titleId}">Cost summary details · At API list prices · ${currentStatsEscape(currentStatsDateRange(generation))}</h2>`,
      '<button type="button" class="yo-cost-current-modal-close" data-stats-current-cost-modal-close aria-label="Close cost details" title="Close">×</button>',
      '</header>',
      '<div class="yo-cost-current-modal-scroll" data-stats-current-cost-modal-scroll>',
      `<p class="yo-cost-current-total">Total: ${currentCostPriceHtml(report.total_micro_usd, report.total_api_list_micro_usd, {strong: true})} · Total tokens: <strong>${currentStatsEscape(currentStatsMetric(report.total_tokens, 'usage'))} tokens</strong> · Priced: ${currentStatsEscape(currentStatsMetric(report.priced.tokens, 'usage'))} tokens / ${report.priced.atoms} atoms · Unpriced: ${currentStatsEscape(currentStatsMetric(report.unpriced.tokens, 'usage'))} tokens / ${report.unpriced.atoms} atoms.</p>`,
      '<details class="yo-cost-current-explainer"><summary>What these columns mean</summary><dl>',
      '<div><dt>Input</dt><dd>Prompt and context tokens sent to the model before provider cache adjustments.</dd></div>',
      '<div><dt>Cache read</dt><dd>Previously stored prompt or KV-cache tokens read again; providers commonly report these separately and often price them below ordinary input.</dd></div>',
      '<div><dt>5m cache write</dt><dd>Input tokens written into a provider cache with a five-minute lifetime.</dd></div>',
      '<div><dt>1h cache write</dt><dd>Input tokens written into a provider cache with a one-hour lifetime.</dd></div>',
      '<div><dt>Output</dt><dd>Tokens generated by the model. Reasoning is not split out because the current source atoms do not distinguish it reliably.</dd></div>',
      '<div><dt>Other</dt><dd>Non-text token usage or request-priced usage that cannot honestly be assigned to the four text dimensions.</dd></div>',
      '<div><dt>Priced / Unpriced</dt><dd>Usage with a matching reviewed catalog rate versus recorded usage whose cost is unknown; unpriced usage remains visible and is never treated as free.</dd></div>',
      '</dl></details>',
      '<section><h3>Token and cost breakdown</h3><div class="yo-cost-current-table-scroll"><table><thead><tr><th>Usage</th><th>Tokens</th><th>Marginal / at API list prices</th></tr></thead><tbody>',
      dimensions,
      '</tbody></table></div></section>',
      currentCostAttributionTable('Cost by Model', report.models, 'model'),
      currentCostAttributionTable('Cost by Agent', report.agents, 'agent'),
      '<section><h3>Pricing attribution</h3>',
      evidence ? `<ol class="yo-cost-current-evidence">${evidence}</ol>` : '<p>No priced catalog evidence in this range.</p>',
      '</section>',
      `<p class="yo-cost-current-catalog">Catalog revision ${report.catalog_revision}. Reasoning breakdown unavailable. ${omissionTotal ? `Bounded report omitted ${report.omissions.models} model, ${report.omissions.agents} agent, and ${report.omissions.evidence} pricing-evidence rows.` : 'No bounded report rows were omitted.'}</p>`,
      '</div></section></div>',
    ].join('');
  }

  function currentCostAttributionTable(title, rows, kind) {
    const body = rows.map(row => {
      const identity = kind === 'model'
        ? `<span class="yo-cost-current-model"><span><span aria-hidden="true">✦</span> ${currentStatsEscape(row.provider)}</span><strong>${currentStatsEscape(row.model)}</strong></span>`
        : `<span class="yo-cost-current-agent" title="${currentStatsEscape(row.label || row.source)}">${currentStatsEscape(currentStatsCanonicalAgentLabel(row.label || row.source))}</span>`;
      const dimensions = CURRENT_COST_DIMENSIONS.map(dimension => (
        `<td>${currentCostDimensionCell(row.dimensions[dimension])}</td>`
      )).join('');
      return `<tr><th scope="row">${identity}</th><td>${currentStatsEscape(currentStatsMetric(row.total_tokens, 'usage'))}<small>${currentCostPriceHtml(row.total_micro_usd, row.total_api_list_micro_usd)}</small></td>${dimensions}<td>${currentStatsEscape(currentStatsMetric(row.priced.tokens, 'usage'))}<small>${row.priced.atoms} atoms</small></td><td>${currentStatsEscape(currentStatsMetric(row.unpriced.tokens, 'usage'))}<small>${row.unpriced.atoms} atoms</small></td></tr>`;
    }).join('');
    return [
      `<section><h3>${currentStatsEscape(title)}</h3>`,
      body
        ? `<div class="yo-cost-current-table-scroll"><table><thead><tr><th>${kind === 'model' ? 'Model' : 'Agent'}</th><th>Total</th>${CURRENT_COST_DIMENSIONS.map(dimension => `<th>${currentStatsEscape(currentCostDimensionLabel(dimension))}</th>`).join('')}<th>Priced</th><th>Unpriced</th></tr></thead><tbody>${body}</tbody></table></div>`
        : '<p>No attributed usage in this range.</p>',
      '</section>',
    ].join('');
  }

  function currentCostDimensionCell(value) {
    return `${currentStatsEscape(currentStatsMetric(value.tokens, 'usage'))}<small>${currentCostPriceHtml(value.micro_usd, value.api_list_micro_usd)}</small>`;
  }

  function currentCostDimensionLabel(dimension) {
    return ({
      input: 'Input', cache_read: 'Cache read', cache_write_5m: '5m cache write', cache_write_1h: '1h cache write', output: 'Output', other: 'Other',
    })[dimension] || dimension;
  }

  function currentStatsMoney(microUsd) {
    const dollars = microUsd / 1_000_000;
    const digits = dollars >= 0.01 ? 2 : 6;
    return `$${dollars.toFixed(digits)}`;
  }

  function currentStatsDateRange(generation) {
    const showSeconds = generation.resolution_seconds === 1;
    return `${currentStatsDateTime(generation.window_start, showSeconds)}–${currentStatsDateTime(generation.window_end, showSeconds)}`;
  }

  function currentStatsDateTime(seconds, showSeconds) {
    const date = new Date(seconds * 1000);
    const dateParts = [date.getFullYear(), date.getMonth() + 1, date.getDate()].map((value, index) => (
      index === 0 ? String(value) : String(value).padStart(2, '0')
    ));
    return `${dateParts.join('-')} ${currentStatsTime(seconds, showSeconds)}`;
  }

  function costModalElementScroll(modalElement, scrollTop) {
    const scroll = modalElement.querySelector?.('[data-stats-current-cost-modal-scroll]');
    if (scroll) scroll.scrollTop = scrollTop;
  }

  function currentStatsGroups(generation, view) {
    const groups = new Map();
    for (const bucket of generation.buckets) {
      for (const [name, item] of Object.entries(bucket.series)) {
        const groupId = currentStatsSeriesGroup(name, view);
        if (!groupId) continue;
        if (!groups.has(groupId)) groups.set(groupId, new Map());
        const series = groups.get(groupId);
        if (!series.has(name)) series.set(name, []);
        series.get(name).push(Object.freeze({
          start: bucket.start,
          duration: bucket.duration,
          value: item.value,
          source_count: item.source_count,
          first_timestamp: item.first_timestamp,
          last_timestamp: item.last_timestamp,
        }));
      }
    }
    for (const series of groups.values()) {
      const ordered = [...series.entries()].sort((left, right) => compareUnicode(left[0], right[0]));
      series.clear();
      for (const [name, points] of ordered) series.set(name, Object.freeze(points));
    }
    return groups;
  }

  function currentStatsSeriesGroup(name, view) {
    if (view === 'cost') {
      if (name === 'cost_micro_usd' || name === 'api_list_cost_micro_usd') return 'cost';
      if (name === 'usage_tokens') return 'usage';
      return null;
    }
    if (name === 'cost_micro_usd' || name === 'api_list_cost_micro_usd') return 'cost';
    if (name === 'usage_tokens') return null;
    if (name.startsWith('agent_tokens_per_minute:')) return 'agent-tokens';
    if (name.startsWith('model_tokens_per_minute:output:')) return 'model-output-tokens';
    if (name.startsWith('model_tokens_per_minute:')) return 'model-usage';
    if (['ask_agents', 'run_agents', 'transition_agents', 'idle_agents', 'ask_sessions', 'run_sessions', 'transition_sessions', 'idle_sessions'].includes(name)) return 'agent-status';
    if (name.startsWith('gpu_')) return 'gpu';
    if (name.startsWith('service_') || name.startsWith('system_memory_')) return 'system';
    if (name.startsWith('browser_')) return 'browser';
    if (name.includes('cpu_percent')) return 'cpu';
    return 'other';
  }

  function currentStatsGroupDefinitions(view) {
    if (view === 'cost') return [
      {id: 'cost', title: 'Marginal / at API list prices', families: ['cost'], compact: true},
      {id: 'usage', title: 'Token usage', families: ['cost'], compact: true},
    ];
    return [
      {id: 'cpu', title: 'CPU', families: ['cpu']},
      {id: 'agent-status', title: 'Agent status', families: ['agent_status']},
      {id: 'gpu', title: 'GPU', families: ['gpu']},
      {id: 'system', title: 'System', families: ['service_load', 'system_memory']},
      {id: 'agent-tokens', title: 'Agent tokens/min', families: ['agent_tokens'], sharedTokenScale: true},
      {id: 'model-output-tokens', title: 'Model output tokens/min', families: ['agent_tokens'], sharedTokenScale: true},
      {id: 'model-usage', title: 'Model usage', families: ['agent_tokens']},
      {id: 'cost', title: 'Marginal / at API list prices', families: ['cost'], compact: true},
      {id: 'browser', title: 'API / SSE', families: ['browser']},
      {id: 'other', title: 'Other', families: []},
    ];
  }

  function currentStatsVisibilityDefinitions() {
    return [
      {id: 'cpu', label: 'CPU', groups: ['cpu']},
      {id: 'agent-status', label: 'Agent status', groups: ['agent-status']},
      {id: 'gpu', label: 'GPU', groups: ['gpu']},
      {id: 'system', label: 'System', groups: ['system']},
      {id: 'agent-tokens', label: 'Agent tokens', groups: ['agent-tokens']},
      {id: 'model-tokens', label: 'Model tokens', groups: ['model-output-tokens', 'model-usage']},
      {id: 'cost', label: 'Cost', groups: ['cost'], defaultVisible: false},
      {id: 'browser', label: 'API/SSE', groups: ['browser']},
      {id: 'other', label: 'Other', groups: ['other']},
    ];
  }

  function currentStatsMaximum(seriesMaps) {
    let maximum = 0;
    for (const series of seriesMaps) {
      if (!series) continue;
      for (const points of series.values()) {
        for (const point of points) maximum = Math.max(maximum, point.value);
      }
    }
    return maximum;
  }

  function currentStatsChartHtml({definition, series, gaps, generation, frame, maximum, rendererId}) {
    const width = CURRENT_STATS_SVG_WIDTH;
    const height = CURRENT_STATS_SVG_HEIGHT;
    const left = CURRENT_STATS_PLOT_LEFT;
    const right = CURRENT_STATS_PLOT_RIGHT;
    const top = CURRENT_STATS_PLOT_TOP;
    const bottom = CURRENT_STATS_PLOT_BOTTOM;
    const plotWidth = width - left - right;
    const plotHeight = height - top - bottom;
    const clipId = `yo-stats-current-clip-${rendererId}-${definition.id}`;
    const paths = [...series.entries()].map(([name, points]) => {
      const rendered = currentStatsPath(points, frame, maximum, left, top, plotWidth, plotHeight);
      const circles = rendered.points.map((point, index) => (
        `<circle cx="${point.x}" cy="${point.y}" r="2" data-series-point="${currentStatsEscape(name)}" data-point-start="${points[index].start}" data-point-duration="${points[index].duration}" data-point-value="${currentStatsNumber(points[index].value)}" data-point-source-count="${points[index].source_count}" data-point-first-timestamp="${points[index].first_timestamp}" data-point-last-timestamp="${points[index].last_timestamp}"></circle>`
      )).join('');
      return [
        `<g class="yo-stats-current-series" data-series="${currentStatsEscape(name)}" data-point-count="${rendered.points.length}">`,
        `<path d="${rendered.path}" fill="none" stroke="currentColor" vector-effect="non-scaling-stroke"></path>${circles}</g>`,
      ].join('');
    }).join('');
    const gapRects = gaps.map(span => {
      const start = Math.max(frame.window_start, span.start);
      const end = Math.min(frame.window_end, span.end);
      if (end <= start) return '';
      const x = left + (start - frame.window_start) / frame.range_seconds * plotWidth;
      const rectWidth = (end - start) / frame.range_seconds * plotWidth;
      return `<rect class="yo-stats-current-no-data" data-no-data-family="${currentStatsEscape(span.family)}" data-no-data-source="${currentStatsEscape(span.source_id)}" x="${currentStatsNumber(x)}" y="${top}" width="${currentStatsNumber(rectWidth)}" height="${plotHeight}" fill="var(--danger, currentColor)" opacity="0.18"></rect>`;
    }).join('');
    const legend = [...series.keys()].map(name => (
      `<li data-series-legend="${currentStatsEscape(name)}">${currentStatsEscape(currentStatsSeriesLabel(name))}</li>`
    )).join('');
    const showSeconds = generation.resolution_seconds === 1;
    return [
      `<article class="yo-stats-current-chart${definition.compact ? ' yo-stats-current-chart--compact' : ''}" data-stats-chart="${definition.id}" data-y-min="0" data-y-max="${currentStatsNumber(maximum)}" data-resolution-seconds="${generation.resolution_seconds}">`,
      `<h3>${currentStatsEscape(definition.title)}</h3>`,
      `<svg viewBox="0 0 ${width} ${height}" role="img" aria-label="${currentStatsEscape(definition.title)}" data-stats-current-svg data-axis-seconds="${showSeconds}">`,
      `<defs><clipPath id="${clipId}"><rect x="${left}" y="${top}" width="${plotWidth}" height="${plotHeight}"></rect></clipPath></defs>`,
      `<g clip-path="url(#${clipId})">${gapRects}${paths}<rect data-stats-current-selection hidden x="${left}" y="${top}" width="0" height="${plotHeight}"></rect></g>`,
      `<text x="${left}" y="${height - 6}" text-anchor="start">${currentStatsEscape(currentStatsTime(frame.window_start, showSeconds))}</text>`,
      `<text x="${width - right}" y="${height - 6}" text-anchor="end">${currentStatsEscape(currentStatsTime(frame.window_end, showSeconds))}</text>`,
      `<text x="${left - 5}" y="${top + plotHeight}" text-anchor="end">0</text>`,
      `<text x="${left - 5}" y="${top + 5}" text-anchor="end">${currentStatsEscape(currentStatsMetric(maximum, definition.id))}</text>`,
      `</svg><div class="yo-stats-current-tooltip" data-stats-current-tooltip role="status" hidden></div><ul class="yo-stats-current-legend">${legend}</ul></article>`,
    ].join('');
  }

  function currentStatsPath(points, frame, maximum, left, top, plotWidth, plotHeight) {
    const rendered = [];
    const commands = [];
    let previousEnd = null;
    const denominator = maximum > 0 ? maximum : 1;
    for (const point of points) {
      const x = left + (point.start + point.duration / 2 - frame.window_start) / frame.range_seconds * plotWidth;
      const y = top + plotHeight - point.value / denominator * plotHeight;
      const command = previousEnd === point.start ? 'L' : 'M';
      const renderedPoint = Object.freeze({x: currentStatsNumber(x), y: currentStatsNumber(y)});
      rendered.push(renderedPoint);
      commands.push(`${command}${renderedPoint.x} ${renderedPoint.y}`);
      previousEnd = point.start + point.duration;
    }
    return Object.freeze({path: commands.join(' '), points: Object.freeze(rendered)});
  }

  function currentStatsDuration(seconds) {
    if (seconds % 3600 === 0) return `${seconds / 3600}h`;
    if (seconds % 60 === 0) return `${seconds / 60}m`;
    return `${seconds}s`;
  }

  function currentStatsTime(seconds, showSeconds) {
    const date = new Date(seconds * 1000);
    const parts = [date.getHours(), date.getMinutes(), ...(showSeconds ? [date.getSeconds()] : [])];
    return parts.map(value => String(value).padStart(2, '0')).join(':');
  }

  function currentStatsMetric(value, groupId) {
    if (groupId === 'cost') return `$${(value / 1_000_000).toFixed(value >= 10_000 ? 2 : 6)}`;
    const absolute = Math.abs(value);
    if (absolute >= 1_000_000_000) return `${(value / 1_000_000_000).toFixed(1).replace(/\.0$/, '')}B`;
    if (absolute >= 1_000_000) return `${(value / 1_000_000).toFixed(1).replace(/\.0$/, '')}M`;
    if (absolute >= 1_000) return `${(value / 1_000).toFixed(1).replace(/\.0$/, '')}K`;
    return currentStatsNumber(value);
  }

  function currentStatsSeriesLabel(name) {
    if (name === 'cost_micro_usd') return 'Marginal';
    if (name === 'api_list_cost_micro_usd') return 'At API list prices';
    if (name.startsWith('agent_tokens_per_minute:')) {
      return currentStatsCanonicalAgentLabel(name.slice('agent_tokens_per_minute:'.length));
    }
    return name.replaceAll('_', ' ').replaceAll(':', ' · ');
  }

  // Usage series retain a stable pane-level key, while the visible identity is the tmux
  // session. Cost rows carry that same safe key from the server, so both surfaces call this
  // one owner instead of independently trimming their agent labels.
  function currentStatsCanonicalAgentLabel(value) {
    const full = String(value || '').trim();
    if (!full) return 'Unknown';
    if (full.startsWith('claude-bg:')) {
      const [, projectValue = '', sessionValue = ''] = full.split(':');
      const projectParts = projectValue.split('-').filter(Boolean);
      const project = projectParts.slice(-2).join('-') || projectValue;
      const session = sessionValue.slice(0, 8);
      return ['claude-bg', project, session].filter(Boolean).join(':');
    }
    const parts = full.split('|');
    if (parts.length >= 2 && parts.length <= 4 && ['claude', 'codex', 'term'].includes(parts.at(-1))) {
      return parts[0] || full;
    }
    return full;
  }

  function currentStatsNumber(value) {
    return String(Number(value.toFixed(3)));
  }

  // This component is shipped/tested as an isolated closure, so keep its strict
  // string-only escaping and URI fallback local instead of depending on app globals.
  function currentStatsEscape(value) {
    return String(value).replace(/[&<>"']/g, character => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
    })[character]);
  }

  function currentStatsClientId() {
    const generated = globalThis.crypto?.randomUUID?.();
    if (generated) return `stats-${generated}`;
    return `stats-${Date.now().toString(36)}-${nextRendererId}`;
  }

  async function fetchJson(fetchImpl, url, allowNotModified = false, requestOptions = {}) {
    const response = await fetchImpl(url, Object.freeze({
      method: requestOptions.method || 'GET',
      credentials: 'same-origin',
      cache: 'no-store',
      headers: Object.freeze({Accept: 'application/json'}),
    }));
    if (allowNotModified && response?.status === 304) return SNAPSHOT_NOT_MODIFIED;
    let failurePayload = null;
    if (response?.status !== 200 && response?.status !== 304 && typeof response?.json === 'function') {
      try {
        failurePayload = await response.json();
      } catch (_error) {
        // A malformed/unreadable error response is an ordinary transport failure.
      }
      const retryAfterSeconds = Number(failurePayload?.retry_after_seconds);
      if (
        failurePayload?.status === 'pending'
        && Number.isSafeInteger(retryAfterSeconds)
        && retryAfterSeconds >= 1
        && retryAfterSeconds <= CURRENT_STATS_PENDING_RETRY_MAX_SECONDS
      ) {
        const error = new Error(`stats request is pending for ${retryAfterSeconds}s`);
        error.status = response.status;
        error.pending = true;
        error.retryAfterMs = retryAfterSeconds * 1000;
        throw error;
      }
    }
    if (!response || response.status !== 200 || typeof response.json !== 'function') {
      const reason = String(failurePayload?.reason || '').trim();
      const error = new Error(reason || `stats request failed with HTTP ${response?.status ?? 'unknown'}`);
      error.status = response?.status ?? 0;
      error.reason = reason;
      error.terminal = failurePayload?.terminal === true;
      error.versionFence = response?.status === 426
        && failurePayload?.status === 'upgrade_required';
      // Read-side upgrade fences can be reclaimed by the stats lifecycle retry. A writer
      // fence is deliberately terminal for the page that sent stale observations, so never
      // use its terminal marker to trigger that read recovery path.
      error.recoverableReadFence = error.versionFence && !error.terminal;
      error.requiredProtocolVersion = Number(failurePayload?.required_protocol_version) || 0;
      error.requiredSchemaGeneration = Number(failurePayload?.required_schema_generation) || 0;
      error.requiredBuild = String(failurePayload?.required_build || '');
      throw error;
    }
    return response.json();
  }

  function exactUrl(path, fields) {
    return `${path}?${fields.map(([name, value]) => (
      `${encodeURIComponent(name)}=${encodeURIComponent(String(value))}`
    )).join('&')}`;
  }

  function normalizeCapabilities(value) {
    exactFields(value, CAPABILITY_FIELDS, 'stats capabilities');
    if (!Array.isArray(value.ranges) || !value.ranges.length) throw new Error('stats capabilities require ranges');
    if (
      !Array.isArray(value.resolution_choices)
      || value.resolution_choices.length !== CURRENT_RESOLUTIONS.length
      || value.resolution_choices.some((choice, index) => choice !== CURRENT_RESOLUTIONS[index])
    ) throw new Error('stats capabilities have an unsupported resolution universe');
    const maxBuckets = positiveInteger(value.max_buckets, 'max_buckets');
    if (maxBuckets > 600) throw new Error('max_buckets cannot exceed 600');
    const minBuckets = positiveInteger(value.min_buckets, 'min_buckets');
    if (minBuckets > maxBuckets) throw new Error('min_buckets cannot exceed max_buckets');
    const maxLiveCadence = positiveInteger(value.max_live_cadence_seconds, 'max_live_cadence_seconds');
    if (maxLiveCadence !== 60) throw new Error('current live cadence cap must be one minute');
    let previousRange = 0;
    const ranges = value.ranges.map(entry => {
      exactFields(entry, CAPABILITY_RANGE_FIELDS, 'stats capability range');
      const rangeSeconds = positiveInteger(entry.range_seconds, 'range_seconds');
      if (rangeSeconds <= previousRange) throw new Error('capability ranges must be unique and increasing');
      previousRange = rangeSeconds;
      const choices = entry.explicit_resolution_seconds;
      if (!Array.isArray(choices) || !choices.length) throw new Error('range requires resolutions');
      const explicit = choices.map(choice => positiveInteger(choice, 'resolution'));
      if (
        explicit.some((choice, index) => !CURRENT_RESOLUTIONS.includes(choice) || (index > 0 && choice <= explicit[index - 1]))
      ) throw new Error('range resolutions must be current, unique, and increasing');
      const auto = positiveInteger(entry.auto_resolution_seconds, 'auto_resolution_seconds');
      if (!explicit.includes(auto)) throw new Error('AUTO resolution must be explicit');
      if (!entry.buckets || typeof entry.buckets !== 'object' || Array.isArray(entry.buckets)) {
        throw new Error('range buckets must be an object');
      }
      const bucketKeys = Object.keys(entry.buckets);
      if (
        bucketKeys.length !== explicit.length
        || explicit.some(resolution => !Object.prototype.hasOwnProperty.call(entry.buckets, resolution))
      ) throw new Error('range bucket counts must match its resolutions');
      for (const resolution of explicit) {
        const count = positiveInteger(entry.buckets[resolution], 'bucket count');
        if (
          rangeSeconds % resolution !== 0
          || count !== rangeSeconds / resolution
          || count < minBuckets
          || count > maxBuckets
        ) throw new Error('range bucket count violates the server capability bounds');
      }
      return Object.freeze({
        range_seconds: rangeSeconds,
        auto_resolution_seconds: auto,
        explicit_resolution_seconds: Object.freeze(explicit),
        buckets: freezeJson({...entry.buckets}),
      });
    });
    return Object.freeze({
      resolution_choices: CURRENT_RESOLUTIONS,
      max_buckets: maxBuckets,
      min_buckets: minBuckets,
      max_live_cadence_seconds: maxLiveCadence,
      ranges: Object.freeze(ranges),
    });
  }

  function normalizeSelection(capabilities, savedRange, savedResolution) {
    const matched = capabilities.ranges.find(entry => entry.range_seconds === Number(savedRange));
    const capability = matched || capabilities.ranges[0];
    const numericResolution = canonicalSavedInteger(savedResolution);
    const resolution = matched && (
      savedResolution === 'AUTO' || capability.explicit_resolution_seconds.includes(numericResolution)
    )
      ? (savedResolution === 'AUTO' ? 'AUTO' : numericResolution)
      : 'AUTO';
    return {range_seconds: capability.range_seconds, resolution, capability};
  }

  function canonicalSavedInteger(value) {
    if (Number.isInteger(value) && value > 0) return value;
    if (typeof value !== 'string' || !/^\d+$/.test(value)) return null;
    const parsed = Number(value);
    return String(parsed) === value && Number.isInteger(parsed) && parsed > 0 ? parsed : null;
  }

  function validateSnapshot(snapshot, selection, concrete, maxBuckets) {
    exactFields(snapshot, SNAPSHOT_FIELDS, 'snapshot');
    if (
      snapshot.protocol_version !== CURRENT_STATS_WIRE_PROTOCOL_VERSION
      || snapshot.range_seconds !== selection.range_seconds
      || snapshot.requested_resolution !== selection.resolution
      || snapshot.resolution_seconds !== concrete
    ) throw new Error('snapshot key does not match the active request');
    generationNumber(snapshot.source_generation, 'source_generation');
    generationNumber(snapshot.cache_generation, 'cache_generation');
    const start = generationNumber(snapshot.window_start, 'window_start');
    const end = generationNumber(snapshot.window_end, 'window_end');
    if (end - start !== selection.range_seconds) throw new Error('snapshot window is not exact');
    if (!Number.isFinite(snapshot.generated_at) || snapshot.generated_at < 0) {
      throw new Error('snapshot generated_at is invalid');
    }
    if (typeof snapshot.rightmost_open !== 'boolean') throw new Error('snapshot open state is invalid');
    validateBuckets(snapshot.buckets, concrete, maxBuckets, true, start, end);
    validateNoData(snapshot.no_data, start, end);
    validateCostReport(snapshot.cost_report);
    const finalOpen = snapshot.buckets.at(-1)?.open === true;
    if (finalOpen !== snapshot.rightmost_open) throw new Error('snapshot open state disagrees');
  }

  function validateBuckets(buckets, duration, maxBuckets, dense, windowStart = 0, windowEnd = null) {
    if (!Array.isArray(buckets) || buckets.length > maxBuckets) throw new Error('too many buckets');
    if (dense && buckets.length === 0) return;
    let expected = windowStart;
    let previous = -1;
    for (const bucket of buckets) {
      exactFields(bucket, BUCKET_FIELDS, 'bucket');
      if (
        bucket.duration !== duration || bucket.start % duration !== 0
        || !bucket.series || typeof bucket.series !== 'object' || Array.isArray(bucket.series)
        || !finiteJson(bucket.series)
        || (dense && bucket.start !== expected)
        || bucket.start <= previous
        || (windowEnd !== null && (bucket.start < windowStart || bucket.start + duration > windowEnd))
      ) throw new Error('bucket duration, alignment, series, or density is invalid');
      for (const [name, series] of Object.entries(bucket.series)) {
        identityText(name, 'series name');
        exactFields(series, SERIES_VALUE_FIELDS, `series ${name}`);
        if (!Number.isFinite(series.value)) throw new Error('series value must be finite');
        positiveInteger(series.source_count, 'series source_count');
        if (
          !Number.isFinite(series.first_timestamp) || series.first_timestamp < 0
          || !Number.isFinite(series.last_timestamp)
          || series.last_timestamp < series.first_timestamp
        ) throw new Error('series timestamps are missing or reversed');
      }
      exactFields(bucket.source, SOURCE_FIELDS, 'bucket.source');
      const count = generationNumber(bucket.source.count, 'bucket.source.count');
      const first = bucket.source.first_timestamp;
      const last = bucket.source.last_timestamp;
      if (
        (count === 0 && (first !== null || last !== null || Object.keys(bucket.series).length !== 0))
        || (count > 0 && (
          !Number.isFinite(first) || first < 0 || !Number.isFinite(last) || last < first
          || Object.keys(bucket.series).length === 0
        ))
      ) throw new Error('bucket source facts do not match its series');
      if (
        typeof bucket.open !== 'boolean'
        || (bucket.open && windowEnd !== null && bucket.start + duration !== windowEnd)
      ) throw new Error('bucket open state is invalid');
      expected = bucket.start + duration;
      previous = bucket.start;
    }
    if (dense && expected !== windowEnd) throw new Error('snapshot buckets do not fill the exact window');
  }

  function validateNoData(spans, windowStart, windowEnd) {
    if (!Array.isArray(spans)) throw new Error('no_data must be an array');
    const sourceEnds = new Map();
    let previous = null;
    for (const span of spans) {
      exactFields(span, NO_DATA_FIELDS, 'no_data span');
      identityText(span.family, 'no_data.family');
      identityText(span.source_id, 'no_data.source_id');
      identityText(span.epoch, 'no_data.epoch');
      identityText(span.reason, 'no_data.reason');
      if (
        !Number.isFinite(span.start) || !Number.isFinite(span.end) || span.end <= span.start
        || span.start < 0
        || !Number.isFinite(span.source_cadence_seconds) || span.source_cadence_seconds <= 0
        || (windowStart !== undefined && span.start < windowStart)
        || (windowEnd !== undefined && span.end > windowEnd)
      ) throw new Error('no_data bounds or cadence are invalid');
      const source = `${span.family}\n${span.source_id}`;
      if ((previous && compareNoData(span, previous) <= 0) || span.start < (sourceEnds.get(source) ?? span.start)) {
        throw new Error('no_data spans overlap or are unordered');
      }
      previous = span;
      sourceEnds.set(source, span.end);
    }
  }

  function validateCostReport(value) {
    exactFields(value, CURRENT_COST_REPORT_FIELDS, 'cost_report');
    if (value.schema_version !== CURRENT_COST_REPORT_SCHEMA_VERSION || value.reasoning_available !== false) {
      throw new Error('cost_report schema or reasoning availability is unsupported');
    }
    const totalTokens = costInteger(value.total_tokens, 'cost_report.total_tokens');
    const totalMicroUsd = costInteger(value.total_micro_usd, 'cost_report.total_micro_usd');
    const totalApiListMicroUsd = costInteger(
      value.total_api_list_micro_usd,
      'cost_report.total_api_list_micro_usd',
    );
    const dimensions = validateCostDimensions(value.dimensions, 'cost_report.dimensions');
    if (
      CURRENT_COST_DIMENSIONS.reduce((sum, dimension) => sum + dimensions[dimension].tokens, 0) !== totalTokens
      || CURRENT_COST_DIMENSIONS.reduce((sum, dimension) => sum + dimensions[dimension].micro_usd, 0) !== totalMicroUsd
      || CURRENT_COST_DIMENSIONS.reduce((sum, dimension) => sum + dimensions[dimension].api_list_micro_usd, 0) !== totalApiListMicroUsd
    ) throw new Error('cost_report totals disagree with dimensions');
    const priced = validateCostCoverage(value.priced, 'cost_report.priced');
    const unpriced = validateCostCoverage(value.unpriced, 'cost_report.unpriced');
    if (priced.tokens + unpriced.tokens !== totalTokens) {
      throw new Error('cost_report priced and unpriced tokens disagree with total');
    }
    validateCostRows(value.models, 'cost_report.models', CURRENT_COST_MODEL_FIELDS, CURRENT_COST_MAX_MODELS, 'model');
    validateCostRows(value.agents, 'cost_report.agents', CURRENT_COST_AGENT_FIELDS, CURRENT_COST_MAX_AGENTS, 'agent');
    const evidence = validateCostEvidence(value.evidence);
    const revision = costInteger(value.catalog_revision, 'cost_report.catalog_revision');
    if (evidence.some(row => row.catalog_revision > revision)) {
      throw new Error('cost_report catalog revision predates evidence');
    }
    exactFields(value.omissions, CURRENT_COST_OMISSION_FIELDS, 'cost_report.omissions');
    for (const field of CURRENT_COST_OMISSION_FIELDS) {
      costInteger(value.omissions[field], `cost_report.omissions.${field}`);
    }
    return value;
  }

  function validateCostDimensions(value, name) {
    exactFields(value, CURRENT_COST_DIMENSIONS, name);
    for (const dimension of CURRENT_COST_DIMENSIONS) {
      exactFields(value[dimension], CURRENT_COST_DIMENSION_FIELDS, `${name}.${dimension}`);
      costInteger(value[dimension].tokens, `${name}.${dimension}.tokens`);
      costInteger(value[dimension].micro_usd, `${name}.${dimension}.micro_usd`);
      costInteger(value[dimension].api_list_micro_usd, `${name}.${dimension}.api_list_micro_usd`);
    }
    return value;
  }

  function validateCostCoverage(value, name) {
    exactFields(value, CURRENT_COST_COVERAGE_FIELDS, name);
    costInteger(value.atoms, `${name}.atoms`);
    costInteger(value.tokens, `${name}.tokens`);
    return value;
  }

  function validateCostRows(value, name, fields, maximum, kind) {
    if (!Array.isArray(value) || value.length > maximum) throw new Error(`${name} is not bounded`);
    const keys = new Set();
    let previous = null;
    for (const [index, row] of value.entries()) {
      const rowName = `${name}[${index}]`;
      exactFields(row, fields, rowName);
      validateCostKey(row.key, `${rowName}.key`);
      if (keys.has(row.key)) throw new Error(`${name} keys are not unique`);
      keys.add(row.key);
      if (kind === 'model') {
        identityText(row.provider, `${rowName}.provider`);
        identityText(row.model, `${rowName}.model`);
      } else {
        identityText(row.source, `${rowName}.source`);
        identityText(row.label, `${rowName}.label`);
      }
      const totalTokens = costInteger(row.total_tokens, `${rowName}.total_tokens`);
      const totalMicroUsd = costInteger(row.total_micro_usd, `${rowName}.total_micro_usd`);
      const totalApiListMicroUsd = costInteger(
        row.total_api_list_micro_usd,
        `${rowName}.total_api_list_micro_usd`,
      );
      const dimensions = validateCostDimensions(row.dimensions, `${rowName}.dimensions`);
      if (
        CURRENT_COST_DIMENSIONS.reduce((sum, dimension) => sum + dimensions[dimension].tokens, 0) !== totalTokens
        || CURRENT_COST_DIMENSIONS.reduce((sum, dimension) => sum + dimensions[dimension].micro_usd, 0) !== totalMicroUsd
        || CURRENT_COST_DIMENSIONS.reduce((sum, dimension) => sum + dimensions[dimension].api_list_micro_usd, 0) !== totalApiListMicroUsd
      ) throw new Error(`${rowName} totals disagree with dimensions`);
      const priced = validateCostCoverage(row.priced, `${rowName}.priced`);
      const unpriced = validateCostCoverage(row.unpriced, `${rowName}.unpriced`);
      if (priced.tokens + unpriced.tokens !== totalTokens) throw new Error(`${rowName} coverage disagrees with total`);
      const rank = [-totalTokens, -totalApiListMicroUsd, -totalMicroUsd, row.key];
      if (previous && compareCostRank(rank, previous) <= 0) throw new Error(`${name} rank order is invalid`);
      previous = rank;
    }
    return value;
  }

  function validateCostEvidence(value) {
    if (!Array.isArray(value) || value.length > CURRENT_COST_MAX_EVIDENCE) {
      throw new Error('cost_report.evidence is not bounded');
    }
    const keys = new Set();
    let previous = null;
    for (const [index, row] of value.entries()) {
      const name = `cost_report.evidence[${index}]`;
      exactFields(row, CURRENT_COST_EVIDENCE_FIELDS, name);
      validateCostKey(row.key, `${name}.key`);
      if (keys.has(row.key)) throw new Error('cost_report.evidence keys are not unique');
      keys.add(row.key);
      for (const field of CURRENT_COST_EVIDENCE_FIELDS) {
        if (['key', 'tokens', 'micro_usd', 'api_list_micro_usd', 'priced_atoms', 'rate_scale', 'catalog_revision', 'source_url'].includes(field)) continue;
        identityText(row[field], `${name}.${field}`);
      }
      if (!CURRENT_COST_DIMENSIONS.includes(row.dimension)) throw new Error(`${name}.dimension is unsupported`);
      if (row.source_url !== '') {
        identityText(row.source_url, `${name}.source_url`);
        if (!/^https?:\/\//.test(row.source_url)) throw new Error(`${name}.source_url is unsupported`);
      }
      const tokens = costInteger(row.tokens, `${name}.tokens`);
      const microUsd = costInteger(row.micro_usd, `${name}.micro_usd`);
      const apiListMicroUsd = costInteger(row.api_list_micro_usd, `${name}.api_list_micro_usd`);
      costInteger(row.priced_atoms, `${name}.priced_atoms`);
      costInteger(row.rate_scale, `${name}.rate_scale`);
      costInteger(row.catalog_revision, `${name}.catalog_revision`);
      const rank = [-tokens, -apiListMicroUsd, -microUsd, row.key];
      if (previous && compareCostRank(rank, previous) <= 0) throw new Error('cost_report.evidence rank order is invalid');
      previous = rank;
    }
    return value;
  }

  function validateCostKey(value, name) {
    if (typeof value !== 'string' || !/^[0-9a-f]{24}$/.test(value)) {
      throw new Error(`${name} is invalid`);
    }
  }

  function compareCostRank(left, right) {
    return left[0] - right[0]
      || left[1] - right[1]
      || left[2] - right[2]
      || compareUnicode(left[3], right[3]);
  }

  function costInteger(value, name) {
    return generationNumber(value, name);
  }

  function validateTombstones(tombstones, resolution, maximum) {
    if (!Array.isArray(tombstones) || tombstones.length > maximum) {
      throw new Error('tombstones must be a bounded array');
    }
    const values = new Map();
    let previous = null;
    for (const tombstone of tombstones) {
      let parts;
      if (tombstone?.kind === 'bucket') {
        exactFields(tombstone, BUCKET_TOMBSTONE_FIELDS, 'bucket tombstone');
        const start = generationNumber(tombstone.start, 'bucket tombstone start');
        const duration = positiveInteger(tombstone.duration, 'bucket tombstone duration');
        if (duration !== resolution || start % resolution !== 0) {
          throw new Error('bucket tombstone duration or alignment is invalid');
        }
        parts = ['bucket', start, duration];
      } else if (tombstone?.kind === 'no_data') {
        exactFields(tombstone, NO_DATA_TOMBSTONE_FIELDS, 'no_data tombstone');
        identityText(tombstone.family, 'no_data tombstone family');
        identityText(tombstone.source_id, 'no_data tombstone source_id');
        identityText(tombstone.epoch, 'no_data tombstone epoch');
        if (
          !Number.isFinite(tombstone.start) || tombstone.start < 0
          || !Number.isFinite(tombstone.end) || tombstone.end <= tombstone.start
        ) throw new Error('no_data tombstone bounds are invalid');
        parts = [
          'no_data', tombstone.family, tombstone.source_id, tombstone.epoch,
          tombstone.start, tombstone.end,
        ];
      } else {
        throw new Error('tombstone kind must be bucket or no_data');
      }
      if (previous && compareIdentityParts(parts, previous) <= 0) {
        throw new Error('tombstone identities must be unique and ordered');
      }
      previous = parts;
      values.set(JSON.stringify(parts), tombstone);
    }
    return values;
  }

  function bucketIdentityKey(bucket) {
    return JSON.stringify(['bucket', bucket.start, bucket.duration]);
  }

  function noDataIdentityKey(span) {
    return JSON.stringify([
      'no_data', span.family, span.source_id, span.epoch, span.start, span.end,
    ]);
  }

  function compareIdentityParts(left, right) {
    for (let index = 0; index < left.length; index += 1) {
      if (left[index] === right[index]) continue;
      if (typeof left[index] === 'string') return compareUnicode(left[index], right[index]);
      return left[index] - right[index];
    }
    return 0;
  }

  function compareUnicode(left, right) {
    const leftPoints = [...left].map(value => value.codePointAt(0));
    const rightPoints = [...right].map(value => value.codePointAt(0));
    const common = Math.min(leftPoints.length, rightPoints.length);
    for (let index = 0; index < common; index += 1) {
      if (leftPoints[index] !== rightPoints[index]) return leftPoints[index] - rightPoints[index];
    }
    return leftPoints.length - rightPoints.length;
  }

  function identityText(value, name) {
    if (typeof value !== 'string' || !value || /[\x00-\x1f\x7f]/.test(value) || utf8ByteLength(value) > 256) {
      throw new Error(`${name} must be bounded non-empty text without controls`);
    }
    return value;
  }

  function utf8ByteLength(value) {
    try {
      return encodeURIComponent(value).replace(/%[0-9A-F]{2}|./g, 'x').length;
    } catch (_error) {
      return Infinity;
    }
  }

  function compareNoData(left, right) {
    return compareUnicode(left.family, right.family)
      || compareUnicode(left.source_id, right.source_id)
      || left.start - right.start
      || left.end - right.end;
  }

  function exactFields(value, expected, name) {
    if (!value || typeof value !== 'object' || Array.isArray(value)) {
      throw new Error(`${name} must be an object`);
    }
    const actual = Object.keys(value).sort();
    const wanted = [...expected].sort();
    if (actual.length !== wanted.length || actual.some((field, index) => field !== wanted[index])) {
      throw new Error(`${name} fields are not exact`);
    }
  }

  function finiteJson(value) {
    if (value === null || typeof value === 'string' || typeof value === 'boolean') return true;
    if (typeof value === 'number') return Number.isFinite(value);
    if (Array.isArray(value)) return value.every(finiteJson);
    return Boolean(value) && typeof value === 'object'
      && Object.keys(value).every(key => typeof key === 'string')
      && Object.values(value).every(finiteJson);
  }

  function sameJson(left, right) {
    if (Object.is(left, right)) return true;
    if (Array.isArray(left) || Array.isArray(right)) {
      return Array.isArray(left) && Array.isArray(right)
        && left.length === right.length
        && left.every((value, index) => sameJson(value, right[index]));
    }
    if (!left || !right || typeof left !== 'object' || typeof right !== 'object') return false;
    const leftKeys = Object.keys(left).sort();
    const rightKeys = Object.keys(right).sort();
    return leftKeys.length === rightKeys.length
      && leftKeys.every((key, index) => key === rightKeys[index] && sameJson(left[key], right[key]));
  }

  function generationNumber(value, name) {
    if (!Number.isSafeInteger(value) || value < 0) throw new Error(`${name} must be a non-negative safe integer`);
    return value;
  }

  function positiveInteger(value, name) {
    if (!Number.isSafeInteger(value) || value <= 0) throw new Error(`${name} must be a positive safe integer`);
    return value;
  }

  function freezeJson(value) {
    if (!value || typeof value !== 'object' || Object.isFrozen(value)) return value;
    Object.values(value).forEach(freezeJson);
    return Object.freeze(value);
  }

  globalThis.YOLOmuxStatsCurrent = Object.freeze({
    canonicalAgentLabel: currentStatsCanonicalAgentLabel,
    createBrowserClient,
    createController,
    mount,
    resolutionChoices: CURRENT_RESOLUTIONS,
  });
})();
