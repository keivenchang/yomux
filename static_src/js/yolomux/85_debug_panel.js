// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
// Debug stats projection, rendering, panels, and graph interaction.

// `stats_sample` arrives on the shared client-events EventSource.  Its record
// is the durable one-second owner delta, so the visible graph advances without
// waiting for the 30-second history-backfill poll.  Polling remains the
// range/zoom and reconnect fallback, not the live-tail transport.
function applyJsDebugStatsSamplePush(payload = {}) {
  // Protocol-v2 snapshots/deltas are the sole exact-mode owner. The legacy
  // shared stats_sample event carries a different bucket dialect and must not
  // race the exact stream or repopulate a just-switched Resolution.
  if (jsDebugGraphExactResolutionEnabled) return false;
  if (!payload || typeof payload !== 'object') return false;
  const sample = payload.sample && typeof payload.sample === 'object' ? payload.sample : {};
  const record = payload.record && typeof payload.record === 'object' ? payload.record : null;
  if (!record) return false;
  const sequence = Number(payload.sequence);
  const cursor = Number.isFinite(sequence) ? sequence : Number(record.sequence || 0);
  recordJsDebugStatsSample({
    ...sample,
    history: {sequence: cursor, latest_sequence: cursor, records: [record]},
  }, {advanceHistoryCursor: true});
  return true;
}

function clearJsDebugGraphData() {
  jsDebugGraphBuckets.clear();
  jsDebugGraphPendingServerBuckets.clear();
  // Invalidate any in-flight silent prefetch so its late response cannot repopulate
  // the cache we just cleared (kept the reload-idempotency of the rendered history).
  jsDebugHistoryPrefetchState.generation += 1;
}

function debugGraphBucketForServerRecord(record) {
  if (!record || typeof record !== 'object') return null;
  const startSeconds = Number(record.start);
  const durationSeconds = Number(record.duration);
  if (!Number.isFinite(startSeconds) || !Number.isFinite(durationSeconds) || durationSeconds <= 0) return null;
  const durationMs = Math.max(jsDebugGraphRawBucketMs, durationSeconds * 1000);
  const startMs = Math.floor(startSeconds * 1000);
  return debugGraphBucket(jsDebugGraphBuckets, startMs, durationMs);
}

function debugGraphApplyServerRecord(record) {
  const bucket = debugGraphBucketForServerRecord(record);
  if (!bucket) return;
  bucket.apiCount = Math.max(bucket.apiCount, Number(record.api_count || 0));
  bucket.sseCount = Math.max(bucket.sseCount, Number(record.sse_count || 0));
  bucket.latencyTotalMs = Math.max(bucket.latencyTotalMs, Number(record.latency_total_ms || 0));
  bucket.latencyCount = Math.max(bucket.latencyCount, Number(record.latency_count || 0));
  bucket.bandwidthBytes = Math.max(bucket.bandwidthBytes, Number(record.bandwidth_bytes || 0));
  bucket.heartbeatCount = Math.max(bucket.heartbeatCount, Number(record.heartbeat_count || 0));
  bucket.disconnectedMs = Math.max(bucket.disconnectedMs, Number(record.disconnected_ms || 0));
  debugGraphApplyServerClients(bucket, record.clients);
  debugGraphApplyServerProcesses(bucket, record.servers);
  bucket.cpuTotalPercent = Math.max(bucket.cpuTotalPercent, Number(record.cpu_total_percent || 0));
  bucket.cpuCount = Math.max(bucket.cpuCount, Number(record.cpu_count || 0));
  bucket.systemCpuTotalPercent = Math.max(bucket.systemCpuTotalPercent, Number(record.system_cpu_total_percent || 0));
  bucket.systemCpuCount = Math.max(bucket.systemCpuCount, Number(record.system_cpu_count || 0));
  debugGraphApplyHostMetrics(bucket, record.host_metrics);
  debugGraphApplyServerAgentStatus(bucket, record);
  bucket.tokensPerAgentTotal = Math.max(bucket.tokensPerAgentTotal, Number(record.tokens_per_agent_total || 0));
  bucket.agentTokenSamples = Math.max(bucket.agentTokenSamples, Number(record.agent_token_samples || 0));
  debugGraphApplyServerAgentTokenRates(bucket, record.agent_token_rates);
  debugGraphApplyServerCostSummary(bucket, record.cost_summary);
}

// Cost projection stays attached to the existing stats bucket. The pricing owner supplies
// integer micro-USD amounts, so this view never introduces a float-based cost cache or a
// second time-range selection path.
function debugGraphCostInteger(value) {
  const number = Number(value);
  return Number.isSafeInteger(number) && number >= 0 ? number : 0;
}

function debugGraphCostOptionalInteger(value) {
  return value === null || value === undefined ? null : debugGraphCostInteger(value);
}

function debugGraphCostRows(value) {
  return Array.isArray(value) ? value.filter(item => item && typeof item === 'object') : [];
}

function debugGraphApplyServerCostSummary(bucket, source) {
  if (!source || typeof source !== 'object' || Array.isArray(source)) return;
  bucket.costSummary = {
    totalMicroUsd: debugGraphCostInteger(source.total_micro_usd),
    apiListMicroUsd: debugGraphCostApiListMicroUsd(source),
    totalTokenQuantity: Math.max(0, Number(source.total_token_quantity) || 0),
    dimensionTotals: source.dimension_totals && typeof source.dimension_totals === 'object' && !Array.isArray(source.dimension_totals) ? {...source.dimension_totals} : null,
    rangeReport: source.range_report === true,
    knownMicroUsd: debugGraphCostInteger(source.known_micro_usd),
    lowerMicroUsd: debugGraphCostInteger(source.lower_micro_usd ?? source.known_micro_usd),
    upperMicroUsd: debugGraphCostInteger(source.upper_micro_usd ?? source.total_micro_usd ?? source.known_micro_usd),
    pricedCount: debugGraphCostInteger(source.priced_count),
    complete: source.complete === true,
    unpricedCount: debugGraphCostInteger(source.unpriced_count),
    unpricedTokenQuantity: Math.max(0, Number(source.unpriced_token_quantity) || 0),
    components: debugGraphCostRows(source.components),
    models: debugGraphCostRows(source.models),
    sources: debugGraphCostRows(source.sources),
    tmuxWindows: debugGraphCostRows(source.tmux_windows),
    catalogRevision: String(source.catalog_revision || '').slice(0, 160),
    activeCatalogRevision: String(source.active_catalog_revision || '').slice(0, 160),
    freshness: String(source.freshness || '').slice(0, 80),
  };
}

function debugGraphApplyHostMetricProcesses(target, source, valueKey = 'totalPercent') {
  if (!source || typeof source !== 'object' || Array.isArray(source)) return;
  for (const [key, record] of Object.entries(source)) {
    if (!record || typeof record !== 'object') continue;
    const total = Number(valueKey === 'totalBytes' ? record.total_bytes : record.total_percent || 0);
    const samples = Number(record.samples || 0);
    if (!Number.isFinite(total) || !Number.isFinite(samples) || samples <= 0) continue;
    const item = target.get(key) || {label: String(record.label || key), [valueKey]: 0, samples: 0};
    item.label = String(record.label || item.label || key);
    item[valueKey] = Math.max(item[valueKey], Math.max(0, total));
    item.samples = Math.max(item.samples, Math.max(0, samples));
    target.set(key, item);
  }
}

function debugGraphApplyHostMetrics(bucket, source) {
  if (!source || typeof source !== 'object' || Array.isArray(source)) return;
  const target = bucket.hostMetrics || (bucket.hostMetrics = debugGraphNewHostMetrics());
  target.systemMemoryUsedTotalBytes = Math.max(target.systemMemoryUsedTotalBytes, Number(source.system_memory_used_total_bytes || 0));
  target.systemMemoryCapacityTotalBytes = Math.max(target.systemMemoryCapacityTotalBytes, Number(source.system_memory_capacity_total_bytes || 0));
  target.systemMemoryCount = Math.max(target.systemMemoryCount, Number(source.system_memory_count || 0));
  const macMemoryFields = {
    macPhysicalMemoryTotalBytes: 'mac_physical_memory_total_bytes', macMemoryUsedTotalBytes: 'mac_memory_used_total_bytes',
    macCachedFilesTotalBytes: 'mac_cached_files_total_bytes', macSwapUsedTotalBytes: 'mac_swap_used_total_bytes',
    macAppMemoryTotalBytes: 'mac_app_memory_total_bytes', macWiredMemoryTotalBytes: 'mac_wired_memory_total_bytes',
    macCompressedMemoryTotalBytes: 'mac_compressed_memory_total_bytes', macMemoryPressureTotalPercent: 'mac_pressure_total_percent',
    macMemoryPressureLevel: 'mac_pressure_level',
  };
  let macMemoryDetailSeen = false;
  for (const [targetKey, sourceKey] of Object.entries(macMemoryFields)) {
    const value = Number(source[sourceKey]);
    if (!Number.isFinite(value)) continue;
    target[targetKey] = Number.isFinite(Number(target[targetKey])) ? Math.max(target[targetKey], value) : value;
    macMemoryDetailSeen = true;
  }
  if (macMemoryDetailSeen) target.macMemoryDetailCount = Math.max(target.macMemoryDetailCount, Number(source.mac_memory_count || 1));
  if (source.cpu_label) target.cpuLabel = String(source.cpu_label);
  if (source.system_memory_label) target.systemMemoryLabel = String(source.system_memory_label);
  debugGraphApplyHostMetricProcesses(target.cpuProcesses, source.cpu_processes);
  debugGraphApplyHostMetricProcesses(target.memoryProcesses, source.memory_processes, 'totalBytes');
  debugGraphApplyHostMetricProcesses(target.gpuUtilProcesses, source.gpu_util_processes);
  debugGraphApplyHostMetricProcesses(target.gpuMemoryProcesses, source.gpu_memory_processes, 'totalBytes');
  if (source.service_load && typeof source.service_load === 'object' && !Array.isArray(source.service_load)) {
    for (const [key, record] of Object.entries(source.service_load)) {
      if (!record || typeof record !== 'object') continue;
      const item = target.serviceLoad.get(key) || debugGraphNewServiceLoadItem(record.label || key);
      item.label = String(record.label || item.label || key);
      for (const prefix of ['cpu', 'rss']) {
        const unit = prefix === 'cpu' ? 'Percent' : 'Bytes';
        const sourceUnit = prefix === 'cpu' ? 'percent' : 'bytes';
        const samplesKey = `${prefix}Samples`;
        const sourceSamples = Math.max(0, Number(record[`${prefix}_samples`] || 0));
        if (sourceSamples < Number(item[samplesKey] || 0)) continue;
        item[`${prefix}Total${unit}`] = Math.max(0, Number(record[`${prefix}_total_${sourceUnit}`] || 0));
        item[samplesKey] = sourceSamples;
        item[`${prefix}Min${unit}`] = Math.max(0, Number(record[`${prefix}_min_${sourceUnit}`] || 0));
        item[`${prefix}Max${unit}`] = Math.max(0, Number(record[`${prefix}_max_${sourceUnit}`] || 0));
        if (prefix === 'cpu') {
          item.cpuRangeAvailable = record.cpu_min_percent !== null && record.cpu_min_percent !== undefined
            && record.cpu_max_percent !== null && record.cpu_max_percent !== undefined
            && Number.isFinite(Number(record.cpu_min_percent)) && Number.isFinite(Number(record.cpu_max_percent));
        }
      }
      target.serviceLoad.set(key, item);
    }
  }
  if (!source.gpu_devices || typeof source.gpu_devices !== 'object' || Array.isArray(source.gpu_devices)) return;
  for (const [key, record] of Object.entries(source.gpu_devices)) {
    if (!record || typeof record !== 'object') continue;
    const samples = Number(record.samples || 0);
    if (!Number.isFinite(samples) || samples <= 0) continue;
    const item = target.gpuDevices.get(key) || {label: String(record.label || key), utilTotalPercent: 0, memoryUsedTotalBytes: 0, memoryCapacityTotalBytes: 0, samples: 0};
    item.label = String(record.label || item.label || key);
    item.utilTotalPercent = Math.max(item.utilTotalPercent, Math.max(0, Number(record.util_total_percent || 0)));
    item.memoryUsedTotalBytes = Math.max(item.memoryUsedTotalBytes, Math.max(0, Number(record.memory_used_total_bytes || 0)));
    item.memoryCapacityTotalBytes = Math.max(item.memoryCapacityTotalBytes, Math.max(0, Number(record.memory_capacity_total_bytes || 0)));
    item.samples = Math.max(item.samples, Math.max(0, samples));
    target.gpuDevices.set(key, item);
  }
}

function debugGraphAgentStatusSnapshot(record) {
  const askAgentTotal = Number(record?.ask_agent_total);
  const runAgentTotal = Number(record?.run_agent_total);
  const transitionAgentTotal = Number(record?.transition_agent_total);
  const idleAgentTotal = Number(record?.idle_agent_total);
  const hasSplitAgentTotals = [askAgentTotal, runAgentTotal, transitionAgentTotal, idleAgentTotal].some(Number.isFinite);
  if (!hasSplitAgentTotals && !Number.isFinite(Number(record?.active_agent_total)) && !Number.isFinite(Number(record?.inactive_agent_total))) return null;
  const ask = hasSplitAgentTotals ? Math.max(0, askAgentTotal || 0) : 0;
  const run = hasSplitAgentTotals ? Math.max(0, runAgentTotal || 0) : Math.max(0, Number(record.active_agent_total || 0));
  const idle = hasSplitAgentTotals && Number.isFinite(idleAgentTotal)
    ? Math.max(0, idleAgentTotal)
    : Math.max(0, Number(record.inactive_agent_total || 0));
  const transition = hasSplitAgentTotals
    ? Math.max(0, (transitionAgentTotal || 0) - (Number.isFinite(idleAgentTotal) ? 0 : idle))
    : 0;
  return {
    askAgentTotal: ask,
    runAgentTotal: run,
    transitionAgentTotal: transition,
    idleAgentTotal: idle,
    activeAgentTotal: ask + run + transition,
    inactiveAgentTotal: idle,
    agentActivitySamples: Math.max(0, Number(record.agent_activity_samples || 0)),
  };
}

function debugGraphApplyServerAgentStatus(bucket, record) {
  const snapshot = debugGraphAgentStatusSnapshot(record);
  if (!snapshot) return;
  const sequence = Number(record.sequence);
  if (Number.isFinite(sequence)) {
    if (sequence < Number(bucket.agentStatusSequence ?? -1)) return;
    bucket.agentStatusSequence = sequence;
  } else if (snapshot.agentActivitySamples < Number(bucket.agentActivitySamples || 0)) {
    return;
  }
  Object.assign(bucket, snapshot);
}

function debugGraphApplyServerProcesses(bucket, servers) {
  if (!servers || typeof servers !== 'object' || Array.isArray(servers)) return;
  if (!(bucket.servers instanceof Map)) bucket.servers = new Map();
  for (const [processId, record] of Object.entries(servers)) {
    const cleanProcessId = String(processId || '').trim();
    if (!cleanProcessId || !record || typeof record !== 'object') continue;
    const process = bucket.servers.get(cleanProcessId) || {label: cleanProcessId, cpuTotalPercent: 0, cpuCount: 0};
    process.label = String(record.label || process.label || cleanProcessId);
    process.cpuTotalPercent = Math.max(process.cpuTotalPercent, Number(record.cpu_total_percent || 0));
    process.cpuCount = Math.max(process.cpuCount, Number(record.cpu_count || 0));
    bucket.servers.set(cleanProcessId, process);
  }
}

function debugGraphApplyServerClients(bucket, clients) {
  if (!clients || typeof clients !== 'object' || Array.isArray(clients)) return;
  if (!(bucket.clients instanceof Map)) bucket.clients = new Map();
  for (const [clientId, record] of Object.entries(clients)) {
    const cleanClientId = String(clientId || '').trim();
    if (!cleanClientId || !record || typeof record !== 'object') continue;
    const client = bucket.clients.get(cleanClientId) || debugGraphNewClientBucket();
    client.apiCount = Math.max(client.apiCount, Number(record.api_count || 0));
    client.sseCount = Math.max(client.sseCount, Number(record.sse_count || 0));
    client.latencyTotalMs = Math.max(client.latencyTotalMs, Number(record.latency_total_ms || 0));
    client.latencyCount = Math.max(client.latencyCount, Number(record.latency_count || 0));
    client.bandwidthBytes = Math.max(client.bandwidthBytes, Number(record.bandwidth_bytes || 0));
    client.heartbeatCount = Math.max(client.heartbeatCount, Number(record.heartbeat_count || 0));
    client.disconnectedMs = Math.max(client.disconnectedMs, Number(record.disconnected_ms || 0));
    bucket.clients.set(cleanClientId, client);
  }
}

function debugGraphApplyServerAgentTokenRates(bucket, rates) {
  const items = Array.isArray(rates) ? rates : [];
  if (!items.length) return;
  if (!(bucket.agentTokenRates instanceof Map)) bucket.agentTokenRates = new Map();
  for (const item of items) {
    if (!item || typeof item !== 'object') continue;
    const key = String(item.key || '').trim();
    if (!key) continue;
    const total = Number(item.total ?? item.rate ?? item.value);
    const samples = Number(item.samples || 0);
    const tokens = Number(item.tokens || 0);
    const seconds = Number(item.seconds || 0);
    if (!Number.isFinite(total) && !Number.isFinite(samples) && !Number.isFinite(tokens)) continue;
    const label = String(item.label || key).trim() || key;
    const existing = bucket.agentTokenRates.get(key) || {label, total: 0, samples: 0, tokens: 0, seconds: 0, modelRates: new Map()};
    existing.label = label;
    if (Number.isFinite(total)) existing.total = Math.max(Number(existing.total || 0), Math.max(0, total));
    if (Number.isFinite(samples)) existing.samples = Math.max(Number(existing.samples || 0), Math.max(0, samples));
    if (Number.isFinite(tokens)) existing.tokens = Math.max(Number(existing.tokens || 0), Math.max(0, tokens));
    if (Number.isFinite(seconds)) existing.seconds = Math.max(Number(existing.seconds || 0), Math.max(0, seconds));
    const billable = item.billable_tokens && typeof item.billable_tokens === 'object' ? item.billable_tokens : {};
    const billableSamples = item.billable_samples && typeof item.billable_samples === 'object' ? item.billable_samples : {};
    existing.billableAvailable = existing.billableAvailable === true || item.billable_available === true;
    if (!existing.billableTokens || typeof existing.billableTokens !== 'object') {
      existing.billableTokens = {input: 0, cacheRead: 0, cacheWrite: 0, all: 0};
    }
    if (!existing.billableSamples || typeof existing.billableSamples !== 'object') {
      existing.billableSamples = {input: 0, cacheRead: 0, cacheWrite: 0, all: 0};
    }
    existing.billableTokens.input = Math.max(Number(existing.billableTokens.input || 0), Math.max(0, Number(billable.input) || 0));
    existing.billableTokens.cacheRead = Math.max(Number(existing.billableTokens.cacheRead || 0), Math.max(0, Number(billable.cache_read) || 0));
    existing.billableTokens.cacheWrite = Math.max(Number(existing.billableTokens.cacheWrite || 0), Math.max(0, Number(billable.cache_write) || 0));
    existing.billableTokens.all = Math.max(Number(existing.billableTokens.all || 0), Math.max(0, Number(billable.all) || 0));
    existing.billableSamples.input = Math.max(Number(existing.billableSamples.input || 0), Math.max(0, Number(billableSamples.input) || 0));
    existing.billableSamples.cacheRead = Math.max(Number(existing.billableSamples.cacheRead || 0), Math.max(0, Number(billableSamples.cache_read) || 0));
    existing.billableSamples.cacheWrite = Math.max(Number(existing.billableSamples.cacheWrite || 0), Math.max(0, Number(billableSamples.cache_write) || 0));
    existing.billableSamples.all = Math.max(Number(existing.billableSamples.all || 0), Math.max(0, Number(billableSamples.all) || 0));
    if (!(existing.modelRates instanceof Map)) existing.modelRates = new Map();
    const modelRates = item.model_rates && typeof item.model_rates === 'object' && !Array.isArray(item.model_rates)
      ? Object.entries(item.model_rates)
      : [];
    for (const [rawModel, rawRate] of modelRates) {
      if (!rawRate || typeof rawRate !== 'object') continue;
      const model = String(rawModel || 'unknown').trim() || 'unknown';
      const current = existing.modelRates.get(model) || {total: 0, samples: 0, tokens: 0, seconds: 0};
      const modelTotal = Number(rawRate.total ?? rawRate.rate ?? rawRate.value);
      const modelSamples = Number(rawRate.samples || 0);
      const modelTokens = Number(rawRate.tokens || 0);
      const modelSeconds = Number(rawRate.seconds || 0);
      if (Number.isFinite(modelTotal)) current.total = Math.max(Number(current.total || 0), Math.max(0, modelTotal));
      if (Number.isFinite(modelSamples)) current.samples = Math.max(Number(current.samples || 0), Math.max(0, modelSamples));
      if (Number.isFinite(modelTokens)) current.tokens = Math.max(Number(current.tokens || 0), Math.max(0, modelTokens));
      if (Number.isFinite(modelSeconds)) current.seconds = Math.max(Number(current.seconds || 0), Math.max(0, modelSeconds));
      existing.modelRates.set(model, current);
    }
    bucket.agentTokenRates.set(key, existing);
}
}

function debugGraphRemoveCoarserServerBuckets(startSeconds, endSeconds, resolutionSeconds) {
  const startMs = Number(startSeconds) * 1000;
  const endMs = Number(endSeconds) * 1000;
  const resolutionMs = Number(resolutionSeconds) * 1000;
  if (!Number.isFinite(startMs) || !Number.isFinite(endMs) || endMs <= startMs || !Number.isFinite(resolutionMs) || resolutionMs <= 0) return 0;
  let removed = 0;
  for (const [key, bucket] of jsDebugGraphBuckets.entries()) {
    const bucketStart = Number(bucket?.startMs);
    const bucketDuration = Math.max(jsDebugGraphRawBucketMs, Number(bucket?.durationMs) || jsDebugGraphRawBucketMs);
    const bucketEnd = bucketStart + bucketDuration;
    // Remove EVERY coarser bucket that intersects the authoritative finer interval, not
    // only those fully contained. A coarse boundary bucket that merely straddles the
    // interval edge otherwise survives, claims an aggregate prefix around a real no-data
    // gap, and forces the whole view back to its coarse duration. Its portion outside the
    // domain is re-provided by the shared wide-range/prefetch cache; a straddling partial
    // aggregate must never be retained inside a finer-covered domain.
    if (bucketDuration <= resolutionMs || bucketEnd <= startMs || bucketStart >= endMs) continue;
    jsDebugGraphBuckets.delete(key);
    removed += 1;
  }
  return removed;
}

function debugGraphApplyServerHistory(history = {}, {advanceLiveCursor = true, replaceCoverage = null} = {}) {
  if (!history || typeof history !== 'object') return;
  if (replaceCoverage) {
    const replacements = Array.isArray(replaceCoverage) ? replaceCoverage : [replaceCoverage];
    for (const interval of replacements) {
      debugGraphRemoveCoarserServerBuckets(
        interval.start ?? interval.start_seconds ?? interval.covered_start,
        interval.end ?? interval.end_seconds ?? interval.covered_end,
        interval.resolution_seconds ?? interval.resolution,
      );
    }
  }
  // Compact local fine buckets before applying an authoritative server coarse bucket. Applying
  // first would merge the same measurements a second time at the 1h/2h tier boundaries.
  compactJsDebugGraphBuckets();
  const sequence = Number(history.latest_sequence ?? history.sequence);
  if (advanceLiveCursor && Number.isFinite(sequence)) jsDebugStatsServerSequence = Math.max(0, sequence);
  debugGraphApplyUsageAtomBackfill(history.usage_atom_backfill);
  const records = Array.isArray(history.records) ? history.records : [];
  records.forEach(debugGraphApplyServerRecord);
  compactJsDebugGraphBuckets();
}

function debugGraphApplyUsageAtomBackfill(backfill) {
  if (!backfill || typeof backfill !== 'object' || Array.isArray(backfill)) {
    jsDebugUsageAtomBackfill.state = 'unknown';
    jsDebugUsageAtomBackfill.sources = 0;
    jsDebugUsageAtomBackfill.missing = 0;
    return;
  }
  const state = String(backfill.state || '').toLowerCase();
  jsDebugUsageAtomBackfill.state = ['pending', 'running', 'partial', 'complete'].includes(state) ? state : 'unknown';
  jsDebugUsageAtomBackfill.sources = Math.max(0, Number(backfill.sources) || 0);
  jsDebugUsageAtomBackfill.missing = Math.max(0, Number(backfill.missing) || 0);
}

// The compact token side-stream is gone (ONE history stream since 2026-07):
// token detail rides every history record and lands in the same unified bucket
// cache. This per-range value survives ONLY as the token charts' display
// floor, so wide-range token bars keep their pre-unification widths
// (>=120s at 4h+, >=300s at 16h+); it is not a second fetch resolution.
function debugGraphAgentTokenResolution(nowMs = Date.now()) {
  const rangeSeconds = debugGraphDomain(nowMs).rangeSeconds;
  if (rangeSeconds < 4 * 60 * 60) return 0;
  return rangeSeconds >= 16 * 60 * 60 ? 5 * 60 : 2 * 60;
}

function debugGraphAggregateBucket(map, source, scaleMs, multiplier = 1) {
  const durationMs = Math.max(jsDebugGraphRawBucketMs, Number(scaleMs) || jsDebugGraphRawBucketMs);
  const startMs = Math.floor(source.startMs / durationMs) * durationMs;
  const bucket = debugGraphBucket(map, startMs, durationMs);
  debugGraphMergeBucket(bucket, source, multiplier);
}

function debugGraphBucketInRange(bucket, cutoffMs, nowMs) {
  const startMs = Number(bucket.startMs);
  if (!Number.isFinite(startMs)) return false;
  const durationMs = Math.max(jsDebugGraphRawBucketMs, Number(bucket.durationMs) || jsDebugGraphRawBucketMs);
  return startMs + durationMs > cutoffMs && startMs <= nowMs;
}

function debugGraphAvailableRangeOptions(nowMs = Date.now()) {
  compactJsDebugGraphBuckets(nowMs);
  return jsDebugGraphRangeOptions;
}

function debugGraphMinimumDisplayResolutionMs(domain, nowMs = Date.now()) {
  const domainStartMs = Number(domain?.startMs);
  return Math.max(
    Number.isFinite(domainStartMs) ? debugGraphBucketDurationForTime(domainStartMs, nowMs) : jsDebugGraphRawBucketMs,
    ...debugGraphContributingSourceSlices(domain).map(slice => slice.sourceDurationMs),
  );
}

function debugGraphExactResolutionChoices(rangeSeconds) {
  const range = Math.max(1, Number(rangeSeconds) || 0);
  return jsDebugGraphResolutionChoices.filter(resolution => {
    const bucketCount = range / resolution;
    return Number.isInteger(bucketCount)
      && bucketCount >= 12
      && bucketCount <= jsDebugGraphOverridePointCap
      && !(range === 3600 && resolution === 10);
  });
}

function debugGraphAvailableResolutionChoices(domain = debugGraphDomain(), nowMs = Date.now()) {
  const rangeSeconds = Math.max(1, Number(domain?.rangeSeconds) || 0);
  if (jsDebugGraphExactResolutionEnabled && !debugGraphZoomDomainValid()) {
    return debugGraphExactResolutionChoices(rangeSeconds);
  }
  const domainStartMs = Number(domain?.startMs);
  const retainedSeconds = Number.isFinite(domainStartMs)
    ? debugGraphBucketDurationForTime(domainStartMs, nowMs) / 1000
    : jsDebugGraphRawBucketMs / 1000;
  // A 30-minute-or-longer chart should not offer sub-10-second overrides:
  // they create hundreds or thousands of mostly empty cells and resurrected
  // the misleading coarse-boundary menu. Short live ranges retain 1/2/5s.
  const friendlyMinimumSeconds = rangeSeconds >= 30 * 60 ? 10 : 1;
  return jsDebugGraphResolutionChoices.filter(value => value >= Math.max(retainedSeconds, friendlyMinimumSeconds) && value * 10 <= rangeSeconds);
}

function normalizedDebugGraphResolutionOverrideSeconds(value, domain = debugGraphDomain(), nowMs = Date.now()) {
  const requested = Math.max(0, Number(value) || 0);
  if (requested === 0) return 0;
  const choices = debugGraphAvailableResolutionChoices(domain, nowMs);
  if (!choices.length) return 0;
  if (choices.includes(requested)) return requested;
  // An explicit choice belongs to the range that offered it. Persisting a different
  // explicit value while changing ranges prevents AUTO from returning to the finest
  // supported interval when the user comes back to a short range.
  return 0;
}

function syncDebugGraphResolutionOverride(nowMs = Date.now(), {persist = false, domain = debugGraphDomain(nowMs)} = {}) {
  const normalized = normalizedDebugGraphResolutionOverrideSeconds(debugRuntimeState.graphResolutionOverrideSeconds, domain, nowMs);
  if (normalized === debugRuntimeState.graphResolutionOverrideSeconds) return false;
  debugRuntimeState.graphResolutionOverrideSeconds = normalized;
  if (persist) saveJsDebugStatsUiPreferences();
  return true;
}

function debugGraphDisplayResolutionMs(domain, minimumResolutionSeconds = 0, nowMs = Date.now()) {
  // EXACT mode: render at exactly the requested preset resolution (the server
  // already returned uniform buckets at it), so the client never re-coarsens the
  // exact data down to the 120-point display cap.
  if (jsDebugGraphExactResolutionEnabled && !debugGraphZoomDomainValid()) {
    return debugGraphExactRequestResolutionSeconds() * 1000;
  }
  const domainStartMs = Number(domain?.startMs);
  const domainEndMs = Number(domain?.endMs);
  const domainSpanMs = Number.isFinite(domainStartMs) && Number.isFinite(domainEndMs)
    ? Math.max(jsDebugGraphRawBucketMs, domainEndMs - domainStartMs)
    : jsDebugGraphDefaultRangeSeconds * 1000;
  const targetMs = domainSpanMs / jsDebugGraphMaxDisplayPoints;
  const displayMs = jsDebugGraphDisplayBucketMs.find(bucketMs => bucketMs >= targetMs)
    || jsDebugGraphDisplayBucketMs.at(-1);
  // A display set has one bar width. Its scale must therefore accommodate the
  // coarsest retained source in the whole domain, not merely the tier at its
  // left edge. This also covers server history overlapping the live raw tail.
  // The retained-tier minimum (`debugGraphMinimumDisplayResolutionMs`) already
  // coarsens to the server's authoritative resolution for the domain from the
  // ACTUALLY-LOADED source buckets. Do NOT additionally clamp to
  // `jsDebugHistoryCoverageResolutionForRange`: that scans the last request's
  // coverage intervals, which can be STALE from a wider range (e.g. a 24h fetch
  // whose old tail is 600s), and would wrongly coarsen a 10s pick at 1h to 600s
  // when the 1h fetch is rejected/pending — the reported "10s does nothing / shows
  // 600s" regression. One resolution per view still holds via the retained tier.
  const retainedMs = debugGraphMinimumDisplayResolutionMs(domain, nowMs);
  const minimumMs = Math.max(0, Number(minimumResolutionSeconds) || 0) * 1000;
  const overrideMs = normalizedDebugGraphResolutionOverrideSeconds(debugRuntimeState.graphResolutionOverrideSeconds, domain, nowMs) * 1000;
  if (overrideMs > 0) {
    let effectiveMs = Math.max(jsDebugGraphRawBucketMs, retainedMs, minimumMs, overrideMs);
    // Point-cap: an explicit override that would render more than the budget of buckets
    // for this domain is clamped UP to the finest universe choice that stays within the
    // cap. The label reads back this effective (coarser) value so the render never blows
    // past the point budget even when the picker offers a finer value.
    const budgetMs = domainSpanMs / jsDebugGraphOverridePointCap;
    if (effectiveMs < budgetMs) {
      const cappedMs = jsDebugGraphResolutionChoices
        .map(seconds => seconds * 1000)
        .find(candidateMs => candidateMs >= budgetMs) ?? jsDebugGraphResolutionChoices[jsDebugGraphResolutionChoices.length - 1] * 1000;
      effectiveMs = Math.max(effectiveMs, cappedMs);
    }
    return effectiveMs;
  }
  return Math.max(jsDebugGraphRawBucketMs, displayMs, retainedMs, minimumMs);
}

function debugGraphSourceBuckets(domain) {
  return [...jsDebugGraphBuckets.values()]
    .filter(bucket => debugGraphBucketInRange(bucket, domain.startMs, domain.endMs))
    .sort((left, right) => left.startMs - right.startMs);
}

function debugGraphAddCoveredInterval(intervals, startMs, endMs) {
  if (!Number.isFinite(startMs) || !Number.isFinite(endMs) || endMs <= startMs) return;
  let index = 0;
  while (index < intervals.length && intervals[index].endMs < startMs) index += 1;
  let mergedStart = startMs;
  let mergedEnd = endMs;
  while (index < intervals.length && intervals[index].startMs <= mergedEnd) {
    mergedStart = Math.min(mergedStart, intervals[index].startMs);
    mergedEnd = Math.max(mergedEnd, intervals[index].endMs);
    intervals.splice(index, 1);
  }
  intervals.splice(index, 0, {startMs: mergedStart, endMs: mergedEnd});
}

function debugGraphUncoveredIntervals(intervals, startMs, endMs) {
  const uncovered = [];
  let cursor = startMs;
  for (const interval of intervals) {
    if (interval.endMs <= cursor) continue;
    if (interval.startMs >= endMs) break;
    if (interval.startMs > cursor) uncovered.push({startMs: cursor, endMs: Math.min(endMs, interval.startMs)});
    cursor = Math.max(cursor, interval.endMs);
    if (cursor >= endMs) break;
  }
  if (cursor < endMs) uncovered.push({startMs: cursor, endMs});
  return uncovered;
}

function debugGraphContributingSourceSlices(domain) {
  const domainStartMs = Number(domain?.startMs);
  const domainEndMs = Number(domain?.endMs);
  if (!Number.isFinite(domainStartMs) || !Number.isFinite(domainEndMs) || domainEndMs <= domainStartMs) return [];
  const coveredIntervals = [];
  const slices = [];
  const sources = debugGraphSourceBuckets(domain).sort((left, right) => (
    (Number(left.durationMs) - Number(right.durationMs))
    || (Number(left.startMs) - Number(right.startMs))
  ));
  for (const bucket of sources) {
    const sourceStartMs = Number(bucket.startMs);
    const sourceDurationMs = Math.max(jsDebugGraphRawBucketMs, Number(bucket.durationMs) || jsDebugGraphRawBucketMs);
    const visibleStartMs = Math.max(domainStartMs, sourceStartMs);
    // Keep the current/right-edge bucket whole: its sample is attached to the
    // bucket start even while the interval is still in progress. Only the old
    // left edge has an out-of-view prefix that can contaminate a narrower range.
    const visibleEndMs = sourceStartMs + sourceDurationMs;
    if (visibleEndMs <= visibleStartMs) continue;
    for (const interval of debugGraphUncoveredIntervals(coveredIntervals, visibleStartMs, visibleEndMs)) {
      slices.push({
        bucket,
        startMs: interval.startMs,
        endMs: interval.endMs,
        sourceDurationMs,
        multiplier: (interval.endMs - interval.startMs) / sourceDurationMs,
      });
    }
    // Coverage is clipped to the selected domain. A coarse bucket retained for a wider
    // range must not claim an out-of-view prefix and poison a fully fine-covered view.
    debugGraphAddCoveredInterval(coveredIntervals, visibleStartMs, visibleEndMs);
  }
  return slices;
}

function debugGraphDisplayBuckets(nowMs = Date.now(), {minimumResolutionSeconds = 0, rangeSeconds = debugRuntimeState.graphRangeSeconds} = {}) {
  compactJsDebugGraphBuckets(nowMs);
  const domain = debugGraphDomain(nowMs, rangeSeconds);
  const scaleMs = debugGraphDisplayResolutionMs(domain, minimumResolutionSeconds, nowMs);
  const buckets = new Map();
  for (const slice of debugGraphContributingSourceSlices(domain)) {
    // Once a finer source has claimed an instant, a coarser history response may
    // only fill the remaining visible interval. Place that proportional slice in
    // its visible cell while retaining the complete source bucket for wider views.
    debugGraphAggregateBucket(buckets, {...slice.bucket, startMs: slice.startMs}, scaleMs, slice.multiplier);
  }
  return [...buckets.values()].sort((a, b) => a.startMs - b.startMs);
}

// Token/model charts read the SAME unified bucket cache as every other chart.
// The only token-specific behavior left is the display floor: at least the
// token sampling cadence (60s), coarsened per range by
// debugGraphAgentTokenResolution so wide-range bars keep their legacy widths.
function debugGraphAgentTokenDisplayBuckets(nowMs = Date.now()) {
  const floorSeconds = Math.max(jsDebugGraphAgentTokenBucketSeconds, debugGraphAgentTokenResolution(nowMs));
  return debugGraphDisplayBuckets(nowMs, {minimumResolutionSeconds: floorSeconds, rangeSeconds: debugRuntimeState.graphRangeSeconds});
}

function debugGraphDomain(nowMs = Date.now(), rangeSeconds = debugRuntimeState.graphRangeSeconds) {
  const fallbackEndMs = Number.isFinite(Number(nowMs)) ? Number(nowMs) : Date.now();
  if (debugGraphZoomDomainValid()) {
    const startMs = Math.max(fallbackEndMs - jsDebugGraphRetentionMs, Number(jsDebugGraphZoomDomain.startMs));
    const endMs = Math.max(startMs + 1000, Number(jsDebugGraphZoomDomain.endMs));
    return {startMs, endMs, rangeSeconds: (endMs - startMs) / 1000, zoomed: true};
  }
  const endMs = fallbackEndMs;
  const activeRangeSeconds = normalizedJsDebugGraphRange(rangeSeconds, endMs);
  const durationMs = Math.max(1000, activeRangeSeconds * 1000);
  return {startMs: endMs - durationMs, endMs, rangeSeconds: activeRangeSeconds, zoomed: false};
}

function debugGraphBucketRate(bucket, value) {
  const seconds = Math.max(1, Number(bucket?.durationMs || jsDebugGraphRawBucketMs) / 1000);
  return Number(value || 0) / seconds;
}

function debugGraphAgentTokenBucketValue(bucket, item) {
  const tokens = Number(item?.tokens);
  if (Number.isFinite(tokens) && tokens > 0) {
    // `seconds` is the real elapsed span over which the transcript counter advanced. It remains
    // correct after the server folds raw samples into 2/5-minute history buckets; using the rendered
    // bucket width here made the same activity look like a different tokens/min rate as the view changed.
    const seconds = Number(item?.seconds);
    if (Number.isFinite(seconds) && seconds > 0) return (tokens / seconds) * 60;
    const minutes = Math.max(1 / 60, Number(bucket?.durationMs || jsDebugGraphAgentTokenBucketSeconds * 1000) / 60000);
    return tokens / minutes;
  }
  return Number(item?.samples || 0) > 0 ? Number(item.total || 0) / Number(item.samples || 1) : 0;
}

function debugGraphAgentTokenDisplayedSum(buckets) {
  let total = 0;
  for (const bucket of buckets || []) {
    if (!(bucket?.agentTokenRates instanceof Map)) continue;
    for (const item of bucket.agentTokenRates.values()) {
      const tokens = Number(item?.tokens);
      if (Number.isFinite(tokens) && tokens >= 0) {
        total += tokens;
        continue;
      }
      if (Number(item?.samples || 0) <= 0) continue;
      const minutes = Math.max(1 / 60, Number(bucket?.durationMs || jsDebugGraphAgentTokenBucketSeconds * 1000) / 60000);
      total += debugGraphAgentTokenBucketValue(bucket, item) * minutes;
    }
  }
  return Math.max(0, total);
}

function debugGraphModelTokenDisplayedSum(buckets) {
  let total = 0;
  for (const bucket of buckets || []) {
    if (!(bucket?.agentTokenRates instanceof Map)) continue;
    for (const item of bucket.agentTokenRates.values()) {
      if (!(item?.modelRates instanceof Map)) continue;
      for (const rate of item.modelRates.values()) total += Math.max(0, Number(rate?.tokens) || 0);
    }
  }
  return total;
}

function debugGraphBucketFieldSum(bucket, fields) {
  return fields.reduce((bucketTotal, field) => (
    bucketTotal + Math.max(0, Number(bucket?.[field]) || 0)
  ), 0);
}

function debugGraphDisplayedClientFieldSum(buckets, fields) {
  return (buckets || []).reduce((total, bucket) => {
    const clientBuckets = bucket?.clients instanceof Map && bucket.clients.size ? [...bucket.clients.values()] : [bucket];
    return total + clientBuckets.reduce((clientTotal, clientBucket) => clientTotal + debugGraphBucketFieldSum(clientBucket, fields), 0);
  }, 0);
}

function debugGraphDisplayedSummary(group, buckets) {
  const spec = jsDebugGraphDisplayedSummarySpecs[group?.displayedSummary];
  if (!spec) return null;
  const value = Math.max(0, Number(spec.value(buckets)) || 0);
  return {
    attribute: spec.attribute,
    descKey: spec.descKey,
    text: t(spec.labelKey, {count: spec.format(value)}),
    value,
  };
}

function debugGraphThisClientMetricBucket(bucket, metric) {
  if (!bucket || !metric) return null;
  if (bucket.clients instanceof Map) {
    const mapped = bucket.clients.get(jsDebugStatsClientIdForRequest());
    if (mapped) return mapped;
  }
  // Current wire values are statsd's fair all-client averages. The retained renderer
  // still uses its original top-level client slot; no browser-side aggregation occurs.
  return metric.hasData(bucket) ? bucket : null;
}

function debugGraphOtherClientMetricBuckets(bucket, metric) {
  if (!(bucket?.clients instanceof Map) || !metric) return [];
  const thisClientId = jsDebugStatsClientIdForRequest();
  return [...bucket.clients.entries()]
    .filter(([clientId, clientBucket]) => clientId !== thisClientId
      && (metric.key !== 'latency' || metric.hasData(clientBucket)))
    .map(([, clientBucket]) => clientBucket);
}

function debugGraphOtherClientMetricAverage(bucket, metric) {
  const clientBuckets = debugGraphOtherClientMetricBuckets(bucket, metric);
  if (!clientBuckets.length) return 0;
  return clientBuckets.reduce((sum, clientBucket) => sum + metric.value(clientBucket), 0) / clientBuckets.length;
}

function debugGraphClientSeriesDef(metric, {key = metric.key, labelKey, clientId, clientAggregate, clientLinePattern, color = ''}) {
  const otherClients = clientAggregate === jsDebugGraphOtherClientsAverageAggregate;
  return {
    ...metric, key, labelKey, metricLabelKey: metric.labelKey, cssKey: metric.key, clientMetric: true, metricKey: metric.key, clientId, clientAggregate, clientLinePattern,
    ...(color ? {color} : {}),
    value: bucket => otherClients ? debugGraphOtherClientMetricAverage(bucket, metric) : (() => { const clientBucket = debugGraphThisClientMetricBucket(bucket, metric); return clientBucket ? metric.value(clientBucket) : 0; })(),
    hasData: bucket => otherClients ? debugGraphOtherClientMetricBuckets(bucket, metric).length > 0 : (() => { const clientBucket = debugGraphThisClientMetricBucket(bucket, metric); return Boolean(clientBucket && (metric.key !== 'latency' || metric.hasData(clientBucket))); })(),
  };
}

function debugGraphProcessCpuBucketValue(bucket, processId) {
  const process = bucket?.servers instanceof Map ? bucket.servers.get(processId) : null;
  return Number(process?.cpuCount || 0) > 0
    ? Number(process.cpuTotalPercent || 0) / Number(process.cpuCount || 1)
    : 0;
}

function debugGraphProcessCpuBucketHasData(bucket, processId) {
  return Number(bucket?.servers instanceof Map ? bucket.servers.get(processId)?.cpuCount : 0) > 0;
}

function debugGraphHostMetricBucketItem(bucket, series) {
  const mapName = series.hostProcessId
    ? (series.hostMetric === 'cpu' ? 'cpuProcesses' : series.hostMetric === 'memory' ? 'memoryProcesses' : series.hostMetric === 'gpuUtil' ? 'gpuUtilProcesses' : 'gpuMemoryProcesses')
    : 'gpuDevices';
  return bucket?.hostMetrics?.[mapName] instanceof Map ? bucket.hostMetrics[mapName].get(series.hostProcessId || series.gpuDeviceId) : null;
}

function debugGraphHostMetricBucketValue(bucket, series) {
  const item = debugGraphHostMetricBucketItem(bucket, series);
  if (series.hostProcessId) {
    const total = series.hostMetric === 'memory' || series.hostMetric === 'gpuMemory' ? Number(item?.totalBytes || 0) : Number(item?.totalPercent || 0);
    return Number(item?.samples || 0) > 0 ? total / Number(item.samples || 1) : 0;
  }
  const total = series.hostMetric === 'gpuUtil' ? Number(item?.utilTotalPercent || 0) : Number(item?.memoryUsedTotalBytes || 0);
  return Number(item?.samples || 0) > 0 ? total / Number(item.samples || 1) : 0;
}

function debugGraphHostMetricBucketHasData(bucket, series) {
  return Number(debugGraphHostMetricBucketItem(bucket, series)?.samples || 0) > 0;
}

// True when any cached bucket (any range) carries a GPU device sample. Distinguishes a
// host with no GPU telemetry at all from a window that merely lacks GPU samples, so the
// unavailable state can explain itself precisely. Only consulted when a visible GPU
// chart has no series for the current window (not on the hot per-bucket render path).
function debugGraphAnyGpuDeviceSamplesCached() {
  for (const bucket of jsDebugGraphBuckets.values()) {
    for (const item of bucket?.hostMetrics?.gpuDevices?.values?.() || []) {
      if (Number(item?.samples || 0) > 0) return true;
    }
  }
  return false;
}

function debugGraphMovingAverageValues(values, sampleCount = jsDebugGraphMovingAverageSamples) {
  const count = Math.max(1, Math.floor(Number(sampleCount) || 1));
  const window = [];
  let total = 0;
  return values.map(value => {
    const sample = Math.max(0, Number(value) || 0);
    window.push(sample);
    total += sample;
    if (window.length > count) total -= window.shift();
    return total / window.length;
  });
}

function debugGraphNiceCeil(value) {
  const number = Number(value);
  if (!Number.isFinite(number) || number <= 0) return 0;
  const magnitude = 10 ** Math.floor(Math.log10(number));
  const scaled = number / magnitude;
  for (const step of [1, 2, 5, 10]) {
    if (scaled <= step) return step * magnitude;
  }
  return 10 * magnitude;
}

function debugGraphNiceCountPerSecondAxisMax(value) {
  const number = Number(value);
  if (!Number.isFinite(number) || number <= 0) return 0;
  const whole = Math.max(2, Math.ceil(number));
  return whole % 2 === 0 ? whole : whole + 1;
}

function debugGraphNiceBytesPerSecondAxisMax(value) {
  const number = Number(value);
  if (!Number.isFinite(number) || number <= 0) return 0;
  const unit = number >= 1024 * 1024 ? 1024 * 1024 : (number >= 1024 ? 1024 : 1);
  return debugGraphNiceCeil(number / unit) * unit;
}

function debugGraphNiceAxisMax(value, unit) {
  if (unit === 'count') return Math.max(1, Math.ceil(debugGraphNiceCeil(value)));
  if (unit === 'countPerSecond') return debugGraphNiceCountPerSecondAxisMax(value);
  if (unit === 'ms') return debugGraphNiceCeil(value);
  if (unit === 'bytesPerSecond') return debugGraphNiceBytesPerSecondAxisMax(value);
  if (unit === 'tokens') return Math.max(1, debugGraphNiceCeil(value));
  if (unit === 'tokensPerMinute') return Math.max(1, debugGraphNiceCeil(value));
  // Percent charts without a fixed 0-100 axis (e.g. Daemons load, where a single
  // multi-core service can exceed 100%) still need round tick steps. A 1/2/5
  // ceil keeps the max and its half-step both round (100->50, 50->25, 20->10)
  // instead of the raw data max (the 88.3% / 44.1% ticks in the report).
  if (unit === 'percent') return Math.max(1, debugGraphNiceCeil(value));
  return value;
}

function debugGraphTokenNumberText(value) {
  const number = Math.max(0, Number(value) || 0);
  if (number >= 1000 * 1000) {
    const millions = number / 1000 / 1000;
    return `${millions.toFixed(Number.isInteger(millions) || number >= 100 * 1000 * 1000 ? 0 : 1)}M`;
  }
  if (number >= 1000) return `${(number / 1000).toFixed(number >= 100 * 1000 ? 0 : 1)}k`;
  if (Number.isInteger(number)) return String(number);
  return number >= 100 ? number.toFixed(0) : number.toFixed(number >= 10 ? 1 : 2);
}

function debugGraphTokensText(value) {
  return t('debug.graph.unit.tokens', {count: debugGraphTokenNumberText(value)});
}

function debugGraphTokensPerMinuteText(value) {
  return t('debug.graph.unit.tokensPerMinute', {count: debugGraphTokenNumberText(value)});
}

function debugGraphValueText(value, unit) {
  const number = Number(value);
  if (!Number.isFinite(number)) return '0';
  if (unit === 'count') return Number.isInteger(number) ? String(number) : number.toFixed(number >= 10 ? 1 : 2);
  if (unit === 'countPerSecond') return `${Number.isInteger(number) ? String(number) : number.toFixed(number >= 10 ? 1 : 2)}/s`;
  if (unit === 'ms' && number >= 1000) return `${number >= 10000 ? (number / 1000).toFixed(0) : (number / 1000).toFixed(1)} s`;
  if (unit === 'ms') return `${number >= 100 ? number.toFixed(0) : number.toFixed(1)} ms`;
  if (unit === 'bytes') {
    if (number >= 1024 * 1024) return `${(number / 1024 / 1024).toFixed(number >= 100 * 1024 * 1024 ? 0 : 1)} MB`;
    if (number >= 1024) return `${(number / 1024).toFixed(number >= 100 * 1024 ? 0 : 1)} KB`;
    return `${number >= 100 ? number.toFixed(0) : number.toFixed(1)} B`;
  }
  if (unit === 'bytesPerSecond') {
    if (number >= 1024 * 1024) return `${(number / 1024 / 1024).toFixed(number >= 100 * 1024 * 1024 ? 0 : 1)}MB/s`;
    if (number >= 1024) return `${(number / 1024).toFixed(number >= 100 * 1024 ? 0 : 1)}kB/s`;
    return `${number >= 100 ? number.toFixed(0) : number.toFixed(1)}B/s`;
  }
  if (unit === 'tokens') return debugGraphTokensText(number);
  if (unit === 'tokensPerMinute') return debugGraphTokensPerMinuteText(number);
  if (unit === 'percent') return `${number >= 100 ? number.toFixed(0) : number.toFixed(1)}%`;
  return number >= 10 ? number.toFixed(1) : number.toFixed(2);
}

function debugGraphTerseTimeText(milliseconds) {
  const number = Math.max(0, Number(milliseconds) || 0);
  if (number >= 1000) {
    const seconds = number / 1000;
    return `${Number.isInteger(seconds) ? String(seconds) : seconds.toFixed(seconds >= 10 ? 1 : 2)}s`;
  }
  return `${number >= 100 ? number.toFixed(0) : number.toFixed(1)}ms`;
}

function debugGraphTerseBytesText(bytes) {
  const number = Math.max(0, Number(bytes) || 0);
  if (number >= 1024 * 1024 * 1024) return `${(number / 1024 / 1024 / 1024).toFixed(number >= 100 * 1024 * 1024 * 1024 ? 0 : 1)}GB`;
  if (number >= 1024 * 1024) return `${(number / 1024 / 1024).toFixed(number >= 100 * 1024 * 1024 ? 0 : 1)}MB`;
  if (number >= 1024) return `${(number / 1024).toFixed(number >= 100 * 1024 ? 0 : 1)}kB`;
  return `${number >= 100 ? number.toFixed(0) : number.toFixed(1)}B`;
}

function debugGraphAxisValueText(value, unit) {
  const number = Number(value);
  if (!Number.isFinite(number) || number <= 0) {
    if (unit === 'count') return '0';
    if (unit === 'countPerSecond') return '0';
    if (unit === 'ms') return '0';
    if (unit === 'bytes') return '0GB';
    if (unit === 'bytesPerSecond') return '0';
    if (unit === 'tokens') return '0';
    if (unit === 'tokensPerMinute') return '0';
    if (unit === 'percent') return '0%';
    return '0';
  }
  if (unit === 'countPerSecond') return Number.isInteger(number) ? String(number) : number.toFixed(number >= 10 ? 1 : 2);
  if (unit === 'ms') return debugGraphTerseTimeText(number);
  if (unit === 'bytes' || unit === 'bytesPerSecond') return debugGraphTerseBytesText(number);
  if (unit === 'tokens') return debugGraphTokenNumberText(number);
  if (unit === 'tokensPerMinute') return debugGraphTokenNumberText(number);
  return debugGraphValueText(number, unit);
}

function debugGraphUptimeText(seconds) {
  const total = Math.max(0, Math.floor(Number(seconds) || 0));
  const days = Math.floor(total / 86400);
  const hours = Math.floor((total % 86400) / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const secs = total % 60;
  if (days) return `${days}d ${hours}h ${minutes}m`;
  if (hours) return `${hours}h ${minutes}m ${secs}s`;
  if (minutes) return `${minutes}m ${secs}s`;
  return `${secs}s`;
}

function debugGraphBytesText(bytes) {
  const value = Number(bytes);
  if (!Number.isFinite(value) || value <= 0) return '';
  const mib = value / 1024 / 1024;
  if (mib >= 1024) return `${(mib / 1024).toFixed(1)} GiB`;
  return `${mib.toFixed(mib >= 100 ? 0 : 1)} MiB`;
}

function debugGraphTotalMegabytesText(bytes) {
  const value = Math.max(0, Number(bytes) || 0) / 1024 / 1024;
  if (value >= 100) return value.toFixed(0);
  if (value >= 10) return value.toFixed(1);
  return value.toFixed(2);
}

function debugGraphMetaItem(labelKey, params = {}) {
  return {text: t(labelKey, params), labelKey, descKey: jsDebugGraphDescriptionKeyByLabelKey[labelKey]};
}

function debugGraphPlainMetaItem(text, descKey = '') {
  return {text: String(text || ''), descKey};
}

function debugRemovalLatencyMetaItem() {
  if (typeof terminalRemovalLatencySummary !== 'function') return '';
  const summary = terminalRemovalLatencySummary();
  if (!summary?.count) return '';
  return debugGraphMetaItem('debug.graph.meta.removal', {
    last: debugGraphTerseTimeText(summary.last?.durationMs),
    average: debugGraphTerseTimeText(summary.averageMs),
    count: summary.count,
  });
}

function debugClientPerfRows() {
  if (typeof clientPerfSummary !== 'function') return [];
  const preferred = ['focusSet', 'tabActivationPaint', 'tabberFullRefresh', 'tabberLayoutSync', 'statsHistoryFetch', 'statsHistoryParse', 'statsHistoryApply', 'statsHistoryRender', 'statsHistoryPaint', 'statsHistoryLoading', 'statsNoDataSweep', 'keydownToTermData', 'term.onData', 'wsSend', 'echoToTermWrite', 'xtermWrite', 'terminalUnderlineRender', 'terminalAttentionScan', 'terminalBlankProbe', 'finderRefresh', 'sessionFilesRefresh', 'sessionFilesRender', 'renderInfoPanel', 'renderSessionButtons', 'renderPaneTabStrips', 'renderPanels', 'sseEvent', 'autoStatusRender', 'longTask'];
  const order = new Map(preferred.map((name, index) => [name, index]));
  return clientPerfSummary()
    .filter(row => Number(row.count || 0) > 0)
    .sort((left, right) => (order.get(left.name) ?? 999) - (order.get(right.name) ?? 999) || Number(right.maxMs || 0) - Number(left.maxMs || 0))
    .slice(0, 18);
}

function debugClientPerfText(row) {
  const parts = [
    `${row.name}`,
    `n=${Math.floor(Number(row.count || 0))}`,
  ];
  if (Number.isFinite(Number(row.avgMs)) && Number(row.avgMs) > 0) parts.push(`avg=${Number(row.avgMs).toFixed(1)}ms`);
  if (Number.isFinite(Number(row.maxMs)) && Number(row.maxMs) > 0) parts.push(`max=${Number(row.maxMs).toFixed(1)}ms`);
  if (Number(row.rows || 0) > 0) parts.push(`rows=${Math.floor(Number(row.rows))}`);
  if (Number(row.nodes || 0) > 0) parts.push(`nodes=${Math.floor(Number(row.nodes))}`);
  if (Number(row.bytes || 0) > 0) parts.push(`bytes=${Math.floor(Number(row.bytes))}`);
  if (Number(row.skipped || 0) > 0) parts.push(`skipped=${Math.floor(Number(row.skipped))}`);
  return parts.join(' ');
}

function debugClientPerfHtml() {
  if (debugModeExplicitUrlEnabled !== true) return '';
  const rows = debugClientPerfRows();
  const longTasks = typeof clientPerfLongTaskSummary === 'function' ? clientPerfLongTaskSummary() : {count: 0, averageMs: 0, maxMs: 0};
  const activeAnimations = typeof clientPerfActiveAnimationCount === 'function' ? clientPerfActiveAnimationCount() : 0;
  if (!rows.length && !longTasks.count && !activeAnimations) return '';
  const timing = longTasks.count ? t('debug.graph.clientWorkTiming', {average: longTasks.averageMs, max: longTasks.maxMs}) : '';
  const header = t('debug.graph.clientWork', {animations: activeAnimations, tasks: longTasks.count, timing});
  return `<div class="js-debug-client-perf" data-js-debug-client-perf>
    <div class="js-debug-client-perf-title">${esc(header)}</div>
    <div class="js-debug-client-perf-grid">${rows.map(row => `<div class="js-debug-client-perf-row">${esc(debugClientPerfText(row))}</div>`).join('')}</div>
  </div>`;
}

function debugGraphMetaItems() {
  const items = [];
  if (Number.isFinite(jsDebugStatsServerUptimeSeconds)) items.push(debugGraphMetaItem('debug.graph.meta.uptime', {uptime: debugGraphUptimeText(jsDebugStatsServerUptimeSeconds)}));
  if (Number.isFinite(jsDebugStatsServerPid)) items.push(debugGraphPlainMetaItem(`PID=${Math.floor(jsDebugStatsServerPid)}`));
  const rss = debugGraphBytesText(jsDebugStatsServerRssBytes);
  if (rss) items.push(debugGraphMetaItem('debug.graph.meta.rss', {rss}));
  if (Number.isFinite(jsDebugStatsServerSequence) && jsDebugStatsServerSequence > 0) items.push(debugGraphMetaItem('debug.graph.meta.serverSequence', {sequence: Math.floor(jsDebugStatsServerSequence)}));
  const removalLatency = debugRemovalLatencyMetaItem();
  if (removalLatency) items.push(removalLatency);
  if (items.length) {
    const counts = debugEventCounts();
    const uploadedMb = debugGraphTotalMegabytesText(counts.apiRequestBytes);
    const downloadedMb = debugGraphTotalMegabytesText(counts.apiResponseBytes + counts.sseBytes);
    items.push(debugGraphMetaItem('debug.graph.meta.totalTraffic', {uploaded: uploadedMb, downloaded: downloadedMb}));
  }
  return items;
}

function debugGraphWaitingForServerStats() {
  return debugGraphMetaItems().length === 0;
}

function debugGraphMetaHtml() {
  const items = debugGraphMetaItems();
  const initialHistoryOverlayOwnsLoading = jsDebugHistoryReadinessStateName() === 'loading-initial'
    && jsDebugHistoryReadiness.overlayVisible === true;
  const metaHtml = items.length
    ? items.map(item => `<span class="js-debug-graph-meta-item"${debugGraphExplainAttrs(item.text, item.descKey, {attribute: 'data-js-debug-meta-desc'})}>${esc(item.text)}</span>`).join('<span aria-hidden="true"> | </span>')
    : (initialHistoryOverlayOwnsLoading || jsDebugHistoryReadiness.phase === 'error' ? '' : textWithMovingEllipsisHtml(t('debug.waitingForServerStats')));
  return `<div class="js-debug-graph-meta" data-js-debug-uptime="${esc(Number.isFinite(jsDebugStatsServerUptimeSeconds) ? debugGraphUptimeText(jsDebugStatsServerUptimeSeconds) : '')}">${metaHtml}</div>`;
}

function debugGraphHistoryOverlayText(state = jsDebugHistoryReadiness) {
  const range = jsDebugGraphRangeLabel(state.requestedRangeSeconds);
  const stateName = jsDebugHistoryReadinessStateName(state);
  if (stateName === 'loading-initial') return t('debug.graph.history.loadingInitial');
  if (stateName === 'loading-older') return t('debug.graph.history.loadingOlder', {range});
  if (stateName === 'retrying') return state.error || t('debug.graph.history.retrying', {range});
  if (stateName === 'error') return t('debug.graph.history.error', {range, error: state.error || t('common.unknown')});
  return '';
}

function debugGraphHistoryOverlayContentHtml(state = jsDebugHistoryReadiness) {
  const text = debugGraphHistoryOverlayText(state);
  if (!text) return '';
  const message = jsDebugHistoryReadinessBusy(state)
    ? textWithMovingEllipsisHtml(text, 'js-debug-history-loading-dots')
    : esc(text);
  const retry = state.phase === 'error'
    ? `<button type="button" class="preferences-inline-action js-debug-history-retry" data-js-debug-history-retry>${esc(t('common.retry'))}</button>`
    : '';
  return `<div class="js-debug-history-overlay-message"><span>${message}</span>${retry}</div>`;
}

function debugGraphHistoryOverlayHtml(state = jsDebugHistoryReadiness) {
  const hidden = state.overlayVisible === true ? '' : ' hidden';
  return `<div class="js-debug-history-overlay" data-js-debug-history-overlay aria-live="polite" aria-atomic="true"${hidden}>${debugGraphHistoryOverlayContentHtml(state)}</div>`;
}

function debugGraphTokenSeriesDefs(buckets, dimension = 'agent') {
  const tokenItems = new Map();
  for (const bucket of buckets) {
    if (!(bucket.agentTokenRates instanceof Map)) continue;
    for (const [key, item] of bucket.agentTokenRates.entries()) {
      if (dimension === 'agent') {
        const existing = tokenItems.get(String(key)) || {label: item?.label || String(key), samples: 0};
        existing.label = item?.label || existing.label;
        existing.samples += Number(item?.samples || 0);
        tokenItems.set(String(key), existing);
        continue;
      }
      if (!(item?.modelRates instanceof Map)) continue;
      for (const [model, rate] of item.modelRates.entries()) {
        const modelKey = String(model || 'unknown').trim() || 'unknown';
        const existing = tokenItems.get(modelKey) || {label: modelKey, samples: 0};
        existing.samples += Number(rate?.samples || 0);
        tokenItems.set(modelKey, existing);
      }
    }
  }
  const prefix = dimension === 'agent' ? jsDebugGraphAgentTokenSeriesPrefix : jsDebugGraphModelTokenSeriesPrefix;
  const displayedItems = [...tokenItems.entries()]
    .filter(([, item]) => item.samples > 0)
    .sort((a, b) => a[1].label.localeCompare(b[1].label) || a[0].localeCompare(b[0]));
  const visuals = debugGraphDisplayedTokenVisuals(displayedItems, ([key]) => key);
  return displayedItems.map(([key, item], index) => ({
      key: `${prefix}${key}`,
      label: item.label,
      descKey: dimension === 'agent' ? 'debug.graph.series.agentToken.desc' : 'debug.graph.series.modelToken.desc',
      descParams: dimension === 'agent' ? {agent: item.label} : {model: item.label},
      unit: 'tokensPerMinute',
      cssKey: 'agentToken',
      tokenPatternSeries: true,
      agentTokenSeries: dimension === 'agent',
      agentTokenKey: key,
      tokenDimension: dimension,
      agentTokenPatternIndex: visuals[index].patternIndex,
      color: visuals[index].color,
      value: bucket => {
        const tokenItem = bucket?.agentTokenRates instanceof Map ? bucket.agentTokenRates.get(key) : null;
        if (dimension === 'agent') {
          if (!tokenItem) return 0;
          return debugGraphAgentTokenBucketValue(bucket, tokenItem);
        }
        let value = 0;
        if (bucket?.agentTokenRates instanceof Map) {
          for (const agentRate of bucket.agentTokenRates.values()) {
            const modelRate = agentRate?.modelRates instanceof Map ? agentRate.modelRates.get(key) : null;
            if (!modelRate) continue;
            value += debugGraphAgentTokenBucketValue(bucket, {...modelRate, seconds: agentRate.seconds});
          }
        }
        return value;
      },
      hasData: bucket => {
        if (dimension === 'agent') {
          const tokenItem = bucket?.agentTokenRates instanceof Map ? bucket.agentTokenRates.get(key) : null;
          return Number(tokenItem?.samples || 0) > 0 || Number(tokenItem?.tokens || 0) > 0;
        }
        return [...(bucket?.agentTokenRates?.values?.() || [])].some(agentRate => {
          const modelRate = agentRate?.modelRates instanceof Map ? agentRate.modelRates.get(key) : null;
          return Number(modelRate?.samples || 0) > 0 || Number(modelRate?.tokens || 0) > 0;
        });
      },
      sampleCount: bucket => {
        if (dimension === 'agent') {
          const tokenItem = bucket?.agentTokenRates instanceof Map ? bucket.agentTokenRates.get(key) : null;
          return Math.max(0, Number(tokenItem?.samples) || 0);
        }
        let samples = 0;
        for (const agentRate of bucket?.agentTokenRates?.values?.() || []) {
          samples += Math.max(0, Number(agentRate?.modelRates?.get(key)?.samples) || 0);
        }
        return samples;
      },
    }));
}

function debugGraphAgentTokenSeriesDefs(buckets) {
  return debugGraphTokenSeriesDefs(buckets, 'agent');
}

function debugGraphStablePaletteIndex(identity, count) {
  const size = Math.max(1, Math.floor(Number(count) || 0));
  let hash = 2166136261;
  for (const character of String(identity || 'unknown')) {
    hash ^= character.codePointAt(0) || 0;
    hash = Math.imul(hash, 16777619) >>> 0;
  }
  return hash % size;
}

function debugGraphDisplayedTokenVisuals(items, identityForItem = item => item?.key) {
  const colorCount = Math.max(1, jsDebugGraphAgentTokenColors.length);
  const patternCount = Math.max(1, jsDebugGraphAgentTokenPatternCount);
  const combinations = [];
  const pairedCount = Math.min(colorCount, patternCount);
  for (let index = 0; index < pairedCount; index += 1) combinations.push([index, index]);
  for (let colorIndex = 0; colorIndex < colorCount; colorIndex += 1) {
    for (let patternIndex = 0; patternIndex < patternCount; patternIndex += 1) {
      if (colorIndex === patternIndex && colorIndex < pairedCount) continue;
      combinations.push([colorIndex, patternIndex]);
    }
  }
  return (items || []).map((item, index) => {
    const identity = identityForItem(item);
    const combinationIndex = index < combinations.length
      ? index
      : debugGraphStablePaletteIndex(identity, combinations.length);
    const [colorIndex, patternIndex] = combinations[combinationIndex];
    return {color: jsDebugGraphAgentTokenColors[colorIndex], colorIndex, patternIndex};
  });
}

function debugGraphSelectedModelTokenBucketValue(bucket) {
  let total = 0;
  for (const agentRate of bucket?.agentTokenRates?.values?.() || []) {
    for (const rate of agentRate?.modelRates?.values?.() || []) total += debugGraphAgentTokenBucketValue(bucket, {...rate, seconds: agentRate.seconds});
  }
  return total;
}

function debugGraphModelTokenSeriesDefs(buckets) {
  return debugGraphTokenSeriesDefs(buckets, 'model');
}


function debugGraphClientMetricSeriesDefs(buckets) {
  const peerSeries = jsDebugGraphClientMetrics
    .filter(metric => !['api', 'sse'].includes(metric.key))
    .filter(metric => buckets.some(bucket => debugGraphOtherClientMetricBuckets(bucket, metric).length > 0))
    .map(metric => debugGraphClientSeriesDef(metric, {
      key: `client:${jsDebugGraphOtherClientsAverageId}:${metric.key}`,
      labelKey: 'debug.graph.series.otherClientsAverage',
      clientId: jsDebugGraphOtherClientsAverageId,
      clientAggregate: jsDebugGraphOtherClientsAverageAggregate,
      clientLinePattern: jsDebugGraphOtherClientsAverageLinePattern,
      color: 'var(--bad)',
    }));
  const apiMetric = jsDebugGraphClientMetrics.find(metric => metric.key === 'api');
  const sseMetric = jsDebugGraphClientMetrics.find(metric => metric.key === 'sse');
  if (!apiMetric || !sseMetric || !buckets.some(bucket => debugGraphOtherClientMetricBuckets(bucket, apiMetric).length > 0)) return peerSeries;
  // API and SSE are two transports for the same request-rate comparison. A single red peer
  // line shows their summed per-client average rather than misleading parallel red averages.
  return [{
    key: `client:${jsDebugGraphOtherClientsAverageId}:apiSse`,
    chartMetricKey: 'api',
    metricKey: 'apiSse',
    cssKey: 'api',
    labelKey: 'debug.graph.series.otherClientsAverage',
    metricLabelKey: 'debug.graph.chart.clientApiSse',
    clientMetric: true,
    clientId: jsDebugGraphOtherClientsAverageId,
    clientAggregate: jsDebugGraphOtherClientsAverageAggregate,
    clientLinePattern: jsDebugGraphOtherClientsAverageLinePattern,
    unit: 'countPerSecond',
    color: 'var(--bad)',
    value: bucket => debugGraphOtherClientMetricAverage(bucket, apiMetric) + debugGraphOtherClientMetricAverage(bucket, sseMetric),
    hasData: bucket => debugGraphOtherClientMetricBuckets(bucket, apiMetric).length > 0,
  }, ...peerSeries];
}

function debugGraphProcessCpuSeriesDefs(buckets) {
  const processes = new Map();
  for (const bucket of buckets) {
    if (!(bucket.servers instanceof Map)) continue;
    for (const [processId, process] of bucket.servers.entries()) {
      if (Number(process?.cpuCount || 0) <= 0) continue;
      if (!processes.has(processId)) processes.set(processId, {label: String(process?.label || processId)});
    }
  }
  const currentPort = String(location.port || (location.protocol === 'https:' ? '443' : '80')).trim();
  const currentProcessId = `port:${currentPort}`;
  // Truthfulness: the exact serving `port:N` is ALWAYS the one solid series, even with zero
  // samples in this window (it renders as an honest gap). It is never dropped, a peer is never
  // promoted in its place, and there is no aggregate `cpu` fallback series.
  if (!processes.has(currentProcessId)) processes.set(currentProcessId, {label: currentProcessId});
  let peerIndex = 0;
  const definitions = [...processes.entries()]
    .sort((a, b) => a[1].label.localeCompare(b[1].label) || a[0].localeCompare(b[0]))
    .map(([processId, process]) => {
      const current = processId === currentProcessId;
      const legacyWebPort = String(processId).match(/^port:(\d+)$/);
      const displayLabel = legacyWebPort && process.label === processId
        ? (current ? 'yolomux.py (web)' : `yolomux.py (web) :${legacyWebPort[1]}`)
        : process.label;
      const color = current
        ? jsDebugGraphProcessCpuColors.current
        : jsDebugGraphProcessCpuColors.peers[peerIndex++ % jsDebugGraphProcessCpuColors.peers.length];
      return {
        key: `cpu:${processId}`,
        labelKey: 'debug.graph.series.processCpu',
        labelParams: {process: displayLabel},
        unit: 'percent',
        cssKey: 'cpu',
        chartMetricKey: 'cpu',
        processCpu: true,
        processId,
        linePattern: current ? 'solid' : 'dot',
        color,
        value: bucket => debugGraphProcessCpuBucketValue(bucket, processId),
        hasData: bucket => debugGraphProcessCpuBucketHasData(bucket, processId),
      };
    });
  return definitions;
}

function debugGraphGpuDeviceSeriesDefs(buckets, metric) {
  const devices = new Map();
  for (const bucket of buckets) {
    const source = bucket.hostMetrics?.gpuDevices;
    if (!(source instanceof Map)) continue;
    for (const [key, item] of source.entries()) {
      if (Number(item?.samples || 0) <= 0) continue;
      devices.set(key, String(item.label || key));
    }
  }
  return [...devices.entries()]
    .sort((a, b) => a[1].localeCompare(b[1]) || a[0].localeCompare(b[0]))
    .map(([deviceId, label], index) => ({
      key: `gpu:${metric}:${deviceId}`,
      label,
      descKey: 'debug.graph.series.gpuDevice.desc',
      descParams: {device: label, metric: metric === 'gpuMemory' ? t('debug.graph.chart.gpuMemory') : t('debug.graph.chart.gpuUtil')},
      unit: metric === 'gpuMemory' ? 'bytes' : 'percent',
      hostMetric: metric,
      gpuDeviceId: deviceId,
      color: jsDebugGraphGpuDeviceColors[index % jsDebugGraphGpuDeviceColors.length],
      value: bucket => debugGraphHostMetricBucketValue(bucket, {hostMetric: metric, gpuDeviceId: deviceId}),
      hasData: bucket => debugGraphHostMetricBucketHasData(bucket, {hostMetric: metric, gpuDeviceId: deviceId}),
      sampleCount: bucket => Number(debugGraphHostMetricBucketItem(bucket, {hostMetric: metric, gpuDeviceId: deviceId})?.samples || 0),
      familyHasData: bucket => [...(bucket?.hostMetrics?.gpuDevices?.values?.() || [])]
        .some(item => Number(item?.samples || 0) > 0),
      displayHoldMs: jsDebugGraphDisplayHoldExpiryMs.tenSecondGauge,
    }));
}

function debugGraphHostMetricSeriesDefs(buckets) {
  return [
    ...debugGraphGpuDeviceSeriesDefs(buckets, 'gpuUtil'),
    ...debugGraphGpuDeviceSeriesDefs(buckets, 'gpuMemory'),
  ];
}

function normalizedDebugGraphServiceLoadMode(value) {
  return debugGraphServiceLoadModes.includes(value) ? value : 'avg';
}

const debugGraphServiceLoadModes = Object.freeze(['avg', 'max', 'min']);

function normalizedDebugGraphServiceLoadPreference(value) {
  return debugGraphServiceLoadModes.includes(value) ? value : 'auto';
}

function debugGraphDefaultServiceLoadMode(buckets) {
  return (buckets || []).some(bucket => Number(bucket?.durationMs || 0) >= 60000) ? 'max' : 'avg';
}

function debugGraphVisibleServiceLoadItems(buckets) {
  const items = [];
  for (const bucket of buckets || []) {
    for (const [key, item] of bucket?.hostMetrics?.serviceLoad?.entries?.() || []) {
      // Old retained buckets contain the synthetic web row. Its PID is already shown by CPU.
      if (key === 'web' || Number(item?.cpuSamples || 0) <= 0) continue;
      items.push([key, item]);
    }
  }
  return items;
}

function debugGraphServiceLoadRangeAvailable(buckets) {
  let sampled = 0;
  for (const [, item] of debugGraphVisibleServiceLoadItems(buckets)) {
    sampled += 1;
    if (item.cpuRangeAvailable !== true) return false;
  }
  return sampled > 0;
}

function debugGraphServiceLoadEffectiveMode(buckets, mode = debugRuntimeState.serviceLoadMode) {
  const preferred = normalizedDebugGraphServiceLoadPreference(mode);
  const normalized = preferred === 'auto' ? debugGraphDefaultServiceLoadMode(buckets) : preferred;
  return normalized === 'avg' || debugGraphServiceLoadRangeAvailable(buckets) ? normalized : 'avg';
}

function debugGraphServiceLoadValue(item, mode = debugRuntimeState.serviceLoadMode) {
  const samples = Number(item?.cpuSamples || 0);
  if (samples <= 0) return 0;
  const normalized = normalizedDebugGraphServiceLoadMode(mode);
  if (normalized === 'max' && item.cpuRangeAvailable === true) return Math.max(0, Number(item.cpuMaxPercent || 0));
  if (normalized === 'min' && item.cpuRangeAvailable === true) return Math.max(0, Number(item.cpuMinPercent || 0));
  return Math.max(0, Number(item.cpuTotalPercent || 0)) / samples;
}

function debugGraphServiceLoadSeriesDefs(buckets) {
  const mode = debugGraphServiceLoadEffectiveMode(buckets);
  const services = new Map();
  for (const [key, item] of debugGraphVisibleServiceLoadItems(buckets)) {
    services.set(key, String(item.label || key));
  }
  const items = [...services.entries()].sort((left, right) => left[1].localeCompare(right[1]) || left[0].localeCompare(right[0]));
  const visuals = debugGraphDisplayedTokenVisuals(items, ([key]) => key);
  const linePatterns = ['solid', 'dash', 'dot'];
  return items.map(([key, label], index) => ({
    key: `serviceLoad:${key}`, label, unit: 'percent', serviceLoad: true,
    color: visuals[index].color, linePattern: linePatterns[visuals[index].patternIndex % linePatterns.length],
    value: bucket => {
      const item = bucket?.hostMetrics?.serviceLoad?.get?.(key);
      return debugGraphServiceLoadValue(item, mode);
    },
    hasData: bucket => Number(bucket?.hostMetrics?.serviceLoad?.get?.(key)?.cpuSamples || 0) > 0,
    sampleCount: bucket => Number(bucket?.hostMetrics?.serviceLoad?.get?.(key)?.cpuSamples || 0),
    familyHasData: bucket => debugGraphVisibleServiceLoadItems([bucket]).length > 0,
    displayHoldMs: jsDebugGraphDisplayHoldExpiryMs.tenSecondGauge,
  }));
}

function debugGraphDisplayHoldOutage(bucket) {
  return Number(bucket?.disconnectedMs || 0) > 0;
}

function debugGraphProjectSeriesSamples(def, buckets) {
  const holdMs = Math.max(0, Number(def?.displayHoldMs) || 0);
  const values = [];
  const hasDataValues = [];
  const observedDataValues = [];
  const provenanceValues = [];
  let heldSample = null;
  for (const [index, bucket] of (buckets || []).entries()) {
    const value = def.value(bucket);
    const observed = def.hasData(bucket) === true;
    if (observed) {
      const bucketStartMs = Number(bucket?.startMs) || 0;
      const requestedSampleTimeMs = typeof def.sampleTimeMs === 'function' ? Number(def.sampleTimeMs(bucket)) : NaN;
      const sampleTimeMs = Number.isFinite(requestedSampleTimeMs) ? requestedSampleTimeMs : bucketStartMs;
      const requestedSampleCount = typeof def.sampleCount === 'function' ? Number(def.sampleCount(bucket)) : 1;
      const sampleCount = Number.isFinite(requestedSampleCount) ? Math.max(0, requestedSampleCount) : 0;
      const provenance = {
        sampleTimeMs,
        sampleCount,
        sourceBucketStartMs: bucketStartMs,
        sourceBucketDurationMs: Math.max(jsDebugGraphRawBucketMs, Number(bucket?.durationMs) || jsDebugGraphRawBucketMs),
        sourceIndex: index,
        expiresAtMs: holdMs > 0 ? sampleTimeMs + holdMs : sampleTimeMs,
        held: false,
      };
      values.push(value);
      hasDataValues.push(true);
      observedDataValues.push(true);
      provenanceValues.push(provenance);
      heldSample = holdMs > 0 ? {value, provenance} : null;
      continue;
    }
    const familyObserved = typeof def.familyHasData === 'function' && def.familyHasData(bucket) === true;
    if (familyObserved || debugGraphDisplayHoldOutage(bucket)) heldSample = null;
    const bucketStartMs = Number(bucket?.startMs) || 0;
    const bucketDurationMs = Math.max(jsDebugGraphRawBucketMs, Number(bucket?.durationMs) || jsDebugGraphRawBucketMs);
    const bucketEndMs = bucketStartMs + bucketDurationMs;
    const held = heldSample && bucketStartMs >= heldSample.provenance.sampleTimeMs
      && bucketEndMs <= heldSample.provenance.expiresAtMs;
    values.push(held ? heldSample.value : value);
    hasDataValues.push(Boolean(held));
    observedDataValues.push(false);
    provenanceValues.push(held ? {...heldSample.provenance, held: true} : null);
  }
  return {values, hasDataValues, observedDataValues, provenanceValues};
}

function debugGraphSeriesData(buckets) {
  const times = buckets.map(bucket => Number(bucket.startMs) || 0);
  const durations = buckets.map(bucket => Math.max(jsDebugGraphRawBucketMs, Number(bucket.durationMs) || jsDebugGraphRawBucketMs));
  const defs = [...jsDebugGraphSeries, ...debugGraphClientMetricSeriesDefs(buckets), ...debugGraphProcessCpuSeriesDefs(buckets), ...debugGraphHostMetricSeriesDefs(buckets), ...debugGraphServiceLoadSeriesDefs(buckets), ...debugGraphAgentTokenSeriesDefs(buckets), ...debugGraphModelTokenSeriesDefs(buckets)];
  return defs.map(def => {
    const localizedDef = {...def, label: debugGraphLocalizedLabel(def)};
    const {values, hasDataValues, observedDataValues, provenanceValues} = debugGraphProjectSeriesSamples(def, buckets);
    const colorValues = typeof def.colorValue === 'function' ? buckets.map(bucket => def.colorValue(bucket)) : null;
    const sampleValues = values.filter((_value, index) => observedDataValues[index]);
    const sampleTimes = provenanceValues
      .filter((_provenance, index) => observedDataValues[index])
      .map(provenance => provenance.sampleTimeMs);
    const samples = sampleValues.length;
    const displayValues = values.filter((_value, index) => hasDataValues[index]);
    const displaySamples = displayValues.length;
    const max = Math.max(0, ...displayValues);
    const current = displayValues.length ? displayValues[displayValues.length - 1] : 0;
    const movingAverageSamples = Number(def.movingAverageSamples || 0);
    const movingAverageValues = movingAverageSamples > 0 ? debugGraphMovingAverageValues(sampleValues, movingAverageSamples) : [];
    return {
      ...localizedDef,
      values,
      times,
      durations,
      hasDataValues,
      observedDataValues,
      provenanceValues,
      colorValues,
      movingAverageValues,
      movingAverageTimes: sampleTimes,
      movingAverageSamples,
      max,
      current,
      samples,
      displaySamples,
    };
  });
}

function debugGraphResolutionLabelHtml(nowMs = Date.now()) {
  const domain = debugGraphDomain(nowMs);
  syncDebugGraphResolutionOverride(nowMs, {persist: true, domain});
  const resolutionSeconds = debugGraphDisplayResolutionMs(domain, 0, nowMs) / 1000;
  const availableChoices = debugGraphAvailableResolutionChoices(domain, nowMs);
  const overrideSeconds = Number(debugRuntimeState.graphResolutionOverrideSeconds) || 0;
  return `<label class="js-debug-resolution-label" data-js-debug-resolution data-js-debug-resolution-seconds="${esc(resolutionSeconds)}">${esc(t('debug.graph.control.resolution', {resolution: `${resolutionSeconds}s`}))}<select data-js-debug-resolution-override aria-label="${esc(t('debug.graph.control.resolution', {resolution: `${resolutionSeconds}s`}))}"><option value="0"${overrideSeconds === 0 ? ' selected' : ''}>AUTO</option>${availableChoices.map(value => `<option value="${value}"${overrideSeconds === value ? ' selected' : ''}>${value}s</option>`).join('')}</select></label>`;
}

function debugGraphRangeControlsHtml(nowMs = Date.now()) {
  const activeRange = activeJsDebugGraphRangeSeconds(nowMs);
  const options = debugGraphAvailableRangeOptions(nowMs);
  if (!options.length) return '';
  const sliderId = 'js-debug-range-options';
  const value = jsDebugGraphRangeOptionIndex(activeRange, nowMs);
  const zoomed = debugGraphZoomDomainValid();
  const domain = debugGraphDomain(nowMs);
  const fullRangeLabel = zoomed ? debugGraphCostRangeText(domain) : '';
  const rangeLabel = zoomed ? debugGraphCompactRangeText(domain) : jsDebugGraphRangeLabel(activeRange, nowMs);
  const resetLabel = debugGraphZoomResetLabel();
  return `<div class="js-debug-range-slider-control${zoomed ? ' js-debug-range-slider-control--zoomed' : ''}" data-js-debug-range-control>
    ${zoomed ? `<button type="button" class="js-debug-zoom-reset" data-js-debug-zoom-reset aria-label="${esc(resetLabel)}">${esc(resetLabel)}</button>` : ''}
    ${debugGraphRangePrefixVisible(zoomed) ? `<span class="js-debug-range-prefix" aria-hidden="true">${esc(debugGraphRangePrefixText())}</span>` : ''}
    <input class="js-debug-range-slider" type="range" min="0" max="${esc(Math.max(0, options.length - 1))}" step="any" value="${esc(value)}" list="${esc(sliderId)}" data-js-debug-range-slider aria-label="${esc(t('debug.graph.control.timeRange'))}"${zoomed ? ' disabled aria-disabled="true"' : ''}>
    <datalist id="${esc(sliderId)}">${options.map((option, index) => `<option value="${esc(index)}" label="${esc(option.label)}" data-js-debug-range="${esc(option.seconds)}"></option>`).join('')}</datalist>
    <span class="js-debug-range-label${zoomed ? ' js-debug-range-label--zoomed' : ''}" data-js-debug-range-label${zoomed ? ` title="${esc(fullRangeLabel)}"` : ''}>${esc(rangeLabel)}</span>
  </div>`;
}

function debugGraphZoomResetLabel() {
  return `${t('common.reset')} ${t('debug.graph.control.zoom')}`;
}

function debugGraphRangePrefixText() {
  return t('debug.graph.control.timeRange');
}

function debugGraphRangePrefixVisible(zoomed) {
  return !zoomed;
}

function debugGraphChartToggleControlsHtml() {
  return `<details class="js-debug-chart-toggle-control" data-js-debug-chart-menu>
    <summary aria-label="${esc(t('debug.graph.control.charts'))}">${esc(t('debug.graph.control.charts'))}</summary>
    <div class="js-debug-chart-toggle-menu" role="group" aria-label="${esc(t('debug.graph.control.charts'))}">
    ${jsDebugGraphChartControlItems.map(group => {
      const label = debugGraphLocalizedLabel(group);
      const visible = debugGraphChartVisible(group.key);
      return `<label title="${esc(label)}"><input type="checkbox" data-js-debug-chart-toggle="${esc(group.key)}"${visible ? ' checked' : ''}>${esc(label)}</label>`;
    }).join('')}
    </div>
  </details>`;
}

function debugGraphLayoutControlsHtml() {
  return `<div class="js-debug-chart-layout-control" role="group" aria-label="${esc(t('debug.graph.control.size'))}"><span>${esc(t('debug.graph.control.size'))}:</span>${['AUTO', 'S', 'M', 'L', 'MAX'].map((label, value) => `<button type="button" data-js-debug-chart-layout="${value}" aria-pressed="${debugRuntimeState.graphChartLayout === value ? 'true' : 'false'}">${label}</button>`).join('')}</div>`;
}

function debugGraphRangeResolutionControlsHtml(nowMs = Date.now()) {
  return `<div class="js-debug-range-resolution-controls">${debugGraphRangeControlsHtml(nowMs)}${debugGraphResolutionLabelHtml(nowMs)}</div>`;
}

function debugGraphServiceLoadModeLabel(mode) {
  return t(`debug.graph.serviceLoad.mode.${normalizedDebugGraphServiceLoadMode(mode)}`);
}

function debugGraphServiceLoadModeControlsHtml(buckets = []) {
  const rangeAvailable = debugGraphServiceLoadRangeAvailable(buckets);
  const selected = debugGraphServiceLoadEffectiveMode(buckets);
  const label = t('debug.graph.chart.serversLoad');
  return `<fieldset class="js-debug-service-load-mode-control" role="radiogroup" aria-label="${esc(label)}">${debugGraphServiceLoadModes.map(mode => {
    const checked = mode === selected;
    const disabled = mode !== 'avg' && !rangeAvailable;
    return `<label class="preferences-radio"><input type="radio" name="js-debug-service-load-mode" value="${esc(mode)}" data-js-debug-service-load-mode="${esc(mode)}"${checked ? ' checked' : ''}${disabled ? ' disabled' : ''} aria-checked="${checked ? 'true' : 'false'}" aria-disabled="${disabled ? 'true' : 'false'}"><span>${esc(debugGraphServiceLoadModeLabel(mode))}</span></label>`;
  }).join('')}</fieldset>`;
}

function debugGraphControlsHtml(nowMs = Date.now()) {
  return `<div class="js-debug-graph-controls">
    ${debugGraphChartToggleControlsHtml()}
    ${debugGraphLayoutControlsHtml()}
    ${debugGraphRangeResolutionControlsHtml(nowMs)}
  </div>`;
}

function debugGraphLocalDateKey(ms) {
  if (!Number.isFinite(ms)) return '';
  const date = new Date(ms);
  if (Number.isNaN(date.getTime())) return '';
  return [date.getFullYear(), date.getMonth() + 1, date.getDate()]
    .map((value, index) => String(value).padStart(index === 0 ? 4 : 2, '0'))
    .join('-');
}

function debugGraphTimeLabel(ms, {includeDate = false, includeSeconds = !includeDate} = {}) {
  if (!Number.isFinite(ms)) return '';
  if (typeof localizedDateTimeFormat === 'function') {
    const options = includeDate
      ? {month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit'}
      : {hour: '2-digit', minute: '2-digit'};
    if (includeSeconds) options.second = '2-digit';
    const localized = localizedDateTimeFormat(ms / 1000, options);
    if (localized) return localized;
  }
  const date = new Date(ms);
  const hours = String(date.getHours()).padStart(2, '0');
  const minutes = String(date.getMinutes()).padStart(2, '0');
  const seconds = String(date.getSeconds()).padStart(2, '0');
  const time = includeSeconds ? `${hours}:${minutes}:${seconds}` : `${hours}:${minutes}`;
  return includeDate ? `${debugGraphLocalDateKey(ms)} ${time}` : time;
}

function debugGraphExactTimeLabel(ms) {
  if (!Number.isFinite(ms)) return '';
  const localized = localizedDateTimeFormat(ms / 1000, {
    year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', second: '2-digit',
  });
  return localized || debugGraphTimeLabel(ms, {includeDate: true, includeSeconds: true});
}

function debugGraphSeriesTimeMs(series, index) {
  const times = Array.isArray(series.times) ? series.times : [];
  const value = Number(times[index]);
  return Number.isFinite(value) ? value : NaN;
}

function debugGraphPolylinePoints(values, times, chartMax, domain, hasDataValues = null) {
  return debugGraphPolylinePointSegments(values, times, chartMax, domain, hasDataValues).map(segment => segment.join(' ')).join(' ');
}

// True when [startMs, endMs) overlaps any genuine no-data range (a real
// coverage/communication hole), so the line should BREAK there instead of
// bridging it. Everything not inside such a range is treated as covered — the
// line stays continuous across it even when the recorded resolution is coarser
// than the display (linear interpolation between the surrounding real samples).
function debugGraphTimeInNoDataRange(noDataRanges, startMs, endMs) {
  if (!Array.isArray(noDataRanges) || !noDataRanges.length) return false;
  if (!Number.isFinite(startMs) || !Number.isFinite(endMs) || endMs <= startMs) return false;
  return noDataRanges.some(range => Number(range?.startMs) < endMs && Number(range?.endMs) > startMs);
}

function debugGraphPolylinePointSegments(values, times, chartMax, domain, hasDataValues = null, durations = [], gapThresholdMs = 0, logScale = false, noDataRanges = null, observedValues = null) {
  // Coverage-aware breaking: when a genuine no-data range list is supplied, an
  // empty display cell never breaks the line on its own (a covered-but-coarser
  // span reads as one continuous, linearly interpolated line). The line breaks
  // only where a real recorded gap lies between two samples. Without a range
  // list, fall back to the legacy time-threshold behavior.
  const rangeBreak = Array.isArray(noDataRanges);
  const segments = [];
  let current = [];
  let previousDataEndMs = NaN;
  values.forEach((value, index) => {
    const timeMs = Number(times[index]);
    const durationMs = Math.max(jsDebugGraphRawBucketMs, Number(durations[index]) || jsDebugGraphRawBucketMs);
    // A HELD (carried-forward, non-observed) value that lands inside a genuine
    // no-data range is dropped and ends the run, so a held gauge can never leak a
    // flat line through a real recorded hole. A genuinely OBSERVED sample is always
    // drawable — coverage never erases a real measurement, it only stops the fill.
    const observed = !observedValues || observedValues[index] === true;
    const heldInNoData = rangeBreak && !observed && hasDataValues && hasDataValues[index] === true
      && debugGraphTimeInNoDataRange(noDataRanges, timeMs, Number.isFinite(timeMs) ? timeMs + durationMs : timeMs + 1);
    if ((hasDataValues && hasDataValues[index] !== true) || heldInNoData) {
      if (current.length && (heldInNoData || (!rangeBreak && gapThresholdMs <= 0))) {
        segments.push(current);
        current = [];
      }
      return;
    }
    const breakHere = current.length && (rangeBreak
      ? debugGraphTimeInNoDataRange(noDataRanges, previousDataEndMs, timeMs)
      : (gapThresholdMs > 0 && Number.isFinite(previousDataEndMs) && Number.isFinite(timeMs) && timeMs - previousDataEndMs >= gapThresholdMs));
    if (breakHere) {
      segments.push(current);
      current = [];
    }
    current.push(debugGraphPointForValue(value, timeMs, chartMax, domain, logScale).join(','));
    previousDataEndMs = Number.isFinite(timeMs) ? timeMs + durationMs : NaN;
  });
  if (current.length) segments.push(current);
  return segments;
}

function debugGraphPointForValue(value, timeMs, chartMax, domain, logScale = false) {
  const startMs = Number(domain?.startMs);
  const endMs = Number(domain?.endMs);
  const spanMs = Math.max(1, endMs - startMs);
  const rawX = Number.isFinite(Number(timeMs)) && Number.isFinite(startMs) && Number.isFinite(endMs)
    ? ((Number(timeMs) - startMs) / spanMs) * jsDebugGraphGeometry.width
    : jsDebugGraphGeometry.width;
  const x = Math.max(0, Math.min(jsDebugGraphGeometry.width, rawX));
  const y = debugGraphPlotYForValue(value, chartMax, logScale);
  return [x.toFixed(1), y.toFixed(1)];
}

function debugGraphPlotYForValue(value, chartMax, logScale = false) {
  const max = Math.max(Number(chartMax) || 0, 1);
  const rawValue = Math.max(0, Number(value) || 0);
  let normalized;
  if (logScale?.mode === 'broken-linear') {
    const threshold = Math.max(1, Math.min(max, Number(logScale.threshold) || max));
    const upperFraction = Math.max(0.1, Math.min(0.3, Number(logScale.upperFraction) || 0.18));
    normalized = rawValue <= threshold || max <= threshold
      ? (rawValue / threshold) * (1 - upperFraction)
      : (1 - upperFraction) + (((rawValue - threshold) / (max - threshold)) * upperFraction);
    normalized = Math.max(0, Math.min(1, normalized));
  } else {
    normalized = logScale === true
      ? Math.max(0, Math.min(1, Math.log1p(rawValue) / Math.log1p(max)))
      : Math.max(0, Math.min(1, rawValue / max));
  }
  return jsDebugGraphGeometry.plotTop + ((1 - normalized) * jsDebugGraphGeometry.plotHeight);
}

function debugGraphXForTime(timeMs, domain) {
  const startMs = Number(domain?.startMs);
  const endMs = Number(domain?.endMs);
  const spanMs = Math.max(1, endMs - startMs);
  if (!Number.isFinite(Number(timeMs)) || !Number.isFinite(startMs) || !Number.isFinite(endMs)) return 0;
  return Math.max(0, Math.min(jsDebugGraphGeometry.width, ((Number(timeMs) - startMs) / spanMs) * jsDebugGraphGeometry.width));
}

function debugGraphDisconnectedRanges(buckets, domain) {
  const domainStart = Number(domain?.startMs);
  const domainEnd = Number(domain?.endMs);
  if (!Number.isFinite(domainStart) || !Number.isFinite(domainEnd) || domainEnd <= domainStart) return [];
  const ranges = [];
  for (const bucket of buckets || []) {
    const startMs = Number(bucket?.startMs);
    const durationMs = Math.max(jsDebugGraphRawBucketMs, Number(bucket?.durationMs) || jsDebugGraphRawBucketMs);
    // EventSource can reconnect while ordinary API requests still succeed. A stream reconnect is
    // useful telemetry, but it is not a full client outage and must not paint over real latency.
    if (debugGraphCurrentClientCommunicationCount(bucket) > 0) continue;
    const disconnectedMs = Math.min(durationMs, Math.max(0, debugGraphCurrentClientDisconnectedMs(bucket)));
    if (!Number.isFinite(startMs) || disconnectedMs <= 0) continue;
    const rangeStart = Math.max(domainStart, startMs);
    const rangeEnd = Math.min(domainEnd, startMs + disconnectedMs);
    if (rangeEnd <= rangeStart) continue;
    ranges.push({startMs: rangeStart, endMs: rangeEnd});
  }
  return debugGraphMergeTimeRanges(ranges, domain)
    .map(range => ({...range, disconnectedMs: range.endMs - range.startMs}));
}

function debugGraphDisconnectedRectsHtml(buckets, domain, ranges = null) {
  const disconnectedRanges = Array.isArray(ranges) ? ranges : debugGraphDisconnectedRanges(buckets, domain);
  return disconnectedRanges.map((range, index) => {
    const x1 = debugGraphXForTime(range.startMs, domain);
    const x2 = debugGraphXForTime(range.endMs, domain);
    const width = Math.max(1.5, x2 - x1);
    const title = t('debug.graph.badConnection', {duration: debugGraphTerseTimeText(range.disconnectedMs)});
    return debugGraphPlotOverlayRectHtml('js-debug-disconnected-range', 'data-js-debug-disconnected-range', index, x1, width, title);
  }).join('');
}

function debugGraphBucketRanges(buckets) {
  return (buckets || [])
    .map(bucket => {
      const startMs = Number(bucket?.startMs);
      const durationMs = Math.max(jsDebugGraphRawBucketMs, Number(bucket?.durationMs) || jsDebugGraphRawBucketMs);
      return Number.isFinite(startMs) ? {bucket, startMs, endMs: startMs + durationMs, durationMs} : null;
    })
    .filter(Boolean)
    .sort((a, b) => a.startMs - b.startMs);
}

function debugGraphMergeTimeRanges(ranges, domain = null) {
  const domainStart = Number(domain?.startMs);
  const domainEnd = Number(domain?.endMs);
  const hasDomain = Number.isFinite(domainStart) && Number.isFinite(domainEnd) && domainEnd > domainStart;
  const normalized = (ranges || [])
    .map(range => {
      const rawStart = Number(range?.startMs);
      const rawEnd = Number(range?.endMs);
      if (!Number.isFinite(rawStart) || !Number.isFinite(rawEnd) || rawEnd <= rawStart) return null;
      const startMs = hasDomain ? Math.max(domainStart, rawStart) : rawStart;
      const endMs = hasDomain ? Math.min(domainEnd, rawEnd) : rawEnd;
      return endMs > startMs ? {startMs, endMs} : null;
    })
    .filter(Boolean)
    .sort((a, b) => a.startMs - b.startMs || a.endMs - b.endMs);
  const merged = [];
  for (const range of normalized) {
    const previous = merged.at(-1);
    if (previous && range.startMs <= previous.endMs + 1) previous.endMs = Math.max(previous.endMs, range.endMs);
    else merged.push({...range});
  }
  return merged;
}

function debugGraphComplementTimeRanges(ranges, domain) {
  const domainStart = Number(domain?.startMs);
  const domainEnd = Number(domain?.endMs);
  if (!Number.isFinite(domainStart) || !Number.isFinite(domainEnd) || domainEnd <= domainStart) return [];
  const gaps = [];
  let cursor = domainStart;
  for (const range of debugGraphMergeTimeRanges(ranges, domain)) {
    if (range.startMs > cursor) gaps.push({startMs: cursor, endMs: range.startMs});
    cursor = Math.max(cursor, range.endMs);
  }
  if (cursor < domainEnd) gaps.push({startMs: cursor, endMs: domainEnd});
  return gaps;
}

function debugGraphCurrentClientSeriesItems(seriesItems) {
  const items = Array.isArray(seriesItems) ? seriesItems.filter(Boolean) : [];
  const currentClientItems = items.filter(series => series.clientMetric === true && series.clientAggregate === jsDebugGraphThisClientAggregate);
  return currentClientItems.length ? currentClientItems : items;
}

function debugGraphCurrentClientRecord(bucket) {
  const clients = bucket?.clients;
  if (clients instanceof Map && clients.size > 0) return clients.get(jsDebugStatsClientIdForRequest()) || null;
  return bucket && typeof bucket === 'object' ? bucket : null;
}

function debugGraphCurrentClientCommunicationCount(bucket) {
  const record = debugGraphCurrentClientRecord(bucket);
  if (!record) return 0;
  return ['apiCount', 'sseCount', 'latencyCount', 'bandwidthBytes', 'heartbeatCount']
    .reduce((total, key) => total + Math.max(0, Number(record[key] || 0)), 0);
}

function debugGraphCurrentClientDisconnectedMs(bucket) {
  return Math.max(0, Number(debugGraphCurrentClientRecord(bucket)?.disconnectedMs || 0));
}

function debugGraphCommunicationGapThresholdMs(seriesItems) {
  const displayResolutionMs = Math.max(jsDebugGraphRawBucketMs, ...(seriesItems || []).flatMap(series => series?.durations || []));
  return jsDebugStatsHistoryFlushMs + Math.min(jsDebugStatsHistoryFlushMs, displayResolutionMs);
}

function debugGraphNoDataRuns(buckets, domain, seriesItems) {
  const items = Array.isArray(seriesItems) ? seriesItems.filter(Boolean) : [];
  if (!items.length) return [];
  const domainStart = Number(domain?.startMs);
  const domainEnd = Number(domain?.endMs);
  if (!Number.isFinite(domainStart) || !Number.isFinite(domainEnd) || domainEnd <= domainStart) return [];
  const perf = clientPerfStart('statsNoDataSweep');
  try {
    const hasCurrentClientCommunication = debugGraphBucketRanges(buckets)
      .some(item => debugGraphCurrentClientCommunicationCount(item.bucket) > 0);
    const dataRanges = debugGraphBucketRanges(buckets)
      .filter(item => hasCurrentClientCommunication
        ? debugGraphCurrentClientCommunicationCount(item.bucket) > 0
        : items.some(series => series.hasData(item.bucket)))
      .map(item => ({startMs: item.startMs, endMs: item.endMs}));
    const disconnectedRanges = debugGraphDisconnectedRanges(buckets, domain);
    return debugGraphComplementTimeRanges([...dataRanges, ...disconnectedRanges], domain)
      .map(range => ({...range, startMs: range.startMs + jsDebugGraphNoDataOverlayDelayMs}))
      .filter(range => range.endMs > range.startMs);
  } finally {
    clientPerfEnd(perf, {rows: (buckets || []).length});
  }
}

function debugGraphNoDataRectsHtml(buckets, domain, seriesItems) {
  return debugGraphNoDataRuns(buckets, domain, seriesItems).map((range, index) => {
    const x1 = debugGraphXForTime(range.startMs, domain);
    const x2 = debugGraphXForTime(range.endMs, domain);
    const width = Math.max(1.5, x2 - x1);
    return debugGraphPlotOverlayRectHtml('js-debug-no-data-range', 'data-js-debug-no-data-range', index, x1, width, t('debug.noCommunicationData'));
  }).join('');
}

function jsDebugHistoryCoverageFamilyForGroup(group) {
  const key = String(group?.key || '');
  if (!key) return '';
  return jsDebugStatsFamilyByChartGroup[key] || '';
}

function jsDebugHistoryCoverageIntervalsForFamily(family) {
  const stores = jsDebugHistoryReadiness.storeCoverageIntervals || {};
  const manifestEntry = jsDebugStatsFamilyManifest[family];
  for (const key of manifestEntry ? [family, ...manifestEntry.legacyAliases] : []) {
    if (Object.prototype.hasOwnProperty.call(stores, key)) return stores[key];
  }
  // Per-family independence: once the server reports ANY per-store coverage, a
  // family with no store entry of its own was never recorded (fresh install or
  // a newly added metric such as system_memory / service_load). Treat it as
  // fully uncovered so its never-recorded window paints red, instead of
  // borrowing another family's coverage through the compatibility-global
  // intervals (cross-family inference). Only a legacy all-empty store map — the
  // pre-per-store protocol — falls back to the global coverage.
  if (Object.keys(stores).length > 0) return [];
  return jsDebugHistoryReadiness.coverageIntervals;
}

function debugGraphHistoryCoverageGapRuns(group, domain, alreadyPaintedRanges = []) {
  const family = jsDebugHistoryCoverageFamilyForGroup(group);
  if (!family) return [];
  const requestedRanges = (jsDebugHistoryReadiness.requestCoverageIntervals || []).map(interval => ({
    startMs: Number(interval.startSeconds) * 1000,
    endMs: Number(interval.endSeconds) * 1000,
  }));
  const coveredRanges = jsDebugHistoryCoverageIntervalsForFamily(family).map(interval => ({
    startMs: Number(interval.startSeconds) * 1000,
    endMs: Number(interval.endSeconds) * 1000,
  }));
  const gaps = [];
  for (const requested of debugGraphMergeTimeRanges(requestedRanges, domain)) {
    gaps.push(...debugGraphComplementTimeRanges(coveredRanges, requested));
  }
  const mergedGaps = debugGraphMergeTimeRanges(gaps, domain);
  const trimmedGaps = !alreadyPaintedRanges.length
    ? mergedGaps
    : debugGraphMergeTimeRanges(
      mergedGaps.flatMap(gap => debugGraphComplementTimeRanges(alreadyPaintedRanges, gap)),
      domain,
    );
  return debugGraphMeaningfulCoverageGaps(trimmedGaps, domain);
}

// A durable-coverage gap should paint only when it spans at least one rendered
// bucket at its own age. The 1-2 second sampler micro-breaks at owner/epoch
// handoffs (server restarts) are sub-bucket at coarse ranges; without this
// filter each inflated to a 1.5px red hairline and a fully recorded region read
// as a fake "missing chunk". Genuine holes and never-recorded prefixes stay.
function debugGraphMeaningfulCoverageGaps(ranges, domain) {
  const nowMs = Number(domain?.endMs) || Date.now();
  return (ranges || []).filter(range => {
    const startMs = Number(range?.startMs);
    const endMs = Number(range?.endMs);
    if (!Number.isFinite(startMs) || !Number.isFinite(endMs) || endMs <= startMs) return false;
    return endMs - startMs >= debugGraphBucketDurationForTime(startMs, nowMs);
  });
}

function debugGraphHistoryCoverageGapRectsHtml(group, domain, alreadyPaintedRanges = []) {
  const family = jsDebugHistoryCoverageFamilyForGroup(group);
  return debugGraphHistoryCoverageGapRuns(group, domain, alreadyPaintedRanges).map((range, index) => {
    const x1 = debugGraphXForTime(range.startMs, domain);
    const x2 = debugGraphXForTime(range.endMs, domain);
    return `<g data-js-debug-history-coverage-family="${esc(family)}">${debugGraphPlotOverlayRectHtml(
      'js-debug-no-data-range js-debug-history-no-data-range',
      'data-js-debug-history-no-data-range',
      index,
      x1,
      Math.max(1.5, x2 - x1),
      t('debug.graph.noDataRecorded'),
    )}</g>`;
  }).join('');
}

// The union of every genuine no-data range painted for a chart (real coverage
// holes, client communication gaps, agent-status gaps, and disconnected spans).
// These are the ONLY places a series line/area may break; everything else is a
// covered span the line stays continuous across (interpolating a coarser tier).
function debugGraphChartGenuineNoDataRanges(group, domain, overlayBuckets, disconnectedRanges, groupSeries) {
  const ranges = [];
  const statusRuns = group.statusNoDataOverlay === true ? debugGraphAgentStatusNoDataRuns(overlayBuckets, domain) : [];
  ranges.push(...debugGraphHistoryCoverageGapRuns(group, domain, statusRuns));
  ranges.push(...statusRuns);
  if (group.noDataOverlay === true) {
    ranges.push(...debugGraphNoDataRuns(overlayBuckets, domain, debugGraphCurrentClientSeriesItems(groupSeries)));
  }
  // A disconnected span is a genuine sampling outage for EVERY series (the gauge
  // was not observed), so it always breaks the line — not only on charts that
  // draw the dedicated disconnected overlay.
  ranges.push(...(Array.isArray(disconnectedRanges) ? disconnectedRanges : debugGraphDisconnectedRanges(overlayBuckets, domain)));
  return debugGraphMergeTimeRanges(ranges, domain);
}

function debugGraphAgentStatusNoDataRuns(buckets, domain) {
  const ranges = debugGraphBucketRanges(buckets);
  const statusRanges = ranges
    .filter(item => Number(item.bucket?.agentActivitySamples || 0) > 0)
    .map(item => ({startMs: item.startMs, endMs: item.endMs}));
  const serverRanges = ranges.filter(item => Number(item.bucket?.cpuCount || 0) > 0 || Number(item.bucket?.agentActivitySamples || 0) > 0);
  if (!serverRanges.length) return [];
  const scope = {
    startMs: Math.max(Number(domain?.startMs) || 0, serverRanges[0].startMs),
    endMs: Number(domain?.endMs) || 0,
  };
  if (scope.endMs <= scope.startMs) return [];
  const lastServerEndMs = serverRanges.at(-1).endMs;
  return debugGraphComplementTimeRanges(statusRanges, scope)
    .map(range => range.startMs >= lastServerEndMs - 1
      ? {...range, startMs: range.startMs + jsDebugGraphNoDataOverlayDelayMs}
      : range)
    .filter(range => range.endMs > range.startMs);
}

function debugGraphAgentStatusNoDataRectsHtml(buckets, domain) {
  return debugGraphAgentStatusNoDataRuns(buckets, domain).map((range, index) => {
    const x1 = debugGraphXForTime(range.startMs, domain);
    const x2 = debugGraphXForTime(range.endMs, domain);
    return debugGraphPlotOverlayRectHtml(
      'js-debug-no-data-range js-debug-agent-status-no-data-range',
      'data-js-debug-agent-status-no-data-range',
      index,
      x1,
      Math.max(1.5, x2 - x1),
      t('debug.graph.agentStatus.noData'),
    );
  }).join('');
}

function debugGraphPlotOverlayRectHtml(className, attribute, index, x, width, title) {
  return `<rect class="${esc(className)}" ${attribute}="${esc(index)}" x="${esc(x.toFixed(1))}" y="${esc(jsDebugGraphGeometry.plotTop)}" width="${esc(width.toFixed(1))}" height="${esc(jsDebugGraphGeometry.plotHeight)}"><title>${esc(title)}</title></rect>`;
}

function debugGraphSeriesPlotValues(series) {
  return Array.isArray(series.plotValues) ? series.plotValues : (series.values || []);
}

function debugGraphSeriesPlotHasDataValues(series) {
  return Array.isArray(series.plotHasDataValues) ? series.plotHasDataValues : (series.hasDataValues || null);
}

function debugGraphSeriesPlotObservedValues(series) {
  return Array.isArray(series.plotObservedValues) ? series.plotObservedValues : (series.observedDataValues || null);
}

function debugGraphSeriesClassKey(series) {
  return String(series?.cssKey || series?.key || '').replace(/[^A-Za-z0-9_-]/g, '-');
}

function debugGraphAgentTokenPatternIndex(series) {
  if (series?.tokenPatternSeries !== true) return -1;
  const index = Math.floor(Number(series.agentTokenPatternIndex));
  return Number.isFinite(index) && index >= 0 ? index % jsDebugGraphAgentTokenPatternCount : 0;
}

function debugGraphAgentTokenPatternId(series, suffix = '') {
  const patternIndex = debugGraphAgentTokenPatternIndex(series);
  if (patternIndex < 0) return '';
  const scope = String(series?.agentTokenPatternScope || '')
    .replace(/[^A-Za-z0-9_-]/g, '-')
    .slice(-64);
  const key = String(series?.agentTokenKey || series?.key || 'series')
    .replace(/[^A-Za-z0-9_-]/g, '-')
    .slice(-64);
  return `js-debug-agent-token-pattern-${scope ? `${scope}-` : ''}${patternIndex}-${key || 'series'}${suffix}`;
}

function debugGraphAgentTokenPatternShapeHtml(patternIndex) {
  return jsDebugGraphAgentTokenPatternShapes[patternIndex] || '';
}

function debugGraphAgentTokenPatternDefinitionHtml(series, options = {}) {
  const patternIndex = debugGraphAgentTokenPatternIndex(series);
  if (patternIndex < 0) return '';
  const legend = options.legend === true;
  const patternId = debugGraphAgentTokenPatternId(series, legend ? '-legend' : '');
  const shape = debugGraphAgentTokenPatternShapeHtml(patternIndex);
  const dataAttr = legend ? 'data-js-debug-token-legend-pattern-def' : 'data-js-debug-token-pattern-def';
  return `<pattern id="${esc(patternId)}" ${dataAttr}="${esc(patternIndex)}" patternUnits="userSpaceOnUse" width="6" height="2"${debugGraphSeriesStyleAttr(series)}><rect width="6" height="2" fill="var(--js-debug-series-color, var(--accent-sky-strong))"></rect>${shape ? `<g class="js-debug-agent-token-pattern-ink">${shape}</g>` : ''}</pattern>`;
}

function debugGraphAgentTokenPatternDefsHtml(seriesItems) {
  const patterns = (seriesItems || [])
    .filter(series => debugGraphAgentTokenPatternIndex(series) >= 0)
    .map(series => debugGraphAgentTokenPatternDefinitionHtml(series));
  return patterns.length ? `<defs>${patterns.join('')}</defs>` : '';
}

function debugGraphAgentTokenLegendSwatchHtml(series) {
  const patternId = debugGraphAgentTokenPatternId(series, '-legend');
  if (!patternId) return '';
  return `<svg class="js-debug-legend-token-swatch" viewBox="0 0 10 10" aria-hidden="true"${debugGraphSeriesStyleAttr(series)}><defs>${debugGraphAgentTokenPatternDefinitionHtml(series, {legend: true})}</defs><rect width="10" height="10" rx="1.5" fill="url(#${esc(patternId)})"></rect></svg>`;
}

// Activity Monitor color follows the kernel's semantic pressure state, not a
// threshold guessed from the separately plotted headroom percentage.
function debugGraphMacMemoryPressureColor(value) {
  const level = Number(value);
  if (level === 1) return 'var(--good)';
  if (level === 2) return 'var(--warning-border-strong)';
  if (level >= 4) return 'var(--bad)';
  return 'var(--muted)';
}

function debugGraphSeriesDisplayColor(series) {
  if (typeof series?.colorForValue !== 'function') return String(series?.color || '').trim();
  const values = Array.isArray(series?.colorValues) ? series.colorValues : debugGraphSeriesPlotValues(series);
  for (let index = values.length - 1; index >= 0; index -= 1) {
    const value = Number(values[index]);
    if (Number.isFinite(value)) return String(series.colorForValue(value) || '').trim();
  }
  return '';
}

function debugGraphSeriesStyleAttr(series, {barPattern = false} = {}) {
  const color = debugGraphSeriesDisplayColor(series);
  const declarations = color ? [`--js-debug-series-color: ${color}`] : [];
  const patternId = barPattern ? debugGraphAgentTokenPatternId(series) : '';
  if (patternId) declarations.push(`fill: url(#${patternId})`);
  return declarations.length ? ` style="${esc(`${declarations.join('; ')};`)}"` : '';
}

function debugGraphSeriesClientAttrs(series) {
  if (series?.clientMetric !== true) return '';
  const clientId = String(series.clientId || 'this');
  return ` data-js-debug-client-series="${esc(clientId)}" data-js-debug-client-line="${esc(series.clientLinePattern || 'solid')}"`;
}

function debugGraphSeriesLinePattern(series) {
  const pattern = String(series?.linePattern || (series?.clientMetric === true ? series.clientLinePattern : '') || '').trim();
  return ['solid', 'dot', 'dash'].includes(pattern) ? pattern : '';
}

function debugGraphSeriesLinePatternAttrs(series) {
  const pattern = debugGraphSeriesLinePattern(series);
  return pattern ? ` data-js-debug-line-pattern="${esc(pattern)}"` : '';
}

function debugGraphSeriesLineClassName(series, extraClass = '') {
  const classes = ['js-debug-line', `js-debug-line--${debugGraphSeriesClassKey(series)}`];
  const linePattern = debugGraphSeriesLinePattern(series);
  if (linePattern) classes.push('js-debug-line--pattern', `js-debug-line--pattern-${linePattern}`);
  if (series?.clientMetric === true) {
    classes.push('js-debug-line--client', `js-debug-line--client-${series.clientLinePattern || 'solid'}`);
  }
  if (extraClass) classes.push(extraClass);
  return classes.join(' ');
}

function debugGraphSeriesTokenAgentAttrs(series) {
  if (series?.tokenPatternSeries !== true) return '';
  return ` data-js-debug-token-agent="${esc(series.agentTokenKey || '')}" data-js-debug-token-agent-label="${esc(series.label || '')}" data-js-debug-token-pattern="${esc(debugGraphAgentTokenPatternIndex(series))}"`;
}

function debugGraphPolylineHtml(series, chartMax, domain, logScale = false, noDataRanges = null) {
  // The line is one continuous, linearly interpolated stroke across every covered
  // span — a coarser recorded resolution (e.g. 60s data on a 10s chart) never
  // shows as a gap. It breaks ONLY at genuine no-data ranges (real coverage or
  // communication holes) supplied by the chart, which stay honest as red no-data
  // bands. Without a supplied range list (legacy callers) fall back to the old
  // time-threshold: client metrics break at their communication-gap threshold,
  // other series at any gap.
  const rangeAware = Array.isArray(noDataRanges);
  const cpuSeries = series?.processCpu === true || series?.key === 'cpu' || series?.key === 'systemCpu';
  const heldGaugeSeries = Number(series?.displayHoldMs || 0) > 0;
  const gapThresholdMs = rangeAware ? 0 : (series?.clientMetric === true
    ? debugGraphCommunicationGapThresholdMs([series])
    : ((cpuSeries || heldGaugeSeries) ? 1 : 0));
  return debugGraphPolylinePointSegments(
    debugGraphSeriesPlotValues(series),
    series.times || [],
    chartMax,
    domain,
    debugGraphSeriesPlotHasDataValues(series),
    series.durations || [],
    gapThresholdMs,
    logScale,
    rangeAware ? noDataRanges : null,
    rangeAware ? debugGraphSeriesPlotObservedValues(series) : null,
  ).map((points, index) => {
    if (!points.length) return '';
    const segmentAttr = index > 0 ? ` data-js-debug-series-segment="${esc(index)}"` : '';
    return `<polyline class="${esc(debugGraphSeriesLineClassName(series))}" data-js-debug-series="${esc(series.key)}"${debugGraphSeriesTokenAgentAttrs(series)}${debugGraphSeriesClientAttrs(series)}${debugGraphSeriesLinePatternAttrs(series)}${segmentAttr} points="${esc(points.join(' '))}" fill="none" vector-effect="non-scaling-stroke"${debugGraphSeriesStyleAttr(series)}><title>${esc(series.fullLabel || series.label)}</title></polyline>`;
  }).join('');
}

function debugGraphAreaPathHtml(series, chartMax, domain, noDataRanges = null) {
  const values = debugGraphSeriesPlotValues(series);
  const hasDataValues = debugGraphSeriesPlotHasDataValues(series);
  const pointIndexes = values
    .map((_value, index) => index)
    .filter(index => !hasDataValues || hasDataValues[index] === true);
  if (!pointIndexes.length) return '';
  const baseline = jsDebugGraphGeometry.plotBottom;
  const lowerValues = Array.isArray(series.stackBaseValues) ? series.stackBaseValues : null;
  // Split the fill into runs broken ONLY at genuine no-data ranges, so a
  // covered-but-coarser span fills continuously (matching the line) while a real
  // recorded hole stays an honest gap under its red no-data band.
  const observedValues = debugGraphSeriesPlotObservedValues(series);
  const runs = [];
  let run = [];
  let previousEndMs = NaN;
  for (const index of pointIndexes) {
    const startMs = debugGraphSeriesTimeMs(series, index);
    const durationMs = Math.max(jsDebugGraphRawBucketMs, Number(series.durations?.[index]) || jsDebugGraphRawBucketMs);
    // Drop a HELD (non-observed) point that lands inside a genuine no-data range so
    // the fill never leaks into a real hole; a real measurement is always kept.
    const observed = !observedValues || observedValues[index] === true;
    if (!observed && debugGraphTimeInNoDataRange(noDataRanges, startMs, Number.isFinite(startMs) ? startMs + durationMs : startMs + 1)) {
      if (run.length) { runs.push(run); run = []; }
      continue;
    }
    if (run.length && debugGraphTimeInNoDataRange(noDataRanges, previousEndMs, startMs)) {
      runs.push(run);
      run = [];
    }
    run.push(index);
    previousEndMs = Number.isFinite(startMs) ? startMs + durationMs : NaN;
  }
  if (run.length) runs.push(run);
  const stacked = lowerValues ? ` data-js-debug-area-stacked="${esc(series.key)}"` : '';
  const plotCurrent = values.at(-1);
  const total = Number.isFinite(Number(plotCurrent)) ? ` data-js-debug-area-total="${esc(Number(plotCurrent))}"` : '';
  return runs.map(runIndexes => {
    const upperPoints = runIndexes.map(index => debugGraphPointForValue(values[index], debugGraphSeriesTimeMs(series, index), chartMax, domain));
    const lowerPoints = lowerValues
      ? runIndexes.map(index => debugGraphPointForValue(lowerValues[index], debugGraphSeriesTimeMs(series, index), chartMax, domain))
      : upperPoints.map(point => [point[0], baseline.toFixed(1)]);
    const firstLower = lowerPoints[0] || [upperPoints[0][0], baseline.toFixed(1)];
    const path = [
      `M ${firstLower[0]},${firstLower[1]}`,
      ...upperPoints.map(point => `L ${point[0]},${point[1]}`),
      ...lowerPoints.slice().reverse().map(point => `L ${point[0]},${point[1]}`),
      'Z',
    ].join(' ');
    return `<path class="js-debug-area js-debug-area--${esc(debugGraphSeriesClassKey(series))}" data-js-debug-area-series="${esc(series.key)}"${debugGraphSeriesTokenAgentAttrs(series)}${stacked}${total} d="${esc(path)}"${debugGraphSeriesStyleAttr(series)}><title>${esc(series.fullLabel || series.label)}</title></path>`;
  }).join('');
}

function debugGraphBarRectsHtml(series, chartMax, domain, logScale = false) {
  const values = debugGraphSeriesPlotValues(series);
  const hasDataValues = debugGraphSeriesPlotHasDataValues(series);
  const lowerValues = Array.isArray(series.stackBaseValues) ? series.stackBaseValues : null;
  const durations = Array.isArray(series.durations) ? series.durations : [];
  const classKey = debugGraphSeriesClassKey(series);
  return values.map((value, index) => {
    if (hasDataValues && hasDataValues[index] !== true) return '';
    const topValue = Math.max(0, Number(value) || 0);
    const bottomValue = Math.max(0, Number(lowerValues?.[index] || 0));
    if (topValue <= bottomValue && series.zeroBar !== true) return '';
    const startMs = debugGraphSeriesTimeMs(series, index);
    const durationMs = Math.max(1000, Number(durations[index] || jsDebugGraphAgentTokenBucketSeconds * 1000));
    const x1 = debugGraphXForTime(startMs, domain);
    // GUI-only (not a data change): draw each bar across its full DISPLAY slot — from
    // this grid point to the next — so bars look contiguous instead of thin spikes at
    // a bucket's active token duration. A ~1px gap keeps adjacent bars visually
    // distinct. The bucket's real durationMs still drives the tokens/min rate math;
    // this only affects the rectangle width. The last bar (no next point) falls back
    // to its own duration.
    const nextStartMs = debugGraphSeriesTimeMs(series, index + 1);
    const slotEndMs = Number.isFinite(nextStartMs) && nextStartMs > startMs ? nextStartMs : startMs + durationMs;
    const x2 = debugGraphXForTime(slotEndMs, domain);
    const slotWidth = Math.max(0, x2 - x1);
    // Agent status is a dense stacked band (10s slots) that reads best fully
    // contiguous, so keep it gapless; the wider token bars get up to a 1px gap
    // (always > 0 so adjacent bars stay visually distinct) so they look like
    // distinct-but-contiguous bars rather than thin spikes.
    const gap = jsDebugAgentStatusSeriesKeys.includes(series.key) ? 0 : Math.min(1, Math.max(0.1, slotWidth * 0.1));
    const x = x1 + gap / 2;
    const width = Math.max(0.5, slotWidth - gap);
    const vertical = debugGraphBarVerticalGeometry(topValue, bottomValue, chartMax, series.zeroBar === true, logScale);
    const stacked = lowerValues ? ` data-js-debug-bar-stacked="${esc(series.key)}"` : '';
    return `<rect class="js-debug-bar js-debug-bar--${esc(classKey)}" data-js-debug-bar-series="${esc(series.key)}"${debugGraphSeriesTokenAgentAttrs(series)}${stacked} data-js-debug-bar-total="${esc(topValue)}" data-js-debug-bar-gap="${esc(gap.toFixed(2))}" x="${esc(x.toFixed(2))}" y="${esc(vertical.y.toFixed(2))}" width="${esc(width.toFixed(2))}" height="${esc(vertical.height.toFixed(2))}"${debugGraphSeriesStyleAttr(series, {barPattern: true})}><title>${esc(series.fullLabel || series.label)}</title></rect>`;
  }).join('');
}

function debugGraphBarVerticalGeometry(topValue, bottomValue, chartMax, zeroBar = false, logScale = false) {
  const top = debugGraphPlotYForValue(topValue, chartMax, logScale);
  const bottom = debugGraphPlotYForValue(bottomValue, chartMax, logScale);
  const height = Math.max(0, bottom - top);
  if (height > 0 || !zeroBar) return {y: top, height};
  const zeroHeight = 0.75;
  return {y: bottom - zeroHeight, height: zeroHeight};
}

function debugGraphMovingAveragePolylineHtml(series, chartMax, domain) {
  const sampleCount = Number(series?.movingAverageSamples || 0);
  if (sampleCount <= 0) return '';
  const points = debugGraphPolylinePoints(series.movingAverageValues || [], series.movingAverageTimes || [], chartMax, domain);
  if (!points) return '';
  const title = t('debug.graph.movingAverage', {label: series.label, count: sampleCount});
  return `<polyline class="${esc(debugGraphSeriesLineClassName(series, 'js-debug-line--moving-average'))}" data-js-debug-moving-average="${esc(series.key)}"${debugGraphSeriesTokenAgentAttrs(series)}${debugGraphSeriesClientAttrs(series)}${debugGraphSeriesLinePatternAttrs(series)} data-js-debug-moving-average-samples="${esc(sampleCount)}" points="${esc(points)}" fill="none" vector-effect="non-scaling-stroke"${debugGraphSeriesStyleAttr(series)}><title>${esc(title)}</title></polyline>`;
}

function debugGraphInteractionOverlayHtml() {
  return `<rect class="js-debug-selection-rect" data-js-debug-selection-rect x="0" y="${esc(jsDebugGraphGeometry.plotTop)}" width="0" height="${esc(jsDebugGraphGeometry.plotHeight)}"></rect><line class="js-debug-hover-line" data-js-debug-hover-line x1="0" y1="${esc(jsDebugGraphGeometry.plotTop)}" x2="0" y2="${esc(jsDebugGraphGeometry.hoverBottom)}" vector-effect="non-scaling-stroke"></line>`;
}

function debugGraphLegendHtml(seriesItems) {
  return `<div class="js-debug-legend" aria-label="${esc(t('debug.summary'))}">
    ${seriesItems.map(series => {
      const descKey = series.descKey || jsDebugGraphDescriptionKeyByLabelKey[series.labelKey] || jsDebugGraphDescriptionKeyByLabelKey[series.metricLabelKey];
      return `<div class="js-debug-legend-item" data-js-debug-legend="${esc(series.key)}"${debugGraphSeriesTokenAgentAttrs(series)}${debugGraphSeriesClientAttrs(series)}>${debugGraphLegendSwatchHtml(series)}<span${debugGraphExplainAttrs(series.fullLabel || series.label, descKey, {attribute: 'data-js-debug-legend-label-desc', desc: debugGraphLocalizedDescription({...series, descKey})})}>${esc(series.label)}</span></div>`;
    }).join('')}
  </div>`;
}

function debugGraphLegendSwatchHtml(series) {
  if (series?.tokenPatternSeries === true) return debugGraphAgentTokenLegendSwatchHtml(series);
  if (series?.clientMetric === true || series?.processCpu === true || series?.key === 'systemCpu' || series?.key === 'systemMemory' || debugGraphSeriesLinePattern(series)) {
    return `<svg class="js-debug-legend-line" viewBox="0 0 18 4" aria-hidden="true"><line class="${esc(debugGraphSeriesLineClassName(series))}"${debugGraphSeriesLinePatternAttrs(series)} x1="0" y1="2" x2="18" y2="2" vector-effect="non-scaling-stroke"${debugGraphSeriesStyleAttr(series)}></line></svg>`;
  }
  return `<span class="js-debug-legend-swatch js-debug-legend-swatch--${esc(debugGraphSeriesClassKey(series))}"${debugGraphSeriesStyleAttr(series)}></span>`;
}

function debugGraphIntegerAxisValues(max) {
  const axisMax = Math.max(0, Math.ceil(Number(max) || 0));
  if (axisMax <= 0) return [0];
  const stride = axisMax <= 10 ? 1 : Math.max(1, Math.ceil(axisMax / 8));
  const values = [];
  for (let value = axisMax; value >= 0; value -= stride) values.push(value);
  if (values.at(-1) !== 0) values.push(0);
  return values;
}

function debugGraphIntegerGridValues(max) {
  const axisMax = Math.max(0, Math.ceil(Number(max) || 0));
  return Array.from({length: axisMax + 1}, (_unused, index) => axisMax - index);
}

function debugGraphIntegerAxisHtml(group, max) {
  const axisMax = Math.max(0, Math.ceil(Number(max) || 0));
  const ticks = debugGraphIntegerAxisValues(axisMax);
  return `<div class="js-debug-y-axis js-debug-y-axis--integer" data-js-debug-axis="${esc(group.key)}">
    ${ticks.map(value => {
      const marker = value === axisMax
        ? ` data-js-debug-axis-max="${esc(group.key)}"`
        : value === 0
          ? ` data-js-debug-axis-zero="${esc(group.key)}"`
          : '';
      return `<span data-js-debug-axis-tick="${esc(group.key)}" data-js-debug-axis-value="${esc(value)}"${marker}${debugGraphAxisTickStyle(value, axisMax)}>${esc(debugGraphAxisValueText(value, group.unit))}</span>`;
    }).join('')}
  </div>`;
}

function debugGraphGridLineY(value, chartMax, logScale = false) {
  return debugGraphPlotYForValue(value, chartMax, logScale);
}

function debugGraphAxisTickStyle(value, chartMax, logScale = false) {
  const percent = (debugGraphGridLineY(value, chartMax, logScale) / jsDebugGraphGeometry.height) * 100;
  return ` style="--js-debug-axis-y: ${esc(percent.toFixed(3))}%;"`;
}

function debugGraphGridLinesHtml(group, axisMax) {
  const max = Math.max(0, Number(axisMax) || 0);
  const fallbackMax = max > 0 ? max : 1;
  const scale = group.scale ?? (group.logScale === true);
  const values = group.integerGridLines === true
    ? debugGraphIntegerGridValues(max)
    : scale?.mode === 'broken-linear'
    ? [fallbackMax, scale.threshold, scale.threshold / 2, 0]
    : scale === true
    ? [fallbackMax, Math.expm1(Math.log1p(fallbackMax) / 2), 0]
    : [fallbackMax, fallbackMax / 2, 0];
  return values.map(value => {
    const y = debugGraphGridLineY(value, max, scale).toFixed(1);
    const axisValue = group.integerGridLines === true ? ` data-js-debug-grid-value="${esc(value)}"` : '';
    return `<line class="js-debug-grid-line${group.integerGridLines === true ? ' js-debug-grid-line--integer' : ''}" data-js-debug-grid-line="${esc(group.key)}"${axisValue} x1="0" y1="${esc(y)}" x2="${esc(jsDebugGraphGeometry.width)}" y2="${esc(y)}" vector-effect="non-scaling-stroke"></line>`;
  }).join('');
}

function debugGraphAxisBreakHtml(group, axisMax, scale) {
  if (scale?.mode !== 'broken-linear') return '';
  const y = debugGraphGridLineY(scale.threshold, axisMax, scale);
  const left = `M0 ${y - 2}l4 4 4-4 4 4`;
  const rightX = jsDebugGraphGeometry.width - 12;
  const right = `M${rightX} ${y - 2}l4 4 4-4 4 4`;
  return `<path class="js-debug-axis-break" data-js-debug-axis-break="${esc(group.key)}" data-js-debug-axis-break-value="${esc(scale.threshold)}" d="${esc(`${left} ${right}`)}" fill="none" vector-effect="non-scaling-stroke"></path>`;
}

function debugGraphAxisHtml(group, max) {
  const axisMax = Math.max(0, Number(max) || 0);
  if (group.integerAxis === true) return debugGraphIntegerAxisHtml(group, axisMax);
  const positionMax = axisMax > 0 ? axisMax : 1;
  const scale = group.scale ?? (group.logScale === true);
  if (scale?.mode === 'broken-linear') {
    const threshold = Math.min(positionMax, Number(scale.threshold) || positionMax);
    return `<div class="js-debug-y-axis js-debug-y-axis--broken" data-js-debug-axis="${esc(group.key)}" data-js-debug-axis-break="${esc(threshold)}">
      <span data-js-debug-axis-max="${esc(group.key)}"${debugGraphAxisTickStyle(positionMax, positionMax, scale)}>${esc(debugGraphAxisValueText(axisMax, group.unit))}</span>
      <span data-js-debug-axis-break-label="${esc(group.key)}"${debugGraphAxisTickStyle(threshold, positionMax, scale)}>${esc(debugGraphAxisValueText(threshold, group.unit))}</span>
      <span data-js-debug-axis-mid="${esc(group.key)}"${debugGraphAxisTickStyle(threshold / 2, positionMax, scale)}>${esc(debugGraphAxisValueText(threshold / 2, group.unit))}</span>
      <span data-js-debug-axis-zero="${esc(group.key)}"${debugGraphAxisTickStyle(0, positionMax, scale)}>${esc(debugGraphAxisValueText(0, group.unit))}</span>
    </div>`;
  }
  return `<div class="js-debug-y-axis" data-js-debug-axis="${esc(group.key)}">
    <span data-js-debug-axis-max="${esc(group.key)}"${debugGraphAxisTickStyle(positionMax, positionMax, scale)}>${esc(debugGraphAxisValueText(axisMax, group.unit))}</span>
    <span data-js-debug-axis-mid="${esc(group.key)}"${debugGraphAxisTickStyle(scale === true ? Math.expm1(Math.log1p(positionMax) / 2) : positionMax / 2, positionMax, scale)}>${esc(debugGraphAxisValueText(scale === true ? Math.expm1(Math.log1p(axisMax) / 2) : axisMax / 2, group.unit))}</span>
    <span data-js-debug-axis-zero="${esc(group.key)}"${debugGraphAxisTickStyle(0, positionMax, scale)}>${esc(debugGraphAxisValueText(0, group.unit))}</span>
  </div>`;
}

function debugGraphXAxisHtml(domain) {
  const startMs = Number(domain?.startMs);
  const endMs = Number(domain?.endMs);
  if (!Number.isFinite(startMs) || !Number.isFinite(endMs)) return '';
  const ticks = [
    {name: 'start', ms: startMs},
    {name: 'mid', ms: startMs + ((endMs - startMs) / 2)},
    {name: 'end', ms: endMs},
  ];
  const includeDate = debugGraphLocalDateKey(startMs) !== debugGraphLocalDateKey(endMs);
  // Show seconds when the chart is actually rendering at 1-second resolution — where
  // the data (and the wall-clock slide) genuinely tick every second — regardless of the
  // range's span. Coarser resolutions (10s/60s/300s) show HH:MM because a seconds digit
  // there is fake precision. Keyed off the same effective-resolution owner the Resolution
  // label reads, not a span proxy.
  const resolutionSeconds = debugGraphDisplayResolutionMs(domain, 0, Date.now()) / 1000;
  const includeSeconds = !includeDate && resolutionSeconds <= 1;
  return `<div class="js-debug-x-axis" data-js-debug-x-axis>
    ${ticks.map(tick => `<span data-js-debug-x-tick="${esc(tick.name)}"${includeDate ? ` data-js-debug-x-date="${esc(debugGraphLocalDateKey(tick.ms))}"` : ''}>${esc(debugGraphTimeLabel(tick.ms, {includeDate, includeSeconds}))}</span>`).join('')}
  </div>`;
}

function debugGraphGroupSeriesItems(group, seriesItems) {
  if (group.serviceLoad === true) return seriesItems.filter(series => series.serviceLoad === true);
  if (group.dynamicAgentTokens === true) return seriesItems.filter(series => series.agentTokenSeries === true);
  if (group.dynamicTokenDimension) return seriesItems.filter(series => series.tokenDimension === group.dynamicTokenDimension);
  if (group.hostMetric) {
    const hostSeries = seriesItems.filter(series => series.hostMetric === group.hostMetric);
    if (group.hostMetric === 'cpu') {
      return seriesItems.filter(series => series.processCpu === true || series.key === 'cpu' || series.key === 'systemCpu');
    }
    if (hostSeries.length || group.hostMetric !== 'cpu') {
      return [...hostSeries, ...seriesItems.filter(series => group.hostMetric === 'memory' && series.key === 'systemMemory')];
    }
    // Existing history predates host process sampling. Keep its per-YOLOmux CPU lines readable
    // until those one-second buckets age out instead of rendering an empty CPU chart.
    return seriesItems.filter(series => series.processCpu === true || series.key === 'cpu' || series.key === 'systemCpu');
  }
  const seriesKeys = new Set(group.series);
  return seriesItems.filter(series => seriesKeys.has(series.chartMetricKey || (series.clientMetric === true ? series.metricKey : series.key)));
}

function debugGraphMacMemoryCardAvailable(buckets) {
  return (buckets || []).some(bucket => Number(bucket?.hostMetrics?.macMemoryDetailCount || 0) > 0);
}

function debugGraphResolvedChartGroup(group, buckets) {
  if (group?.key !== 'memory' || !debugGraphMacMemoryCardAvailable(buckets)) return group;
  return {
    ...group,
    label: 'Memory pressure',
    labelKey: '',
    desc: 'macOS memory pressure with Activity Monitor-style memory facts. Physical allocation and cached files do not by themselves mean the Mac is out of memory.',
    descKey: '',
    series: ['macMemoryPressure'],
    unit: 'percent',
    fixedMax: 100,
    kind: 'area',
    stacked: false,
    capacityMetric: '',
    hostMetric: '',
    macMemoryCard: true,
  };
}

function debugGraphMacMemoryDetailsHtml(buckets) {
  const bucket = [...(buckets || [])].reverse().find(item => Number(item?.hostMetrics?.macMemoryDetailCount || 0) > 0);
  const host = bucket?.hostMetrics;
  if (!host) return '';
  const count = Math.max(1, Number(host.macMemoryDetailCount || 0));
  const facts = [
    ['Physical Memory', 'macPhysicalMemoryTotalBytes'], ['Memory Used', 'macMemoryUsedTotalBytes'], ['Cached Files', 'macCachedFilesTotalBytes'],
    ['Swap Used', 'macSwapUsedTotalBytes'], ['App Memory', 'macAppMemoryTotalBytes'], ['Wired Memory', 'macWiredMemoryTotalBytes'], ['Compressed', 'macCompressedMemoryTotalBytes'],
  ];
  return `<dl class="js-debug-mac-memory-details" data-js-debug-mac-memory-details>${facts.map(([label, key]) => {
    const value = Number(host[key]);
    const text = Number.isFinite(value) ? debugGraphValueText(value / count, 'bytes') : '—';
    return `<div><dt>${esc(label)}</dt><dd>${esc(text)}</dd></div>`;
  }).join('')}</dl>`;
}

function debugGraphLegendSeriesItems(group, groupSeries) {
  const legendKeys = Array.isArray(group?.legendSeries) ? group.legendSeries : null;
  if (!legendKeys) return groupSeries;
  const seriesByKey = new Map(groupSeries.map(series => [series.key, series]));
  return legendKeys.map(key => seriesByKey.get(key)).filter(Boolean);
}

function debugGraphVisibleChartGroups(seriesItems) {
  return jsDebugGraphChartGroups.filter(group => {
    if (!debugGraphChartVisible(group.key)) return false;
    if (group.optional !== true) return true;
    return debugGraphGroupSeriesItems(group, seriesItems).some(series => Number(series?.samples || 0) > 0);
  });
}

function debugGraphStackedSeries(seriesItems) {
  const count = Math.max(0, ...seriesItems.map(series => (series.values || []).length));
  const totals = Array.from({length: count}, () => 0);
  return seriesItems.map(series => {
    const values = series.values || [];
    const stackBaseValues = totals.slice();
    const plotValues = values.map((value, index) => {
      const next = totals[index] + Math.max(0, Number(value) || 0);
      totals[index] = next;
      return next;
    });
    return {
      ...series,
      plotValues,
      stackBaseValues,
      plotHasDataValues: series.hasDataValues || null,
      plotMax: Math.max(0, ...plotValues),
    };
  });
}

function debugGraphChartAxisMax(group, rawMax) {
  const fixedMax = Number(group.fixedMax);
  if (Number.isFinite(fixedMax) && fixedMax > 0) return fixedMax;
  const minimumAxisMax = Math.max(0, Number(group.minimumAxisMax) || 0);
  if (group.exactIntegerAxisMax === true) return Math.max(minimumAxisMax, Math.ceil(Number(rawMax) || 0));
  return debugGraphNiceAxisMax(rawMax, group.unit);
}

function debugGraphChartCapacityMax(group, buckets) {
  if (group.capacityMetric === 'systemMemory') {
    return Math.max(0, ...(buckets || []).map(bucket => {
      const host = bucket?.hostMetrics;
      return Number(host?.systemMemoryCount || 0) > 0
        ? Number(host.systemMemoryCapacityTotalBytes || 0) / Number(host.systemMemoryCount || 1)
        : 0;
    }));
  }
  if (group.capacityMetric === 'gpuMemory') {
    return Math.max(0, ...(buckets || []).map(bucket => {
      if (!(bucket?.hostMetrics?.gpuDevices instanceof Map)) return 0;
      let total = 0;
      for (const item of bucket.hostMetrics.gpuDevices.values()) {
        if (Number(item?.samples || 0) > 0) total += Number(item.memoryCapacityTotalBytes || 0) / Number(item.samples || 1);
      }
      return total;
    }));
  }
  return 0;
}

function debugGraphBucketsForChartGroup(group, defaultBuckets, nowMs = Date.now()) {
  if (group?.key === 'agentTokens' || group?.key === 'modelTokens') return debugGraphAgentTokenDisplayBuckets(nowMs);
  const bucketSeconds = Number(group?.bucketSeconds);
  if (Number.isFinite(bucketSeconds) && bucketSeconds > 0) {
    return debugGraphDisplayBuckets(nowMs, {minimumResolutionSeconds: bucketSeconds, rangeSeconds: debugRuntimeState.graphRangeSeconds});
  }
  return defaultBuckets;
}

function debugGraphHoverBucketIndex(buckets, timestamp) {
  let low = 0;
  let high = buckets.length - 1;
  let index = -1;
  while (low <= high) {
    const middle = Math.floor((low + high) / 2);
    if (Number(buckets[middle]?.startMs) <= timestamp) {
      index = middle;
      low = middle + 1;
    } else {
      high = middle - 1;
    }
  }
  if (index < 0) return -1;
  const bucket = buckets[index];
  const end = Number(bucket?.startMs) + Math.max(1, Number(bucket?.durationMs) || 0);
  return timestamp < end ? index : -1;
}

function debugGraphServiceLoadHoverSeriesAtTime(chart, timestamp, event) {
  const data = jsDebugGraphHoverChartData.get(String(chart?.dataset?.jsDebugChart || ''));
  if (data?.group?.key !== 'serversLoad') return null;
  const index = debugGraphHoverBucketIndex(data.buckets, timestamp);
  if (index < 0) return null;
  const available = data.groupSeries.filter(series => !Array.isArray(series.hasDataValues) || series.hasDataValues[index] === true);
  if (!available.length) return null;
  const directKey = String(event?.target?.closest?.('[data-js-debug-series]')?.dataset?.jsDebugSeries || '');
  const direct = available.find(series => series.key === directKey);
  if (direct) return direct;
  const svg = chart?.querySelector?.('.js-debug-line-chart');
  const rect = svg?.getBoundingClientRect?.();
  const clientY = Number(event?.clientY);
  if (!Number.isFinite(clientY) || !Number.isFinite(Number(rect?.top)) || !Number.isFinite(Number(rect?.height)) || Number(rect.height) <= 0) return available[0];
  const pointerY = ((clientY - Number(rect.top)) / Number(rect.height)) * jsDebugGraphGeometry.height;
  const axisMax = Math.max(1, Number(chart?.dataset?.jsDebugChartAxisMax) || 0);
  const scaleName = String(chart?.dataset?.jsDebugChartScale || 'linear');
  const scale = scaleName === 'broken-linear'
    ? {mode: 'broken-linear', threshold: Number(chart?.dataset?.jsDebugChartAxisBreak) || axisMax, upperFraction: 0.18}
    : scaleName === 'log';
  return available.reduce((nearest, series) => {
    const startValue = Math.max(0, Number(series.values?.[index]) || 0);
    const startTime = Number(series.times?.[index]);
    const nextIndex = index + 1;
    const nextAvailable = nextIndex < series.values.length
      && (!Array.isArray(series.hasDataValues) || series.hasDataValues[nextIndex] === true);
    const nextTime = Number(series.times?.[nextIndex]);
    const nextValue = Math.max(0, Number(series.values?.[nextIndex]) || 0);
    const fraction = nextAvailable && Number.isFinite(startTime) && Number.isFinite(nextTime) && nextTime > startTime
      ? Math.max(0, Math.min(1, (Number(timestamp) - startTime) / (nextTime - startTime)))
      : 0;
    const renderedValue = startValue + ((nextValue - startValue) * fraction);
    const distance = Math.abs(debugGraphPlotYForValue(renderedValue, axisMax, scale) - pointerY);
    return !nearest || distance < nearest.distance ? {series, distance} : nearest;
  }, null)?.series || available[0];
}

function debugGraphHoverDetailAtTime(chart, timestamp, event) {
  const key = String(chart?.dataset?.jsDebugChart || '');
  const data = jsDebugGraphHoverChartData.get(key);
  if (data?.group?.key === 'serversLoad') {
    const index = debugGraphHoverBucketIndex(data.buckets, timestamp);
    const series = debugGraphServiceLoadHoverSeriesAtTime(chart, timestamp, event);
    if (index < 0 || !series) return {text: debugGraphValueText(0, data.group.unit), seriesKey: ''};
    return {
      text: `${series.label}: ${debugGraphValueText(series.values?.[index], 'percent')}`,
      seriesKey: series.key,
    };
  }
  return {text: debugGraphHoverValueAtTime(chart, timestamp), seriesKey: ''};
}

function debugGraphHoverValueAtTime(chart, timestamp) {
  const key = String(chart?.dataset?.jsDebugChart || '');
  const data = jsDebugGraphHoverChartData.get(key);
  if (!data) return debugGraphValueText(0, chart?.dataset?.jsDebugChartUnit);
  const index = debugGraphHoverBucketIndex(data.buckets, timestamp);
  if (index < 0) return debugGraphValueText(0, data.group.unit);
  const series = data.group.key === 'activity'
    ? data.groupSeries.filter(item => item.key !== 'idleAgents')
    : data.groupSeries;
  const values = series
    .filter(item => !Array.isArray(item.hasDataValues) || item.hasDataValues[index] === true)
    .map(item => Math.max(0, Number(item.values?.[index]) || 0));
  const value = data.group.stacked === true
    ? values.reduce((total, item) => total + item, 0)
    : Math.max(0, ...values);
  return debugGraphValueText(value, data.group.unit);
}

function debugGraphTokenHoverDetailAtTime(chart, timestamp) {
  const key = String(chart?.dataset?.jsDebugChart || '');
  const data = jsDebugGraphHoverChartData.get(key);
  if (!data || !['agentTokens', 'modelTokens'].includes(data.group?.key)) return null;
  const index = debugGraphHoverBucketIndex(data.buckets, timestamp);
  const bucket = index >= 0 ? data.buckets[index] : null;
  const startMs = Number(bucket?.startMs);
  const endMs = startMs + Math.max(1, Number(bucket?.durationMs) || 0);
  const hoveredTime = debugGraphTimeLabel(timestamp, {includeSeconds: false});
  const span = Number.isFinite(startMs)
    ? `${debugGraphTimeLabel(startMs, {includeSeconds: false})}–${debugGraphTimeLabel(endMs, {includeSeconds: false})}`
    : hoveredTime;
  if (index < 0) return {span, detail: debugGraphCostText('debug.graph.tokens.noData', 'No token samples'), noData: true};
  const activeSeries = data.groupSeries
    .filter(series => !data.group.dynamicTokenDimension || series.tokenDimension === data.group.dynamicTokenDimension)
    .filter(series => !Array.isArray(series.hasDataValues) || series.hasDataValues[index] === true);
  const sampleCount = activeSeries.reduce((total, series) => {
    const provenance = Array.isArray(series.provenanceValues) ? series.provenanceValues[index] : null;
    return total + Math.max(0, Number(provenance?.sampleCount) || 0);
  }, 0);
  if (!activeSeries.length || sampleCount <= 0) {
    return {span, detail: debugGraphCostText('debug.graph.tokens.noData', 'No token samples'), noData: true};
  }
  const value = debugGraphHoverValueAtTime(chart, timestamp);
  const sampleLabel = sampleCount === 1 ? 'sample' : 'samples';
  return {span, detail: `${value} · ${debugSystemNumber(sampleCount)} ${sampleLabel}`, noData: false};
}

function debugGraphHoverProvenanceAtTime(chart, timestamp, seriesKey = '') {
  const key = String(chart?.dataset?.jsDebugChart || '');
  const data = jsDebugGraphHoverChartData.get(key);
  if (!data) return [];
  const index = debugGraphHoverBucketIndex(data.buckets, timestamp);
  if (index < 0) return [];
  return data.groupSeries.flatMap(series => {
    if (seriesKey && series.key !== seriesKey) return [];
    if (Array.isArray(series.hasDataValues) && series.hasDataValues[index] !== true) return [];
    const provenance = Array.isArray(series.provenanceValues) ? series.provenanceValues[index] : null;
    return provenance ? [{series: series.key, ...provenance}] : [];
  });
}

function debugGraphHeldProvenanceText(provenance) {
  const held = (provenance || []).filter(item => item?.held === true && Number.isFinite(Number(item.sampleTimeMs)));
  if (!held.length) return '';
  const sampleTimeMs = Math.max(...held.map(item => Number(item.sampleTimeMs)));
  const sampleCount = held
    .filter(item => Number(item.sampleTimeMs) === sampleTimeMs)
    .reduce((total, item) => total + Math.max(0, Number(item.sampleCount) || 0), 0);
  return `↳ ${debugGraphExactTimeLabel(sampleTimeMs)} · n=${debugSystemNumber(sampleCount)}`;
}

function debugGraphLivePulseHtml(groupSeries, buckets, domain, nowMs = Date.now()) {
  if (domain?.zoomed || Number(domain?.rangeSeconds) > 3600) return '';
  const domainEnd = Number(domain?.endMs);
  if (!Number.isFinite(domainEnd) || nowMs > domainEnd + 1000) return '';
  // The live edge is the cell that contains "now" at this chart's own display
  // resolution. Mark it on EVERY live chart whether or not a sample has landed
  // in it yet: sparse charts (agent/model tokens) have no data bucket at the
  // edge but are still live, so the shared heartbeat must appear there too. The
  // pulse only ever marks this one ongoing cell, never a gap, and its paint is
  // driven solely by the shared agent-status opacity clock.
  const durationMs = Math.max(1, Number(buckets?.at?.(-1)?.durationMs) || debugGraphBucketDurationForTime(nowMs, nowMs));
  const startMs = Math.floor(nowMs / durationMs) * durationMs;
  const xStart = debugGraphXForTime(startMs, domain);
  const xLimit = debugGraphXForTime(startMs + durationMs, domain);
  const width = Math.max(0.5, xLimit - xStart);
  return `<rect class="js-debug-live-pulse heartbeat-pulse" data-js-debug-live-pulse x="${esc(xStart)}" y="0" width="${esc(width)}" height="${esc(jsDebugGraphGeometry.height)}" pointer-events="none"></rect>`;
}

function debugGraphLiveAgentWindowRows() {
  const rows = [];
  const seen = new Set();
  const revisions = new Set();
  const sessionRevisions = new Map();
  for (const [session, payload] of autoApproveStates.entries()) {
    const revision = agentWindowSnapshotRevision(payload);
    if (revision > 0) revisions.add(revision);
    sessionRevisions.set(String(session), revision);
    for (const agent of agentWindowPayloadRows(payload?.agent_windows)) {
      const kind = agentWindowKind(agent?.kind);
      const physical = agentWindowPhysicalKey(agent);
      if (!kind || !physical) continue;
      const key = `${session}\u0000${physical}\u0000${kind}`;
      if (seen.has(key)) continue;
      seen.add(key);
      rows.push({session, agent, kind, revision});
    }
  }
  rows.sort((left, right) => String(left.session).localeCompare(String(right.session))
    || (agentWindowIndex(left.agent) ?? Number.MAX_SAFE_INTEGER) - (agentWindowIndex(right.agent) ?? Number.MAX_SAFE_INTEGER)
    || left.kind.localeCompare(right.kind));
  return {rows, revisions, sessionRevisions};
}

function debugGraphLiveAgentWindowDetailHtml(groupKey = 'activity') {
  const chartRevision = Number(jsDebugStatsPollState.agentWindowSnapshotRevision) || 0;
  const {rows, revisions, sessionRevisions} = debugGraphLiveAgentWindowRows();
  if (!chartRevision) {
    return `<div class="js-debug-agent-window-detail" data-js-debug-agent-window-detail="${esc(groupKey)}" data-js-debug-agent-window-detail-state="changed">${esc('Live status is waiting for the chart snapshot')}</div>`;
  }
  // A live status payload may advance after the chart's accepted snapshot while
  // the next stats poll is in flight. Newer rows are still authoritative live
  // truth; only rows older than the chart (or missing a revision) are stale.
  const currentRows = rows.filter(row => row.revision >= chartRevision);
  const staleSessions = [...new Set(rows
    .filter(row => row.revision < chartRevision)
    .map(row => String(row.session)))]
    .sort((left, right) => left.localeCompare(right))
    .map(session => ({session, revision: Number(sessionRevisions.get(session)) || 0}));
  // A stale revision means these rows may be old, not that the known roster is empty.
  // Keep the count and breakdown honest while the per-session stale text explains freshness.
  const sessions = new Set(rows.map(row => row.session));
  // ONE compact summary carries the stale count; the per-session stale specifics move under the
  // Live breakdown rather than being repeated as header prose.
  const summary = `${rows.length} agent windows across ${sessions.size} sessions${staleSessions.length ? ` (${staleSessions.length} stale)` : ''}`;
  const details = rows.map(({session, agent, kind}) => {
    const label = agentWindowCanonicalLabel(agentWindowIndex(agent), kind, kind);
    const state = agentWindowStateKey(agent?.state);
    return `<li>${esc(session)} → ${esc(label)} → ${esc(kind)} → ${esc(state)}</li>`;
  }).join('');
  const stalePerSession = staleSessions.length
    ? `<ul class="js-debug-agent-window-detail-stale">${staleSessions.map(item => `<li>${esc(`${item.session} status is stale (rev ${item.revision || 'missing'} vs ${chartRevision})`)}</li>`).join('')}</ul>`
    : '';
  const state = staleSessions.length ? 'stale' : 'current';
  return `<div class="js-debug-agent-window-detail" data-js-debug-agent-window-detail="${esc(groupKey)}" data-js-debug-agent-window-detail-state="${state}"><span>${esc(summary)}</span><details><summary>${esc('Live breakdown')}</summary><ul>${details}</ul>${stalePerSession}</details></div>`;
}

function refreshDebugAgentWindowLiveDetails() {
  for (const detail of document.querySelectorAll('[data-js-debug-agent-window-detail]')) {
    detail.outerHTML = debugGraphLiveAgentWindowDetailHtml(detail.dataset.jsDebugAgentWindowDetail || 'activity');
  }
}

function debugGraphChartHtml(group, seriesItems, domain, buckets = [], overlayBuckets = buckets, disconnectedRanges = null, options = {}) {
  group = debugGraphResolvedChartGroup(group, buckets);
  const groupLabel = debugGraphChartLabel(group, buckets);
  const groupTitleAttrs = debugGraphExplainAttrs(groupLabel, group.descKey, {attribute: 'data-js-debug-chart-desc'});
  const groupSeries = debugGraphGroupSeriesItems(group, seriesItems);
  jsDebugGraphHoverChartData.set(group.key, {buckets, group, groupSeries});
  // Series lines/areas stay continuous across every covered span and break only
  // at these genuine no-data ranges (the same holes painted as red no-data bands).
  const genuineNoDataRanges = debugGraphChartGenuineNoDataRanges(group, domain, overlayBuckets, disconnectedRanges, groupSeries);
  const legendSeries = debugGraphLegendSeriesItems(group, groupSeries);
  const plottedGroupSeries = groupSeries.filter(series => series.movingAverageOnly !== true && series.overlayLineOnly !== true);
  const overlayLineSeries = groupSeries.filter(series => series.overlayLineOnly === true);
  const areaSeries = group.kind === 'area' ? plottedGroupSeries.filter(series => series.hostMetric && series.hostProcessId) : [];
  const lineSeries = group.kind === 'area' ? plottedGroupSeries.filter(series => !areaSeries.includes(series)) : plottedGroupSeries;
  const plotSeries = group.kind === 'area'
    ? debugGraphStackedSeries(areaSeries)
    : (group.stacked === true ? debugGraphStackedSeries(plottedGroupSeries) : plottedGroupSeries);
  // Both subviews stay mounted so switching modes preserves their DOM. Namespace the
  // SVG paint-server IDs by surface; otherwise Cost bars resolve Graphs' now-hidden
  // <pattern> definitions and become invisible even though both views share the data.
  const patternScope = String(options.patternScope || `graphs-${group.key}`).replace(/[^A-Za-z0-9_-]/g, '-');
  const scopedPatternSeries = series => ({...series, agentTokenPatternScope: patternScope});
  const renderedLegendSeries = legendSeries.map(scopedPatternSeries);
  const renderedPlotSeries = plotSeries.map(scopedPatternSeries);
  const spikeAxis = (group.key === 'agentTokens' || group.key === 'modelTokens')
    ? options.spikeAxis
    : (group.key === 'serversLoad' ? debugGraphSpikeCompressedAxisDescriptor(group, plotSeries.flatMap(debugGraphSeriesPlotValues)) : null);
  const plotScale = spikeAxis?.scale || debugGraphUsesLogScale(group, plotSeries);
  const movingAverageSeries = groupSeries.filter(series => Number(series.movingAverageSamples || 0) > 0);
  const rawMax = Math.max(0, ...plotSeries.map(series => Number(series.plotMax ?? series.max) || 0), ...lineSeries.map(series => Number(series.max) || 0), debugGraphChartCapacityMax(group, buckets));
  const max = spikeAxis ? spikeAxis.axisMax : debugGraphChartAxisMax(group, rawMax);
  const axisMax = max > 0 ? max : 0;
  const chartClasses = ['js-debug-chart'];
  if (group.dynamicAgentTokens === true || group.dynamicTokenDimension) chartClasses.push('js-debug-chart--token-agents');
  if (group.macMemoryCard === true) chartClasses.push('js-debug-chart--mac-memory');
  const bucketSeconds = Number(group.bucketSeconds);
  const bucketAttr = Number.isFinite(bucketSeconds) && bucketSeconds > 0 ? ` data-js-debug-chart-bucket-seconds="${esc(bucketSeconds)}"` : '';
  const displayedSummary = debugGraphDisplayedSummary(group, buckets);
  const displayedSummaryHtml = displayedSummary === null
    ? ''
    : `<span class="js-debug-chart-summary"${debugGraphExplainAttrs(displayedSummary.text, displayedSummary.descKey, {attribute: 'data-js-debug-summary-desc'})} data-js-debug-${esc(displayedSummary.attribute)}="${esc(displayedSummary.value)}">${esc(displayedSummary.text)}</span>`;
  const gpuUnavailable = (group.hostMetric === 'gpuUtil' || group.hostMetric === 'gpuMemory') && !groupSeries.length;
  // A GPU chart with no device series must explain itself precisely, never the ambiguous
  // generic "None" (screenshot 010): distinguish a host with NO GPU telemetry at all from
  // one whose samples exist outside the current window.
  const gpuUnavailableText = gpuUnavailable
    ? (debugGraphAnyGpuDeviceSamplesCached()
      ? debugGraphCostText('debug.graph.gpuNoWindowSamples', 'No GPU samples in this time window')
      : debugGraphCostText('debug.graph.gpuUnavailableHost', 'GPU telemetry is not available on this host'))
    : '';
  const chartUnavailable = gpuUnavailable;
  const chartUnavailableText = gpuUnavailableText;
  const scaleAttr = plotScale?.mode === 'broken-linear' ? 'broken-linear' : (plotScale === true ? 'log' : 'linear');
  const breakAttr = plotScale?.mode === 'broken-linear' ? ` data-js-debug-chart-axis-break="${esc(plotScale.threshold)}"` : '';
  return `<section class="${esc(chartClasses.join(' '))}" data-js-debug-chart="${esc(group.key)}" data-js-debug-chart-kind="${esc(group.kind || 'line')}" data-js-debug-chart-axis-max="${esc(axisMax)}" data-js-debug-chart-unit="${esc(group.unit || '')}"${spikeAxis && (group.key === 'agentTokens' || group.key === 'modelTokens') ? ' data-js-debug-token-axis="shared"' : ''}${breakAttr}${bucketAttr}${group.stacked === true ? ' data-js-debug-chart-stacked="true"' : ''} data-js-debug-chart-scale="${esc(scaleAttr)}">
      <div class="js-debug-chart-head">
      <div class="js-debug-chart-heading-row">
        <span class="js-debug-chart-title"${groupTitleAttrs}>${esc(groupLabel)}</span>
        ${group.key === 'serversLoad' ? debugGraphServiceLoadModeControlsHtml(buckets) : displayedSummaryHtml}
        <button type="button" class="js-debug-chart-close control-active-hover" data-js-debug-chart-close="${esc(group.key)}" aria-label="${esc(t('common.close'))} ${esc(groupLabel)}" title="${esc(t('common.close'))}">×</button>
      </div>
      ${group.key === 'activity' ? debugGraphLiveAgentWindowDetailHtml(group.key) : ''}
      ${chartUnavailable ? '' : debugGraphLegendHtml(renderedLegendSeries)}
      ${group.macMemoryCard === true ? debugGraphMacMemoryDetailsHtml(buckets) : ''}
    </div>
    ${chartUnavailable ? `<div class="js-debug-chart-unavailable"${gpuUnavailable ? ` data-js-debug-gpu-unavailable="${esc(group.key)}"` : ' data-js-debug-agent-billable-unavailable'}>${esc(chartUnavailableText)}</div>` : `<div class="js-debug-chart-body">
      ${debugGraphAxisHtml({...group, scale: plotScale}, axisMax)}
      <div class="js-debug-plot">
        <svg class="js-debug-line-chart" viewBox="0 0 ${esc(jsDebugGraphGeometry.width)} ${esc(jsDebugGraphGeometry.height)}" role="img" aria-label="${esc(groupLabel)}" preserveAspectRatio="none">
          ${group.kind === 'bar' ? debugGraphAgentTokenPatternDefsHtml(renderedPlotSeries) : ''}
          ${group.kind === 'area' ? plotSeries.map(series => debugGraphAreaPathHtml(series, Math.max(axisMax, 1), domain, genuineNoDataRanges)).join('') : ''}
          ${group.kind === 'bar' ? renderedPlotSeries.map(series => debugGraphBarRectsHtml({...series, zeroBar: group.zeroBar === true}, Math.max(axisMax, 1), domain, plotScale)).join('') : ''}
          ${debugGraphGridLinesHtml({...group, scale: plotScale}, axisMax)}
          ${plotScale?.mode === 'broken-linear' ? debugGraphAxisBreakHtml(group, axisMax, plotScale) : ''}
          ${group.noDataOverlay === true ? debugGraphNoDataRectsHtml(overlayBuckets, domain, debugGraphCurrentClientSeriesItems(groupSeries)) : ''}
          ${group.statusNoDataOverlay === true ? debugGraphAgentStatusNoDataRectsHtml(overlayBuckets, domain) : ''}
          ${debugGraphHistoryCoverageGapRectsHtml(group, domain, group.statusNoDataOverlay === true ? debugGraphAgentStatusNoDataRuns(overlayBuckets, domain) : [])}
          ${group.kind === 'bar' ? '' : (group.kind === 'area' ? lineSeries : plotSeries).map(series => debugGraphPolylineHtml(series, Math.max(axisMax, 1), domain, plotScale, genuineNoDataRanges)).join('')}
          ${overlayLineSeries.map(series => debugGraphPolylineHtml(series, Math.max(axisMax, 1), domain, plotScale, genuineNoDataRanges)).join('')}
          ${movingAverageSeries.map(series => debugGraphMovingAveragePolylineHtml(series, Math.max(axisMax, 1), domain)).join('')}
          ${debugGraphLivePulseHtml(groupSeries, buckets, domain)}
          ${group.disconnectedOverlay === true ? debugGraphDisconnectedRectsHtml(overlayBuckets, domain, disconnectedRanges) : ''}
          ${debugGraphInteractionOverlayHtml()}
        </svg>
      </div>
      ${debugGraphXAxisHtml(domain)}
    </div>`}
    ${chartUnavailable ? '' : '<div class="js-debug-hover-tooltip" data-js-debug-hover-tooltip hidden><span data-js-debug-hover-max></span><span aria-hidden="true"> · </span><time data-js-debug-hover-time></time><span data-js-debug-hover-source-separator aria-hidden="true" hidden> · </span><span data-js-debug-hover-source hidden></span></div>'}
  </section>`;
}

function debugGraphUsesLogScale(group, seriesItems) {
  const candidates = (seriesItems || []).flatMap(series => series.plotValues || series.values || []);
  const values = candidates.map(Number).filter(value => Number.isFinite(value) && value > 0);
  if (!values.length) return false;
  const max = Math.max(...values);
  if (group?.key === 'latency') return max > 1000;
  return false;
}

function debugGraphSpikeCompressedAxisDescriptor(group, candidates) {
  const values = (candidates || []).map(Number).filter(value => Number.isFinite(value) && value > 0);
  const rawMax = Math.max(0, ...values);
  const axisMax = debugGraphChartAxisMax(group, rawMax);
  const sorted = [...values].sort((left, right) => left - right);
  const normalMax = sorted.length >= 8 ? sorted[Math.floor((sorted.length - 1) * 0.9)] : rawMax;
  const threshold = debugGraphChartAxisMax(group, normalMax);
  const peakCount = sorted.filter(value => value > threshold).length;
  const broken = sorted.length >= 8
    && peakCount > 0
    && peakCount <= Math.max(1, Math.ceil(sorted.length * 0.1))
    && rawMax >= Math.max(threshold * 2.5, normalMax * 3)
    && threshold < axisMax;
  return Object.freeze({
    axisMax,
    scale: broken ? Object.freeze({mode: 'broken-linear', threshold, upperFraction: 0.18}) : false,
  });
}

function debugGraphTokenSpikeAxisDescriptor(buckets) {
  const values = (buckets || []).map(bucket => {
    let total = 0;
    for (const rate of bucket?.agentTokenRates?.values?.() || []) {
      total += debugGraphAgentTokenBucketValue(bucket, rate);
    }
    // Model input/cache can legitimately exceed generated output. The shared
    // descriptor must include the selected Model chart while remaining one
    // exact axis for both token charts.
    return Math.max(total, debugGraphSelectedModelTokenBucketValue(bucket));
  }).filter(value => Number.isFinite(value) && value > 0);
  const group = jsDebugGraphChartGroups.find(item => item.key === 'agentTokens');
  return debugGraphSpikeCompressedAxisDescriptor(group || {unit: 'tokensPerMinute'}, values);
}

function debugGraphChartLabel(group, buckets = []) {
  const label = debugGraphLocalizedLabel(group);
  const detailKey = group?.key === 'cpu' ? 'cpuLabel' : group?.key === 'memory' ? 'systemMemoryLabel' : '';
  if (!detailKey) return label;
  const detail = buckets.map(bucket => String(bucket?.hostMetrics?.[detailKey] || '').trim()).find(Boolean);
  return detail ? `${label} (${detail})` : label;
}

function debugGraphChartShellHtml(gridHtml = '', domain = debugGraphDomain()) {
  return `<div class="js-debug-chart-shell">
    <div class="js-debug-chart-grid" data-js-debug-chart-grid data-js-debug-chart-layout="${esc(debugRuntimeState.graphChartLayout)}" data-js-debug-domain-start="${esc(Math.floor(domain.startMs))}" data-js-debug-domain-end="${esc(Math.floor(domain.endMs))}"${domain.zoomed ? ' data-js-debug-zoomed="true"' : ''}>${gridHtml}</div>
    ${debugGraphHistoryOverlayHtml()}
  </div>`;
}

function debugGraphCostText(key, fallback, params = {}) {
  const translated = t(key, params);
  return translated === key ? fallback : translated;
}

function debugGraphCostMicroUsd(item) {
  return debugGraphCostInteger(item?.micro_usd ?? item?.total_micro_usd ?? item?.cost_micro_usd);
}

function debugGraphCostApiListMicroUsd(item) {
  for (const value of [item?.api_list_micro_usd, item?.total_api_list_micro_usd, item?.apiListMicroUsd, item?.totalApiListMicroUsd]) {
    if (value !== undefined && value !== null && Number.isSafeInteger(Number(value)) && Number(value) >= 0) return Number(value);
  }
  return null;
}

function debugGraphCostUsdText(microUsd) {
  const value = debugGraphCostInteger(microUsd);
  if (value === 0) return '$0.00';
  const usd = value / 1000000;
  if (usd >= 1) return `$${usd.toFixed(2)}`;
  if (usd >= 0.01) return `$${usd.toFixed(4)}`;
  return `$${usd.toFixed(6)}`;
}

function debugGraphCostRangeUsdText(summary) {
  const lower = debugGraphCostInteger(summary?.lowerMicroUsd ?? summary?.knownMicroUsd);
  const upper = Math.max(lower, debugGraphCostInteger(summary?.upperMicroUsd ?? summary?.totalMicroUsd ?? summary?.knownMicroUsd));
  if (lower === upper) return debugGraphCostUsdText(lower);
  return `${debugGraphCostUsdText(lower)} – ${debugGraphCostUsdText(upper)}`;
}

function debugGraphCostKind(item) {
  return String(item?.key || item?.kind || item?.direction || item?.label || '').toLowerCase();
}

function debugGraphCostClass(item) {
  const unit = String(item?.unit || 'tokens').toLowerCase();
  const modality = String(item?.modality || 'text').toLowerCase();
  if (unit !== 'tokens' || modality !== 'text') return 'other';
  const cacheRole = String(item?.cache_role || '').toLowerCase();
  if (['read', 'write', 'write_5m', 'write_1h'].includes(cacheRole)) return 'cache';
  const direction = String(item?.direction || debugGraphCostKind(item)).toLowerCase();
  if (direction.includes('input') || direction.includes('uncached')) return 'input';
  if (direction.includes('output')) return 'output';
  return 'other';
}

// One copy owner for the visible usage columns. Cached deliberately bundles cache
// reads and writes, and every concise legend entry derives from this same key set.
const debugGraphCostUsageColumnCopy = Object.freeze({
  input: Object.freeze({description: ['debug.cost.input.desc', 'Newly processed prompt/context tokens, counted after cache reads and writes are separated into Cached. Reused cached context is never double-counted here.'], gloss: ['debug.cost.input.gloss', 'new prompt/context tokens']}),
  cache_read: Object.freeze({description: ['debug.modelTokens.cacheRead.desc', 'Tokens served from model-cache reads, usually cheaper than uncached input.'], gloss: ['debug.cost.cacheRead.gloss', 'hits and refreshes']}),
  cache_write: Object.freeze({description: ['debug.modelTokens.cacheWrite.desc', 'Input tokens written into a provider cache. Anthropic records 5-minute and 1-hour lifetimes separately in Cost by Model; OpenAI does not expose a cache-write counter.'], gloss: ['debug.cost.cacheWrite.gloss', '5m/1h cache creation']}),
  cache_write_5m: Object.freeze({description: ['debug.cost.class.cacheWrite5m', 'Input tokens written into the provider cache for a 5-minute lifetime.'], gloss: ['debug.cost.cacheWrite5m.gloss', '5-minute lifetime']}),
  cache_write_1h: Object.freeze({description: ['debug.cost.class.cacheWrite1h', 'Input tokens written into the provider cache for a 1-hour lifetime.'], gloss: ['debug.cost.cacheWrite1h.gloss', '1-hour lifetime']}),
  output: Object.freeze({description: ['debug.cost.output.desc', 'Model-generated tokens, including provider-reported reasoning and tool-call output.'], gloss: ['debug.cost.output.gloss', 'generated tokens']}),
  other: Object.freeze({description: ['debug.cost.other.desc', 'Retained usage that fits none of Input / Cached / Output, such as non-text or non-token units. Non-token image, audio, request, and tool units can add cost in Cost by Model without being added to token totals.'], gloss: ['debug.cost.other.gloss', 'non-token units (image/audio/tool)']}),
  total: Object.freeze({description: ['debug.cost.total.desc', 'The reconciliation of the four columns: Input + Cached + Output + Other. The projection is mutually exclusive, so each token is counted in exactly one column and the sum is not double-counted.'], gloss: ['debug.cost.total.gloss', 'Input + Cached + Output + Other']}),
});

function debugGraphCostUsageColumnDescription(key) {
  const entry = debugGraphCostUsageColumnCopy[key]?.description;
  return entry ? debugGraphCostText(entry[0], entry[1]) : '';
}

function debugGraphCostUsageColumnLabel(key) {
  if (key === 'cache_read') {
    const providerLabel = debugGraphCostText('debug.modelTokens.cacheRead', 'Cache read');
    return providerLabel === 'Cache hits & refreshes' ? 'Cache read' : providerLabel;
  }
  if (key === 'cache_write') return debugGraphCostText('debug.modelTokens.cacheWrite', 'Cache write');
  if (key === 'cache_write_5m') return debugGraphCostText('debug.cost.class.cacheWrite5m', '5m cache write');
  if (key === 'cache_write_1h') return debugGraphCostText('debug.cost.class.cacheWrite1h', '1h cache write');
  return debugGraphCostText(`debug.cost.${key}`, key);
}

function debugGraphCostUsageColumnGloss(key) {
  const entry = debugGraphCostUsageColumnCopy[key]?.gloss;
  if (!entry) return '';
  return debugGraphCostText(entry[0], entry[1]);
}

// One always-visible legend for the cost tables' Input/Cached/Output/Other/Total columns, so the
// meaning is glanceable without hovering or expanding. Reuses the column swatch colors and the
// shared header explain-attrs (full description as the hover tooltip); the gloss is the terse
// visible layer. Rendered ONCE per report, above the usage tables.
function debugGraphCostUsageColumnLegendHtml() {
  const labels = {
    input: debugGraphCostText('debug.cost.input', 'Input'),
    cache_read: debugGraphCostUsageColumnLabel('cache_read'),
    cache_write: debugGraphCostUsageColumnLabel('cache_write'),
    output: debugGraphCostText('debug.cost.output', 'Output'),
    other: debugGraphCostText('debug.cost.other', 'Other'),
    total: debugGraphCostText('debug.cost.total', 'Total'),
  };
  const legendLabel = debugGraphCostText('debug.cost.columnLegend', 'What the columns count');
  return `<dl class="js-debug-cost-column-legend" data-js-debug-cost-column-legend aria-label="${esc(legendLabel)}">${['input', 'cache_read', 'cache_write', 'output', 'other', 'total'].map(key => `<div${debugGraphCostUsageColumnHeaderAttrs(key, labels[key])}><i class="js-debug-cost-usage-swatch js-debug-cost-usage-swatch--${esc(key)}" aria-hidden="true"></i><dt>${esc(labels[key])}</dt><dd>${esc(debugGraphCostUsageColumnGloss(key))}</dd></div>`).join('')}</dl>`;
}

function debugGraphCostUsageColumnHeaderAttrs(key, label) {
  return debugGraphExplainAttrs(label, `debug.cost.${key === 'cache' ? 'cached' : key}.desc`, {attribute: 'data-js-debug-cost-column-desc', desc: debugGraphCostUsageColumnDescription(key)});
}

function debugGraphCostCompactTotals(summary) {
  if (summary.dimensionTotals) return {
    input: debugGraphCostInteger(summary.dimensionTotals.input_micro_usd),
    cache: debugGraphCostInteger(summary.dimensionTotals.cache_micro_usd),
    output: debugGraphCostInteger(summary.dimensionTotals.output_micro_usd) + debugGraphCostInteger(summary.dimensionTotals.other_micro_usd),
  };
  const totals = {input: 0, cache: 0, output: 0};
  for (const item of summary.components) {
    const value = debugGraphCostMicroUsd(item);
    const itemClass = debugGraphCostClass(item);
    if (itemClass === 'input') totals.input += value;
    else if (itemClass === 'cache') totals.cache += value;
    else totals.output += value;
  }
  return totals;
}

function debugGraphCostCompactApiListTotals(summary) {
  if (summary.dimensionTotals) return {
    input: debugGraphCostApiListMicroUsd({api_list_micro_usd: summary.dimensionTotals.input_api_list_micro_usd}),
    cache: debugGraphCostApiListMicroUsd({api_list_micro_usd: summary.dimensionTotals.cache_api_list_micro_usd}),
    output: debugGraphCostInteger(summary.dimensionTotals.output_api_list_micro_usd) + debugGraphCostInteger(summary.dimensionTotals.other_api_list_micro_usd),
  };
  const totals = {input: null, cache: null, output: null};
  for (const item of summary.components) {
    const value = debugGraphCostApiListMicroUsd(item);
    if (value === null) continue;
    const itemClass = debugGraphCostClass(item);
    const key = itemClass === 'input' ? 'input' : itemClass === 'cache' ? 'cache' : 'output';
    totals[key] = (totals[key] ?? 0) + value;
  }
  return totals;
}

function debugGraphCostTokenTotals(summary) {
  if (summary.dimensionTotals) return {
    input: Math.max(0, Number(summary.dimensionTotals.input_tokens) || 0),
    cache: Math.max(0, Number(summary.dimensionTotals.cache_tokens) || 0),
    output: Math.max(0, Number(summary.dimensionTotals.output_tokens) || 0),
    other: Math.max(0, Number(summary.dimensionTotals.other_tokens) || 0),
    total: Math.max(0, Number(summary.totalTokenQuantity) || 0),
  };
  const totals = {input: 0, cache: 0, output: 0, other: 0, total: 0};
  for (const item of summary.components) {
    if (String(item?.unit || 'tokens').toLowerCase() !== 'tokens') continue;
    const quantity = Math.max(0, Number(item?.quantity) || 0);
    const itemClass = debugGraphCostClass(item);
    totals[itemClass] += quantity;
    totals.total += quantity;
  }
  return totals;
}

const DEBUG_GRAPH_COST_SUBTOTAL_FIELDS = Object.freeze(['micro_usd', 'api_list_micro_usd', 'lower_micro_usd', 'upper_micro_usd', 'input_micro_usd', 'cache_micro_usd', 'cache_read_micro_usd', 'cache_write_micro_usd', 'cache_write_5m_micro_usd', 'cache_write_1h_micro_usd', 'output_micro_usd', 'other_micro_usd', 'input_api_list_micro_usd', 'cache_api_list_micro_usd', 'cache_read_api_list_micro_usd', 'cache_write_api_list_micro_usd', 'cache_write_5m_api_list_micro_usd', 'cache_write_1h_api_list_micro_usd', 'output_api_list_micro_usd', 'other_api_list_micro_usd', 'input_lower_micro_usd', 'cache_lower_micro_usd', 'cache_read_lower_micro_usd', 'cache_write_lower_micro_usd', 'cache_write_5m_lower_micro_usd', 'cache_write_1h_lower_micro_usd', 'output_lower_micro_usd', 'other_lower_micro_usd', 'input_upper_micro_usd', 'cache_upper_micro_usd', 'cache_read_upper_micro_usd', 'cache_write_upper_micro_usd', 'cache_write_5m_upper_micro_usd', 'cache_write_1h_upper_micro_usd', 'output_upper_micro_usd', 'other_upper_micro_usd']);
const DEBUG_GRAPH_COST_TOKEN_FIELDS = Object.freeze(['quantity', 'token_quantity', 'priced_token_quantity', 'unpriced_token_quantity', 'input_tokens', 'cache_tokens', 'cache_read_tokens', 'cache_write_tokens', 'cache_write_5m_tokens', 'cache_write_1h_tokens', 'output_tokens', 'other_tokens']);
const DEBUG_GRAPH_COST_COMPONENT_KEY_FIELDS = Object.freeze(['key', 'kind', 'provider', 'model', 'effort', 'pricing_profile', 'service_tier', 'direction', 'modality', 'cache_role', 'unit', 'catalog_revision', 'source_url', 'effective_from', 'rate_usd', 'rate_scale']);
const DEBUG_GRAPH_COST_MODEL_KEY_FIELDS = Object.freeze(['provider', 'model', 'effort']);
const DEBUG_GRAPH_COST_SOURCE_KEY_FIELDS = Object.freeze(['tmux_key', 'tmux_label', 'tmux_session', 'tmux_window', 'tmux_window_label', 'agent_kind', 'root_thread_id', 'agent_thread_id', 'parent_thread_id', 'endpoint', 'tool_name', 'source']);
const DEBUG_GRAPH_COST_TMUX_KEY_FIELDS = Object.freeze(['tmux_key', 'tmux_label', 'tmux_session', 'tmux_window', 'tmux_window_label', 'agent_kind']);
let jsDebugCostSummaryCache = {signature: '', summary: null};

function debugGraphCostAggregateRowInto(grouped, row, keyFields) {
  if (!row || typeof row !== 'object') return;
  let key = '';
  for (let index = 0; index < keyFields.length; index += 1) {
    if (index) key += '\u0000';
    key += String(row[keyFields[index]] || '');
  }
  key ||= 'unknown';
  const current = grouped.get(key) || {...row};
  if (!grouped.has(key)) {
    for (const field of DEBUG_GRAPH_COST_SUBTOTAL_FIELDS) {
      if (!field.includes('api_list') || row?.[field] !== undefined) current[field] = 0;
    }
    for (const field of DEBUG_GRAPH_COST_TOKEN_FIELDS) current[field] = 0;
    grouped.set(key, current);
  }
  for (const field of DEBUG_GRAPH_COST_SUBTOTAL_FIELDS) {
    if (field.includes('api_list') && row?.[field] === undefined) continue;
    current[field] = debugGraphCostInteger(current[field]) + debugGraphCostInteger(row?.[field]);
  }
  for (const field of DEBUG_GRAPH_COST_TOKEN_FIELDS) current[field] += Math.max(0, Number(row?.[field]) || 0);
}

function debugGraphCostAggregateValues(grouped) {
  return [...grouped.values()].sort((left, right) => debugGraphCostMicroUsd(right) - debugGraphCostMicroUsd(left)
    || String(left?.model || left?.label || left?.key || '').localeCompare(String(right?.model || right?.label || right?.key || '')));
}

function debugGraphCostAggregateRows(rows, keyFields) {
  const grouped = new Map();
  for (const row of rows || []) {
    debugGraphCostAggregateRowInto(grouped, row, keyFields);
  }
  return debugGraphCostAggregateValues(grouped);
}

function debugGraphCostSummarySignature(buckets) {
  if (!Array.isArray(buckets) || !buckets.length) {
    return `0:${jsDebugStatsServerSequence}:${jsDebugUsageAtomBackfill.state || ''}:${jsDebugUsageAtomBackfill.sources || 0}:${jsDebugUsageAtomBackfill.missing || 0}`;
  }
  const first = buckets[0] || {};
  const last = buckets[buckets.length - 1] || {};
  return [
    buckets.length,
    Number(first.startMs ?? first.start ?? 0) || 0,
    Number(first.durationMs ?? first.duration ?? 0) || 0,
    Number(first.sequence ?? 0) || 0,
    Number(last.startMs ?? last.start ?? 0) || 0,
    Number(last.durationMs ?? last.duration ?? 0) || 0,
    Number(last.sequence ?? 0) || 0,
    jsDebugStatsServerSequence,
    jsDebugUsageAtomBackfill.state || '',
    jsDebugUsageAtomBackfill.sources || 0,
    jsDebugUsageAtomBackfill.missing || 0,
  ].join(':');
}

function debugGraphCostSummaryForBuckets(buckets) {
  const signature = debugGraphCostSummarySignature(buckets);
  if (signature && jsDebugCostSummaryCache.signature === signature && jsDebugCostSummaryCache.summary) return jsDebugCostSummaryCache.summary;
  const allSummaries = (buckets || []).map(bucket => bucket?.costSummary).filter(Boolean);
  const rangeSummaries = allSummaries.filter(summary => summary.rangeReport === true);
  const summaries = rangeSummaries.length ? rangeSummaries : allSummaries;
  const componentRows = new Map();
  const modelRows = new Map();
  const sourceRows = new Map();
  const tmuxRows = new Map();
  const result = {
    totalMicroUsd: 0, apiListMicroUsd: null, totalTokenQuantity: 0, dimensionTotals: null, knownMicroUsd: 0, lowerMicroUsd: 0, upperMicroUsd: 0, pricedCount: 0, complete: summaries.length > 0,
    unpricedCount: 0, unpricedTokenQuantity: 0, components: [], models: [], sources: [], tmuxWindows: [], catalogRevision: '', activeCatalogRevision: '', freshness: '',
    backfill: {...jsDebugUsageAtomBackfill},
  };
  for (const summary of summaries) {
    result.totalMicroUsd += debugGraphCostInteger(summary.totalMicroUsd);
    const apiListMicroUsd = debugGraphCostApiListMicroUsd(summary);
    if (apiListMicroUsd !== null) result.apiListMicroUsd = (result.apiListMicroUsd ?? 0) + apiListMicroUsd;
    result.totalTokenQuantity += Math.max(0, Number(summary.totalTokenQuantity) || 0);
    if (summary.dimensionTotals) {
      result.dimensionTotals ||= {};
      for (const field of [...DEBUG_GRAPH_COST_TOKEN_FIELDS, ...DEBUG_GRAPH_COST_SUBTOTAL_FIELDS]) {
        if (summary.dimensionTotals[field] === undefined) continue;
        result.dimensionTotals[field] = (Number(result.dimensionTotals[field]) || 0) + Math.max(0, Number(summary.dimensionTotals[field]) || 0);
      }
    }
    result.knownMicroUsd += debugGraphCostInteger(summary.knownMicroUsd);
    result.lowerMicroUsd += debugGraphCostInteger(summary.lowerMicroUsd ?? summary.knownMicroUsd);
    result.upperMicroUsd += debugGraphCostInteger(summary.upperMicroUsd ?? summary.totalMicroUsd ?? summary.knownMicroUsd);
    result.pricedCount += debugGraphCostInteger(summary.pricedCount);
    result.complete = result.complete && summary.complete === true;
    result.unpricedCount += debugGraphCostInteger(summary.unpricedCount);
    result.unpricedTokenQuantity += Math.max(0, Number(summary.unpricedTokenQuantity) || 0);
    for (const row of debugGraphCostRows(summary.components)) debugGraphCostAggregateRowInto(componentRows, row, DEBUG_GRAPH_COST_COMPONENT_KEY_FIELDS);
    for (const row of debugGraphCostRows(summary.models)) debugGraphCostAggregateRowInto(modelRows, row, DEBUG_GRAPH_COST_MODEL_KEY_FIELDS);
    for (const row of debugGraphCostRows(summary.sources)) debugGraphCostAggregateRowInto(sourceRows, row, DEBUG_GRAPH_COST_SOURCE_KEY_FIELDS);
    for (const row of debugGraphCostRows(summary.tmuxWindows)) debugGraphCostAggregateRowInto(tmuxRows, row, DEBUG_GRAPH_COST_TMUX_KEY_FIELDS);
    result.catalogRevision = summary.catalogRevision || result.catalogRevision;
    result.activeCatalogRevision = summary.activeCatalogRevision || result.activeCatalogRevision;
    result.freshness = summary.freshness || result.freshness;
  }
  // Effective price/source evidence is part of a billable component identity:
  // retaining it prevents a displayed-range reprice boundary from being
  // misleadingly collapsed into one synthetic rate row.
  result.components = debugGraphCostAggregateValues(componentRows);
  result.models = debugGraphCostAggregateValues(modelRows);
  result.sources = debugGraphCostAggregateValues(sourceRows);
  result.tmuxWindows = debugGraphCostAggregateValues(tmuxRows);
  if (result.backfill.state !== 'complete') result.complete = false;
  jsDebugCostSummaryCache = {signature, summary: result};
  return result;
}

function debugGraphCostRangeText(domain) {
  const start = debugGraphExactTimeLabel(domain.startMs);
  const end = debugGraphExactTimeLabel(domain.endMs);
  const seconds = Math.max(0, Math.round((Number(domain.endMs) - Number(domain.startMs)) / 1000));
  return `${start} – ${end} · ${debugGraphCostText('debug.cost.duration', `${seconds}s`, {seconds})}`;
}

function debugGraphCompactRangeText(domain) {
  const startMs = Number(domain?.startMs);
  const endMs = Number(domain?.endMs);
  const includeDate = debugGraphLocalDateKey(startMs) !== debugGraphLocalDateKey(endMs);
  const start = debugGraphTimeLabel(startMs, {includeDate, includeSeconds: false});
  const end = debugGraphTimeLabel(endMs, {includeDate, includeSeconds: false});
  const seconds = Math.max(0, Math.round((endMs - startMs) / 1000));
  return `${start}–${end} · ${debugGraphCostText('debug.cost.duration', `${seconds}s`, {seconds})}`;
}

function debugGraphCostModelLabel(row) {
  const label = String(row?.label || row?.model || row?.source || row?.agent || row?.key || 'unknown');
  const effort = String(row?.effort || '').trim();
  return effort ? `${label} · ${effort}` : label;
}

function debugGraphAgentDisplayLabel(value) {
  const full = String(value || '').trim();
  if (!full) return debugGraphCostText('debug.cost.unknown', 'Unknown');
  const canonical = globalThis.YOLOmuxStatsCurrent?.canonicalAgentLabel?.(full);
  if (canonical && canonical !== full) return canonical;
  if (full.startsWith('claude-bg:')) {
    const [, projectValue = '', sessionValue = ''] = full.split(':');
    const projectParts = projectValue.split('-').filter(Boolean);
    const project = projectParts.slice(-2).join('-') || projectValue;
    const session = sessionValue.slice(0, 8);
    return ['claude-bg', project, session].filter(Boolean).join(':');
  }
  if (Array.from(full).length <= 64) return full;
  return `${Array.from(full).slice(0, 39).join('')}…${Array.from(full).slice(-16).join('')}`;
}

function debugGraphCostModelAgentKind(row) {
  const identity = [row?.provider, row?.model, row?.label].map(value => String(value || '').toLowerCase()).join(' ');
  if (identity.includes('anthropic') || identity.includes('claude')) return 'claude';
  if (identity.includes('openai') || identity.includes('gpt') || identity.includes('codex')) return 'codex';
  return '';
}

function debugGraphCostModelIdentityHtml(row, {showProvider = false, secondaryHtml = ''} = {}) {
  const model = String(row?.model || row?.label || row?.source || row?.agent || row?.key || 'unknown');
  const effort = String(row?.effort || '').trim();
  const provider = String(row?.provider || '').trim();
  const meta = [showProvider ? provider : '', effort].filter(Boolean).join(' · ');
  const kind = debugGraphCostModelAgentKind(row);
  const icon = kind ? `<span class="js-debug-cost-model-icon" title="${esc(provider || kind)}" aria-label="${esc(provider || kind)}">${agentIcon(kind)}</span>` : '';
  const secondary = meta || secondaryHtml ? `<span class="js-debug-cost-model-meta">${meta ? `<small>${esc(meta)}</small>` : ''}${secondaryHtml}</span>` : '';
  return `<span class="js-debug-cost-model-identity">${icon}<span class="js-debug-cost-model-copy"><strong>${esc(model)}</strong>${secondary}</span></span>`;
}

function debugGraphCostUsageTokensText(tokens) {
  const value = Math.max(0, Number(tokens) || 0);
  return value > 0 ? debugGraphTokensText(value) : '0';
}

function debugGraphCostUsageUsdText(microUsd, tokens = 1, {pricedTokens = tokens, unpricedTokens = 0} = {}) {
  const unknownTokens = Math.max(0, Number(unpricedTokens) || 0);
  if (unknownTokens > 0) {
    if (Math.max(0, Number(pricedTokens) || 0) <= 0) return 'Unpriced';
    return `Known ${debugGraphCostUsdText(microUsd)} + Unpriced`;
  }
  const value = debugGraphCostOptionalInteger(microUsd);
  if (value === null) return 'Unpriced';
  if (value > 0) return debugGraphCostUsdText(value);
  if (Math.max(0, Number(tokens) || 0) <= 0) return '$0';
  return '$0';
}

function debugGraphCostUsagePriceText(microUsd, apiListMicroUsd, tokens, row, {basis = 'omit'} = {}) {
  const pricedTokens = Math.max(0, Number(row?.priced_token_quantity) || 0);
  const unpricedTokens = Math.max(0, Number(row?.unpriced_token_quantity) || 0);
  if (unpricedTokens > 0) {
    if (pricedTokens <= 0) return debugGraphCostUsageUsdText(microUsd, tokens, {pricedTokens, unpricedTokens});
    return `Known ${debugGraphCostPricePairText(microUsd, apiListMicroUsd, {basis})} + Unpriced`;
  }
  return apiListMicroUsd === null || apiListMicroUsd === undefined
    ? debugGraphCostUsageUsdText(microUsd, tokens)
    : debugGraphCostPricePairText(microUsd, apiListMicroUsd, {basis});
}

function debugGraphCostPricePairText(microUsd, apiListMicroUsd = null, {basis = 'omit'} = {}) {
  const marginalLabel = debugGraphCostText('debug.cost.marginal', 'Marginal');
  const apiListLabel = debugGraphCostText('debug.cost.atApiListPrices', 'At API list prices');
  if (apiListMicroUsd === null || debugGraphCostInteger(apiListMicroUsd) === debugGraphCostInteger(microUsd)) return `${basis === 'inline' ? `${apiListLabel} ` : ''}${debugGraphCostUsdText(apiListMicroUsd ?? microUsd)}`;
  return basis === 'inline'
    ? `${marginalLabel} ${debugGraphCostUsdText(microUsd)} · ${apiListLabel} ${debugGraphCostUsdText(apiListMicroUsd)}`
    : `${debugGraphCostUsdText(microUsd)} ${marginalLabel.toLowerCase()} · ${debugGraphCostUsdText(apiListMicroUsd)} list`;
}

function debugGraphCostPricePairHtml(microUsd, apiListMicroUsd = null, {basis = 'omit'} = {}) {
  const marginalLabel = debugGraphCostText('debug.cost.marginal', 'Marginal');
  const apiListLabel = debugGraphCostText('debug.cost.atApiListPrices', 'At API list prices');
  if (apiListMicroUsd === null || debugGraphCostInteger(apiListMicroUsd) === debugGraphCostInteger(microUsd)) return `<small class="js-debug-cost-price-pair"><span>${basis === 'inline' ? `${esc(apiListLabel)} ` : ''}${esc(debugGraphCostUsdText(apiListMicroUsd ?? microUsd))}</span></small>`;
  return basis === 'inline'
    ? `<small class="js-debug-cost-price-pair"><span>${esc(marginalLabel)} ${esc(debugGraphCostUsdText(microUsd))}</span><span>${esc(apiListLabel)} ${esc(debugGraphCostUsdText(apiListMicroUsd))}</span></small>`
    : `<small class="js-debug-cost-price-pair"><span>${esc(debugGraphCostUsdText(microUsd))} ${esc(marginalLabel.toLowerCase())}</span><span>${esc(debugGraphCostUsdText(apiListMicroUsd))} list</span></small>`;
}

const DEBUG_GRAPH_COST_USAGE_COLUMN_KEYS = Object.freeze([
  'input', 'cache_read', 'cache_write_5m', 'cache_write_1h', 'output', 'other',
]);

function debugGraphCostUsageColumns() {
  return DEBUG_GRAPH_COST_USAGE_COLUMN_KEYS.map(key => ({
    key,
    label: key === 'input' ? debugGraphCostText('debug.cost.input', 'Input')
      : key === 'output' ? debugGraphCostText('debug.cost.output', 'Output')
        : key === 'other' ? debugGraphCostText('debug.cost.other', 'Other')
          : debugGraphCostUsageColumnLabel(key),
  }));
}

function debugGraphCostUsesLifetimeCacheWrites(row) {
  return String(row?.provider || '').trim().toLowerCase() === 'anthropic';
}

function debugGraphCostBreakdownItems(row, {kind = '', total = false} = {}) {
  const exactCacheWriteTokens = Math.max(0, Number(row?.cache_write_5m_tokens) || 0) + Math.max(0, Number(row?.cache_write_1h_tokens) || 0);
  const exactCacheWriteMicroUsd = debugGraphCostInteger(row?.cache_write_5m_micro_usd) + debugGraphCostInteger(row?.cache_write_1h_micro_usd);
  const exactCacheWriteApiListMicroUsd = debugGraphCostInteger(row?.cache_write_5m_api_list_micro_usd) + debugGraphCostInteger(row?.cache_write_1h_api_list_micro_usd);
  const columns = debugGraphCostUsageColumns();
  const byKey = Object.fromEntries(columns.map(column => [column.key, column]));
  const value = key => ({
    key,
    label: byKey[key].label,
    tokens: Math.max(0, Number(row?.[`${key}_tokens`]) || 0),
    microUsd: debugGraphCostInteger(row?.[`${key}_micro_usd`]),
    apiListMicroUsd: debugGraphCostApiListMicroUsd({api_list_micro_usd: row?.[`${key}_api_list_micro_usd`]}),
    columnSpan: 1,
  });
  const detailedCacheWrites = total || (kind === 'model' && debugGraphCostUsesLifetimeCacheWrites(row));
  const cacheWrites = detailedCacheWrites
    ? [value('cache_write_5m'), value('cache_write_1h')]
    : [{
      key: 'cache_write', label: debugGraphCostUsageColumnLabel('cache_write'),
      tokens: Math.max(exactCacheWriteTokens, Number(row?.cache_write_tokens) || 0),
      microUsd: Math.max(exactCacheWriteMicroUsd, debugGraphCostInteger(row?.cache_write_micro_usd)),
      apiListMicroUsd: debugGraphCostApiListMicroUsd({api_list_micro_usd: Math.max(exactCacheWriteApiListMicroUsd, Number(row?.cache_write_api_list_micro_usd) || 0)}),
      columnSpan: 2,
    }];
  return [value('input'), value('cache_read'), ...cacheWrites, value('output'), value('other')];
}

function debugGraphCostPricingSourceEntries(components, modelRow = null) {
  const provider = String(modelRow?.provider || '').trim();
  const model = String(modelRow?.model || '').trim();
  const links = new Map();
  for (const row of components || []) {
    if (provider && String(row?.provider || '').trim() !== provider) continue;
    if (model && String(row?.model || '').trim() !== model) continue;
    const url = normalizedExternalHttpUrl(row?.source_url, {maxLength: 2048});
    if (!url || links.has(url)) continue;
    const sourceLabel = [row?.provider, row?.model].map(value => String(value || '').trim()).filter(Boolean).join(' · ')
      || debugGraphCostText('debug.cost.source', 'Pricing source');
    links.set(url, sourceLabel);
  }
  return [...links].map(([url, label]) => ({url, label}));
}

function debugGraphCostPricingLinksHtml(components, modelRow = null, {compact = false} = {}) {
  const links = debugGraphCostPricingSourceEntries(components, modelRow);
  if (!links.length) return '';
  return `<span class="js-debug-cost-pricing-links${compact ? ' js-debug-cost-pricing-links--compact' : ''}">${links.map(({url, label}) => `<a href="${esc(url)}" target="_blank" rel="noopener noreferrer"${compact ? ` aria-label="${esc(`${label} pricing`)}" title="${esc(`${label} pricing`)}"` : ''}>${esc(compact ? '$' : label)}</a>`).join(' · ')}</span>`;
}

function debugGraphCostAllPricingSourcesHtml(components) {
  const links = debugGraphCostPricingSourceEntries(components);
  if (!links.length) return '';
  return `<section class="js-debug-cost-details-section js-debug-cost-pricing-sources">
    <h2>${esc(debugGraphCostText('debug.cost.pricingSources', 'Pricing sources'))}</h2>
    <div class="js-debug-system-table-wrap js-debug-cost-table-wrap"><table class="js-debug-system-table js-debug-cost-table" data-js-debug-cost-table="pricing-sources"><thead><tr><th scope="col">${esc(debugGraphCostText('debug.cost.source', 'Pricing source'))}</th><th scope="col">URL</th></tr></thead><tbody>${links.map(({url, label}) => `<tr><th scope="row">${esc(label)}</th><td><a href="${esc(url)}" target="_blank" rel="noopener noreferrer">${esc(url)}</a></td></tr>`).join('')}</tbody></table></div>
  </section>`;
}

function debugGraphCostUsageTableCellHtml(tokens, microUsd, {total = false, row = null, apiListMicroUsd = null, unreported = false, notApplicable = false} = {}) {
  if (notApplicable) return '<span class="js-debug-cost-not-applicable" aria-label="Not applicable">—</span>';
  if (unreported) return `<span class="js-debug-cost-unreported" title="${esc('OpenAI/Codex telemetry does not report cache-write tokens; this total is a lower bound.')}" aria-label="Unreported cache write; total is a lower bound">unreported</span>`;
  const unpricedTokens = Math.max(0, Number(row?.unpriced_token_quantity) || 0);
  const hasRange = row && (debugGraphCostInteger(row?.lower_micro_usd) > 0 || debugGraphCostInteger(row?.upper_micro_usd) > 0);
  const cost = total && hasRange && unpricedTokens === 0
    ? debugGraphCostRowRangeUsdText(row)
    : debugGraphCostUsagePriceText(microUsd, total && row ? debugGraphCostApiListMicroUsd(row) : apiListMicroUsd, tokens, row);
  const rowApiListMicroUsd = total && row ? debugGraphCostApiListMicroUsd(row) : apiListMicroUsd;
  const exactTokens = `${Math.max(0, Number(tokens) || 0).toLocaleString()} tokens`;
  const coverageUnknown = unpricedTokens > 0;
  const price = coverageUnknown || rowApiListMicroUsd === null
    ? `<small>${esc(cost)}</small>`
    : debugGraphCostPricePairHtml(microUsd, rowApiListMicroUsd);
  const priceText = coverageUnknown ? debugGraphCostUsagePriceText(microUsd, rowApiListMicroUsd, tokens, row, {basis: 'inline'}) : debugGraphCostPricePairText(microUsd, rowApiListMicroUsd, {basis: 'inline'});
  return `<span class="js-debug-cost-table-metric js-debug-cost-table-metric--inline" title="${esc(`${exactTokens}; ${priceText}`)}"><strong>${esc(debugGraphTokenNumberText(tokens))}</strong><span aria-hidden="true"> · </span>${price}</span>`;
}

function debugGraphCostExactTotalRow(summary) {
  if (!summary?.dimensionTotals) return null;
  return {
    token_quantity: Math.max(0, Number(summary.totalTokenQuantity) || 0),
    priced_token_quantity: Math.max(0, Number(summary.totalTokenQuantity) || 0) - Math.max(0, Number(summary.unpricedTokenQuantity) || 0),
    unpriced_token_quantity: Math.max(0, Number(summary.unpricedTokenQuantity) || 0),
    micro_usd: debugGraphCostOptionalInteger(summary.totalMicroUsd),
    api_list_micro_usd: debugGraphCostApiListMicroUsd(summary),
    ...summary.dimensionTotals,
  };
}

function debugGraphCostUsageTableHtml(rows, {kind, heading, labelHeading, labelFor, components = [], totalRow: exactTotalRow = null} = {}) {
  if (!rows.length) return '';
  const usageColumns = debugGraphCostUsageColumns();
  const usageLabels = Object.fromEntries(usageColumns.map(({key, label}) => [key, label]));
  const totalRow = exactTotalRow || debugGraphCostAggregateRows(rows, [])[0] || {};
  const rowHtml = row => {
    const breakdown = debugGraphCostBreakdownItems(row, {kind});
    const totalTokens = Math.max(0, Number(row?.token_quantity) || 0);
    const pricingLinks = kind === 'model' ? debugGraphCostPricingLinksHtml(components, row, {compact: true}) : '';
    const accessible = `${labelFor(row)}: ${debugGraphCostText('debug.cost.total', 'Total')} ${debugGraphCostUsageTokensText(totalTokens)} ${debugGraphCostUsagePriceText(debugGraphCostMicroUsd(row), debugGraphCostApiListMicroUsd(row), totalTokens, row)}; ${breakdown.map(item => `${usageLabels[item.key]} ${debugGraphCostUsageTokensText(item.tokens)} ${debugGraphCostUsagePriceText(item.microUsd, item.apiListMicroUsd, item.tokens, row)}`).join('; ')}`;
    const label = labelFor(row);
    const fullLabel = String(row?.full_label || row?.agent_label || label);
    const identity = kind === 'model' ? debugGraphCostModelIdentityHtml(row, {secondaryHtml: pricingLinks}) : `<strong title="${esc(fullLabel)}" aria-label="${esc(fullLabel)}">${debugGraphCostAgentLabelHtml(label)}</strong>`;
    const usageCellHtml = item => {
      const formula = kind === 'model' ? debugGraphCostModelFormulaCellHtml(components, row, item) : '';
      return formula || debugGraphCostUsageTableCellHtml(item.tokens, item.microUsd, {
        row,
        apiListMicroUsd: item.apiListMicroUsd,
        unreported: kind === 'model' && item.key === 'cache_write' && String(row?.provider || '').toLowerCase() === 'openai' && item.tokens === 0,
      });
    };
    return `<tr aria-label="${esc(accessible)}"><th scope="row">${identity}</th>${breakdown.map(item => `<td${item.columnSpan > 1 ? ` colspan="${item.columnSpan}"` : ''} data-label="${esc(usageLabels[item.key] || item.label)}">${usageCellHtml(item)}</td>`).join('')}<td data-label="${esc(debugGraphCostText('debug.cost.total', 'Total'))}">${debugGraphCostUsageTableCellHtml(totalTokens, debugGraphCostMicroUsd(row), {total: true, row})}</td></tr>`;
  };
  const totalBreakdown = debugGraphCostBreakdownItems(totalRow, {kind, total: true});
  const totalTokens = Math.max(0, Number(totalRow?.token_quantity) || 0);
  const totalApiListMicroUsd = debugGraphCostApiListMicroUsd(totalRow);
  const grandTotalLabel = totalApiListMicroUsd !== null && totalApiListMicroUsd !== debugGraphCostMicroUsd(totalRow)
    ? debugGraphCostText('debug.cost.grandTotalDual', 'Grand total · marginal / API list prices')
    : debugGraphCostText('debug.cost.grandTotalApiList', 'Grand total');
  const input = usageColumns[0];
  const cacheRead = usageColumns[1];
  const cacheWrite5m = usageColumns[2];
  const cacheWrite1h = usageColumns[3];
  const output = usageColumns[4];
  const other = usageColumns[5];
  const headerCell = (column, {rowSpan = 1, colSpan = 1} = {}) => `<th scope="col"${rowSpan > 1 ? ` rowspan="${rowSpan}"` : ''}${colSpan > 1 ? ` colspan="${colSpan}"` : ''}${debugGraphCostUsageColumnHeaderAttrs(column.key, column.label)}><i class="js-debug-cost-usage-swatch js-debug-cost-usage-swatch--${esc(column.key)}" aria-hidden="true"></i><span class="js-debug-cost-usage-label">${esc(column.label)}</span></th>`;
  return `<section class="js-debug-cost-${esc(kind)}-usages js-debug-cost-details-section js-debug-cost-usage-table-section"><h2>${esc(heading)}</h2><div class="js-debug-system-table-wrap js-debug-cost-table-wrap"><table class="js-debug-system-table js-debug-cost-table" data-js-debug-cost-table="${esc(kind)}"><thead><tr><th scope="col" rowspan="2">${esc(labelHeading)}</th>${headerCell(input, {rowSpan: 2})}${headerCell(cacheRead, {rowSpan: 2})}${headerCell({key: 'cache_write', label: debugGraphCostUsageColumnLabel('cache_write')}, {colSpan: 2})}${headerCell(output, {rowSpan: 2})}${headerCell(other, {rowSpan: 2})}<th scope="col" rowspan="2"${debugGraphCostUsageColumnHeaderAttrs('total', debugGraphCostText('debug.cost.total', 'Total'))}><span class="js-debug-cost-usage-label">${esc(debugGraphCostText('debug.cost.total', 'Total'))}</span></th></tr><tr>${headerCell(cacheWrite5m)}${headerCell(cacheWrite1h)}</tr></thead><tbody>${rows.map(rowHtml).join('')}</tbody><tfoot><tr><th scope="row">${esc(grandTotalLabel)}</th>${totalBreakdown.map(item => `<td${item.columnSpan > 1 ? ` colspan="${item.columnSpan}"` : ''} data-label="${esc(usageLabels[item.key] || item.label)}">${debugGraphCostUsageTableCellHtml(item.tokens, item.microUsd, {apiListMicroUsd: item.apiListMicroUsd})}</td>`).join('')}<td data-label="${esc(debugGraphCostText('debug.cost.total', 'Total'))}">${debugGraphCostUsageTableCellHtml(totalTokens, debugGraphCostMicroUsd(totalRow), {total: true, row: totalRow})}</td></tr></tfoot></table></div></section>`;
}

function debugGraphCostModelUsageChartHtml(rows, components, options = {}) {
  if (options.report !== true) return '';
  return debugGraphCostUsageTableHtml(rows, {
    kind: 'model',
    heading: debugGraphCostText('debug.cost.modelUsages', 'Cost by Model'),
    labelHeading: debugGraphCostText('debug.cost.model', 'Model'),
    labelFor: debugGraphCostModelLabel,
    components,
    totalRow: debugGraphCostExactTotalRow(options.summary),
  });
}

function debugGraphCostComponentRateText(row) {
  const exactRate = String(row?.rate_usd || '').trim();
  const scale = Math.max(0, Number(row?.rate_scale) || 0);
  if (exactRate && scale > 0) return `$${exactRate}/${debugGraphTokenNumberText(scale)}${String(row?.unit || 'unit') === 'tokens' ? '' : ` ${String(row?.unit || 'unit')}`}`;
  const quantity = Number(row?.quantity);
  const microUsd = debugGraphCostMicroUsd(row);
  if (!Number.isFinite(quantity) || quantity <= 0 || microUsd <= 0) return '—';
  return `${debugGraphCostUsdText(Math.round((microUsd * 1000000) / quantity))}/${String(row?.unit || 'unit')}`;
}

function debugGraphCostComponentDimension(row) {
  const role = String(row?.cache_role || '').toLowerCase();
  if (role === 'read') return 'cache_read';
  if (role === 'write_5m') return 'cache_write_5m';
  if (role === 'write_1h') return 'cache_write_1h';
  if (String(row?.direction || '').toLowerCase() === 'input') return 'input';
  if (String(row?.direction || '').toLowerCase() === 'output') return 'output';
  return 'other';
}

function debugGraphCostModelFormulaCellHtml(components, model, item) {
  if (Math.max(0, Number(item?.tokens) || 0) <= 0) return '';
  const cacheWrite = item.key === 'cache_write';
  const rows = (components || []).filter(row => {
    if (String(row?.provider || '').trim() !== String(model?.provider || '').trim()) return false;
    if (String(row?.model || '').trim() !== String(model?.model || '').trim()) return false;
    const dimension = debugGraphCostComponentDimension(row);
    return cacheWrite ? dimension === 'cache_write_5m' || dimension === 'cache_write_1h' : dimension === item.key;
  });
  if (!rows.length) return '';
  const grouped = new Map();
  for (const row of rows) {
    const rate = debugGraphCostComponentRateText(row);
    const current = grouped.get(rate) || {...row, quantity: 0, micro_usd: 0, api_list_micro_usd: 0};
    current.quantity += Math.max(0, Number(row?.quantity) || 0);
    current.micro_usd += debugGraphCostMicroUsd(row);
    current.api_list_micro_usd += debugGraphCostApiListMicroUsd(row) ?? debugGraphCostMicroUsd(row);
    grouped.set(rate, current);
  }
  return [...grouped.values()].map(row => `<span class="js-debug-cost-model-formula" title="${esc(`${Math.max(0, Number(row.quantity) || 0).toLocaleString()} tokens x ${debugGraphCostComponentRateText(row)} = ${debugGraphCostUsdText(debugGraphCostMicroUsd(row))}`)}">${esc(debugGraphTokenNumberText(row.quantity))} x ${esc(debugGraphCostComponentRateText(row))} = ${esc(debugGraphCostUsdText(debugGraphCostMicroUsd(row)))}</span>`).join('<br>');
}

function debugGraphCostSourceLabel(row) {
  const explicit = String(row?.full_label || row?.agent_label || row?.label || '').trim();
  if (explicit) return explicit;
  const root = String(row?.root_thread_id || '').trim();
  const agent = String(row?.agent_thread_id || '').trim();
  const tool = String(row?.tool_name || '').trim();
  const source = String(row?.source || '').trim();
  return [source, agent && agent !== root ? agent : '', tool].filter(Boolean).join(' · ') || root || debugGraphCostText('debug.cost.unknown', 'Unknown');
}

function debugGraphCostRowRangeUsdText(row) {
  const lower = debugGraphCostInteger(row?.lower_micro_usd ?? row?.micro_usd);
  const upper = Math.max(lower, debugGraphCostInteger(row?.upper_micro_usd ?? row?.micro_usd));
  if (lower === upper) return debugGraphCostUsdText(lower);
  return `${debugGraphCostUsdText(lower)} – ${debugGraphCostUsdText(upper)}`;
}

function debugGraphCostSubtotalText(row) {
  const parts = [
    ['input_micro_usd', debugGraphCostText('debug.cost.input', 'Input')],
    ['cache_micro_usd', debugGraphCostText('debug.cost.cache', 'Cache')],
    ['output_micro_usd', debugGraphCostText('debug.cost.output', 'Output')],
    ['other_micro_usd', debugGraphCostText('debug.cost.other', 'Other')],
  ];
  return `${debugGraphTokensText(row?.token_quantity)} · ${parts.map(([key, label]) => `${label} ${debugGraphCostUsdText(debugGraphCostInteger(row?.[key]))}`).join(' · ')} · ${debugGraphCostText('debug.cost.total', 'Total')} ${debugGraphCostPricePairText(debugGraphCostMicroUsd(row), debugGraphCostApiListMicroUsd(row))}`;
}

function debugGraphCostTmuxLabel(row) {
  const label = String(row?.label || '').trim();
  if (label) return label;
  const explicit = String(row?.tmux_label || '').trim();
  if (explicit) return explicit;
  const session = String(row?.tmux_session || '').trim();
  const windowLabel = String(row?.tmux_window_label || row?.tmux_window || '').trim();
  const kind = String(row?.agent_kind || '').trim();
  return [session, windowLabel || kind].filter(Boolean).join(':') || debugGraphCostSourceLabel(row);
}

function debugGraphCostAgentRowsAlphabetically(rows) {
  const sortLabel = row => debugGraphAgentDisplayLabel(debugGraphCostTmuxLabel(row));
  return [...(rows || [])].sort((left, right) => {
    const leftLabel = sortLabel(left);
    const rightLabel = sortLabel(right);
    return leftLabel.localeCompare(rightLabel, undefined, {sensitivity: 'base', numeric: true})
      || leftLabel.localeCompare(rightLabel);
  });
}

function debugGraphCostTmuxBreakdownRows(rows) {
  const grouped = new Map();
  for (const row of rows || []) {
    const key = String(row?.tmux_key || row?.root_thread_id || row?.source || debugGraphCostTmuxLabel(row)).trim() || 'unknown';
    const current = grouped.get(key) || {
      ...row,
      token_quantity: 0,
      micro_usd: 0,
      lower_micro_usd: 0,
      upper_micro_usd: 0,
      input_micro_usd: 0,
      cache_micro_usd: 0,
      output_micro_usd: 0,
      other_micro_usd: 0,
      input_tokens: 0,
      cache_tokens: 0,
      output_tokens: 0,
      other_tokens: 0,
    };
    current.token_quantity += Math.max(0, Number(row?.token_quantity) || 0);
    for (const field of ['micro_usd', 'api_list_micro_usd', 'lower_micro_usd', 'upper_micro_usd', 'input_micro_usd', 'cache_micro_usd', 'output_micro_usd', 'other_micro_usd', 'input_api_list_micro_usd', 'cache_api_list_micro_usd', 'output_api_list_micro_usd', 'other_api_list_micro_usd']) {
      if (field.includes('api_list') && row?.[field] === undefined) continue;
      current[field] = debugGraphCostInteger(current[field]) + debugGraphCostInteger(row?.[field]);
    }
    for (const field of ['input_tokens', 'cache_tokens', 'output_tokens', 'other_tokens']) {
      current[field] += Math.max(0, Number(row?.[field]) || 0);
    }
    grouped.set(key, current);
  }
  return [...grouped.values()];
}

function debugGraphCostTmuxBreakdownHtml(summary) {
  const directRows = debugGraphCostRows(summary?.tmuxWindows);
  const rows = debugGraphCostAgentRowsAlphabetically(
    directRows.length ? directRows : debugGraphCostTmuxBreakdownRows(summary.sources),
  );
  if (!rows.length) return '';
  return debugGraphCostUsageTableHtml(rows, {
    kind: 'agent',
    heading: debugGraphCostText('debug.cost.byAgent', 'Cost by Agent'),
    labelHeading: t('yoagent.action.row.agent'),
    labelFor: debugGraphCostTmuxLabel,
    totalRow: debugGraphCostExactTotalRow(summary),
  });
}

function debugGraphCostTranscriptPath(row) {
  const path = String(row?.transcript || '').trim();
  if (!path.startsWith('/') || !/\.(?:jsonl|ndjson)$/i.test(path) || /[\u0000-\u001f\u007f]/.test(path)) return '';
  const segments = path.split('/');
  if (segments.slice(1).some(segment => !segment || segment === '.' || segment === '..')) return '';
  return path;
}

function debugGraphMiddleTruncatedTextHtml(value, tailLength = 20) {
  const text = String(value || '');
  const characters = Array.from(text);
  const tailSize = Math.max(1, Math.min(characters.length - 1, Math.floor(Number(tailLength) || 20)));
  if (characters.length <= tailSize + 1) return `<span class="js-debug-responsive-text">${esc(text)}</span>`;
  const split = characters.length - tailSize;
  return `<span class="js-debug-responsive-text js-debug-responsive-text--middle"><span class="js-debug-responsive-text-prefix" data-middle-truncate-part="prefix">${esc(characters.slice(0, split).join(''))}</span><span class="js-debug-responsive-text-suffix" data-middle-truncate-part="suffix">${esc(characters.slice(split).join(''))}</span></span>`;
}

function debugGraphCostAgentLabelHtml(value, lineSize = 24) {
  const text = String(value || '');
  const characters = Array.from(text);
  const size = Math.max(12, Math.floor(Number(lineSize) || 24));
  if (characters.length <= size) return `<span class="js-debug-cost-agent-name">${esc(text)}</span>`;
  const first = characters.slice(0, size).join('');
  const second = characters.length <= size * 2
    ? characters.slice(size).join('')
    : `…${characters.slice(-(size - 1)).join('')}`;
  return `<span class="js-debug-cost-agent-name js-debug-cost-agent-name--long" aria-hidden="true"><span>${esc(first)}</span><span>${esc(second)}</span></span>`;
}

function debugGraphCostSourceLabelHtml(row) {
  const label = debugGraphCostSourceLabel(row);
  const transcript = debugGraphCostTranscriptPath(row);
  if (!transcript) return esc(label);
  return `<a href="#" class="js-debug-cost-transcript-link" data-js-debug-cost-transcript-path="${esc(transcript)}" title="${esc(transcript)}" aria-label="${esc(label)}">${debugGraphMiddleTruncatedTextHtml(label)}</a>`;
}

function debugGraphCostSourceTreeHtml(rows) {
  if (!rows.length) return '';
  const usageKeys = ['input', 'cache_read', 'cache_write', 'output', 'other'];
  const usageLabels = {
    input: debugGraphCostText('debug.cost.input', 'Input'),
    cache_read: debugGraphCostUsageColumnLabel('cache_read'),
    cache_write: debugGraphCostUsageColumnLabel('cache_write'),
    output: debugGraphCostText('debug.cost.output', 'Output'),
    other: debugGraphCostText('debug.cost.other', 'Other'),
  };
  const rowHtml = row => {
    const breakdown = debugGraphCostBreakdownItems(row);
    const byKey = new Map(breakdown.map(item => [item.key, item]));
    const totalTokens = Math.max(0, Number(row?.token_quantity) || 0);
    const cells = usageKeys.map(key => {
      const item = byKey.get(key);
      return `<td data-label="${esc(usageLabels[key])}">${debugGraphCostUsageTableCellHtml(item.tokens, item.microUsd, {apiListMicroUsd: item.apiListMicroUsd})}</td>`;
    }).join('');
    return `<tr><th scope="row">${debugGraphCostSourceLabelHtml(row)}</th>${cells}<td data-label="${esc(debugGraphCostText('debug.cost.total', 'Total'))}">${debugGraphCostUsageTableCellHtml(totalTokens, debugGraphCostMicroUsd(row), {total: true, row})}</td></tr>`;
  };
  return `<section class="js-debug-cost-details-section">
    <h2>${esc(debugGraphCostText('debug.cost.bySource', 'Agent and source attribution'))}</h2>
    <div class="js-debug-system-table-wrap js-debug-cost-table-wrap"><table class="js-debug-system-table js-debug-cost-table" data-js-debug-cost-table="source"><thead><tr><th scope="col">${esc(debugGraphCostText('debug.cost.source', 'Source'))}</th>${usageKeys.map(key => `<th scope="col"${debugGraphCostUsageColumnHeaderAttrs(key, usageLabels[key])}><span class="js-debug-cost-usage-label">${esc(usageLabels[key])}</span></th>`).join('')}<th scope="col"${debugGraphCostUsageColumnHeaderAttrs('total', debugGraphCostText('debug.cost.total', 'Total'))}><span class="js-debug-cost-usage-label">${esc(debugGraphCostText('debug.cost.total', 'Total'))}</span></th></tr></thead><tbody>${rows.map(rowHtml).join('')}</tbody></table></div>
  </section>`;
}

function debugGraphCostCatalogDetailsHtml(summary) {
  // One compact catalog-status line replacing the four-row table: every fact
  // (revision, freshness, priced coverage, unpriced exclusions) stays present
  // and localized, wraps as meaningful field groups on a narrow pane, and
  // never stretches four scalars across a wide screen. Unpriced exclusions
  // keep their warning semantics when nonzero.
  const revision = String(summary.activeCatalogRevision || summary.catalogRevision || '').trim() || '—';
  const freshness = String(summary.freshness || '').trim() || debugGraphCostText('debug.cost.unknown', 'Unknown');
  const exclusions = Math.max(0, Number(summary.unpricedCount) || 0);
  const priced = Math.max(0, Number(summary.pricedCount) || 0);
  const groups = [
    {label: debugGraphCostText('debug.cost.catalog', 'Catalog'), value: `${debugGraphCostText('debug.cost.rev', 'rev')} ${revision}`},
    {label: debugGraphCostText('debug.cost.freshnessCompact', 'freshness'), value: freshness.toLowerCase() === freshness ? freshness : freshness.charAt(0).toLowerCase() + freshness.slice(1)},
    {label: debugGraphCostText('debug.cost.coverageCompact', 'coverage'), value: `${priced}/${priced + exclusions}`},
    {label: debugGraphCostText('debug.cost.unpricedCompact', 'unpriced'), value: String(exclusions), warning: exclusions > 0},
  ];
  const accessible = [
    `${debugGraphCostText('debug.cost.catalogRevision', 'Catalog revision')}: ${revision}`,
    `${debugGraphCostText('debug.cost.freshness', 'Catalog freshness')}: ${freshness}`,
    `${debugGraphCostText('debug.cost.coverage', 'Priced coverage')}: ${priced}/${priced + exclusions}`,
    `${debugGraphCostText('debug.cost.exclusions', 'Unpriced exclusions')}: ${exclusions}`,
  ].join('; ');
  return `<p class="js-debug-cost-catalog-line" data-js-debug-cost-catalog aria-label="${esc(accessible)}">${groups.map((group, index) => `<span class="js-debug-cost-catalog-group${group.warning ? ' js-debug-cost-catalog-group--warning' : ''}">${index === 0 ? `${esc(group.label)}: ` : ''}${index > 0 ? `${esc(group.label)} ` : ''}${esc(group.value)}</span>`).join('<span class="js-debug-cost-catalog-separator" aria-hidden="true"> · </span>')}</p>`;
}

function debugGraphCostBackfillText(summary) {
  const state = String(summary?.backfill?.state || 'unknown');
  if (state === 'complete') return '';
  if (state === 'partial') return debugGraphCostText('debug.cost.backfillPartial', 'Backfill incomplete');
  if (state === 'running') return debugGraphCostText('debug.cost.backfillRunning', 'Backfill in progress');
  if (state === 'unknown') return debugGraphCostText('debug.cost.backfillUnknown', 'Backfill status unknown');
  return debugGraphCostText('debug.cost.backfillPending', 'Backfill pending');
}

function debugGraphCostUnpricedUsage(summary) {
  const rows = debugGraphCostRows(summary?.components).filter(row => row?.priced === false || Math.max(0, Number(row?.unpriced_count) || 0) > 0);
  const classesByKey = new Map();
  for (const row of rows) {
    const provider = String(row?.provider || '').trim() || debugGraphCostText('debug.cost.unknown', 'Unknown');
    const model = String(row?.model || '').trim() || debugGraphCostText('debug.cost.unknown', 'Unknown');
    const itemClass = debugGraphCostClass(row);
    const key = `${provider}\u0000${model}\u0000${itemClass}`;
    const current = classesByKey.get(key) || {provider, model, itemClass, tokenQuantity: 0};
    current.tokenQuantity += Math.max(0, Number(row?.unpriced_token_quantity) || (row?.priced === false ? Number(row?.token_quantity ?? row?.quantity) || 0 : 0));
    classesByKey.set(key, current);
  }
  const classes = [...classesByKey.values()];
  const rowsTokenQuantity = rows.reduce((total, row) => total + Math.max(0, Number(row?.unpriced_token_quantity) || (row?.priced === false ? Number(row?.token_quantity ?? row?.quantity) || 0 : 0)), 0);
  const tokenQuantity = Math.max(0, Number(summary?.unpricedTokenQuantity) || rowsTokenQuantity);
  const knownMicroUsd = debugGraphCostInteger(summary?.knownMicroUsd);
  const upperMicroUsd = Math.max(knownMicroUsd, debugGraphCostInteger(summary?.upperMicroUsd));
  return {tokenQuantity, worstCaseMicroUsd: upperMicroUsd - knownMicroUsd, classes};
}

function debugGraphCostUnknownUsageHtml(summary) {
  if (Math.max(0, Number(summary?.unpricedCount) || 0) === 0) return '';
  const usage = debugGraphCostUnpricedUsage(summary);
  const rows = [
    [debugGraphCostText('debug.cost.knownTotal', 'Known priced total'), debugGraphCostUsdText(summary?.knownMicroUsd)],
    [debugGraphCostText('debug.cost.unpricedTokens', 'Unpriced tokens'), debugGraphTokensText(usage.tokenQuantity)],
    [debugGraphCostText('debug.cost.worstCase', 'Worst-case estimate'), debugGraphCostUsdText(usage.worstCaseMicroUsd)],
  ];
  const classesLabel = debugGraphCostText('debug.cost.unpricedModels', 'Unpriced model/classes');
  const classRows = usage.classes.map(item => {
    const label = `${item.provider} · ${item.model} · ${item.itemClass}`;
    return `<tr data-js-debug-unpriced-class><th scope="row">${esc(label)}</th><td>${esc(debugGraphTokensText(item.tokenQuantity))}</td></tr>`;
  }).join('');
  const disclosure = usage.classes.length ? `<details class="js-debug-cost-unpriced-disclosure"><summary aria-label="${esc(`${classesLabel}: ${usage.classes.length}`)}"><span>${esc(classesLabel)}</span><strong>${usage.classes.length}</strong></summary><div class="js-debug-system-table-wrap js-debug-cost-table-wrap"><table class="js-debug-system-table js-debug-cost-table" data-js-debug-cost-table="unpriced-classes"><thead><tr><th scope="col">${esc(debugGraphCostText('debug.cost.modelClass', 'Provider · model · class'))}</th><th scope="col">${esc(debugGraphCostText('debug.modelTokens.label', 'Tokens'))}</th></tr></thead><tbody>${classRows}</tbody></table></div></details>` : '';
  return `<section class="js-debug-cost-details-section js-debug-cost-unknown-usage"><h2>${esc(debugGraphCostText('debug.cost.unpricedUsage', 'Unpriced usage'))}</h2><div class="js-debug-system-table-wrap js-debug-cost-table-wrap"><table class="js-debug-system-table js-debug-cost-table" data-js-debug-cost-table="unpriced"><tbody>${rows.map(([label, value]) => `<tr><th scope="row">${esc(label)}</th><td>${esc(value)}</td></tr>`).join('')}</tbody></table></div>${disclosure}</section>`;
}

function debugGraphCostReportHtml(summary, domain) {
  const hasEstimatedUsage = summary.pricedCount > 0 || summary.unpricedCount > 0 || summary.upperMicroUsd > 0 || Number(summary.apiListMicroUsd) > 0;
  const exact = hasEstimatedUsage && summary.complete === true && summary.unpricedCount === 0 && debugGraphCostInteger(summary.lowerMicroUsd) === debugGraphCostInteger(summary.upperMicroUsd);
  const hasFiniteRange = debugGraphCostInteger(summary.upperMicroUsd) > debugGraphCostInteger(summary.lowerMicroUsd);
  const total = hasEstimatedUsage ? debugGraphCostRangeUsdText(summary) : '—';
  const tokens = debugGraphCostTokenTotals(summary);
  const title = debugGraphCostText('debug.cost.details', 'Cost summary details');
  const apiListBasis = debugGraphCostText('debug.cost.atApiListPrices', 'At API list prices');
  const hasApiListCounterfactual = summary.apiListMicroUsd !== null;
  // Compact report shell: one heading line carrying the range, one totals line
  // replacing the old Summary heading + nested list, one catalog status line
  // replacing the four-row catalog table. Exact values stay reachable through
  // the accessible labels; nothing about estimate/lower-bound semantics changes.
  const estimateSentence = !hasEstimatedUsage
    ? debugGraphCostText('debug.cost.waiting', 'Waiting for priced usage')
    : hasApiListCounterfactual
      ? debugGraphCostPricePairText(summary.totalMicroUsd, summary.apiListMicroUsd)
    : (exact
      ? debugGraphCostText('debug.cost.exact', `Estimated API list-price total ${total}`, {amount: total})
      : hasFiniteRange
        ? debugGraphCostText('debug.cost.range', `Estimated API list-price range ${total}`, {amount: total})
        : debugGraphCostText('debug.cost.lowerBound', `Known estimated lower bound ${total}`, {amount: total}));
  const tokenParts = [
    `${debugGraphCostText('debug.cost.input', 'Input').toLowerCase()}=${debugGraphTokenNumberText(tokens.input)}`,
    `${debugGraphCostText('debug.cost.cache', 'Cache').toLowerCase()}=${debugGraphTokenNumberText(tokens.cache)}`,
    `${debugGraphCostText('debug.cost.output', 'Output').toLowerCase()}=${debugGraphTokenNumberText(tokens.output)}`,
    ...(Math.max(0, Number(tokens.other) || 0) > 0 ? [`${debugGraphCostText('debug.cost.other', 'Other').toLowerCase()}=${debugGraphTokenNumberText(tokens.other)}`] : []),
  ];
  const totalsLine = `${estimateSentence}, ${debugGraphCostText('debug.cost.totalTokens', 'total tokens')}: ${debugGraphTokensText(tokens.total)} (${tokenParts.join(', ')})`;
  const totalsExact = `${debugGraphCostText('debug.cost.totalTokens', 'total tokens')}: ${Math.max(0, Number(tokens.total) || 0).toLocaleString()}; ${['input', 'cache', 'output', 'other'].map(key => `${key}=${Math.max(0, Number(tokens[key]) || 0).toLocaleString()}`).join('; ')}`;
  return `<article class="js-debug-cost-report" aria-label="${esc(title)}">
    <div class="js-debug-cost-report-title">
      <h1>${esc(title)}</h1><span class="js-debug-cost-report-basis">${esc(apiListBasis)}</span><span class="js-debug-cost-report-range meta-muted">${esc(debugGraphCostRangeText(domain))}</span>
    </div>
    <div class="js-debug-cost-report-body">
      <p class="js-debug-cost-report-totals" data-js-debug-cost-report-totals aria-label="${esc(`${estimateSentence}; ${totalsExact}`)}">${esc(totalsLine)}</p>
      ${debugGraphCostUnknownUsageHtml(summary)}
      ${debugGraphCostCatalogDetailsHtml(summary)}
      ${debugGraphCostUsageColumnLegendHtml()}
      ${debugGraphCostTmuxBreakdownHtml(summary)}
      ${debugGraphCostModelUsageChartHtml(summary.models, summary.components, {report: true, summary})}
      ${debugGraphCostSourceTreeHtml(summary.sources)}
      ${debugGraphCostAllPricingSourcesHtml(summary.components)}
    </div>
  </article>`;
}

function debugGraphCostSummaryHtml(buckets, domain) {
  const summary = debugGraphCostSummaryForBuckets(buckets);
  const hasEstimatedUsage = summary.pricedCount > 0 || summary.unpricedCount > 0 || summary.upperMicroUsd > 0 || Number(summary.apiListMicroUsd) > 0;
  const exact = hasEstimatedUsage && summary.complete === true && summary.unpricedCount === 0 && debugGraphCostInteger(summary.lowerMicroUsd) === debugGraphCostInteger(summary.upperMicroUsd);
  const hasFiniteRange = debugGraphCostInteger(summary.upperMicroUsd) > debugGraphCostInteger(summary.lowerMicroUsd);
  const estimated = hasEstimatedUsage ? debugGraphCostRangeUsdText(summary) : '—';
  const compact = debugGraphCostCompactTotals(summary);
  const compactApiList = debugGraphCostCompactApiListTotals(summary);
  const tokens = debugGraphCostTokenTotals(summary);
  const heading = !hasEstimatedUsage
    ? `${debugGraphCostText('debug.cost.atApiListPrices', 'At API list prices')} —, Σ displayed`
    : summary.apiListMicroUsd !== null
      ? `${debugGraphCostText('debug.cost.atApiListPrices', 'At API list prices')} ${debugGraphCostPricePairText(summary.totalMicroUsd, summary.apiListMicroUsd)}, Σ displayed`
      : `${debugGraphCostText('debug.cost.atApiListPrices', 'At API list prices')} ${exact || hasFiniteRange ? 'est. ' : 'est. ≥'}${estimated}, Σ displayed`;
  const accessible = !hasEstimatedUsage
    ? 'No displayed usage has a selected price'
    : summary.apiListMicroUsd !== null
      ? `${debugGraphCostPricePairText(summary.totalMicroUsd, summary.apiListMicroUsd, {basis: 'inline'})} across displayed usage; open model costs and pricing sources`
    : exact
    ? `Estimated API list-price total ${estimated} across displayed usage; open model costs and pricing sources`
    : `Estimated API list-price range ${estimated}; unknown or incomplete displayed usage widens the range`;
  const refreshLabel = debugGraphCostText('common.refresh', 'Refresh');
  const refreshHtml = readOnlyMode ? '' : `<button type="button" class="js-debug-cost-refresh control-active-hover" data-js-debug-cost-refresh aria-label="${esc(refreshLabel)}" title="${esc(jsDebugPricingRefreshState.error || refreshLabel)}"${jsDebugPricingRefreshState.inFlight ? ' disabled aria-busy="true"' : ''}>${esc(jsDebugPricingRefreshState.inFlight ? `${refreshLabel}…` : refreshLabel)}</button>`;
  const refreshStatus = jsDebugPricingRefreshState.error || (jsDebugPricingRefreshState.inFlight ? (jsDebugPricingRefreshState.status || `${refreshLabel}…`) : '');
  const backfillStatus = debugGraphCostBackfillText(summary);
  const moreInfo = debugGraphCostText('debug.cost.moreInfo', 'More Info');
  const compactRows = [
    ['Input', compact.input, compactApiList.input, tokens.input],
    ['Cache', compact.cache, compactApiList.cache, tokens.cache],
    ['Output', compact.output, compactApiList.output, tokens.output],
    ['Total', hasEstimatedUsage ? summary.totalMicroUsd : null, hasEstimatedUsage ? summary.apiListMicroUsd : null, tokens.total],
  ];
  // One row shape shared by tbody (Input/Cache/Output) and tfoot (Total). The row-label cell
  // carries the same per-usage explain-attrs the old <dl> <dt> used, and prices stay concise
  // (basis stated once in the heading, so debugGraphCostPricePairHtml keeps its default omit).
  const summaryRowHtml = ([label, value, apiListValue, tokenCount]) => {
    const key = String(label).toLowerCase();
    const rowLabel = debugGraphCostText(`debug.cost.${key}`, label);
    return `<tr><th scope="row"${debugGraphCostUsageColumnHeaderAttrs(key, rowLabel)}>${esc(rowLabel)}</th><td>${esc(debugGraphTokensText(tokenCount))}</td><td>${value === null ? '—' : debugGraphCostPricePairHtml(value, apiListValue)}</td></tr>`;
  };
  return `<section class="js-debug-chart js-debug-cost-summary" data-js-debug-summary-group="costSummary">
    <div class="js-debug-chart-head">
      <div class="js-debug-chart-heading-row">
        <span class="js-debug-chart-title">${esc(debugGraphCostText('debug.cost.title', 'Cost summary'))}</span>
        <span class="js-debug-chart-summary js-debug-cost-estimate">(${esc(heading)})</span>
        ${refreshHtml}
        <button type="button" class="js-debug-chart-close control-active-hover" data-js-debug-chart-close="costSummary" aria-label="${esc(t('common.close'))} ${esc(debugGraphCostText('debug.cost.title', 'Cost summary'))}" title="${esc(t('common.close'))}">×</button>
      </div>
      <div class="js-debug-cost-range">${esc(debugGraphCostRangeText(domain))}</div>
      ${refreshStatus ? `<div class="js-debug-cost-refresh-status" role="status">${esc(refreshStatus)}</div>` : ''}
      ${backfillStatus ? `<div class="js-debug-cost-refresh-status" role="status">${esc(backfillStatus)}</div>` : ''}
    </div>
    <div class="js-debug-system-table-wrap js-debug-cost-table-wrap">
      <table class="js-debug-system-table js-debug-cost-table" data-js-debug-cost-table="summary" aria-label="${esc(debugGraphCostText('debug.cost.title', 'Cost summary'))}">
        <thead><tr><th scope="col">${esc(debugGraphCostText('debug.cost.usage', 'Usage'))}</th><th scope="col">${esc(debugGraphCostText('debug.modelTokens.label', 'Tokens'))}</th><th scope="col">${esc(debugGraphCostText('debug.cost.priceColumn', 'Price'))}</th></tr></thead>
        <tbody>${compactRows.slice(0, 3).map(summaryRowHtml).join('')}</tbody>
        <tfoot>${summaryRowHtml(compactRows[3])}</tfoot>
      </table>
    </div>
    <span class="js-debug-cost-modal-host"><button type="button" class="js-debug-cost-details control-active-hover" data-js-debug-cost-details aria-label="${esc(accessible)}">${esc(moreInfo)}</button></span>
  </section>`;
}

async function refreshDebugCostPricing() {
  if (readOnlyMode || jsDebugPricingRefreshState.inFlight) return;
  const scope = debugPricingRefreshLifecycleScope();
  jsDebugPricingRefreshState.inFlight = true;
  jsDebugPricingRefreshState.error = '';
  jsDebugPricingRefreshState.lastRequestedAtMs = Date.now();
  refreshDebugGraphSurfaces();
  try {
    const payload = await apiFetchJson('/api/pricing-catalog/refresh', {method: 'POST'});
    if (!scope.current()) return;
    jsDebugPricingRefreshState.status = String(payload?.status || 'running');
    if (jsDebugPricingRefreshState.status === 'running') {
      scheduleDebugCostPricingStatusRefresh();
    } else {
      jsDebugPricingRefreshState.inFlight = false;
    }
  } catch (error) {
    if (!scope.current()) return;
    jsDebugPricingRefreshState.inFlight = false;
    jsDebugPricingRefreshState.error = userMessageText(error, t('common.requestFailed'));
  } finally {
    if (scope.current()) refreshDebugGraphSurfaces();
  }
}

function scheduleDebugCostPricingStatusRefresh() {
  if (!jsDebugCostSubviewVisible()) return false;
  if (jsDebugPricingRefreshState.timer !== null) debugPricingRefreshLifecycleScope().release('status', jsDebugPricingRefreshState.timer);
  const scope = debugPricingRefreshLifecycleScope();
  const timer = setTimeout(() => {
    if (!scope.current() || jsDebugPricingRefreshState.timer !== timer) return;
    scope.relinquish('status', timer);
    jsDebugPricingRefreshState.timer = null;
    void refreshDebugCostPricingStatus(scope);
  }, 750);
  jsDebugPricingRefreshState.timer = timer;
  scope.ownTimer('status', timer);
  return true;
}

function disposeDebugPricingRefreshLifecycle(reason = 'disposed') {
  jsDebugPricingRefreshLifecycleScope.dispose(reason);
  jsDebugPricingRefreshState.timer = null;
}

async function refreshDebugCostPricingStatus(scope = debugPricingRefreshLifecycleScope()) {
  try {
    const payload = await apiFetchJson('/api/pricing-catalog', {cache: 'no-store'});
    if (!scope.current()) return false;
    const refresh = payload?.refresh && typeof payload.refresh === 'object' ? payload.refresh : {};
    const status = String(refresh.status || 'idle');
    jsDebugPricingRefreshState.status = status;
    jsDebugPricingRefreshState.error = status === 'failed' ? String(refresh.error || t('common.requestFailed')) : '';
    jsDebugPricingRefreshState.inFlight = status === 'running';
    if (jsDebugPricingRefreshState.inFlight) scheduleDebugCostPricingStatusRefresh();
  } catch (error) {
    if (!scope.current()) return false;
    jsDebugPricingRefreshState.inFlight = false;
    jsDebugPricingRefreshState.error = userMessageText(error, t('common.requestFailed'));
  }
  if (scope.current()) refreshDebugGraphSurfaces();
  return true;
}

function debugGraphSvgHtml(buckets, seriesItems, chartGroups = debugGraphVisibleChartGroups(seriesItems), nowMs = Date.now(), {includeCostSummary = true, patternScope = 'graphs'} = {}) {
  const domain = debugGraphDomain(nowMs);
  const overlayBuckets = debugGraphSourceBuckets(domain);
  const disconnectedRanges = debugGraphDisconnectedRanges(overlayBuckets, domain);
  const tokenBuckets = debugGraphAgentTokenDisplayBuckets(nowMs);
  const spikeAxis = debugGraphTokenSpikeAxisDescriptor(tokenBuckets);
  const visibleGroupKeys = new Set(chartGroups.map(group => group.key));
  const gridHtml = jsDebugGraphChartGroups.flatMap(group => {
      const groupBuckets = debugGraphBucketsForChartGroup(group, buckets, nowMs);
      const groupSeriesItems = groupBuckets === buckets ? seriesItems : debugGraphSeriesData(groupBuckets);
      const items = visibleGroupKeys.has(group.key)
        ? [debugGraphChartHtml(group, groupSeriesItems, domain, groupBuckets, overlayBuckets, disconnectedRanges, {spikeAxis, patternScope: `${patternScope}-${group.key}`})]
        : [];
      // This is deliberately a non-chart sibling: it consumes precisely the Model tokens/min
      // displayed bucket array from the unified cache, but adds no axes, bars, or
      // independent range state.
      if (includeCostSummary && group.key === 'modelTokens' && debugGraphChartVisible('costSummary')) {
        items.push(debugGraphCostSummaryHtml(groupBuckets, domain));
      }
      return items;
    }).join('');
  return debugGraphChartShellHtml(gridHtml, domain);
}

function debugGraphClassName(nowMs = Date.now()) {
  return `js-debug-graph${debugGraphDisplayBuckets(nowMs).length ? '' : ' js-debug-graph--empty'}${debugGraphZoomDomainValid() ? ' js-debug-graph--zoomed' : ''}`;
}

function debugGraphBodyHtml(nowMs = Date.now()) {
  loadJsDebugStatsUiPreferences();
  activeJsDebugGraphRangeSeconds(nowMs);
  const meta = debugGraphMetaHtml();
  const clientPerf = debugClientPerfHtml();
  const buckets = debugGraphDisplayBuckets(nowMs);
  if (!buckets.length) {
    const empty = debugGraphWaitingForServerStats() ? '' : `<div class="js-debug-graph-empty">${esc(t('debug.empty'))}</div>`;
    const loadingShell = jsDebugHistoryReadiness.overlayVisible === true || jsDebugHistoryReadinessBusy()
      ? debugGraphChartShellHtml('', debugGraphDomain(nowMs))
      : '';
    return `${clientPerf}${empty}${loadingShell}${meta}`;
  }
  const seriesItems = debugGraphSeriesData(buckets);
  const chartGroups = debugGraphVisibleChartGroups(seriesItems);
  return `${clientPerf}${debugGraphSvgHtml(buckets, seriesItems, chartGroups, nowMs)}${meta}`;
}

function debugGraphInnerHtml(nowMs = Date.now()) {
  return `${debugGraphControlsHtml(nowMs)}<div data-js-debug-graph-body>${debugGraphBodyHtml(nowMs)}</div>`;
}

function debugGraphHtml() {
  const nowMs = Date.now();
  return `<div class="${debugGraphClassName(nowMs)}" data-js-debug-graph data-js-debug-graph-rendered-at="${esc(nowMs)}" data-js-debug-history-state="${esc(jsDebugHistoryReadinessStateName())}" aria-busy="${jsDebugHistoryReadinessBusy() ? 'true' : 'false'}" aria-label="${esc(t('debug.summary'))}">${debugGraphInnerHtml(nowMs)}</div>`;
}

function debugGraphBucketSummary(nowMs = Date.now()) {
  activeJsDebugGraphRangeSeconds(nowMs);
  const domain = debugGraphDomain(nowMs, debugRuntimeState.graphRangeSeconds);
  const buckets = debugGraphDisplayBuckets(nowMs, {rangeSeconds: debugRuntimeState.graphRangeSeconds});
  const availableRangeSeconds = debugGraphAvailableRangeOptions(nowMs).map(option => option.seconds);
  // rawBuckets/rollupBuckets survive as derived diagnostics of the ONE bucket Map:
  // "raw" is the finest (sub-middle-tier) durations, "rollup" everything coarser.
  const cachedBuckets = [...jsDebugGraphBuckets.values()];
  return {
    rawBuckets: cachedBuckets.filter(bucket => bucket.durationMs < jsDebugGraphMiddleBucketMs).length,
    rollupBuckets: cachedBuckets.filter(bucket => bucket.durationMs >= jsDebugGraphMiddleBucketMs).length,
    middleBuckets: cachedBuckets.filter(bucket => bucket.durationMs === jsDebugGraphMiddleBucketMs).length,
    oldBuckets: cachedBuckets.filter(bucket => bucket.durationMs === jsDebugGraphRollupBucketMs).length,
    tierBucketCounts: jsDebugGraphTiers.map(tier => cachedBuckets.filter(bucket => bucket.durationMs === tier.bucketMs).length),
    displayBucketSeconds: [...new Set(buckets.map(bucket => bucket.durationMs / 1000))].sort((left, right) => left - right),
    agentTokenDisplayFloorSeconds: Math.max(jsDebugGraphAgentTokenBucketSeconds, debugGraphAgentTokenResolution(nowMs)),
    displayBuckets: buckets.length,
    resolutionSeconds: debugGraphDisplayResolutionMs(domain, 0, nowMs) / 1000,
    rangeSeconds: debugRuntimeState.graphRangeSeconds,
    zoomed: debugGraphZoomDomainValid(),
    zoomRangeSeconds: debugGraphZoomDomainValid() ? (Number(jsDebugGraphZoomDomain.endMs) - Number(jsDebugGraphZoomDomain.startMs)) / 1000 : 0,
    availableRangeSeconds,
    retentionHours: jsDebugGraphRetentionMs / 60 / 60 / 1000,
    rawWindowSeconds: jsDebugGraphRawWindowMs / 1000,
    middleWindowSeconds: jsDebugGraphMiddleWindowMs / 1000,
    middleBucketSeconds: jsDebugGraphMiddleBucketMs / 1000,
    rollupBucketSeconds: jsDebugGraphRollupBucketMs / 1000,
    tiers: jsDebugGraphTiers.map(tier => ({maxAgeSeconds: tier.maxAgeMs / 1000, bucketSeconds: tier.bucketMs / 1000})),
    serverSequence: jsDebugStatsServerSequence,
    pendingServerBuckets: jsDebugGraphPendingServerBuckets.size,
    disconnectedBuckets: buckets.filter(bucket => Number(bucket.disconnectedMs || 0) > 0).length,
    clientId: jsDebugStatsClientIdForRequest(),
    uptimeSeconds: jsDebugStatsServerUptimeSeconds,
    series: jsDebugGraphSeries.map(series => series.key),
    charts: debugGraphVisibleChartGroups(debugGraphSeriesData(buckets)).map(group => group.key),
  };
}

function recordJsDebugCurrentStatsFailure(failure) {
  if (jsDebugCurrentStatsClientState.failureLatched) return false;
  jsDebugCurrentStatsClientState.failureLatched = true;
  const message = String(failure?.message || 'YO!stats stream unavailable').replace(/\s+/g, ' ').trim().slice(0, 160);
  const source = String(failure?.source || '/api/stats-stream').slice(0, 160);
  recordJsDebugStatsDiagnostic('warning', message, {
    category: 'stats_stream',
    requestId: String(failure?.requestId || failure?.request_id || '').slice(0, 128),
    route: source,
    eventType: 'stats-generation',
    deliveryOutcome: /(?:stalled|missing)/i.test(message) ? 'stalled' : 'failed',
  });
  return true;
}

// The page tearing its own stream down is an expected outcome, not a defect, so it is
// recorded at `info`: `jsDebugFailureClassification` only treats a `stats_history` event
// as release-blocking at `warning`/`error`, so this creates no durable receipt and no
// browser failure. It is still recorded, with a machine-readable `reason` naming the
// lifecycle event that caused it, so the close is never silently discarded. It does not
// touch `failureLatched`: a genuine failure after a surviving unload must still report.
function recordJsDebugCurrentStatsRetirement(retirement) {
  const reason = String(retirement?.reason || 'page_unload').slice(0, 32);
  const source = String(retirement?.source || '/api/stats-stream').slice(0, 160);
  recordJsDebugStatsDiagnostic('info', `stream closed by page retirement (${reason})`, {
    category: 'stats_stream',
    route: source,
    eventType: 'stats-generation',
    deliveryOutcome: 'retired',
    reason,
  });
  return true;
}

function acceptJsDebugCurrentStatsPushProof() {
  jsDebugCurrentStatsClientState.failureLatched = false;
}

function jsDebugStatsPanelVisible() {
  return debugModeEnabled === true
    && document.visibilityState !== 'hidden'
    && typeof itemIsActivePaneTab === 'function'
    && itemIsActivePaneTab(debugPaneItemId);
}

function jsDebugStatsDocumentVisible() {
  return document.visibilityState !== 'hidden';
}

function jsDebugStatsLayoutItemsVisible(items) {
  return Array.isArray(items) && items.includes(debugPaneItemId);
}

function jsDebugCurrentStatsSelection() {
  return {
    rangeSeconds: normalizedJsDebugGraphRange(debugRuntimeState.graphRangeSeconds),
    resolution: normalizedDebugGraphResolutionOverrideSeconds(debugRuntimeState.graphResolutionOverrideSeconds) || 'AUTO',
  };
}

function jsDebugCurrentStatsGenerationKey(snapshot) {
  if (!snapshot) return '';
  // AUTO and its explicit twin may share one transport cursor, but each
  // requested selection still owns a distinct readiness transition and paint.
  return [snapshot.range_seconds, snapshot.requested_resolution, snapshot.resolution_seconds, snapshot.source_generation, snapshot.cache_generation].join(':');
}

function jsDebugCurrentStatsStreamEvidence() {
  const client = jsDebugCurrentStatsClientState.client;
  const controller = client?.controller?.() || null;
  const generation = controller?.generation?.() || null;
  const stream = typeof client?.streamEvidence === 'function' ? client.streamEvidence() : null;
  return {
    moduleReady: typeof globalThis.YOLOmuxStatsCurrent?.createBrowserClient === 'function',
    clientReady: client !== null,
    controllerReady: controller !== null,
    generationReady: generation !== null,
    panelVisible: jsDebugStatsPanelVisible(),
    paintedGenerationKey: String(jsDebugCurrentStatsClientState.paintedGenerationKey || ''),
    stream,
  };
}

function commitJsDebugCurrentStatsPaint() {
  const key = String(jsDebugCurrentStatsClientState.pendingGenerationKey || '');
  if (!key) return '';
  jsDebugCurrentStatsClientState.paintedGenerationKey = key;
  jsDebugCurrentStatsClientState.pendingGenerationKey = '';
  return key;
}

function paintJsDebugCurrentStatsGeneration(snapshot, {forceGraphRefresh = true} = {}) {
  if (!snapshot || !jsDebugStatsPanelVisible()) return false;
  const key = jsDebugCurrentStatsGenerationKey(snapshot);
  if (key && [jsDebugCurrentStatsClientState.paintedGenerationKey, jsDebugCurrentStatsClientState.pendingGenerationKey].includes(key)) return false;
  jsDebugCurrentStatsClientState.pendingGenerationKey = key;
  try {
    applyJsDebugCurrentSnapshot(snapshot, {forceGraphRefresh});
  } catch (error) {
    if (jsDebugCurrentStatsClientState.pendingGenerationKey === key) jsDebugCurrentStatsClientState.pendingGenerationKey = '';
    throw error;
  }
  return true;
}

function ensureJsDebugCurrentStatsClient() {
  if (jsDebugCurrentStatsClientState.client) return jsDebugCurrentStatsClientState.client;
  if (typeof globalThis.YOLOmuxStatsCurrent?.createBrowserClient !== 'function') return null;
  loadJsDebugStatsUiPreferences();
  const selection = jsDebugCurrentStatsSelection();
  const client = globalThis.YOLOmuxStatsCurrent.createBrowserClient({
    fetch: apiFetch,
    clientId: jsDebugStatsClientIdForRequest(),
    savedRange: selection.rangeSeconds,
    savedResolution: selection.resolution,
    controllerOptions: {
      onFailure: recordJsDebugCurrentStatsFailure,
      onRetirement: recordJsDebugCurrentStatsRetirement,
      onPushProof: acceptJsDebugCurrentStatsPushProof,
      onGeneration(snapshot) {
        paintJsDebugCurrentStatsGeneration(snapshot);
      },
    },
    onState(state, error) {
      if (state !== 'error') return;
      const liveSelection = jsDebugCurrentStatsSelection();
      const recoverableReadFence = error?.recoverableReadFence === true;
      const requiredProtocol = Number(error?.requiredProtocolVersion) || 0;
      const requiredSchema = Number(error?.requiredSchemaGeneration) || 0;
      const fenceDetail = requiredProtocol && requiredSchema
        ? ` (service protocol ${requiredProtocol}, schema ${requiredSchema})`
        : '';
      setJsDebugHistoryReadiness('error', {
        requestedRangeSeconds: liveSelection.rangeSeconds,
        error: recoverableReadFence
          ? `YO!stats service is updating${fenceDetail}; retrying automatically.`
          : String(error?.reason || error?.message || 'Current stats stream unavailable'),
        nextAutoRetryAtMs: performanceNow() + jsDebugHistoryRetryInitialDelayMs,
      });
    },
  });
  jsDebugCurrentStatsClientState.client = client;
  jsDebugCurrentStatsClientState.selectionKey = `${selection.rangeSeconds}:${selection.resolution}`;
  return client;
}

function syncJsDebugCurrentStatsClient({select = false} = {}) {
  loadJsDebugStatsUiPreferences();
  let client;
  try {
    client = ensureJsDebugCurrentStatsClient();
  } catch (error) {
    recordJsDebugCurrentStatsFailure({
      message: `YO!stats stream initialization unavailable: ${jsDebugErrorText(error)}`,
      source: '/api/stats-stream',
    });
    return true;
  }
  if (!client) return false;
  const documentVisible = jsDebugStatsDocumentVisible();
  client.setVisible(documentVisible);
  if (!documentVisible) return true;
  const selection = jsDebugCurrentStatsSelection();
  const key = `${selection.rangeSeconds}:${selection.resolution}`;
  const controller = client.controller?.();
  const currentSelection = controller?.selection?.();
  const cachedSelection = controller?.generation?.()
    && Number(currentSelection?.range_seconds) === Number(selection.rangeSeconds)
    && String(currentSelection?.resolution) === String(selection.resolution);
  if (select || key !== jsDebugCurrentStatsClientState.selectionKey) {
    jsDebugCurrentStatsClientState.selectionKey = key;
    if (!cachedSelection) client.select(selection.rangeSeconds, selection.resolution);
  }
  paintJsDebugCurrentStatsGeneration(controller?.generation?.());
  if (!jsDebugCurrentStatsClientState.startPromise) {
    try {
      jsDebugCurrentStatsClientState.startPromise = Promise.resolve(client.start())
        .catch(error => {
          recordJsDebugCurrentStatsFailure({
            message: `YO!stats stream initialization unavailable: ${jsDebugErrorText(error)}`,
            source: '/api/stats-stream',
          });
        })
        .finally(() => { jsDebugCurrentStatsClientState.startPromise = null; });
    } catch (error) {
      recordJsDebugCurrentStatsFailure({
        message: `YO!stats stream initialization unavailable: ${jsDebugErrorText(error)}`,
        source: '/api/stats-stream',
      });
    }
  }
  return true;
}

function jsDebugStatsTokenConsumerEnabled() {
  return jsDebugStatsPanelVisible();
}

function stopJsDebugStatsPolling() {
  clearRuntimeInterval('debug-stats');
  if (jsDebugCurrentStatsClientState.client) {
    jsDebugCurrentStatsClientState.client.setVisible(jsDebugStatsDocumentVisible());
  }
}

function jsDebugStatsLivePushEnabled() {
  // A drag zoom is a fixed historical domain. The shared range slider is the
  // live-tail owner for both YO!stats and YO!cost; only its 5m/15m views need
  // every durable one-second push.
  return !debugGraphZoomDomainValid() && debugRuntimeState.graphRangeSeconds < jsDebugStatsLivePushRangeSeconds;
}

function jsDebugStatsPollIntervalMs() {
  if (!jsDebugStatsPollState.firstSampleReceived) return jsDebugStatsPollFastMs;
  return jsDebugStatsLivePushEnabled() ? jsDebugStatsPollMs : jsDebugStatsCoarsePollMs;
}

function syncJsDebugStatsDeliveryMode() {
  if (typeof syncClientEventDemand === 'function') syncClientEventDemand({immediate: true});
  // Re-entering a short live range can follow almost a minute without stats
  // SSE. Fetch once now so the existing history merger fills that delivery
  // gap before subsequent one-second pushes arrive.
  armJsDebugStatsPolling({pollNow: jsDebugStatsLivePushEnabled(), forceGraphRefresh: true});
}

function armJsDebugStatsPolling({pollNow = false, forceGraphRefresh = false} = {}) {
  if (!jsDebugStatsPanelVisible()) {
    stopJsDebugStatsPolling();
    return;
  }
  clearRuntimeInterval('debug-stats');
  if (jsDebugGraphExactResolutionEnabled && syncJsDebugCurrentStatsClient()) return;
  if (pollNow) void pollJsDebugStatsSample({forceGraphRefresh});
  resetRuntimeInterval('debug-stats', pollJsDebugStatsOnInterval, jsDebugStatsPollIntervalMs());
}

function pollJsDebugStatsOnInterval() {
  // Passive cadence ticks never need to queue behind an explicit range,
  // activation, or initial request. The next full interval is soon enough and
  // avoids a slow request degenerating into an immediate back-to-back fetch.
  maybePrefetchJsDebugHistory();
  if (jsDebugStatsPollState.inFlight) return;
  return pollJsDebugStatsSample();
}

// Fire the full-retention prefetch once shortly after the current range lands, then on a
// slow cadence. The poll loop stops when the panel is hidden and the prefetch itself is
// retired (see below), so neither does any work while the panel is hidden. This is only the
// poll/prefetch path: the live SSE stream is separate and tracks DOCUMENT visibility, not the
// active tab, so a hidden panel on a still-visible page keeps applying deltas (it just does
// not repaint); only a hidden document tears the stream down.
function maybePrefetchJsDebugHistory() {
  if (!jsDebugStatsPollState.firstSampleReceived) return;
  if (jsDebugHistoryPrefetchState.inFlight) return;
  const nowMs = performanceNow();
  const due = !jsDebugHistoryPrefetchState.didInitial
    || (nowMs - Number(jsDebugHistoryPrefetchState.lastFullPrefetchAtMs || 0)) >= jsDebugHistoryPrefetchIntervalMs;
  if (!due) return;
  jsDebugHistoryPrefetchState.didInitial = true;
  void prefetchJsDebugHistoryFullRetention();
}

// Silent cache-fill of the whole retention window. Populates ONLY the shared bucket Map
// (jsDebugGraphBuckets) so a later range switch renders
// cached content instantly. Deliberately does NOT touch jsDebugHistoryReadiness, the
// overlay, coverage, or the live cursor: the current view owns loading state, and the
// normal poll revalidates the switched-to range's fresh tail on top of this cache.
// Finest-source-wins at render keeps the live 1s/10s tail intact (no replaceCoverage,
// so no fine buckets are removed).
async function prefetchJsDebugHistoryFullRetention() {
  // The current server pre-materializes every supported Range/Resolution cell.
  // A hidden full-retention prefetch only duplicates work and can starve unrelated requests.
  return false;
}

function jsDebugStatsHistoryTimeoutMs(rangeSeconds = 0) {
  const rangeHoursBeyondFirst = Math.max(0, Math.ceil(Math.max(0, Number(rangeSeconds) || 0) / 3600) - 1);
  return Math.min(jsDebugStatsHistoryMaxTimeoutMs, jsDebugStatsPollTimeoutMs + (rangeHoursBeyondFirst * 1000));
}

function jsDebugStatsTimeoutError(timeoutMs) {
  const error = new Error(`history request timed out after ${Math.round(timeoutMs / 1000)}s`);
  error.name = 'TimeoutError';
  return error;
}

async function fetchJsDebugStatsJson(url, options = {}) {
  const controller = typeof AbortController === 'function' ? new AbortController() : null;
  const phaseTimings = {};
  const timeoutMs = Math.max(1, Number(options.timeoutMs) || jsDebugStatsPollTimeoutMs);
  const requestOptions = {...options};
  delete requestOptions.timeoutMs;
  let timeoutId = null;
  let timeoutError = null;
  try {
    if (controller && typeof setTimeout === 'function') {
      timeoutId = setTimeout(() => {
        timeoutError = jsDebugStatsTimeoutError(timeoutMs);
        controller.abort(timeoutError);
      }, timeoutMs);
    }
    return await apiFetchJsonQuiet(url, {...requestOptions, ...(controller ? {signal: controller.signal} : {})}, phaseTimings);
  } catch (error) {
    if (controller?.signal?.aborted && timeoutError) throw timeoutError;
    throw error;
  } finally {
    if (timeoutId !== null && typeof clearTimeout === 'function') clearTimeout(timeoutId);
    if (Number.isFinite(phaseTimings.fetchMs)) recordClientPerfCounter('statsHistoryFetch', phaseTimings.fetchMs);
    if (Number.isFinite(phaseTimings.parseMs)) recordClientPerfCounter('statsHistoryParse', phaseTimings.parseMs);
  }
}

async function paintJsDebugHistoryResponse(generation, requestedRangeSeconds, requestedStartSeconds) {
  await nextAnimationFrame();
  if (!jsDebugHistoryRequestIsCurrent(generation, requestedRangeSeconds, requestedStartSeconds)) return false;
  for (const graph of document.querySelectorAll('[data-js-debug-graph]')) {
    refreshDebugGraphElement(graph, {force: true});
  }
  const paintStartedAt = performanceNow();
  await nextAnimationFrame();
  recordClientPerfCounter('statsHistoryPaint', performanceNow() - paintStartedAt);
  if (!jsDebugHistoryRequestIsCurrent(generation, requestedRangeSeconds, requestedStartSeconds)) return false;
  const recoveredRetry = jsDebugHistoryReadiness.reason === 'retry';
  setJsDebugHistoryReadiness('ready', {
    requestedRangeSeconds,
    requestedStartSeconds,
    error: '',
    nextAutoRetryAtMs: 0,
  });
  if (recoveredRetry) recordJsDebugStatsDiagnostic('info', 'retry exited after durable history coverage recovered');
  return true;
}

function jsDebugCurrentSeriesValue(series, name) {
  const value = Number(series?.[name]?.value);
  return Number.isFinite(value) && value >= 0 ? value : null;
}

function jsDebugCurrentCostDimensionRows(dimensions = {}) {
  const cacheWrite = dimensions.cache_write || {
    tokens: Number(dimensions.cache_write_5m?.tokens || 0) + Number(dimensions.cache_write_1h?.tokens || 0),
    micro_usd: Number(dimensions.cache_write_5m?.micro_usd || 0) + Number(dimensions.cache_write_1h?.micro_usd || 0),
    api_list_micro_usd: Number(dimensions.cache_write_5m?.api_list_micro_usd || 0) + Number(dimensions.cache_write_1h?.api_list_micro_usd || 0),
  };
  const values = {
    input: dimensions.input,
    cache_read: dimensions.cache_read,
    // The shared usage grid spans 5m/1h cache writes. Retain both lifetime
    // fields so Cost by Model can show the authoritative rate formula.
    cache_write: cacheWrite,
    cache_write_5m: dimensions.cache_write_5m || dimensions.cache_write,
    cache_write_1h: dimensions.cache_write_1h,
    cache: {
      tokens: Number(dimensions.cache_read?.tokens || 0) + Number(cacheWrite.tokens || 0),
      micro_usd: Number(dimensions.cache_read?.micro_usd || 0) + Number(cacheWrite.micro_usd || 0),
      api_list_micro_usd: Number(dimensions.cache_read?.api_list_micro_usd || 0) + Number(cacheWrite.api_list_micro_usd || 0),
    },
    output: dimensions.output,
    other: dimensions.other,
  };
  return Object.fromEntries(Object.entries(values).flatMap(([key, value]) => value ? [
    [`${key}_tokens`, Math.max(0, Number(value.tokens) || 0)],
    [`${key}_micro_usd`, Math.max(0, Number(value.micro_usd) || 0)],
    [`${key}_api_list_micro_usd`, Math.max(0, Number(value.api_list_micro_usd) || 0)],
  ] : []));
}

function jsDebugCurrentCostSummary(report = {}) {
  const priced = report.priced || {};
  const unpriced = report.unpriced || {};
  const modelRows = Array.isArray(report.models) ? report.models.map(row => ({
    provider: row.provider,
    model: row.model,
    label: row.model,
    token_quantity: Number(row.total_tokens) || 0,
    micro_usd: Number(row.total_micro_usd) || 0,
    api_list_micro_usd: Number(row.total_api_list_micro_usd) || 0,
    lower_micro_usd: Number(row.total_micro_usd) || 0,
    upper_micro_usd: Number(row.total_micro_usd) || 0,
    ...jsDebugCurrentCostDimensionRows(row.dimensions),
    priced_count: Math.max(0, Number(row.priced?.atoms) || 0),
    priced_token_quantity: Math.max(0, Number(row.priced?.tokens) || 0),
    unpriced_count: Math.max(0, Number(row.unpriced?.atoms) || 0),
    unpriced_token_quantity: Math.max(0, Number(row.unpriced?.tokens) || 0),
  })) : [];
  const sourceRows = Array.isArray(report.agents) ? report.agents.map(row => ({
    tmux_key: row.key,
    tmux_label: debugGraphAgentDisplayLabel(row.label || row.source),
    agent_kind: row.source,
    agent_label: row.label || row.source,
    full_label: row.label || row.source,
    source: row.source,
    label: debugGraphAgentDisplayLabel(row.label || row.source),
    token_quantity: Number(row.total_tokens) || 0,
    micro_usd: Number(row.total_micro_usd) || 0,
    api_list_micro_usd: Number(row.total_api_list_micro_usd) || 0,
    lower_micro_usd: Number(row.total_micro_usd) || 0,
    upper_micro_usd: Number(row.total_micro_usd) || 0,
    ...jsDebugCurrentCostDimensionRows(row.dimensions),
    priced_count: Math.max(0, Number(row.priced?.atoms) || 0),
    priced_token_quantity: Math.max(0, Number(row.priced?.tokens) || 0),
    unpriced_count: Math.max(0, Number(row.unpriced?.atoms) || 0),
    unpriced_token_quantity: Math.max(0, Number(row.unpriced?.tokens) || 0),
  })) : [];
  const components = Array.isArray(report.evidence) ? report.evidence.map(row => ({
    ...row,
    quantity: Number(row.tokens) || 0,
    token_quantity: Number(row.tokens) || 0,
    micro_usd: Number(row.micro_usd) || 0,
    api_list_micro_usd: Number(row.api_list_micro_usd) || 0,
    lower_micro_usd: Number(row.micro_usd) || 0,
    upper_micro_usd: Number(row.micro_usd) || 0,
    priced: true,
  })) : [];
  const totalMicroUsd = Math.max(0, Number(report.total_micro_usd) || 0);
  const totalApiListMicroUsd = Math.max(0, Number(report.total_api_list_micro_usd) || 0);
  return {
    range_report: true,
    total_micro_usd: totalMicroUsd,
    api_list_micro_usd: totalApiListMicroUsd,
    total_token_quantity: Math.max(0, Number(report.total_tokens) || 0),
    dimension_totals: jsDebugCurrentCostDimensionRows(report.dimensions),
    known_micro_usd: totalMicroUsd,
    lower_micro_usd: totalMicroUsd,
    upper_micro_usd: totalMicroUsd,
    priced_count: Math.max(0, Number(priced.atoms) || 0),
    complete: Math.max(0, Number(unpriced.tokens) || 0) === 0,
    unpriced_count: Math.max(0, Number(unpriced.atoms) || 0),
    unpriced_token_quantity: Math.max(0, Number(unpriced.tokens) || 0),
    components,
    models: modelRows,
    sources: sourceRows,
    tmux_windows: sourceRows,
    catalog_revision: String(report.catalog_revision ?? ''),
    active_catalog_revision: String(report.catalog_revision ?? ''),
    freshness: 'current',
  };
}

function jsDebugCurrentModelComponent(dimension, model, rate, duration) {
  const tokens = Math.max(0, Number(rate) || 0) * Math.max(1, Number(duration) || 1) / 60;
  const values = {
    input: {direction: 'input', cache_role: 'none'},
    cache_read: {direction: 'input', cache_role: 'read'},
    cache_write: {direction: 'input', cache_role: 'write'},
    output: {direction: 'output', cache_role: 'none'},
    other: {direction: 'other', cache_role: 'none'},
  }[dimension] || {direction: 'other', cache_role: 'none'};
  return {provider: '', model, modality: 'text', unit: 'tokens', quantity: tokens, token_quantity: tokens, micro_usd: 0, lower_micro_usd: 0, upper_micro_usd: 0, priced: true, ...values};
}

function jsDebugCurrentCpuProjectionValue(series, averageName, maximumName, duration) {
  const average = jsDebugCurrentSeriesValue(series, averageName);
  if (average === null) return null;
  const maximum = jsDebugCurrentSeriesValue(series, maximumName);
  return duration >= 60 && maximum !== null ? maximum : average;
}

function jsDebugCurrentServiceLoadItem(record, source) {
  const serviceLoad = record.host_metrics.service_load;
  if (!serviceLoad[source]) {
    serviceLoad[source] = {
      label: source,
      cpu_total_percent: 0,
      cpu_samples: 0,
      cpu_min_percent: null,
      cpu_max_percent: null,
      rss_total_bytes: 0,
      rss_samples: 0,
      rss_min_bytes: 0,
      rss_max_bytes: 0,
    };
  }
  return serviceLoad[source];
}

function jsDebugCurrentBucketRecord(bucket, includeRangeCost = false, rangeCost = null) {
  const series = bucket?.series || {};
  const duration = Math.max(1, Number(bucket?.duration) || 1);
  const record = {
    start: Number(bucket?.start) || 0,
    duration,
    clients: {},
    servers: {},
    host_metrics: {gpu_devices: {}, service_load: {}},
    agent_token_rates: [],
  };
  const agentRates = new Map();
  const modelRates = {};
  const modelComponents = [];
  let bucketMarginalMicroUsd = null;
  let bucketApiListMicroUsd = null;
  let bucketUsageTokens = null;
  for (const name of Object.keys(series)) {
    const value = jsDebugCurrentSeriesValue(series, name);
    if (value === null) continue;
    if (name === 'system_cpu_percent') {
      record.system_cpu_total_percent = jsDebugCurrentCpuProjectionValue(series, name, 'system_cpu_max_percent', duration);
      record.system_cpu_count = 1;
    } else if (name.startsWith('cpu_percent:')) {
      const source = name.slice('cpu_percent:'.length);
      const projected = jsDebugCurrentCpuProjectionValue(series, name, `cpu_max_percent:${source}`, duration);
      record.servers[source] = {label: source, cpu_total_percent: projected, cpu_count: 1};
      // No serving-port preference: the exact serving port owns the solid CPU series in
      // debugGraphProcessCpuSeriesDefs, so this aggregate is just the first published sample.
      if (!record.cpu_count) {
        record.cpu_total_percent = value;
        record.cpu_count = 1;
      }
    } else if (name === 'ask_agents') record.ask_agent_total = value;
    else if (name === 'run_agents') record.run_agent_total = value;
    else if (name === 'transition_agents') record.transition_agent_total = value;
    else if (name === 'idle_agents') record.idle_agent_total = value;
    else if (name === 'ask_sessions') record.ask_session_total = value;
    else if (name === 'run_sessions') record.run_session_total = value;
    else if (name === 'transition_sessions') record.transition_session_total = value;
    else if (name === 'idle_sessions') record.idle_session_total = value;
    else if (name === 'agent_window_snapshot_revision') record.agent_window_snapshot_revision = value;
    else if (name === 'system_memory_used_bytes') {
      record.host_metrics.system_memory_used_total_bytes = value;
      record.host_metrics.system_memory_count = 1;
    } else if (name === 'system_memory_capacity_bytes') {
      record.host_metrics.system_memory_capacity_total_bytes = value;
      record.host_metrics.system_memory_count = 1;
    } else if (name.startsWith('mac_')) {
      const macMemorySeries = {
        mac_physical_memory_bytes: 'mac_physical_memory_total_bytes', mac_memory_used_bytes: 'mac_memory_used_total_bytes',
        mac_cached_files_bytes: 'mac_cached_files_total_bytes', mac_swap_used_bytes: 'mac_swap_used_total_bytes',
        mac_app_memory_bytes: 'mac_app_memory_total_bytes', mac_wired_memory_bytes: 'mac_wired_memory_total_bytes',
        mac_compressed_memory_bytes: 'mac_compressed_memory_total_bytes', mac_pressure_percent: 'mac_pressure_total_percent',
        mac_pressure_level: 'mac_pressure_level',
      };
      const target = macMemorySeries[name];
      if (target) {
        record.host_metrics[target] = value;
        record.host_metrics.mac_memory_count = 1;
      }
    } else if (name.startsWith('gpu_util_percent:')) {
      const source = name.slice('gpu_util_percent:'.length);
      const device = record.host_metrics.gpu_devices[source] || {label: source, util_total_percent: 0, memory_used_total_bytes: 0, memory_capacity_total_bytes: 0, samples: 1};
      device.util_total_percent = value;
      record.host_metrics.gpu_devices[source] = device;
    } else if (name.startsWith('gpu_memory_bytes:')) {
      const source = name.slice('gpu_memory_bytes:'.length);
      const device = record.host_metrics.gpu_devices[source] || {label: source, util_total_percent: 0, memory_used_total_bytes: 0, memory_capacity_total_bytes: 0, samples: 1};
      device.memory_used_total_bytes = value;
      record.host_metrics.gpu_devices[source] = device;
    } else if (name.startsWith('service_cpu_percent:')) {
      const source = name.slice('service_cpu_percent:'.length);
      const sourceCount = Math.max(1, Number(series[name]?.source_count) || 1);
      const service = jsDebugCurrentServiceLoadItem(record, source);
      Object.assign(service, {cpu_total_percent: value * sourceCount, cpu_samples: sourceCount});
    } else if (name.startsWith('service_cpu_min_percent:')) {
      const source = name.slice('service_cpu_min_percent:'.length);
      const service = jsDebugCurrentServiceLoadItem(record, source);
      service.cpu_min_percent = value;
    } else if (name.startsWith('service_cpu_max_percent:')) {
      const source = name.slice('service_cpu_max_percent:'.length);
      const service = jsDebugCurrentServiceLoadItem(record, source);
      service.cpu_max_percent = value;
    } else if (name.startsWith('service_rss_bytes:')) {
      const source = name.slice('service_rss_bytes:'.length);
      const service = jsDebugCurrentServiceLoadItem(record, source);
      Object.assign(service, {rss_total_bytes: value, rss_samples: 1, rss_min_bytes: value, rss_max_bytes: value});
    } else if (name === 'cost_micro_usd') bucketMarginalMicroUsd = value;
    else if (name === 'api_list_cost_micro_usd') bucketApiListMicroUsd = value;
    else if (name === 'usage_tokens') bucketUsageTokens = value;
    else if (name === 'browser_api_per_second') record.api_count = value * duration;
    else if (name === 'browser_sse_per_second') record.sse_count = value * duration;
    else if (name === 'browser_latency_ms') { record.latency_total_ms = value; record.latency_count = 1; }
    else if (name === 'browser_bandwidth_bytes_per_second') record.bandwidth_bytes = value * duration;
    else if (name === 'browser_disconnected_ms') record.disconnected_ms = value;
    else if (name.startsWith('agent_tokens_per_minute:')) {
      const key = name.slice('agent_tokens_per_minute:'.length);
      agentRates.set(key, {key, label: debugGraphAgentDisplayLabel(key), fullLabel: key, total: value, samples: 1, tokens: value * duration / 60, seconds: duration, model_rates: {}});
    } else if (name.startsWith('model_tokens_per_minute:')) {
      const parts = name.slice('model_tokens_per_minute:'.length).split(':');
      const dimension = parts.shift();
      const model = parts.join(':') || 'unknown';
      if (dimension === 'output') modelRates[model] = {total: value, samples: 1, tokens: value * duration / 60, seconds: duration};
      if (dimension !== 'all') modelComponents.push(jsDebugCurrentModelComponent(dimension, model, value, duration));
    }
  }
  const statusValues = ['ask_agent_total', 'run_agent_total', 'transition_agent_total', 'idle_agent_total'];
  if (statusValues.some(key => Number.isFinite(record[key]))) record.agent_activity_samples = 1;
  if (Object.keys(modelRates).length) agentRates.set('__models__', {key: '__models__', label: 'Models', total: 0, samples: 0, tokens: 0, seconds: duration, model_rates: modelRates});
  record.agent_token_rates = [...agentRates.values()];
  if (bucketMarginalMicroUsd !== null || bucketApiListMicroUsd !== null || bucketUsageTokens !== null) {
    const hasBucketPrice = bucketMarginalMicroUsd !== null || bucketApiListMicroUsd !== null;
    const marginalMicroUsd = bucketMarginalMicroUsd ?? bucketApiListMicroUsd ?? 0;
    record.cost_summary = {
      range_report: false,
      total_micro_usd: marginalMicroUsd,
      total_token_quantity: bucketUsageTokens ?? 0,
      known_micro_usd: marginalMicroUsd,
      lower_micro_usd: marginalMicroUsd,
      upper_micro_usd: marginalMicroUsd,
      priced_count: hasBucketPrice && bucketUsageTokens !== null ? 1 : 0,
      complete: hasBucketPrice,
      unpriced_count: !hasBucketPrice && bucketUsageTokens !== null ? 1 : 0,
      unpriced_token_quantity: !hasBucketPrice ? bucketUsageTokens ?? 0 : 0,
      components: modelComponents,
    };
    if (bucketApiListMicroUsd !== null) record.cost_summary.api_list_micro_usd = bucketApiListMicroUsd;
  } else if (modelComponents.length) record.cost_summary = {components: modelComponents};
  if (includeRangeCost && rangeCost) record.cost_summary = {...(record.cost_summary || {}), ...jsDebugCurrentCostSummary(rangeCost), components: [...modelComponents, ...jsDebugCurrentCostSummary(rangeCost).components]};
  return record;
}

function jsDebugCurrentSnapshotAgentWindowRevision(snapshot = {}) {
  const buckets = Array.isArray(snapshot?.buckets) ? snapshot.buckets : [];
  for (const bucket of buckets.slice().reverse()) {
    const revision = jsDebugCurrentSeriesValue(bucket?.series || {}, 'agent_window_snapshot_revision');
    if (revision !== null && Number.isInteger(revision) && revision > 0) return revision;
  }
  return 0;
}

function jsDebugCurrentBucketHasFamilyData(bucket, family) {
  const names = Object.keys(bucket?.series || {});
  const prefixed = prefix => names.some(name => name.startsWith(prefix));
  if (family === 'cpu') return names.includes('system_cpu_percent') || prefixed('cpu_percent:');
  if (family === 'service_load') return prefixed('service_cpu_percent:') || prefixed('service_rss_bytes:');
  if (family === 'agent_status') return ['ask_agents', 'run_agents', 'transition_agents', 'idle_agents'].some(name => names.includes(name));
  if (family === 'agent_tokens' || family === 'cost') {
    return prefixed('agent_tokens_per_minute:') || prefixed('model_tokens_per_minute:') || names.includes('cost_micro_usd') || names.includes('api_list_cost_micro_usd') || names.includes('usage_tokens');
  }
  if (family === 'gpu') return prefixed('gpu_util_percent:') || prefixed('gpu_memory_bytes:');
  if (family === 'system_memory') return names.includes('system_memory_used_bytes') || names.includes('system_memory_capacity_bytes') || names.some(name => name.startsWith('mac_'));
  if (family === 'browser') return names.some(name => name.startsWith('browser_'));
  return false;
}

function jsDebugCurrentCoverageIntervals(snapshot, family) {
  const start = Number(snapshot.window_start);
  const end = Number(snapshot.window_end);
  const gaps = (snapshot.no_data || [])
    .filter(span => span.family === family)
    .map(span => ({start: Math.max(start, Number(span.start)), end: Math.min(end, Number(span.end))}))
    .filter(span => span.end > span.start)
    .sort((left, right) => left.start - right.start || left.end - right.end);
  const buckets = (snapshot.buckets || [])
    .filter(bucket => Number(bucket?.start) < end && Number(bucket?.start) + Number(bucket?.duration) > start)
    .sort((left, right) => Number(left.start) - Number(right.start));
  if (buckets.length) {
    const intervals = [];
    let gapIndex = 0;
    for (const bucket of buckets) {
      const bucketStart = Math.max(start, Number(bucket.start));
      const bucketEnd = Math.min(end, Number(bucket.start) + Number(bucket.duration));
      if (!(bucketEnd > bucketStart)) continue;
      while (gapIndex < gaps.length && gaps[gapIndex].end <= bucketStart) gapIndex += 1;
      let hasGap = false;
      for (let index = gapIndex; index < gaps.length && gaps[index].start < bucketEnd; index += 1) {
        if (gaps[index].end > bucketStart) {
          hasGap = true;
          break;
        }
      }
      // no_data is source-scoped. The retained UI can paint only a family-wide
      // band, so an exact value from any source keeps that bucket covered.
      if (!hasGap || jsDebugCurrentBucketHasFamilyData(bucket, family)) {
        const previous = intervals.at(-1);
        if (previous && previous.endSeconds === bucketStart) previous.endSeconds = bucketEnd;
        else intervals.push({startSeconds: bucketStart, endSeconds: bucketEnd, resolutionSeconds: snapshot.resolution_seconds, sourceResolutionSeconds: snapshot.resolution_seconds});
      }
    }
    return intervals;
  }
  const intervals = [];
  let cursor = start;
  for (const gap of gaps) {
    if (gap.start > cursor) intervals.push({startSeconds: cursor, endSeconds: gap.start, resolutionSeconds: snapshot.resolution_seconds, sourceResolutionSeconds: snapshot.resolution_seconds});
    cursor = Math.max(cursor, gap.end);
  }
  if (cursor < end) intervals.push({startSeconds: cursor, endSeconds: end, resolutionSeconds: snapshot.resolution_seconds, sourceResolutionSeconds: snapshot.resolution_seconds});
  return intervals;
}

function applyJsDebugCurrentSnapshot(snapshot, {forceGraphRefresh = false} = {}) {
  const buckets = Array.isArray(snapshot?.buckets) ? snapshot.buckets : [];
  clearJsDebugGraphData();
  buckets.forEach((bucket, index) => debugGraphApplyServerRecord(jsDebugCurrentBucketRecord(bucket, index === buckets.length - 1, snapshot.cost_report)));
  const requestInterval = {startSeconds: snapshot.window_start, endSeconds: snapshot.window_end, resolutionSeconds: snapshot.resolution_seconds, sourceResolutionSeconds: snapshot.resolution_seconds};
  jsDebugHistoryReadiness.phase = 'ready';
  jsDebugHistoryReadiness.reason = '';
  jsDebugHistoryReadiness.overlayVisible = false;
  jsDebugHistoryReadiness.requestCoverageIntervals = [requestInterval];
  jsDebugHistoryReadiness.coverageIntervals = [requestInterval];
  jsDebugHistoryReadiness.storeCoverageIntervals = Object.fromEntries(
    ['cpu', 'service_load', 'agent_status', 'agent_tokens', 'cost', 'gpu', 'system_memory', 'browser'].map(family => [family, jsDebugCurrentCoverageIntervals(snapshot, family)]),
  );
  jsDebugHistoryReadiness.loadedStartSeconds = snapshot.window_start;
  jsDebugHistoryReadiness.loadedEndSeconds = snapshot.window_end;
  jsDebugHistoryReadiness.resolutionSeconds = snapshot.resolution_seconds;
  jsDebugStatsServerSequence = Number(snapshot.cache_generation) || 0;
  debugGraphApplyUsageAtomBackfill(snapshot.usage_atom_backfill);
  jsDebugStatsPollState.agentWindowSnapshotRevision = jsDebugCurrentSnapshotAgentWindowRevision(snapshot);
  const firstSample = !jsDebugStatsPollState.firstSampleReceived;
  jsDebugStatsPollState.lastSampleAtMs = Date.now();
  jsDebugStatsPollState.firstSampleReceived = true;
  resolveDebugGraphResolutionChange(jsDebugHistoryReadiness);
  if (firstSample) armJsDebugStatsPolling();
  scheduleJsDebugPanelRefresh({force: forceGraphRefresh, immediate: true});
}

async function pollJsDebugStatsSample({forceGraphRefresh = false} = {}) {
  if (!jsDebugStatsPanelVisible()) {
    stopJsDebugStatsPolling();
    return;
  }
  // Snapshot and push delivery have one owner. Lifecycle callers may still
  // enter through this compatibility function, but they must never recreate
  // the retired direct snapshot transport.
  syncJsDebugCurrentStatsClient({select: forceGraphRefresh});
}

function scheduleJsDebugStatsHistoryFlush() {
  // Current browser observations are uploaded as original events by
  // scheduleJsDebugCurrentObservationFlush; legacy aggregate buckets are retired.
}

function jsDebugStatsHistoryUploadRequest(records, clientId, since) {
  let low = 1;
  let high = Math.min(records.length, jsDebugStatsHistoryPostMaxRecords);
  let chunkSize = 1;
  let body = '';
  while (low <= high) {
    const candidateSize = Math.floor((low + high) / 2);
    const candidateBody = JSON.stringify({client_id: clientId, since, ack_only: true, records: records.slice(0, candidateSize)});
    if (utf8ByteLength(candidateBody) <= jsDebugStatsHistoryPostMaxBytes) {
      chunkSize = candidateSize;
      body = candidateBody;
      low = candidateSize + 1;
    } else {
      high = candidateSize - 1;
    }
  }
  const chunk = records.slice(0, chunkSize);
  return {
    chunk,
    held: records.slice(chunkSize),
    body: body || JSON.stringify({client_id: clientId, since, ack_only: true, records: chunk}),
  };
}

async function flushJsDebugStatsHistory() {
  // The current observation endpoint owns browser telemetry. Retain this hook because
  // the established renderer still calls it, but never write the retired history API.
  jsDebugGraphPendingServerBuckets.clear();
}

async function clearJsDebugServerHistory() {
  const restartPolling = runtimeIntervalActive('debug-stats');
  stopJsDebugStatsPolling();
  jsDebugStatsPollState.firstSampleReceived = false;
  jsDebugHistoryPrefetchState.didInitial = false;
  jsDebugHistoryPrefetchState.lastFullPrefetchAtMs = 0;
  jsDebugStatsServerSequence = 0;
  jsDebugStatsServerUptimeSeconds = null;
  jsDebugStatsServerPid = null;
  jsDebugStatsServerStartedAt = null;
  jsDebugStatsServerRssBytes = null;
  resetJsDebugHistoryReadiness();
  jsDebugGraphPendingServerBuckets.clear();
  if (jsDebugStatsUploadState.timer) {
    clearTimeout(jsDebugStatsUploadState.timer);
    jsDebugStatsUploadState.timer = null;
  }
  clearJsDebugGraphData();
  scheduleJsDebugPanelRefresh({force: true});
  if (restartPolling) armJsDebugStatsPolling({pollNow: true});
}

function startJsDebugStatsPolling({pollNow = true} = {}) {
  syncJsDebugStatsPolling({pollNow});
}

const jsDebugClientHealthMeasurementState = {inFlight: false, lastObservationAtMs: 0};

async function measureClientHealth() {
  if (document.visibilityState === 'hidden' || jsDebugClientHealthMeasurementState.inFlight || typeof apiFetchJson !== 'function') return null;
  jsDebugClientHealthMeasurementState.inFlight = true;
  const url = `/api/ping?client_id=${encodeURIComponent(jsDebugStatsClientIdForRequest())}`;
  const startedAt = performanceNow();
  try {
    const payload = await apiFetchJson(url, {cache: 'no-store'});
    const latencyMs = Math.max(1, Math.round(performanceNow() - startedAt));
    latencySamples = [...latencySamples, latencyMs].slice(-latencySamplesMax);
    renderLatency(latencyMs);
    const sampleTimeMs = Date.now();
    if (sampleTimeMs - jsDebugClientHealthMeasurementState.lastObservationAtMs < jsDebugCurrentObservationBatchDelayMs) return {latencyMs, observed: false};
    jsDebugClientHealthMeasurementState.lastObservationAtMs = sampleTimeMs;
    const bandwidthBytes = jsDebugRequestBytes(url) + utf8ByteLength(JSON.stringify(payload || {}));
    if (jsDebugGraphExactResolutionEnabled) {
      recordJsDebugClientHealthObservation(latencyMs, bandwidthBytes, sampleTimeMs);
      return {latencyMs, observed: true};
    }
    const bucketRef = debugGraphServerBucketRefForTime(sampleTimeMs, sampleTimeMs);
    const data = {heartbeatCount: 1, latencyMs, bandwidthBytes};
    debugGraphAddBucketData(debugGraphBucketForTime(sampleTimeMs, sampleTimeMs), data);
    debugGraphQueueServerDelta(bucketRef, data);
    compactJsDebugGraphBuckets(sampleTimeMs);
    return {latencyMs, observed: true};
  } catch (_) {
    renderLatency(null);
    return null;
  } finally {
    jsDebugClientHealthMeasurementState.inFlight = false;
  }
}

function syncJsDebugStatsPolling({pollNow = true, forceGraphRefresh = false} = {}) {
  if (!jsDebugStatsPanelVisible()) {
    stopJsDebugStatsPolling();
    return false;
  }
  if (runtimeIntervalActive('debug-stats') && !pollNow) return true;
  armJsDebugStatsPolling({pollNow, forceGraphRefresh});
  return true;
}

async function primeJsDebugStatsBeforeLongLivedStreams() {
  if (!jsDebugStatsPanelVisible() || jsDebugStatsPollState.firstSampleReceived) return false;
  await pollJsDebugStatsSample();
  return jsDebugStatsPollState.firstSampleReceived;
}

async function initializeJsDebugStatsBeforeStreams() {
  if (jsDebugGraphExactResolutionEnabled && syncJsDebugCurrentStatsClient()) {
    return Boolean(jsDebugCurrentStatsClientState.client?.controller?.()?.generation?.());
  }
  await primeJsDebugStatsBeforeLongLivedStreams();
  syncJsDebugStatsPolling({pollNow: false});
  return jsDebugStatsPollState.firstSampleReceived;
}

if (typeof document !== 'undefined' && document?.addEventListener) {
  document.addEventListener('visibilitychange', () => {
    const visible = document.visibilityState === 'visible';
    if (jsDebugGraphExactResolutionEnabled) syncJsDebugCurrentStatsClient();
    syncJsDebugStatsPolling({pollNow: visible, forceGraphRefresh: visible});
    syncDebugSubviewActivation({pollNow: visible});
    if (visible) syncDebugGraphLiveTicker();
    else stopDebugGraphLiveTicker();
  });
}

if (typeof window !== 'undefined' && window?.addEventListener) {
  window.addEventListener('pagehide', event => {
    void event;
    stopDebugGraphLiveTicker();
    disposeDebugPricingRefreshLifecycle('pagehide');
    disposeJsDebugCurrentObservationLifecycle('pagehide');
  });
  window.addEventListener('pageshow', event => {
    if (event?.persisted !== true) return;
    installJsDebugCurrentObservationLiveness();
    scheduleJsDebugCurrentObservationFlush();
    if (jsDebugPricingRefreshState.inFlight) scheduleDebugCostPricingStatusRefresh();
    syncDebugGraphLiveTicker();
  });
}

function jsDebugTextForClipboard() {
  const page = `${location.pathname || ''}${location.search || ''}${location.hash || ''}`;
  const counts = debugEventCounts();
  const removalSummary = terminalRemovalLatencySummary();
  const header = [
    `JS Debug ${new Date().toISOString()}`,
    `page=${page || '/'}`,
    `events=${jsDebugEvents.length}`,
    `api=${counts.apiCalls}`,
    `sse=${counts.sseEvents}`,
    `errors=${counts.errors}`,
    `removals=${removalSummary.count}`,
    `removal_avg=${removalSummary.averageMs}ms`,
    `api_tx=${counts.apiRequestBytes}B`,
    `api_rx=${counts.apiResponseBytes}B`,
    `sse_rx=${counts.sseBytes}B`,
  ].join(' ');
  const apiSummaryRows = debugApiSummaryRows();
  const sseSummaryRows = debugSseSummaryRows();
  const sseLatencySummaryRows = debugSseLatencySummaryRows();
  const clientPerfRows = debugClientPerfRows().map(debugClientPerfText);
  const rows = jsDebugEvents.map(debugEventLineText);
  return [
    header,
    ...(apiSummaryRows.length ? ['Slow API by max latency:', ...apiSummaryRows, ''] : []),
    ...(sseSummaryRows.length ? ['Slow SSE server work:', ...sseSummaryRows, ''] : []),
    ...(sseLatencySummaryRows.length ? ['Slow SSE receive latency:', ...sseLatencySummaryRows, ''] : []),
    ...(clientPerfRows.length ? ['Client work counters:', ...clientPerfRows, ''] : []),
    ...rows,
  ].join('\n');
}

const jsDebugCopyTextProviders = Object.freeze({
  'debug-api': () => jsDebugTextForClipboard(),
  'debug-logs': () => debugLogsTextForClipboard(),
  'debug-mobile': () => debugMobileCaptureTextForClipboard(),
});

function jsDebugCopyTextForFeedbackKey(key) {
  const provider = jsDebugCopyTextProviders[String(key || '')];
  return typeof provider === 'function' ? provider() : null;
}

function debugSystemNumber(value, digits = 0) {
  const number = Number(value);
  return Number.isFinite(number) ? number.toLocaleString(undefined, {maximumFractionDigits: digits}) : t('common.notAvailable');
}

// The ONE label/value list renderer. A third tuple element is the reason the value is an em dash;
// it rides along as a title and a machine-readable attribute so an unmeasured row in a disclosure
// carries its reason exactly the way an unmeasured roster cell already does.
function debugSystemRowsHtml(rows = []) {
  return `<dl class="js-debug-system-kv">${rows.map(([label, value, reason]) => {
    const explain = reason ? ` title="${esc(reason)}" data-value-reason="${esc(reason)}"` : '';
    return `
    <div><dt>${esc(label)}</dt><dd${explain}>${esc(value == null || value === '' ? t('common.notAvailable') : value)}</dd></div>`;
  }).join('')}</dl>`;
}

function debugSystemCardHtml(title, body, options = {}) {
  return `<section class="js-debug-system-card${options.wide ? ' js-debug-system-card--wide' : ''}">
    <h3>${esc(title)}</h3>${body}
  </section>`;
}

// The sentence each tmux control-client state means when the payload published no reason of its
// own. One owner, read by the child row and by its disclosure.
const DEBUG_SYSTEM_TMUX_WATCHER_DEFAULT_REASONS = Object.freeze({
  'never-started': 'Tmux signal watcher has not been started',
  attaching: 'Tmux control client is attaching',
  'no-sessions': 'No tmux sessions are configured to watch',
  attached: 'Control client is attached',
  exited: 'Tmux control client exited',
});

// The ONE predicate for "this watcher is an actionable problem". An exited control client is one;
// so is a watcher that was demanded and never started. An undemanded watcher never is.
function debugSystemTmuxSignalWatcherIsIssue(watcher = {}) {
  const state = DEBUG_SYSTEM_TMUX_WATCHER_STATES.has(String(watcher?.state || '')) ? String(watcher.state) : 'exited';
  return state === 'exited' || (state === 'never-started' && watcher?.demanded !== false);
}

// NO staleness threshold lives here any more, deliberately. It used to be 30 seconds applied to
// the age of the last PUBLISHED document -- but the observer publishes only when a service-state
// signature changes, so on a healthy quiet system that age grows without bound and the panel
// declared its own monitor dead after 30 seconds of everything being fine. The liveness decision
// now belongs to `yolomux_lib/backend_health/observer.py:BackendHealthObserver.liveness`, which is
// the only party that owns the probe thread and the monotonic cadence its deadline is derived
// from, and this panel renders `observer_alive` rather than re-deriving it from a number it cannot
// interpret. The STORE owns history and persistence and no longer decides liveness at all: it
// could not see the cadence, and it aged the fact on the wall clock, so a clock step backwards
// held "alive" forever and a step forwards produced a false red.
// Health metrics that are whole counts. They share the one metric-envelope cell renderer with the
// three process metrics; only the number formatting differs.
const DEBUG_SYSTEM_HEALTH_COUNT_KEYS = new Set(['restart_count', 'observations', 'request_count', 'error_count', 'completed_count']);
// The three measurements the local-service projection publishes per process, versus the retained
// health measurements. They carry different DOM attributes because they have different producers
// and different coverage, and `tests/test_gate_panels.py` pins the process set exactly.
const DEBUG_SYSTEM_PROCESS_METRIC_KEYS = new Set(['cpu_now_percent', 'rss_bytes', 'uptime_seconds']);
// The roster's metric columns, in render order, and for each one which of the two published
// aggregates it is counted by. `retained` totals come from the observer's per-epoch store;
// `counters` come from this web process's own RPC ledger. The two have different denominators and
// independent coverage, so a column is only ever flagged partial by the aggregate it actually came
// from. `parts` means one cell shows two envelopes (average and maximum response time) -- it is
// still two typed metrics, rendered through the same one cell renderer, in one column.
// `priority: secondary` is what the responsive layout drops first; every dropped value is repeated
// in the row's own disclosure, so narrowing the window never hides a number.
const DEBUG_SYSTEM_ROSTER_COLUMNS = Object.freeze([
  {key: 'latency', labelKey: 'debug.system.roster.column.latency', parts: ['latency_average_ms', 'latency_max_ms'], coverage: 'counters', priority: 'primary'},
  {key: 'uptime_seconds', labelKey: 'debug.system.localServices.field.uptime', priority: 'primary'},
  {key: 'rss_bytes', labelKey: 'debug.system.localServices.field.memory', priority: 'secondary'},
  {key: 'cpu_now_percent', labelKey: 'debug.graph.chart.cpu', priority: 'secondary'},
  {key: 'restart_count', labelKey: 'debug.system.roster.column.restarts', coverage: 'retained', priority: 'secondary'},
  {key: 'request_count', labelKey: 'debug.system.roster.column.requests', coverage: 'counters', priority: 'secondary'},
  {key: 'error_count', labelKey: 'debug.system.roster.column.errors', coverage: 'counters', priority: 'secondary'},
]);
// The denominator, spelled out. `web_process` counts what THIS web process issued -- not everything
// the service ever served -- and a reader who is not told that reads the number as the latter.
const DEBUG_SYSTEM_HEALTH_SCOPE_LABELS = Object.freeze({web_process: 'this web process'});
// Machine-readable reason codes rendered as the sentence a reader can act on. An unmapped code is
// printed verbatim rather than dropped, because an unexplained code still names the fact.
const DEBUG_SYSTEM_HEALTH_REASON_TEXT = Object.freeze({
  observer_unattached: 'the backend-health observer is not attached to this web process',
  resource_unobserved: 'the observer has never recorded this service',
  counters_not_observed: 'the observer never read a counter sample, so every retained total would be a structural zero',
  web_process_scope: 'the retained history starts before this web process, so these counts cover less time than the restarts beside them',
  missed_final_sample: 'a restart happened before the final counter sample could be read',
  history_corrupt: 'the retained history file was unreadable and was reset',
  history_unreadable: 'the retained history file could not be read and was reset',
  history_schema_unsupported: 'the retained history was written by a newer schema and was reset',
  history_port_mismatch: 'the retained history belonged to another port and was reset',
  // The roster's two STRUCTURAL absences. Neither is a failure and neither is a zero: the
  // observer watches the six local services from this web process, so it has no restart,
  // request, error or latency series for the web process itself, and the tmux signal watcher
  // is an in-process subsystem with no separate process to measure.
  web_process_not_observed: 'the backend-health observer watches the local services from this web process, so it has never observed this web process itself',
  subsystem_not_observed: 'this is an in-process subsystem, not a separate process, so it has no independent process or traffic measurement',
  schema_unsupported: 'the backend published a local-services schema this panel does not render',
  // Diagnostics the sampler and the watcher publish only once they have run. Before that the field
  // is ABSENT, which is why it renders as the unmeasured em dash and not as `0` or `0ms`.
  sampler_cycles_not_published: 'the stats sampler has not published a cycle count yet',
  sampler_cycle_not_timed: 'the stats sampler has not completed a timed cycle yet',
  history_not_assembled: 'no history query has been assembled since this process started',
  history_never_requested: 'no history request has been served since this process started, so there is no ratio to take',
  watcher_demand_unpublished: 'the watcher has not published whether it is demanded',
  state_reason_unpublished: 'the service published no explanation for its current state',
  process_not_running: 'no process is running for this service, so it has no pid',
  // The CPU-budget block's two absences. The reason for an unpushed PROCESS sample is not spelled
  // here: `system_status_server_block` publishes that sentence inside the envelope, and a second
  // copy in this map would be one more divergent copy of the same fact.
  cpu_budget_stale: 'no CPU sample has arrived recently, so the current CPU reading below is not current',
  cpu_budget_never_sampled: 'statsd has never pushed a CPU sample, so no CPU percentage has been measured against the budget',
  usage_no_accepted_atom: 'no usage atom has been accepted since this process started',
  usage_conflicts_not_published: 'the usage store has not published a quarantined-conflict count',
  // Liveness, from `backend_health/observer.py:BACKEND_HEALTH_NO_CYCLE_OBSERVED`. Attached but not
  // yet probing is a different fact from probing and stopped.
  no_observer_cycle_recorded: 'the observer is attached but has not completed a probe cycle yet',
  observer_cycles_failing: 'the observer is still attempting cycles but they are failing, so nothing new is being recorded',
  observer_cycles_stalled: 'the observer stopped attempting probe cycles',
});

// The ONE status-to-semantics owner for the roster. Every caller that needs a colour, a dot or a
// non-colour state word goes through it, so "green means ready" cannot drift between the roster
// row, the summary strip and the child row. Colour is never the only carrier: the tone picks the
// paint, `debugSystemRosterStateLabel` picks the word, and both are rendered together.
//   good  -- ready and serving
//   muted -- legitimately idle / not demanded (gray, NOT an alert)
//   warn  -- starting, recovering, backing off, stale or partial (amber, transient)
//   bad   -- actionable: degraded, down, transport failed, unavailable (red)
const DEBUG_SYSTEM_STATE_TONES = Object.freeze({
  running: 'good',
  ready: 'good',
  attached: 'good',
  ok: 'good',
  idle: 'muted',
  'no-sessions': 'muted',
  'never-started': 'muted',
  not_running: 'muted',
  not_demanded: 'muted',
  starting: 'warn',
  attaching: 'warn',
  recovering: 'warn',
  backoff: 'warn',
  stale: 'warn',
  partial: 'warn',
  // The panel cannot read this payload's rows, which is not the same as the service being down.
  // `warn`, because the fact being reported is about THIS panel's ability to render, not about
  // the daemon: painting it `bad` would report an outage nobody observed.
  schema_unsupported: 'warn',
});

function debugSystemStateTone(state) {
  const token = String(state || '').trim();
  if (!token) return 'bad';
  return DEBUG_SYSTEM_STATE_TONES[token] || 'bad';
}

// `debugSystemMeasuredMetric` used to live here: an adapter that wrapped a raw number because
// `payload.server` was published as plain floats, outside the typed-metric contract every other
// row obeys. It could not do its job -- `Number(null)` is `0`, and `0` is finite -- so an
// unsampled value arrived stamped `measured`. `yolomux_lib/app.py:system_status_server_block` now
// publishes the web process through the same `local_service_projection.measurement` envelope as
// the six services beside it, so there is no raw-float dialect left to adapt and the roster reads
// ONE shape everywhere.

// A metric that is absent by STRUCTURE, not by failure. It renders as the panel's unavailable
// text with its reason code, exactly like an unmeasured typed envelope, so a column nobody
// observes can never read as `0`.
function debugSystemAbsentMetric(reasonCode) {
  return {state: 'unavailable', value: null, reason_code: String(reasonCode || ''), reason: debugSystemHealthReasonText(reasonCode)};
}

function debugSystemHealthReasonText(code) {
  const token = String(code || '').trim();
  if (!token) return '';
  return DEBUG_SYSTEM_HEALTH_REASON_TEXT[token] || token.replace(/_/g, ' ');
}

// The panel has ONE spelling for a value nobody measured: an em dash carrying its reason. The
// roster cells already obeyed it; the diagnostics inside a row's disclosure did not, and printed
// either `t('common.notAvailable')` ("not available / not available") or a `|| 0` fallback that
// renders an unpublished counter as a confident `0ms`. Both are the same defect wearing different
// clothes, so every unmeasured scalar in a disclosure now comes from here.
//   `format` turns a FINITE value into its text; it is never called for an absent one, so no
//   formatter can invent a zero on the way through.
function debugSystemScalar(value, reasonCode, format = current => debugSystemNumber(current)) {
  const number = Number(value);
  if (value == null || !Number.isFinite(number)) return {text: '—', reason: debugSystemHealthReasonText(reasonCode)};
  return {text: String(format(number)), reason: ''};
}

function debugSystemHealthReasonListText(reasons) {
  const codes = (Array.isArray(reasons) ? reasons : []).map(String).filter(Boolean);
  return codes.map(code => `${code} (${debugSystemHealthReasonText(code)})`).join('; ');
}

// The ONE metric-envelope cell renderer: the three process metrics and every M8 health metric go
// through it. A value that is not `measured` renders its typed reason, never `0` -- a confident
// zero with no measurement behind it is the failure this panel exists to prevent.
function debugSystemMetricText(metric = {}, key = '') {
  if (metric.state !== 'measured') return String(metric.reason || t('common.notAvailable'));
  if (key === 'cpu_now_percent') return `${debugSystemNumber(metric.value, 1)}%`;
  if (key === 'rss_bytes') return debugGraphTerseBytesText(metric.value);
  if (key === 'uptime_seconds') return debugGraphUptimeText(metric.value);
  if (key === 'latency_average_ms' || key === 'latency_max_ms') return debugGraphTerseTimeText(metric.value);
  if (DEBUG_SYSTEM_HEALTH_COUNT_KEYS.has(key)) return debugSystemNumber(metric.value, 0);
  return debugSystemNumber(metric.value, 1);
}

// Is the monitor still LOOKING? That is the question this answers, and it is not the same
// question as "when did a service last change state".
//
// The old model asked the second question and printed the first one's answer. The observer
// persists only when the service-state signature CHANGES, so on a healthy quiet system the
// document's age grows without bound while the observer probes every 2 seconds -- and any age
// over 30 seconds was rendered as "STOPPED UPDATING". A silent system reported its own monitor
// as dead, and the quieter the machine the louder the lie.
//
// The backend now publishes liveness as its own bounded fact, decided by the OBSERVER -- the only
// party that owns the thread whose survival is the question and the monotonic cadence the deadline
// is derived from. There is deliberately NO threshold constant here: a panel cannot see the
// observer's interval, and a second copy of the number is how the two drift apart.
//   unavailable    -- no observer is attached to this web process
//   never-observed -- attached, but no probe cycle has completed yet AND none has failed
//   stopped        -- it was probing and is not completing cycles: the case that earns the banner
//   current        -- it is still probing
function debugSystemHealthStaleness(health = {}) {
  if (health.available !== true) return {state: 'unavailable', ageSeconds: null, cycleAgeSeconds: null};
  const ageSeconds = Number(health.age_seconds);
  const publishedAge = Number.isFinite(ageSeconds) && health.age_seconds != null ? ageSeconds : null;
  const rawCycleAge = Number(health.observer_cycle_age_seconds);
  const cycleAgeSeconds = Number.isFinite(rawCycleAge) && health.observer_cycle_age_seconds != null ? rawCycleAge : null;
  // The TYPED reason first, then the generic absence. An observer that throws on its first cycle
  // and every one after it publishes `observer_cycles_failing` with a NULL cycle age -- there is no
  // completed cycle to age. Reading only the null age classified that as "attached, hasn't looked
  // yet", which is a quiet non-alerting line, so a continuously failing monitor with an empty
  // history raised nothing anywhere on the screen. Only `no_observer_cycle_recorded` means the
  // observer has genuinely not started; any other published reason is a stated failure.
  const livenessReason = String(health.observer_liveness_reason_code || '');
  const typedFailure = health.observer_alive !== true && livenessReason !== '' && livenessReason !== 'no_observer_cycle_recorded';
  if (cycleAgeSeconds === null && !typedFailure) return {state: 'never-observed', ageSeconds: publishedAge, cycleAgeSeconds: null};
  if (health.observer_alive !== true) return {state: 'stopped', ageSeconds: publishedAge, cycleAgeSeconds};
  return {state: 'current', ageSeconds: publishedAge, cycleAgeSeconds};
}

// The one sentence that says what the retained snapshot IS right now. ONE call site:
// `debugSystemHealthExplanations`, which is the only thing that decides where a sentence is
// printed. The headline states the FACT and carries no elapsed time, deliberately, because the
// compact alert slot is a `role="alert"` live region: any elapsed time in it changes on every
// 5-second poll, so a screen reader would re-announce the whole banner every five seconds for as
// long as the condition lasted. The numbers live in the provenance rows below, which are not a
// live region.
function debugSystemHealthSnapshotHeadline(health = {}) {
  const {state, cycleAgeSeconds} = debugSystemHealthStaleness(health);
  // A monitor that has failed every cycle since it started has no last cycle, so the numbers below
  // do not describe one. Saying they do would be the same fabricated measurement this panel spends
  // the rest of its length refusing to print.
  const stoppedTail = cycleAgeSeconds === null
    ? 'Nothing below has been measured by this process.'
    : 'Every retained number below describes its last cycle, not now.';
  // After a restart the RETAINED history is real while THIS observer process has completed no
  // cycle. "This process has not looked yet" and "there is nothing to look at" are different
  // facts with different fixes, and saying the second when the first is true tells a reader their
  // history is gone when it is sitting right underneath.
  const retained = Number(health.resources) > 0 || Number(health.revision) > 0;
  const notYet = retained
    ? `The backend-health observer is attached but this process has not completed its first probe cycle yet — ${debugSystemHealthReasonText(health.observer_liveness_reason_code || 'no_observer_cycle_recorded')}. The numbers below are the retained history from before the restart.`
    : 'The backend-health observer is attached but has not completed a probe cycle yet, and nothing has been retained, so nothing below has been measured.';
  return {
    unavailable: `Backend health is unavailable — ${debugSystemHealthReasonText(health.reason_code || 'observer_unattached')}. Restarts, requests, errors and response times below are not measured; they are not zero.`,
    'never-observed': notYet,
    // The typed reason, not a generic sentence: an observer that is throwing on every cycle is a
    // bug to fix, one that stopped being scheduled is a thread to restart. Static text, so this
    // stays safe inside the `role="alert"` slot -- no elapsed time, no re-announcement.
    stopped: `Backend health STOPPED UPDATING — ${debugSystemHealthReasonText(health.observer_liveness_reason_code || 'observer_cycles_stalled')}. ${stoppedTail}`,
    current: 'Backend health is current: the observer is still completing probe cycles, and a long-unchanged state below means a quiet system, not a stalled one.',
  }[state];
}

// The ONE owner of every stale/error EXPLANATION this panel prints, and of where each one is
// printed. It used to have two producers -- the compact alert slot wrote its own history-reset and
// persistence sentences while the provenance block wrote near-identical ones a screen further down,
// and the stopped/unavailable headline was rendered by BOTH -- so a reader who opened Advanced read
// the same fact twice in two different wordings, which is exactly the divergent-copies defect.
//
// It returns the two disjoint halves of one set:
//   `alerts` -- the explanations that interrupt a reader. Rendered ONCE, only by the compact alert
//               slot above the roster.
//   `quiet`  -- the non-alerting status sentence, or `null` whenever an alert already says it.
//               Rendered ONCE, only by the provenance block inside Advanced.
// Exactly one surface prints any given sentence, and neither surface composes its own; the
// provenance ROWS keep the machine-readable reason code, which is a measured field, not prose.
function debugSystemHealthExplanations(health = {}) {
  const {state} = debugSystemHealthStaleness(health);
  const headline = debugSystemHealthSnapshotHeadline(health);
  const alerting = state === 'stopped' || state === 'unavailable';
  const alerts = alerting ? [['backend-health', headline]] : [];
  const resetReason = String(health.history_reset_reason || '');
  if (resetReason) {
    alerts.push(['history-reset', `${resetReason} — ${debugSystemHealthReasonText(resetReason)}; counts from before the reset are gone.`]);
  }
  const persistenceState = String(health.persistence_state || '');
  if (persistenceState && persistenceState !== 'ok') {
    const why = String(health.persistence_reason_code || '');
    alerts.push(['persistence', `Retained history persistence is ${persistenceState}${why ? ` — ${debugSystemHealthReasonText(why)}` : ''}; the retained history may not survive a restart.`]);
  }
  return {alerts, quiet: alerting ? null : headline};
}

function debugSystemHealthSnapshotHtml(health = {}) {
  const {state, ageSeconds, cycleAgeSeconds} = debugSystemHealthStaleness(health);
  const resetReason = String(health.history_reset_reason || '');
  const persistenceState = String(health.persistence_state || '');
  const persistenceDegraded = persistenceState !== '' && persistenceState !== 'ok';
  const {quiet} = debugSystemHealthExplanations(health);
  // The two facts, adjacent and separately labelled, so a reader can see that a long "Last state
  // change" beside a short "Last checked" is a QUIET system and not a broken one.
  const checked = debugSystemScalar(cycleAgeSeconds, 'no_observer_cycle_recorded', value => `${relativeTimeFormat(value)} (${debugSystemNumber(value, 1)}s ago)`);
  const cycles = debugSystemScalar(health.observer_cycles, health.observer_liveness_reason_code || 'observer_unattached');
  const rawEpochStartedAt = Number(health.observer_epoch_started_at);
  const epochStartedAt = Number.isFinite(rawEpochStartedAt) ? rawEpochStartedAt : 0;
  const rows = [
    ['Snapshot revision', Number(health.revision) > 0 ? `#${String(health.revision)}` : t('common.notAvailable')],
    ['Observer last checked', checked.text, checked.reason],
    // NOT `debugSystemNumber` directly: `Number(null)` is 0, so an absent cycle count would have
    // been rendered as a measured zero -- the fabricated zero this whole block exists to refuse.
    ['Observer cycles', cycles.text, cycles.reason],
    ['Last state change', ageSeconds == null ? 'never' : `${relativeTimeFormat(ageSeconds)} (${debugSystemNumber(ageSeconds, 1)}s old)`],
    ['Observer epoch', String(health.observer_epoch || '') || t('common.notAvailable')],
    // The wall-clock instant this observer epoch began collecting -- i.e. how far back the
    // retained history actually reaches. The backend already publishes it as
    // `observer_epoch_started_at`; this is the reader-facing label for it. Absent (never
    // available) reads as not-available rather than the epoch-zero date a bare `new Date(0)`
    // would print.
    ['History retained since', epochStartedAt > 0 ? debugGraphTimeLabel(epochStartedAt * 1000, {includeDate: true, includeSeconds: true}) : t('common.notAvailable')],
    ['Services retained', Number.isFinite(Number(health.resources)) ? debugSystemNumber(health.resources) : t('common.notAvailable')],
  ];
  // The reason CODE, not its sentence. The sentence is an explanation and the alert slot above the
  // roster owns every one of those; this row's job is to name the machine-readable field the
  // backend published, which is a measured value like every other row here.
  if (resetReason) rows.push(['History reset', resetReason]);
  else if (String(health.history_coverage || '')) rows.push(['History coverage', String(health.history_coverage)]);
  if (persistenceDegraded) {
    const why = String(health.persistence_reason_code || '');
    rows.push(['Persistence', `${persistenceState}${why ? ` — ${why}` : ''}`]);
  }
  const alerting = state === 'stopped' || state === 'unavailable' || persistenceDegraded || Boolean(resetReason);
  // NOT a live region. Every row below carries an age that advances on each 5-second poll, so a
  // `role="status"` here re-announced the whole provenance block every five seconds. Anything
  // here worth interrupting a reader for is already raised, once, by the compact alert slot --
  // which is why `quiet` is null in exactly those states and no headline is printed here.
  return `<div class="js-debug-system-health-snapshot${alerting ? ' js-debug-system-health-snapshot--alert' : ''}" data-system-health-snapshot data-health-available="${health.available === true}" data-health-staleness="${esc(state)}" data-health-alerting="${alerting}">
    ${quiet ? `<p data-system-health-headline>${esc(quiet)}</p>` : ''}${debugSystemRowsHtml(rows)}
  </div>`;
}

function debugSystemHealthCoverage(health = {}) {
  return health.coverage && typeof health.coverage === 'object' ? health.coverage : {};
}

// The coverage state that governs one column, so a `partial` flag always points at the aggregate
// the number in that cell actually came from.
function debugSystemHealthColumnCoverage(health, column) {
  const coverage = debugSystemHealthCoverage(health);
  const value = column.coverage === 'retained' ? coverage.retained_counters : coverage.counters;
  return String(value || 'unavailable');
}

function debugSystemHealthScopeLabel(health = {}) {
  const scope = String(debugSystemHealthCoverage(health).counter_scope || '');
  if (!scope) return '';
  return DEBUG_SYSTEM_HEALTH_SCOPE_LABELS[scope] || scope;
}

// The denominator sentence, and its ONE owner. It is a property of the AGGREGATE, not of any one
// service: every row shares the same `web_process` scope, so rendering it per row printed the same
// paragraph six times and rendering it per column printed "this web process" in three headers --
// where, being `white-space: nowrap`, it also pinned three numeric columns to 121-127px that could
// not shrink. It is now stated once, beneath the table, by this function.
function debugSystemHealthScopeSentence(health = {}) {
  const scope = String(debugSystemHealthCoverage(health).counter_scope || '');
  if (!scope) return '';
  return `Requests, errors and response times count only what ${debugSystemHealthScopeLabel(health)} issued (scope: ${scope}) — not everything this service has ever served.`;
}

// Every sentence that qualifies the numbers in THIS row, so a partial count can never be read as a
// complete one. The scope sentence is deliberately absent: it is identical for every row and is
// stated once by `debugSystemHealthScopeSentence` in the table caption.
function debugSystemHealthCoverageNotes(health = {}) {
  const coverage = debugSystemHealthCoverage(health);
  const notes = [];
  if (String(coverage.counters || '') === 'partial') {
    notes.push(`Requests, errors and response times are PARTIAL: ${debugSystemHealthReasonListText(coverage.counter_reasons) || 'no reason was published'}.`);
  }
  const retained = String(coverage.retained_counters || '');
  if (retained === 'partial') {
    notes.push(`Retained totals (restarts, observations) are PARTIAL: ${debugSystemHealthReasonListText(coverage.retained_counter_reasons) || 'no reason was published'}.`);
  } else if (retained === 'unavailable') {
    notes.push(`Retained totals are unavailable: ${debugSystemHealthReasonText(health.unavailable_reason_code || 'observer_unattached')}.`);
  }
  return notes;
}

function debugSystemHealthObservedText(health = {}) {
  if (health.observed !== true) {
    return `Not observed — ${debugSystemHealthReasonText(health.unavailable_reason_code || 'observer_unattached')}.`;
  }
  const state = String(health.state || '') || t('common.notAvailable');
  const ageSeconds = Number(health.state_age_seconds);
  const held = Number.isFinite(ageSeconds) && health.state_age_seconds != null
    ? ` for ${debugGraphUptimeText(ageSeconds)}`
    : ' (the observer has not published how long)';
  const reason = String(health.reason_code || '');
  const recovery = String(health.recovery_outcome || '');
  const since = Number(health.since_revision) > 0 ? ` since revision #${String(health.since_revision)}` : '';
  const detail = [reason && reason !== 'none' ? `reason ${reason}` : '', recovery && recovery !== 'none' ? `recovery ${recovery}` : '']
    .filter(Boolean)
    .join(', ');
  return `Observed state: ${state}${held}${since}${detail ? ` — ${detail}` : ''}.`;
}

// The published health fields the five columns have no room for. They go through the SAME metric
// envelope, so an unobserved sample count still reads as its reason and never as 0.
function debugSystemHealthDetailText(health = {}) {
  const metrics = health.metrics && typeof health.metrics === 'object' ? health.metrics : {};
  const parts = [
    `observer samples: ${debugSystemMetricText(metrics.observations, 'observations')}`,
    `completed requests: ${debugSystemMetricText(metrics.completed_count, 'completed_count')}`,
  ];
  // A pid and a revision are identifiers, not quantities: `4,242` is not a pid anyone can grep for.
  if (health.observed === true && Number(health.pid) > 0) {
    parts.push(`peer pid ${String(health.pid)} (epoch ${String(health.process_epoch || '') || t('common.notAvailable')})`);
  }
  return `${parts.join(' · ')}.`;
}

// Errors are only actionable with their reasons. This is the web process's own ledger breakdown,
// under the same `web_process` denominator as the Errors column beside it.
function debugSystemHealthErrorsByReasonText(health = {}) {
  const errors = health.errors_by_reason && typeof health.errors_by_reason === 'object' ? health.errors_by_reason : {};
  const entries = Object.entries(errors).filter(([, count]) => Number(count) > 0);
  if (!entries.length) return '';
  return `Errors by reason: ${entries.map(([reason, count]) => `${reason} ${debugSystemNumber(count)}`).join(', ')}.`;
}

// `transitions_truncated` means older rows EXIST. Saying "16 changes" when 42 happened is the same
// lie as a partial count rendered as complete, so the count and the window are always stated apart.
function debugSystemHealthTransitionsHtml(health = {}, nowSeconds) {
  const transitions = Array.isArray(health.transitions) ? health.transitions : [];
  const total = Number(health.transitions_total);
  const totalText = Number.isFinite(total) ? debugSystemNumber(total) : String(transitions.length);
  if (!transitions.length) {
    if (health.observed !== true) return '';
    return `<p data-subsystem-transitions data-transitions-truncated="false">No state change has been recorded for this service yet.</p>`;
  }
  const truncated = health.transitions_truncated === true;
  // "at least N" when the count is a floor. A history retained before the lifetime counter existed
  // can only yield a lower bound, and printing it as an exact total would restate the defect the
  // counter was added to remove.
  const countText = health.transitions_total_exact === false ? `at least ${totalText}` : totalText;
  const header = truncated
    ? `${countText} state changes recorded — showing the latest ${debugSystemNumber(transitions.length)}; older rows exist and are not shown here.`
    : `${countText} state changes recorded — all of them are shown.`;
  const rows = transitions
    .slice()
    .reverse()
    .map(row => {
      const wallTime = Number(row?.wall_time);
      const when = Number.isFinite(wallTime) && wallTime > 0 ? relativeTimeFormat(Math.max(0, nowSeconds - wallTime)) : 'time not retained';
      const reason = String(row?.reason_code || '');
      const recovery = String(row?.recovery_outcome || '');
      const detail = [reason && reason !== 'none' ? reason : '', recovery && recovery !== 'none' ? `recovery ${recovery}` : ''].filter(Boolean).join(', ');
      return `<li>rev #${esc(String(row?.revision ?? '?'))} · ${esc(when)} · ${esc(String(row?.previous_state || '?'))} → ${esc(String(row?.new_state || '?'))}${detail ? ` (${esc(detail)})` : ''}</li>`;
    })
    .join('');
  return `<p data-subsystem-transitions data-transitions-truncated="${truncated}">${esc(header)}</p><ol class="js-debug-system-health-transitions">${rows}</ol>`;
}

// ---------------------------------------------------------------------------------------------
// THE DAEMONS ROSTER
//
// One adapter (`debugSystemRosterRows`) turns the ONE `/api/system-status` payload into stable
// top-level and child rows, and one renderer (`debugSystemRosterHtml`) draws them. Deliberately
// absent, because each would be a second copy of something that already has an owner:
//   * no service inventory -- the ids and their order come from `local_services.inventory`, owned
//     by `yolomux_lib/local_service_projection.py:LOCAL_SERVICE_INVENTORY`;
//   * no label map -- the display names come from the backend row's `label`
//     (`yolomux_lib/app.py:system_status_service`);
//   * no status classifier -- the paint comes from `debugSystemStateTone` and the word from
//     `debugSystemRosterStateLabel`;
//   * no metric formatter -- every cell goes through `debugSystemMetricText`.
// Row order never depends on health, so repeated scanning always finds a service in the same place.
// ---------------------------------------------------------------------------------------------

const DEBUG_SYSTEM_ROSTER_WEB_ID = 'web';
const DEBUG_SYSTEM_ROSTER_TMUX_WATCHER_ID = 'tmux-signal-watcher';
// The row that stands for the whole local-services block when the panel cannot read the payload
// well enough to know which services exist, and the count of rows that are always present.
const DEBUG_SYSTEM_ROSTER_LOCAL_SERVICES_ID = 'local-services';
// Every metric column a row can carry, in one list, so a row whose metrics are absent by
// STRUCTURE spells that absence once instead of repeating the column names per row.
const DEBUG_SYSTEM_ROSTER_METRIC_KEYS = Object.freeze([
  'cpu_now_percent', 'rss_bytes', 'uptime_seconds', 'restart_count',
  'request_count', 'error_count', 'latency_average_ms', 'latency_max_ms',
]);
const debugSystemRosterAbsentMetrics = reasonCode =>
  Object.fromEntries(DEBUG_SYSTEM_ROSTER_METRIC_KEYS.map(key => [key, debugSystemAbsentMetric(reasonCode)]));
// The tmux control-client states this panel knows, and how each one reads as a roster state.
// `never-started` is only an issue when something actually demanded the watcher; undemanded is
// idle-by-design and must stay gray and non-alerting.
const DEBUG_SYSTEM_TMUX_WATCHER_STATES = new Set(['never-started', 'attaching', 'no-sessions', 'attached', 'exited']);
const DEBUG_SYSTEM_ROSTER_STATE_LABEL_KEYS = Object.freeze({
  running: 'debug.system.roster.state.ready',
  idle: 'state.idle',
  issue: 'debug.system.localServices.state.issue',
  unavailable: 'debug.system.roster.state.unavailable',
  attached: 'debug.system.roster.state.attached',
  attaching: 'debug.system.roster.state.attaching',
  'no-sessions': 'debug.system.roster.state.noSessions',
  'never-started': 'debug.system.roster.state.neverStarted',
  exited: 'debug.system.roster.state.exited',
  schema_unsupported: 'debug.system.roster.state.unsupported',
});

function debugSystemRosterStateLabel(state) {
  const key = DEBUG_SYSTEM_ROSTER_STATE_LABEL_KEYS[String(state || '')];
  if (key) return t(key);
  return String(state || '') || t('common.notAvailable');
}

// Status is never carried by colour alone: the dot glyph differs by tone, the state WORD is always
// rendered beside it, and `data-subsystem-state` carries the machine-readable state. A row may
// override the tone when the STATE alone does not decide it (an undemanded watcher that never
// started is idle; a demanded one in the same state is an outage) -- the state word and the
// attribute still report the published state either way.
function debugSystemRosterStatusHtml(state, toneOverride = '') {
  const tone = toneOverride || debugSystemStateTone(state);
  const glyph = tone === 'muted' ? '○' : '●';
  return `<span class="js-debug-roster-status js-debug-system-state js-debug-system-state--${esc(tone)}" data-subsystem-tone="${esc(tone)}">`
    + `<span class="js-debug-roster-dot" aria-hidden="true">${glyph}</span>`
    + `<span data-subsystem-state-label>${esc(debugSystemRosterStateLabel(state))}</span></span>`;
}

// Every value on a roster row, in one shape, whatever produced it. `metrics` is always a full set
// of typed envelopes -- a producer that has no series for a column supplies a STRUCTURAL absence
// with its reason code rather than letting the cell fall back to a bare zero.
function debugSystemRosterWebRow(payload = {}, port = '') {
  const server = payload.server && typeof payload.server === 'object' ? payload.server : {};
  const absent = debugSystemAbsentMetric('web_process_not_observed');
  return {
    id: DEBUG_SYSTEM_ROSTER_WEB_ID,
    kind: 'web',
    parentId: '',
    label: t('debug.system.roster.web'),
    qualifier: port ? `:${port}` : '',
    state: payload.ok === false ? 'issue' : 'running',
    reason: payload.ok === false ? String(jsDebugSystemState.error || '') : '',
    metrics: {
      // Typed envelopes straight from the backend, exactly like a service row. They were built
      // here from raw floats until the producer started publishing the envelope, which is what
      // let an unsampled `0` render as `measured 0.0B` / `measured 0%` and be summed into the
      // strip's Memory and CPU totals while ~160MB of real RSS went uncounted.
      cpu_now_percent: server.cpu_percent,
      rss_bytes: server.rss_bytes,
      uptime_seconds: server.uptime_seconds,
      // The backend-health observer runs IN this web process and observes the six local services;
      // it has never observed this process, so these four have no series at all. Rendering them as
      // 0 restarts / 0 requests / 0 errors / 0 ms would be four fabricated measurements.
      restart_count: absent,
      request_count: absent,
      error_count: absent,
      latency_average_ms: absent,
      latency_max_ms: absent,
    },
    health: {},
  };
}

function debugSystemRosterTmuxWatcherRow(payload = {}) {
  const watcher = payload.tmux_signal_watcher && typeof payload.tmux_signal_watcher === 'object' ? payload.tmux_signal_watcher : {};
  const state = DEBUG_SYSTEM_TMUX_WATCHER_STATES.has(String(watcher.state || '')) ? String(watcher.state) : 'exited';
  return {
    id: DEBUG_SYSTEM_ROSTER_TMUX_WATCHER_ID,
    kind: 'child',
    parentId: DEBUG_SYSTEM_ROSTER_WEB_ID,
    label: t('debug.system.roster.tmuxSignalWatcher'),
    qualifier: '',
    state,
    // Undemanded and never started is idle by design, not an outage; demanded and never started is
    // the outage. The one issue predicate decides the paint here and the compact alert above, and
    // `data-subsystem-state` keeps saying which of the five typed states was actually published.
    tone: debugSystemTmuxSignalWatcherIsIssue(watcher) ? 'bad' : (state === 'attaching' ? 'warn' : (state === 'attached' ? 'good' : 'muted')),
    reason: String(watcher.reason || DEBUG_SYSTEM_TMUX_WATCHER_DEFAULT_REASONS[state] || ''),
    metrics: debugSystemRosterAbsentMetrics('subsystem_not_observed'),
    health: {},
  };
}

// The ONE row a payload this panel cannot render produces. Not a per-service row and not a
// variant of one: a schema mismatch means the panel does not know which services exist, so
// iterating `inventory`, reading `services`, or interpreting any field out of that payload would
// all be the same unearned confidence in different clothes. One row, one typed state, no payload.
function debugSystemRosterUnsupportedSchemaRow() {
  return {
    id: DEBUG_SYSTEM_ROSTER_LOCAL_SERVICES_ID,
    kind: 'service',
    parentId: '',
    label: t('debug.system.localServices.title'),
    // No daemon id to put beside the name: this row stands for the whole block.
    qualifier: '',
    state: 'schema_unsupported',
    reason: debugSystemHealthReasonText('schema_unsupported'),
    pid: 0,
    metrics: debugSystemRosterAbsentMetrics('schema_unsupported'),
    health: {},
    service: {},
  };
}

function debugSystemRosterServiceRow(service = {}) {
  const id = String(service?.id || service?.service || '');
  const metrics = service?.metrics && typeof service.metrics === 'object' ? service.metrics : {};
  const health = service?.health && typeof service.health === 'object' ? service.health : {};
  const healthMetrics = health.metrics && typeof health.metrics === 'object' ? health.metrics : {};
  // No schema guard here. This adapter is reached only for a schema this panel renders, because
  // an unsupported one never gets as far as a per-service row -- see `debugSystemRosterRows`.
  // Guarding field by field made the rule graded rather than absolute: it left the row trusting
  // the unknown payload's `id`, `label` and shape while refusing its numbers, which is a
  // half-trust that has to be re-decided every time a field is added.
  return {
    id,
    kind: 'service',
    parentId: '',
    label: String(service?.label || id),
    qualifier: id,
    state: String(service?.state || 'unavailable'),
    reason: String(service?.reason || ''),
    pid: Number(service?.pid || 0),
    metrics: {
      cpu_now_percent: metrics.cpu_now_percent,
      rss_bytes: metrics.rss_bytes,
      uptime_seconds: metrics.uptime_seconds,
      restart_count: healthMetrics.restart_count,
      request_count: healthMetrics.request_count,
      error_count: healthMetrics.error_count,
      latency_average_ms: healthMetrics.latency_average_ms,
      latency_max_ms: healthMetrics.latency_max_ms,
    },
    health,
    service,
  };
}

// The ONE adapter. Top-level rows in the inventory's declared order, with the web process first and
// its owned in-process subsystem nested beneath it.
function debugSystemRosterRows(payload = {}) {
  // Empty for a schema this panel cannot render, so `inventory`, `services` and the web row's port
  // qualifier are all absent by construction rather than by a branch further down.
  const localServices = debugSystemRenderableLocalServices(payload);
  const inventory = Array.isArray(localServices.inventory) ? localServices.inventory.map(String) : [];
  const services = Array.isArray(localServices.services) ? localServices.services : [];
  const servicesById = new Map(services.map(service => [String(service?.id || service?.service || ''), service]));
  const port = String(localServices.health?.port || '');
  const rows = [debugSystemRosterWebRow(payload, port), debugSystemRosterTmuxWatcherRow(payload)];
  // A version this panel does not render means it does not know which services exist, so it lists
  // none of them and says so once. The web process and its in-process subsystem stay, because
  // neither is described by `local_services` at all.
  if (!debugSystemLocalServicesSchemaSupported(payload)) {
    rows.push(debugSystemRosterUnsupportedSchemaRow());
    return rows;
  }
  for (const id of inventory) {
    // A service in the inventory with no row is a MISSING row, not an absent service: it renders
    // unavailable with a reason rather than silently disappearing from the roster.
    rows.push(debugSystemRosterServiceRow(servicesById.get(id) || {
      id,
      label: id,
      state: 'unavailable',
      reason: 'Service status is missing',
      metrics: {},
    }));
  }
  return rows;
}

// What the whole roster adds up to -- EVERY number in the strip, over ONE population, from the one
// array of rows the table itself renders.
//
// The counts used to come from these rows while CPU and memory came from `local_services.totals`,
// which the backend computes over the six local services only: `LocalServicesCollector.collect`
// raises if anything outside `LOCAL_SERVICE_INVENTORY` appears, so the web process cannot be in it
// and that total is child-only by contract. The strip therefore put "8 ready" next to a CPU figure
// that excluded one of the eight. Summing here, over the rendered rows, makes the two structurally
// incapable of disagreeing -- there is no second population to keep in step.
//
// `cpuMeasured`/`rssMeasured` count how many rows actually published a value, because a sum over
// zero measurements is an ABSENCE, not `0%`.
//
// The state counts run over EVERY row the table draws, including the nested child. They used to
// skip `kind === 'child'`, so the strip said "7" while eight rows rendered and a red tmux-watcher
// row was absent from the `issues` count that is supposed to be why a reader opens this view. The
// filter is gone: `population` is `rows.length`, the same array `debugSystemRosterHtml` renders,
// so the count and the render cannot answer "how many rows are there" differently.
//
// `resourcePopulation` answers a DIFFERENT question -- how many of those rows own a process whose
// CPU and memory can be summed at all. A row with a `parentId` runs INSIDE its parent, so its
// resources are already inside the parent's figure; counting it as an unmeasured row would flag
// every complete total as partial forever. It is counted in this same pass, off the same array,
// off the `parentId` the renderer already uses to nest the row.
function debugSystemRosterSummary(rows = []) {
  const counts = {
    ready: 0, idle: 0, issues: 0,
    population: rows.length, resourcePopulation: 0,
    cpuPercent: 0, rssBytes: 0, cpuMeasured: 0, rssMeasured: 0,
  };
  for (const row of rows) {
    if (!row.parentId) counts.resourcePopulation += 1;
    const tone = row.tone || debugSystemStateTone(row.state);
    if (tone === 'good') counts.ready += 1;
    else if (tone === 'muted') counts.idle += 1;
    else counts.issues += 1;
    const cpu = row.metrics?.cpu_now_percent;
    if (cpu?.state === 'measured') {
      counts.cpuPercent += Number(cpu.value) || 0;
      counts.cpuMeasured += 1;
    }
    const rss = row.metrics?.rss_bytes;
    if (rss?.state === 'measured') {
      counts.rssBytes += Number(rss.value) || 0;
      counts.rssMeasured += 1;
    }
  }
  return counts;
}

// ONE owner for "this number covers less than it looks like", rendered by both the roster's metric
// cells and the summary strip. It used to be the literal word `partial` written out twice, verbatim,
// in two renderers.
//
// It is a FOOTNOTE to the number, so it renders as one: a real `<sup>` marker directly after the
// value (`826MB*`), not a word beside it that reads as a second value in a dense row. The panel had
// no footnote-marker convention before this, so `*` becomes it -- every partial-coverage mark in
// this panel uses this function, not a second glyph.
//
// A bare `*` is meaningless to a screen reader, so the glyph is hidden from the accessibility tree
// and the word rides in the panel's existing `a11y-only` span: a sighted reader gets the marker, a
// screen reader still hears "partial". The full sentence stays where it already was -- the cell's
// own `title` and the row's disclosure -- and every machine-readable attribute stays on the caller's
// element, not on this marker.
const DEBUG_SYSTEM_COVERAGE_FLAG_MARKER = '*';
function debugSystemCoverageFlagHtml() {
  return `<sup class="js-debug-system-coverage-flag" data-coverage-flag><span aria-hidden="true">${DEBUG_SYSTEM_COVERAGE_FLAG_MARKER}</span><span class="a11y-only">${esc(t('debug.system.roster.coverage.partial'))}</span></sup>`;
}

// One cell. `parts` renders two typed envelopes side by side; both still go through the one metric
// text renderer and both still carry their own state and coverage, so an untimed average and a
// measured maximum cannot be collapsed into one number.
function debugSystemRosterMetricCellHtml(row, column) {
  const health = row.health && typeof row.health === 'object' ? row.health : {};
  const keys = column.parts || [column.key];
  const coverageState = column.coverage ? debugSystemHealthColumnCoverage(health, column) : '';
  const rendered = keys.map(key => {
    const metric = row.metrics?.[key];
    const attribute = DEBUG_SYSTEM_PROCESS_METRIC_KEYS.has(key) ? 'data-subsystem-metric' : 'data-subsystem-health-metric';
    const measured = metric?.state === 'measured';
    // A partial count is flagged in the cell itself; the reason sits in the row's own disclosure.
    const flag = measured && coverageState === 'partial' ? debugSystemCoverageFlagHtml() : '';
    const coverageAttr = column.coverage ? ` data-metric-coverage="${esc(coverageState)}"` : '';
    // An unmeasured value is an em dash in the ROW and its full sentence in the row's disclosure.
    // Printing the whole reason in the cell is what made the old table wider than its scroller; the
    // rule that matters is unchanged -- it is never `0`, never blank and never green -- and the
    // reason is carried in three readable places: the cell's title, the cell's reason code, and
    // the metric list inside the disclosure.
    const reason = measured ? '' : debugSystemMetricText(metric, key);
    const explain = measured ? '' : ` title="${esc(reason)}" data-metric-reason="${esc(metric?.reason_code || '')}"`;
    return `<span ${attribute}="${esc(key)}" data-metric-state="${esc(metric?.state || 'unavailable')}"${coverageAttr}${explain}>${measured ? esc(debugSystemMetricText(metric, key)) : '—'}${flag}</span>`;
  });
  const body = rendered.join('<span class="js-debug-roster-sep" aria-hidden="true"> / </span>');
  // The column's own name, inside the cell, from the SAME `column.labelKey` the header uses -- one
  // label string, two positions. It is shown if and only if the header row is hidden, which is the
  // contract `.js-debug-roster-dropped` already has with the columns it copies. Below 36rem the row
  // stacks into two lines and a header can no longer label anything above it, and `12.5ms / 340ms`
  // with no name beside it is not a readable row.
  const label = `<span class="js-debug-roster-celllabel">${esc(t(column.labelKey))}</span>`;
  return `<td role="cell" class="js-debug-roster-cell js-debug-roster-cell--${esc(column.priority)}" data-subsystem-column="${esc(column.key)}">${label}${body}</td>`;
}

// The explicit ARIA roles below are load-bearing, not decoration: below 36rem the roster's boxes
// become `display: block` so the row can lay out as two readable lines, and a `display: block`
// table loses its implicit table/row/cell semantics in every engine. The roles restate exactly what
// the native elements already mean at every other width, so the structure a screen reader hears is
// the same one at 390px as at 1920px.
function debugSystemRosterHeaderHtml() {
  const columns = DEBUG_SYSTEM_ROSTER_COLUMNS.map(column => {
    const healthColumn = column.coverage ? ` data-subsystem-health-column="${esc(column.parts ? column.parts[0] : column.key)}"` : '';
    return `<th role="columnheader" scope="col" class="js-debug-roster-cell js-debug-roster-cell--${esc(column.priority)}" data-subsystem-column="${esc(column.key)}"${healthColumn}>${esc(t(column.labelKey))}</th>`;
  }).join('');
  return `<thead role="rowgroup"><tr role="row"><th role="columnheader" scope="col" class="js-debug-roster-service">${esc(t('debug.system.roster.column.service'))}</th>`
    + `<th role="columnheader" scope="col" class="js-debug-roster-statushead">${esc(t('debug.system.localServices.field.status'))}</th>${columns}</tr></thead>`;
}

function debugSystemRosterRowHtml(row, {expanded, columnCount, payload, nowSeconds}) {
  const detailId = `js-debug-roster-detail-${row.id}`;
  const rowClass = `js-debug-roster-row js-debug-roster-row--${row.kind}`;
  const name = row.qualifier ? `${row.label} · ${row.qualifier}` : row.label;
  const toggleLabel = t(expanded ? 'debug.system.roster.collapse' : 'debug.system.roster.expand', {service: name});
  const cells = DEBUG_SYSTEM_ROSTER_COLUMNS.map(column => debugSystemRosterMetricCellHtml(row, column)).join('');
  const head = `<tr role="row" class="${rowClass}" data-subsystem-row data-subsystem-id="${esc(row.id)}" data-subsystem-kind="${esc(row.kind)}" data-subsystem-state="${esc(row.state)}"${row.parentId ? ` data-subsystem-parent="${esc(row.parentId)}"` : ''}>
    <th role="rowheader" scope="row" class="js-debug-roster-service">
      <button type="button" class="js-debug-roster-toggle" data-js-debug-roster-toggle="${esc(row.id)}" data-js-debug-system-focus-key="roster-toggle:${esc(row.id)}" aria-expanded="${expanded ? 'true' : 'false'}" aria-controls="${esc(detailId)}" aria-label="${esc(toggleLabel)}">
        <span class="js-debug-roster-chevron" aria-hidden="true">${expanded ? '▾' : '▸'}</span>
        <span class="js-debug-system-service-name">${esc(row.label)}</span>${row.qualifier ? `<span class="js-debug-roster-qualifier">${esc(row.qualifier)}</span>` : ''}
      </button>
    </th>
    <td role="cell" class="js-debug-roster-statuscell" data-subsystem-value>${debugSystemRosterStatusHtml(row.state, row.tone)}<span class="js-debug-roster-reason" data-subsystem-reason${row.reason ? '' : ' hidden'}>${esc(row.reason)}</span></td>
    ${cells}
  </tr>`;
  // Lazy CONTENT, stable TARGET. The transition list, the coverage notes, the sampler families and
  // the cache dictionaries are BUILT only for an expanded row -- rendering them for eight rows and
  // hiding them with CSS is the default-DOM weight this redesign exists to remove. The empty
  // container still exists, because `aria-controls` must resolve to a real element for the state
  // the button reports to mean anything.
  return `${head}<tr role="row" class="js-debug-roster-detailrow" data-subsystem-detail-row data-subsystem-id="${esc(row.id)}" data-subsystem-detail-built="${expanded ? 'true' : 'false'}"${expanded ? '' : ' hidden'}>
    <td role="cell" colspan="${columnCount}" id="${esc(detailId)}">${expanded ? debugSystemRosterDetailHtml(row, payload, nowSeconds) : ''}</td>
  </tr>`;
}

// The roster. `expanded` is passed in rather than read from module state so the render is a pure
// function of the payload plus the disclosure set, which is what makes it testable off-browser.
function debugSystemRosterHtml(payload = {}, {nowSeconds = Date.now() / 1000, expanded = new Set()} = {}) {
  const rows = debugSystemRosterRows(payload);
  const columnCount = 2 + DEBUG_SYSTEM_ROSTER_COLUMNS.length;
  // The denominator, once. Every row publishes the same counter scope, so this is a fact about the
  // table and it is stated beneath it -- not repeated in three column headers and in every expanded
  // row, which is how the same sentence used to appear nine times in one view.
  const sentences = [...new Set(rows.map(row => debugSystemHealthScopeSentence(row.health)).filter(Boolean))];
  // A sibling paragraph rather than a `<caption>`: a caption participates in the table's own
  // min-content width, so a 120-character sentence would become the new reason the roster cannot
  // narrow -- trading three over-wide columns for one over-wide table.
  const caption = sentences.length === 1
    ? `<p class="js-debug-system-column-scope" data-subsystem-scope-caption>${esc(sentences[0])}</p>`
    : '';
  const body = rows.map(row => debugSystemRosterRowHtml(row, {
    expanded: expanded.has(row.id),
    columnCount,
    payload,
    nowSeconds,
  })).join('');
  return `<div class="js-debug-system-table-wrap js-debug-roster-wrap"><table role="table" class="js-debug-system-table js-debug-roster-table" data-js-debug-roster>
    ${debugSystemRosterHeaderHtml()}
    <tbody role="rowgroup">${body}</tbody>
  </table></div>${caption}`;
}

// The values the responsive layout may have DROPPED -- and only those. Service, Status, Latency and
// Uptime are `priority: primary` and survive every width, so listing them here restated, directly
// underneath, seven columns the reader could already see: that was ~40 lines of disclosure of which
// the top third was duplication.
//
// The secondary columns stay in the markup because "a narrow window must never be the reason a
// number is unreachable" is the load-bearing rule -- but they are only DISPLAYED by the same
// container query that hides them from the row (`.js-debug-roster-dropped`), so at a width where
// the column is visible its copy is not. One source of truth for which columns can drop:
// DEBUG_SYSTEM_ROSTER_COLUMNS' own `priority`, the same field the CSS class comes from.
function debugSystemRosterMetricListHtml(row) {
  const entries = [];
  for (const column of DEBUG_SYSTEM_ROSTER_COLUMNS) {
    if (column.priority !== 'secondary') continue;
    const keys = column.parts || [column.key];
    entries.push([t(column.labelKey), keys.map(key => debugSystemMetricText(row.metrics?.[key], key)).join(' / ')]);
  }
  if (!entries.length) return '';
  return `<div class="js-debug-roster-dropped" data-subsystem-dropped-metrics>${debugSystemRowsHtml(entries)}</div>`;
}

function debugSystemRosterDetailHtml(row, payload = {}, nowSeconds = Date.now() / 1000) {
  const sections = [debugSystemRosterMetricListHtml(row)];
  if (row.kind === 'web') sections.push(debugSystemWebProcessDetailHtml(payload));
  else if (row.kind === 'child') sections.push(debugSystemTmuxSignalWatcherDetailHtml(payload.tmux_signal_watcher));
  else sections.push(debugSystemServiceDetailHtml(row, payload, nowSeconds));
  return `<div class="js-debug-roster-detail">${sections.filter(Boolean).join('')}</div>`;
}

// The retained observation, spelled out, for one service row. This is the block that used to be
// rendered under EVERY service whether or not anyone asked for it.
function debugSystemServiceHealthDetailHtml(health = {}, nowSeconds = Date.now() / 1000) {
  const notes = debugSystemHealthCoverageNotes(health);
  const errorsByReason = debugSystemHealthErrorsByReasonText(health);
  return `<div class="js-debug-system-health-row" data-subsystem-health-row data-subsystem-observed="${health.observed === true}">
    <p data-subsystem-health-state>${esc(debugSystemHealthObservedText(health))}</p>
    <p data-subsystem-health-detail>${esc(debugSystemHealthDetailText(health))}</p>
    ${errorsByReason ? `<p data-subsystem-errors-by-reason>${esc(errorsByReason)}</p>` : ''}
    ${notes.length ? `<ul class="js-debug-system-health-notes">${notes.map(note => `<li data-subsystem-coverage-note>${esc(note)}</li>`).join('')}</ul>` : ''}
    ${debugSystemHealthTransitionsHtml(health, nowSeconds)}
  </div>`;
}

// The schema this panel renders. M8 bumped it to 2 when every row grew a `health` block and the
// payload grew a snapshot-level one; W13 bumped it to 3 when the dead `alert` summary was removed
// from the payload. The guard is exact, not `>=`: rendering an older payload through the roster
// would print absent health as though it had been measured, which is the defect the version number
// exists to prevent.
//
// It has ONE reader: `debugSystemRosterServiceRow`, which turns a false answer into the typed
// `schema_unsupported` row state. There used to be a second -- a whole retained per-cell table,
// with its own classifier, its own lifecycle state map and its own freshness rules -- rendered as
// a card inside Advanced for exactly this case. Two renderers and two classifiers for one
// condition the roster already covers is the divergent-copies defect; the roster says it in one
// typed state now, and the legacy view is gone.
function debugSystemLocalServicesSchemaSupported(payload = {}) {
  return Number(payload.local_services?.schema_version) === 3;
}

// The ONE reader of `payload.local_services`, and the reason the rule above is absolute rather
// than aspirational. It hands back an EMPTY block for a schema this panel cannot render, so no
// field of an unreadable payload can reach any renderer by any path -- including a path added
// later by someone who never read the version guard.
//
// This exists because the guard used to live inside `debugSystemRosterRows`, one branch DOWN from
// the reads: `inventory`, `services` and `health.port` were all pulled out of the payload above
// it, and the port was handed to the web row before the branch was ever evaluated. An unsupported
// payload therefore still changed the rendered HTML while the tests asserted it was interpreted
// nowhere, and the suite stayed green. A rule written at one call site is not a rule; a rule that
// owns the only read is.
function debugSystemRenderableLocalServices(payload = {}) {
  if (!debugSystemLocalServicesSchemaSupported(payload)) return {};
  return payload.local_services && typeof payload.local_services === 'object' ? payload.local_services : {};
}

function debugSystemRolesHtml(roles = {}) {
  const rows = Object.entries(roles && typeof roles === 'object' ? roles : {});
  if (!rows.length) return `<p class="js-debug-system-empty">${esc(t('common.notAvailable'))}</p>`;
  return `<div class="js-debug-system-table-wrap"><table class="js-debug-system-table">
    <thead><tr><th>Role</th><th>Status</th><th>Refreshes</th><th>Fallbacks</th><th>Stale reads</th></tr></thead>
    <tbody>${rows.map(([name, role]) => `<tr>
      <td>${esc(name)}</td><td>${esc(role?.status || (role?.owner ? 'owner' : 'follower'))}</td>
      <td>${esc(debugSystemNumber(role?.refresh_requests))}</td><td>${esc(debugSystemNumber(role?.fallback_count))}</td>
      <td>${esc(debugSystemNumber(role?.follower_stale_reads))}</td>
    </tr>`).join('')}</tbody>
  </table></div>`;
}

function debugSystemPerformanceTableHtml(rows = [], kind = 'endpoint') {
  if (!Array.isArray(rows) || !rows.length) return `<p class="js-debug-system-empty">${esc(t('common.notAvailable'))}</p>`;
  return `<div class="js-debug-system-table-wrap"><table class="js-debug-system-table">
    <thead><tr><th>${kind === 'endpoint' ? 'Endpoint' : 'Worker'}</th><th>Calls</th><th>Max</th><th>Payload</th></tr></thead>
    <tbody>${rows.map(row => `<tr>
      <td>${esc(kind === 'endpoint' ? row.surface : `${row.role || ''} · ${row.surface || ''}`)}</td>
      <td>${esc(debugSystemNumber(row.count))}</td>
      <td>${esc(debugGraphTerseTimeText(row.compute_ms_max))}</td>
      <td>${esc(debugGraphTerseBytesText(row.payload_bytes_total))}</td>
    </tr>`).join('')}</tbody>
  </table></div>`;
}

function debugSystemRecurringWorkHtml(rows = [], nowSeconds = Date.now() / 1000) {
  if (!Array.isArray(rows) || !rows.length) return `<p class="js-debug-system-empty">${esc(t('common.notAvailable'))}</p>`;
  return `<div class="js-debug-system-table-wrap"><table class="js-debug-system-table js-debug-system-fixed-table" data-js-debug-recurring-work>
    <thead><tr><th>Owner</th><th>Class</th><th>Cadence</th><th>Demand</th><th>Attempt / useful / unchanged / failed</th><th>Last useful</th><th>Next due</th></tr></thead>
    <tbody>${rows.map(row => {
      const cadence = Math.max(0, Number(row?.cadence_seconds) || 0);
      const lastUsefulAt = Math.max(0, Number(row?.last_useful_at) || 0);
      const nextDue = Math.max(0, Number(row?.next_due_in_seconds) || 0);
      return `<tr data-js-debug-recurring-work-owner="${esc(row?.owner || '')}">
        <th scope="row">${esc(row?.owner || t('common.notAvailable'))}</th><td>${esc(row?.class || t('common.notAvailable'))}</td>
        <td>${esc(cadence > 0 ? debugGraphTerseTimeText(cadence * 1000) : '—')}</td><td>${row?.demanded ? 'Yes' : 'No'}</td>
        <td>${esc(`${debugSystemNumber(row?.attempts)} / ${debugSystemNumber(row?.useful)} / ${debugSystemNumber(row?.no_change)} / ${debugSystemNumber(row?.failures)}`)}</td>
        <td>${esc(lastUsefulAt > 0 ? relativeTimeFormat(Math.max(0, nowSeconds - lastUsefulAt)) : '—')}</td>
        <td>${esc(nextDue > 0 ? debugGraphTerseTimeText(nextDue * 1000) : '—')}</td>
      </tr>`;
    }).join('')}</tbody>
  </table></div>`;
}

function debugSystemSamplerFamilyEntries(value) {
  if (Array.isArray(value)) {
    return value.map((family, index) => [String(family?.family || family?.name || index), family || {}]);
  }
  if (!value || typeof value !== 'object') return [];
  return Object.entries(value).filter(([, family]) => family && typeof family === 'object');
}

function debugSystemSamplerFamilyNumber(family, ...keys) {
  for (const key of keys) {
    if (family?.[key] == null) continue;
    const value = Number(family[key]);
    if (Number.isFinite(value)) return Math.max(0, value);
  }
  return 0;
}

function debugSystemSamplerFamilySuccessAge(family, nowSeconds) {
  const reportedAge = debugSystemSamplerFamilyNumber(family, 'last_success_age_seconds');
  if (reportedAge > 0) return relativeTimeFormat(reportedAge);
  let succeededAt = debugSystemSamplerFamilyNumber(family, 'last_success_at', 'last_success');
  if (succeededAt > 1e12) succeededAt /= 1000;
  return succeededAt > 0 ? relativeTimeFormat(Math.max(0, nowSeconds - succeededAt)) : '—';
}

function debugSystemGeneratedAge(value, nowSeconds = Date.now() / 1000) {
  const timestamp = Number(value);
  // This field is wall-clock data only. Monotonic counters are small positive
  // numbers too, but subtracting one from Date.now() produces a confident,
  // false multi-year age instead of admitting that no wall-clock time exists.
  if (!Number.isFinite(timestamp) || timestamp < 1_000_000_000) return t('common.notAvailable');
  return relativeTimeFormat(Math.max(0, nowSeconds - timestamp));
}

function debugSystemSamplerFamilySeconds(family, secondsKeys, millisecondsKeys) {
  const seconds = debugSystemSamplerFamilyNumber(family, ...secondsKeys);
  if (seconds > 0) return seconds;
  return debugSystemSamplerFamilyNumber(family, ...millisecondsKeys) / 1000;
}

function debugSystemSamplerHeaderHtml(shortKey, fullKey) {
  const full = t(fullKey);
  return `<th scope="col" title="${esc(full)}" aria-label="${esc(full)}"><span aria-hidden="true">${esc(t(shortKey))}</span></th>`;
}

function debugSystemSamplerFamiliesHtml(value, nowSeconds = Date.now() / 1000) {
  const families = debugSystemSamplerFamilyEntries(value);
  if (!families.length) return '';
  return `<div class="js-debug-system-table-wrap" data-js-debug-sampler-families><table class="js-debug-system-table js-debug-system-fixed-table js-debug-system-sampler-table">
    <thead><tr>${[
      ['debug.system.sampler.header.family.short', 'debug.system.sampler.header.family.short'],
      ['debug.system.sampler.header.cadence.short', 'debug.system.sampler.header.cadence.full'],
      ['debug.system.sampler.header.aliveRun.short', 'debug.system.sampler.header.aliveRun.full'],
      ['debug.system.sampler.header.attSuccFails.short', 'debug.system.sampler.header.attSuccFails.full'],
      ['debug.system.sampler.header.lateMiss.short', 'debug.system.sampler.header.lateMiss.full'],
      ['debug.system.sampler.header.runtime.short', 'debug.system.sampler.header.runtime.full'],
      ['debug.system.sampler.header.lastOk.short', 'debug.system.sampler.header.lastOk.full'],
      ['debug.system.sampler.header.lastFail.short', 'debug.system.localServices.field.lastFailure'],
    ].map(([shortKey, fullKey]) => debugSystemSamplerHeaderHtml(shortKey, fullKey)).join('')}</tr></thead>
    <tbody>${families.map(([name, family]) => {
      const cadence = debugSystemSamplerFamilySeconds(family, ['cadence_seconds', 'interval_seconds'], ['cadence_ms', 'interval_ms']);
      const runtime = debugSystemSamplerFamilySeconds(family, ['last_runtime_seconds', 'runtime_seconds', 'last_runtime'], ['last_runtime_ms', 'runtime_ms']);
      const running = family.running === true || family.in_flight === true;
      const alive = family.alive === true || family.sampler_alive === true || running;
      const attempts = debugSystemSamplerFamilyNumber(family, 'attempts', 'attempt_count');
      const successes = debugSystemSamplerFamilyNumber(family, 'successes', 'success_count');
      const failures = debugSystemSamplerFamilyNumber(family, 'failures', 'failure_count');
      const late = debugSystemSamplerFamilyNumber(family, 'late', 'late_cycles');
      const missed = debugSystemSamplerFamilyNumber(family, 'missed', 'missed_cycles');
      return `<tr data-js-debug-sampler-family="${esc(name)}">
        <th scope="row">${esc(name)}</th><td>${esc(cadence > 0 ? debugGraphTerseTimeText(cadence * 1000) : '—')}</td>
        <td>${alive ? 'Yes' : 'No'} / ${running ? 'Yes' : 'No'}</td>
        <td>${esc(`${debugSystemNumber(attempts)} / ${debugSystemNumber(successes)} / ${debugSystemNumber(failures)}`)}</td>
        <td>${esc(`${debugSystemNumber(late)} / ${debugSystemNumber(missed)}`)}</td>
        <td>${esc(runtime > 0 ? debugGraphTerseTimeText(runtime * 1000) : '—')}</td>
        <td>${esc(debugSystemSamplerFamilySuccessAge(family, nowSeconds))}</td>
        <td>${esc(family.last_failure || '—')}</td>
      </tr>`;
    }).join('')}</tbody>
  </table></div>`;
}

// The YO!stats sampler diagnostics. One owner, rendered in exactly one place: statsd's own row
// disclosure. It used to be a standalone default card two screens above the service it describes.
function debugSystemStatsSamplerBodyHtml(services = [], nowSeconds = Date.now() / 1000) {
  const statsd = (services || []).find(service => String(service?.service || '') === 'statsd') || {};
  const profile = statsd.history_profile && typeof statsd.history_profile === 'object' ? statsd.history_profile : {};
  const requests = Math.max(0, Number(statsd.history_requests) || 0);
  const hits = Math.min(requests, Math.max(0, Number(statsd.history_cache_hits) || 0));
  // A ratio with no denominator and an unassembled query are absences too, so they carry their
  // reason like every other em dash in this block rather than being three bare dashes beside three
  // explained ones.
  const hitRate = requests > 0
    ? {text: `${debugSystemNumber((hits / requests) * 100, 1)}% (${hits}/${requests})`, reason: ''}
    : {text: '—', reason: debugSystemHealthReasonText('history_never_requested')};
  const historyQuery = profile.returned_records == null || profile.source_records == null
    ? {text: '—', reason: debugSystemHealthReasonText('history_not_assembled')}
    : {text: `${debugSystemNumber(profile.returned_records)} returned · ${debugSystemNumber(profile.source_records)} source`, reason: ''};
  const usage = statsd.usage && typeof statsd.usage === 'object' ? statsd.usage : {};
  const usageHealth = usage.health && typeof usage.health === 'object' ? usage.health : {};
  const usageState = ['ok', 'warning', 'idle'].includes(usageHealth.state) ? usageHealth.state : 'idle';
  const usageLabel = usageState === 'warning' ? 'Warning' : (usageState === 'ok' ? 'Healthy' : 'Idle');
  const usageReason = usageHealth.reason || 'no usage health evidence';
  // The last two unmeasured values in this disclosure. They read "—" and "not available" side by
  // side in the same strip before this; both are absences and both now carry their reason.
  const accepted = debugSystemScalar(usageHealth.last_accepted_atom_age_seconds, 'usage_no_accepted_atom', value => debugGraphTerseTimeText(value * 1000));
  const conflicts = debugSystemScalar(usage.quarantined_conflict_count, 'usage_conflicts_not_published');
  const usageHtml = `<div class="js-debug-usage-health js-debug-usage-health--${esc(usageState)}" data-js-debug-usage-health="${esc(usageState)}" ${usageState === 'warning' ? 'role="alert"' : 'role="status"'}>
    <strong>${esc(usageLabel)}:</strong> <span>${esc(usageReason)}</span>
    <span>Last accepted <span${accepted.reason ? ` title="${esc(accepted.reason)}" data-value-reason="${esc(accepted.reason)}"` : ''}>${esc(accepted.text)}</span> · Quarantined conflicts <span${conflicts.reason ? ` title="${esc(conflicts.reason)}" data-value-reason="${esc(conflicts.reason)}"` : ''}>${esc(conflicts.text)}</span></span>
  </div>`;
  // Every unpublished sampler field goes through the ONE unmeasured spelling. Before this, three
  // rows here each lied differently: the deadlines printed "not available / not available" while
  // the rest of the panel printed an em dash with a reason, and `Last cycle` and `Last history
  // latency` used `|| 0` -- rendering a sampler that has never run as a measured `0ms`.
  const lastCycle = debugSystemScalar(statsd.sampler_last_cycle_seconds, 'sampler_cycle_not_timed', value => debugGraphTerseTimeText(value * 1000));
  const late = debugSystemScalar(statsd.sampler_late_cycles, 'sampler_cycles_not_published');
  const missed = debugSystemScalar(statsd.sampler_missed_cycles, 'sampler_cycles_not_published');
  const assemble = debugSystemScalar(profile.assemble_ms, 'history_not_assembled', value => debugGraphTerseTimeText(value));
  const aggregate = debugSystemRowsHtml([
    ['Status', statsd.sampler_alive === true ? 'Running' : 'Idle'],
    ['Last cycle', lastCycle.text, lastCycle.reason],
    ['Late / missed deadlines', `${late.text} / ${missed.text}`, late.reason || missed.reason],
    ['History cache hit rate', hitRate.text, hitRate.reason],
    ['Last history latency', assemble.text, assemble.reason],
    ['Last history query', historyQuery.text, historyQuery.reason],
  ]);
  return `<h4 class="js-debug-roster-detail-title">${esc(t('debug.system.roster.detail.sampler'))}</h4>${aggregate}${usageHtml}${debugSystemSamplerFamiliesHtml(statsd.sampler_families, nowSeconds)}`;
}

// ---- rehomed diagnostics: each block now lives under the row that owns it -------------------

function debugSystemWebProcessDetailHtml(payload = {}) {
  const server = payload.server && typeof payload.server === 'object' ? payload.server : {};
  const clientEvents = payload.client_events && typeof payload.client_events === 'object' ? payload.client_events : {};
  const chat = payload.chat && typeof payload.chat === 'object' ? payload.chat : {};
  // System CPU is the fourth metric off the same unpushed sample. It printed a confident `0.0%`
  // beside three cells that had already learned to say why they were empty, so it goes through
  // the ONE envelope renderer with its reason like every other unmeasured value in a disclosure.
  const systemCpu = server.system_cpu_percent && typeof server.system_cpu_percent === 'object' ? server.system_cpu_percent : {};
  const systemCpuText = debugSystemScalar(systemCpu.value, '', value => `${debugSystemNumber(value, 1)}%`);
  const identity = debugSystemRowsHtml([
    ['Version', server.version],
    ['PID', server.pid],
    ['State directory', payload.state_dir],
    ['System CPU', systemCpuText.text, systemCpuText.text === '—' ? String(systemCpu.reason || '') : ''],
  ]);
  const events = debugSystemRowsHtml([
    ['SSE subscribers', Object.values(clientEvents.channel_counts || {}).reduce((sum, value) => sum + Number(value || 0), 0)],
    ['Published events', clientEvents.published_events],
    ['Delivered events', clientEvents.delivered_events],
    ['Chat subscribers', chat.subscribers],
    ['Chat messages', chat.store?.message_rows],
    ['Typing leases', chat.store?.typing_leases],
  ]);
  return `<h4 class="js-debug-roster-detail-title">${esc(t('debug.system.roster.detail.process'))}</h4>${identity}
    <p class="js-debug-roster-note" data-subsystem-unobserved-note>${esc(debugSystemHealthReasonText('web_process_not_observed'))}</p>
    <h4 class="js-debug-roster-detail-title">${esc(t('debug.system.roster.detail.eventsChat'))}</h4>${events}`;
}

function debugSystemTmuxSignalWatcherDetailHtml(watcher = {}) {
  const state = DEBUG_SYSTEM_TMUX_WATCHER_STATES.has(String(watcher?.state || '')) ? String(watcher.state) : 'exited';
  const demandKnown = typeof watcher?.demanded === 'boolean';
  const demanded = watcher?.demanded === true;
  const sessions = Array.isArray(watcher?.sessions) ? watcher.sessions.filter(value => typeof value === 'string' && value).join(', ') : '';
  const issue = debugSystemTmuxSignalWatcherIsIssue(watcher);
  const stateLabel = state === 'never-started' && demandKnown && !demanded ? t('state.idle') : debugSystemRosterStateLabel(state);
  return `<div data-js-debug-tmux-signal-watcher data-tmux-signal-watcher-state="${esc(state)}" data-tmux-signal-watcher-demanded="${demandKnown ? String(demanded) : 'unknown'}" role="${issue ? 'alert' : 'status'}">
    ${debugSystemRowsHtml([
      ['State', stateLabel],
      // One unmeasured spelling in this view too: an unpublished demand flag is the em dash with
      // its reason, not a fourth wording of "not available" beside three em dashes.
      ['Demand', demandKnown ? (demanded ? 'Yes' : 'No') : '—', demandKnown ? '' : debugSystemHealthReasonText('watcher_demand_unpublished')],
      ['Control client PID', Number(watcher?.process_pid) > 0 ? watcher.process_pid : '—'],
      ['Sessions', sessions || '—'],
      ['Detail', watcher?.reason || DEBUG_SYSTEM_TMUX_WATCHER_DEFAULT_REASONS[state]],
    ])}
    <p class="js-debug-roster-note" data-subsystem-unobserved-note>${esc(debugSystemHealthReasonText('subsystem_not_observed'))}</p>
  </div>`;
}

function debugSystemSearchCachesDetailHtml(payload = {}) {
  const search = payload.search_index && typeof payload.search_index === 'object' ? payload.search_index : {};
  const caches = payload.caches && typeof payload.caches === 'object' ? payload.caches : {};
  return `<h4 class="js-debug-roster-detail-title">${esc(t('debug.system.roster.detail.searchCaches'))}</h4>${debugSystemRowsHtml([
    ['Indexed roots', search.root_count], ['Builds', search.build_count], ['Scanned entries', search.scanned_entries],
    ['Ignored entries', search.ignored_entries], ['Index bytes', debugGraphTerseBytesText(search.cache_bytes)],
    ['Session files cache', `${debugSystemNumber(caches.session_files?.files)} files · ${debugGraphTerseBytesText(caches.session_files?.bytes)}`],
    ['Activity cache', `${debugSystemNumber(caches.activity?.files)} files · ${debugGraphTerseBytesText(caches.activity?.bytes)}`],
  ])}`;
}

// Which rehomed diagnostic block belongs to which service. One table, so a block cannot end up
// rendered twice or under a service that does not own it.
const DEBUG_SYSTEM_ROSTER_SERVICE_DETAIL = Object.freeze({
  statsd: (payload, nowSeconds) => debugSystemStatsSamplerBodyHtml(
    (services => (Array.isArray(services) ? services : []))(debugSystemRenderableLocalServices(payload).services),
    nowSeconds,
  ),
  indexd: payload => debugSystemSearchCachesDetailHtml(payload),
});

function debugSystemServiceDetailHtml(row, payload = {}, nowSeconds = Date.now() / 1000) {
  const identity = debugSystemRowsHtml([
    ['Service id', row.id],
    ['PID', Number(row.pid) > 0 ? row.pid : '—', Number(row.pid) > 0 ? '' : debugSystemHealthReasonText('process_not_running')],
    ['Reason', row.reason || '—', row.reason ? '' : debugSystemHealthReasonText('state_reason_unpublished')],
  ]);
  const owned = DEBUG_SYSTEM_ROSTER_SERVICE_DETAIL[row.id];
  return `<h4 class="js-debug-roster-detail-title">${esc(t('debug.system.roster.detail.process'))}</h4>${identity}
    ${debugSystemServiceHealthDetailHtml(row.health, nowSeconds)}
    ${owned ? owned(payload, nowSeconds) : ''}`;
}

function debugSystemCpuBudgetCardHtml(budget = {}) {
  // `stale` was missing from this list, so the ONE state the backend publishes to say "no sample
  // stands behind these numbers" was coerced to `ok` -- the card rendered healthy exactly when the
  // budget was unknown. `DEBUG_SYSTEM_STATE_TONES` has painted `stale` as `warn` all along; the
  // vocabulary existed and this whitelist dropped the connection.
  const status = ['ok', 'watching', 'warning', 'stale'].includes(budget.status) ? budget.status : 'ok';
  const consumers = Array.isArray(budget.top_consumers) ? budget.top_consumers : [];
  const consumerText = consumers.map(row => {
    const owner = [row?.role, row?.surface].filter(Boolean).join(':');
    return `${owner || t('common.notAvailable')} ${debugSystemNumber(row?.compute_ms_total, 1)}ms`;
  }).join(' · ') || 'None';
  // The numerator is a measurement and the denominator is a policy constant. Only the numerator
  // can be absent, and when it is, the pair reads "— / 30.0%" with its reason rather than a
  // confident "0.0% / 30.0%" that looks like a measured idle server.
  const current = debugSystemScalar(budget.current_percent, 'cpu_budget_never_sampled', value => `${debugSystemNumber(value, 1)}%`);
  return debugSystemCardHtml('CPU budget', `<div data-js-debug-cpu-budget="${esc(status)}"${budget.stale === true ? ' data-js-debug-cpu-budget-stale="true"' : ''}>${debugSystemRowsHtml([
    ['Status', status, status === 'stale' ? debugSystemHealthReasonText('cpu_budget_stale') : ''],
    ['Current / budget', `${current.text} / ${debugSystemNumber(budget.budget_percent, 1)}%`, current.reason],
    ['Sustained', `${debugSystemNumber(budget.sustained_seconds, 0)}s / ${debugSystemNumber(budget.sustained_budget_seconds, 0)}s`],
    ['Top compute owners', consumerText],
  ])}</div>`);
}

// The compact summary strip: what the whole roster adds up to, in one line, above it.
// Its facts container is deliberately NOT a live region: it carries "Updated N seconds ago",
// which changes on every 5-second poll, so announcing it meant announcing the clock forever.
// The meaningful half -- the state counts -- is spoken by debugSystemAnnounceHtml, which is its
// own render region precisely so a moving timestamp cannot rewrite it.
function debugSystemSummaryStripHtml(payload = {}) {
  const counts = debugSystemRosterSummary(debugSystemRosterRows(payload));
  const owner = payload.owner && typeof payload.owner === 'object' ? payload.owner : {};
  const generatedAgo = debugSystemGeneratedAge(payload.generated_at);
  // NO CPU BUDGET DENOMINATOR HERE. The strip used to print `CPU 172.5% / 30%`: a POPULATION sum
  // over every roster row, divided by `SERVER_CPU_BUDGET_PERCENT`, which is the budget for the WEB
  // PROCESS ALONE (`yolomux_lib/app.py:SERVER_CPU_BUDGET_PERCENT`, published through
  // `server_cpu_budget_payload` beside the web process's OWN `current_percent`). Eight processes
  // measured against one process's budget reads as a 575%-over-budget alarm that nothing is
  // actually breaching. There is no published population budget to divide by, and inventing one
  // here (30% x rows) would be this renderer re-deciding a backend policy constant.
  //   So the number is LABELLED as what it is -- a sum across the roster -- and the budget stays
  //   with the one figure it applies to, in the CPU budget card (`debugSystemCpuBudgetCardHtml`),
  //   which renders the web process's own current reading against it. That also removes the strip
  //   as a second renderer of the budget percentage.
  // A sum over SOME of the population is not the population's total. Excluding an unmeasured row
  // stops the strip fabricating a zero, but a bare `48.0MB` still reads as "this is the memory" --
  // the same silent undercount with a better reason. The panel already has ONE spelling for
  // "this number covers less than it looks like": `data-metric-coverage="partial"` plus the
  // footnote marker `debugSystemCoverageFlagHtml` renders for the roster's metric cells. That one
  // function is called here rather than inventing a second convention such as an inline `1/2`,
  // which would also mean composing English outside the locale catalog.
  //   The denominator comes from the SAME `counts` the numbers do -- one array, one pass,
  //   no second accumulator. It is `resourcePopulation`, not `population`: a nested row runs
  //   inside its parent's process, so its CPU and memory are already inside the parent's figure
  //   and it is not a row this total failed to cover.
  //   The coverage object carries its OWN measured/population numbers, so the renderer below
  //   never re-derives which count a fact belongs to from its key.
  const coverageFor = measuredRows => {
    const population = counts.resourcePopulation;
    if (measuredRows <= 0 || measuredRows >= population) return {state: '', reason: '', measured: measuredRows, population};
    return {
      state: 'partial',
      measured: measuredRows,
      population,
      reason: `this total covers the ${measuredRows} of ${population} rows that own a process and published a measurement; the rest are unmeasured, not zero`,
    };
  };
  const cpuCoverage = coverageFor(counts.cpuMeasured);
  const memoryCoverage = coverageFor(counts.rssMeasured);
  const facts = [
    ['counts', t('debug.system.roster.summary.counts', counts)],
    // An em dash when NOTHING in the population was measured -- the same one unmeasured spelling
    // the roster cells and the disclosures use, never a summed `0`.
    ['cpu', t('debug.system.roster.summary.cpu', {
      current: counts.cpuMeasured > 0 ? `${debugSystemNumber(counts.cpuPercent, 1)}%` : '—',
    }), cpuCoverage],
    ['memory', t('debug.system.roster.summary.memory', {value: counts.rssMeasured > 0 ? debugGraphTerseBytesText(counts.rssBytes) : '—'}),
      memoryCoverage],
    ['owner', t('debug.system.roster.summary.owner', {
      value: owner.owner ? t('backgroundOwner.thisServer') : (Number(owner.current_owner?.port) > 0 ? `:${owner.current_owner.port}` : t('common.notAvailable')),
    })],
    ['updated', `${t('debug.system.roster.summary.updated', {time: generatedAgo})}${jsDebugSystemState.error ? ` · ${jsDebugSystemState.error}` : ''}`],
  ];
  // The Refresh control is `aria-disabled` while a refresh is in flight, never `disabled`. A
  // `disabled` attribute makes the browser BLUR the element the moment it lands, so activating
  // Refresh by mouse or by Enter threw the reader out to `document.body`: by the time
  // `refreshDebugSystemViews` looked for a focus key, the view no longer contained
  // `document.activeElement`, so it captured nothing and the next render had nothing to restore.
  // The same blur hit the plain 5-second poll for anyone whose focus was resting on this button.
  // `aria-disabled` keeps the control focusable and reachable to a screen reader mid-refresh, and
  // re-entry stays impossible because `pollDebugSystemStatus` returns early while `inFlight` --
  // that early return is the ONE owner of the "one refresh at a time" rule, not a second guard here.
  return `<div class="js-debug-system-toolbar js-debug-roster-summary" data-js-debug-roster-summary>
    <h3 class="js-debug-roster-summary-title">${esc(t('debug.tab.services'))}</h3>
    <div class="js-debug-roster-summary-facts">${facts.map(([key, text, coverage = {state: '', reason: ''}]) => {
      const explain = coverage.reason || '';
      const flag = coverage.state === 'partial' ? debugSystemCoverageFlagHtml() : '';
      return `<span data-js-debug-roster-summary-fact="${esc(key)}"`
        + `${coverage.state ? ` data-metric-coverage="${esc(coverage.state)}" data-metric-measured-rows="${esc(String(coverage.measured))}" data-metric-population-rows="${esc(String(coverage.population))}"` : ''}`
        + `${explain ? ` title="${esc(explain)}" data-value-reason="${esc(explain)}"` : ''}`
        + `>${esc(text)}${flag}</span>`;
    }).join('')}</div>
    <button type="button" class="preferences-inline-action" data-js-debug-system-refresh data-js-debug-system-focus-key="roster-refresh"${jsDebugSystemState.inFlight ? ' aria-disabled="true"' : ''}>${esc(t('common.refresh'))}</button>
  </div>`;
}

// ONE compact alert slot. Every critical recovery/persistence/observer failure lands here, once,
// above the roster -- not repeated in every row and not restored as a second large alert card.
function debugSystemAlertsHtml(payload = {}) {
  const localServices = debugSystemRenderableLocalServices(payload);
  const health = localServices.health && typeof localServices.health === 'object' ? localServices.health : {};
  // Every backend-health explanation, from the ONE owner. This slot does not compose a sentence of
  // its own -- when it did, the same fact reached the reader twice in two wordings. `stopped` means
  // the observer is no longer probing; a quiet system -- one where nothing has changed for hours --
  // is `current`, and the owner returns it as `quiet`, so no alert is raised at all.
  const alerts = [...debugSystemHealthExplanations(health).alerts];
  if (debugSystemTmuxSignalWatcherIsIssue(payload.tmux_signal_watcher)) {
    const watcher = payload.tmux_signal_watcher || {};
    const watcherState = DEBUG_SYSTEM_TMUX_WATCHER_STATES.has(String(watcher.state || '')) ? String(watcher.state) : 'exited';
    alerts.push(['tmux-signal-watcher', `${t('debug.system.roster.tmuxSignalWatcher')}: ${watcher.reason || DEBUG_SYSTEM_TMUX_WATCHER_DEFAULT_REASONS[watcherState]}`]);
  }
  for (const event of Array.isArray(localServices.recovery_events) ? localServices.recovery_events : []) {
    alerts.push(['recovery', `${event?.subsystem || 'Subsystem'} recovered from ${event?.event || 'a storage failure'}: quarantined ${event?.quarantined_artifact || t('common.notAvailable')} at ${event?.quarantined_path || t('common.notAvailable')}; fresh storage is active at ${event?.destination_path || t('common.notAvailable')}.${event?.reason ? ` ${event.reason}` : ''}`]);
  }
  if (!alerts.length) return '';
  return `<div class="js-debug-system-alert" data-js-debug-system-alert role="alert">${alerts.map(([kind, text]) => `<p data-system-alert="${esc(kind)}"${kind === 'recovery' ? ' data-system-recovery-banner' : ''}>${esc(text)}</p>`).join('')}</div>`;
}

// Everything a reader consults deliberately rather than scans. Collapsed by default and BUILT only
// when open: a closed section costs nothing but its summary line.
//
// `advanced` is the view of the SEPARATE `/api/system-status/advanced` body (see
// `debugSystemAdvancedView`). Since the snapshot split, `refresh`, the top-N folds and
// `owner.debug` no longer exist in the core payload at all, so reading them off `payload` would
// render six permanently empty cards. When the advanced body has not arrived, the cards that need
// it are NOT drawn as empty shells -- the section says which state it is in and draws only the
// cards the core body genuinely owns.
function debugSystemAdvancedHtml(payload = {}, advanced = {}) {
  const open = jsDebugSystemRosterState.advancedOpen === true;
  const summary = `<summary data-js-debug-system-advanced-summary data-js-debug-system-focus-key="advanced-summary">${esc(t('debug.system.roster.advanced'))}</summary>`;
  if (!open) return `<details class="js-debug-system-advanced" data-js-debug-system-advanced>${summary}</details>`;
  const body = advanced.payload && typeof advanced.payload === 'object' ? advanced.payload : null;
  const owner = payload.owner && typeof payload.owner === 'object' ? payload.owner : {};
  const currentOwner = owner.current_owner || {};
  const advancedOwner = body && body.owner && typeof body.owner === 'object' ? body.owner : {};
  const refresh = body && body.refresh && typeof body.refresh === 'object' ? body.refresh : {};
  const localRefreshing = refresh.local_refreshing || {};
  const coalescing = refresh.coalescing || {};
  const totals = debugSystemRenderableLocalServices(payload).totals || {};
  const cards = [
    debugSystemCpuBudgetCardHtml(payload.cpu_budget || {}),
    debugSystemCardHtml('Distributed owner', debugSystemRowsHtml([
      ['Status', owner.status], ['This server owns work', owner.owner ? 'Yes' : 'No'],
      ['Owner port', currentOwner.port], ['Owner PID', currentOwner.pid],
      ['Index mode', owner.search_index?.mode],
      ...(body ? [['Generations', advancedOwner.debug?.generation_count]] : []),
    ])),
    ...(body ? [
      debugSystemCardHtml('Refresh coordination', debugSystemRowsHtml([
        ['Processes', totals.processes],
        ['Refreshing now', Object.entries(localRefreshing).filter(([, value]) => Boolean(value)).map(([key, value]) => `${key} ${value === true ? '' : value}`.trim()).join(' · ') || 'None'],
        ['Pending refreshes', coalescing.recent_pending_count ?? 0], ['Coalesced requests', refresh.counters?.coalesced_refresh_requests ?? 0],
      ])),
      debugSystemCardHtml('Recurring work', debugSystemRecurringWorkHtml(Array.isArray(refresh.recurring_work) ? refresh.recurring_work : []), {wide: true}),
      debugSystemCardHtml('Distributed roles', debugSystemRolesHtml(refresh.roles), {wide: true}),
      debugSystemCardHtml('Top API endpoints', debugSystemPerformanceTableHtml(body.top_endpoints, 'endpoint'), {wide: true}),
      debugSystemCardHtml('Top background work', debugSystemPerformanceTableHtml(body.top_background_work, 'worker'), {wide: true}),
    ] : []),
    debugSystemCardHtml('Backend-health snapshot', debugSystemHealthSnapshotHtml(debugSystemRenderableLocalServices(payload).health || {}), {wide: true}),
  ];
  return `<details class="js-debug-system-advanced" data-js-debug-system-advanced open>${summary}
    ${debugSystemAdvancedStatusHtml(advanced)}
    <div class="js-debug-system-grid">${cards.filter(Boolean).join('')}</div>
  </details>`;
}

// The four regions of the Daemons view, in the order a reader needs them. `debugSystemInnerHtml`
// stays the ONE place the shape is declared; `refreshDebugSystemViews` replaces regions one at a
// time so a poll does not destroy an open disclosure or the focused control inside it.
// The ONE live region that speaks on its own: what the roster adds up to, and nothing else. It is
// its own render region deliberately -- the region cache only rewrites a region whose generated
// HTML changed, so keeping the volatile "Updated N seconds ago" out of this one is what stops a
// screen reader being re-announced at every 5-second poll. Counts come from the same
// `debugSystemRosterSummary` owner the visible strip uses, so there is no second state map.
function debugSystemAnnounceHtml(payload = {}) {
  const counts = debugSystemRosterSummary(debugSystemRosterRows(payload));
  return `<p class="a11y-only" role="status" aria-live="polite" data-js-debug-system-announce>${esc(t('debug.system.roster.summary.counts', counts))}</p>`;
}

function debugSystemRegionHtml(region, payload) {
  if (region === 'announce') return debugSystemAnnounceHtml(payload);
  if (region === 'summary') return debugSystemSummaryStripHtml(payload);
  if (region === 'alerts') return debugSystemAlertsHtml(payload);
  if (region === 'roster') {
    const generatedAt = Number(payload.generated_at);
    return debugSystemRosterHtml(payload, {
      nowSeconds: Number.isFinite(generatedAt) && generatedAt > 0 ? generatedAt : Date.now() / 1000,
      expanded: jsDebugSystemRosterState.expanded,
    });
  }
  return debugSystemAdvancedHtml(payload, debugSystemAdvancedView());
}

const DEBUG_SYSTEM_REGIONS = Object.freeze(['announce', 'summary', 'alerts', 'roster', 'advanced']);

// ---------------------------------------------------------------------------------------------
// THE TYPED SNAPSHOT REFUSAL
//
// `/api/system-status` and `/api/system-status/advanced` are served from retained background
// snapshots. Before the first publish, or past the freshness deadline, the answer is HTTP 200 with
// `ok:false` and a `snapshot` block -- and WITHOUT the body: the aged report is withheld, never
// relabelled as current. Both routes share one shape, so this panel parses it once, and every
// surface renders the state it was told rather than an absence dressed up as a measurement.
// ---------------------------------------------------------------------------------------------
const DEBUG_SYSTEM_SNAPSHOT_STATE_TEXT = Object.freeze({
  unavailable: 'No system-status snapshot has been published yet.',
  stale: 'The newest system-status snapshot is past its freshness deadline, so its aged numbers are withheld.',
});

function debugSystemSnapshotRefusal(payload) {
  if (!payload || typeof payload !== 'object' || payload.ok !== false) return null;
  const snapshot = payload.snapshot && typeof payload.snapshot === 'object' ? payload.snapshot : {};
  // `age_seconds` is published as null for a snapshot that never existed. `Number(null)` is 0, and
  // printing "age 0.0s" for a snapshot nobody has ever built is the same defect this whole panel is
  // about, so the absence is carried as an absence.
  const age = snapshot.age_seconds === null || snapshot.age_seconds === undefined ? NaN : Number(snapshot.age_seconds);
  return {
    state: String(snapshot.state || 'unavailable'),
    reasonCode: String(snapshot.reason_code || ''),
    reason: String(snapshot.reason || ''),
    ageSeconds: Number.isFinite(age) && age >= 0 ? age : null,
  };
}

// The whole Daemons view when the core body is a refusal. It replaces the roster rather than
// drawing one: with no payload behind it, every row would be a fabricated `unavailable`, which is
// exactly the "unmeasured shown as measured" failure this panel exists to avoid.
function debugSystemSnapshotRefusalHtml(refusal) {
  const headline = DEBUG_SYSTEM_SNAPSHOT_STATE_TEXT[refusal.state] || `The system-status snapshot is ${refusal.state}.`;
  const age = refusal.ageSeconds === null ? '' : ` Newest snapshot age ${debugSystemNumber(refusal.ageSeconds, 1)}s.`;
  const retry = ` Retrying every ${debugSystemNumber(jsDebugSystemRefusalPollMs / 1000, 1)}s.`;
  return `<div class="js-debug-system-loading" role="status" data-js-debug-system-snapshot-state="${esc(refusal.state)}" data-js-debug-system-snapshot-reason-code="${esc(refusal.reasonCode)}">${esc(`${headline}${refusal.reason ? ` ${refusal.reason}` : ''}${age}${retry}`)}</div>`;
}

// The ONE view of the Advanced body handed to the renderer: the payload when it is current, the
// typed refusal when the producer withheld it, and this client's own fetch error when the request
// itself failed.
function debugSystemAdvancedView() {
  const refusal = debugSystemSnapshotRefusal(jsDebugSystemAdvancedState.payload);
  return {
    payload: refusal ? null : (jsDebugSystemAdvancedState.payload || null),
    refusal,
    error: jsDebugSystemAdvancedState.error,
    inFlight: jsDebugSystemAdvancedState.inFlight,
  };
}

function debugSystemAdvancedStatusHtml(advanced = {}) {
  if (advanced.payload) return '';
  if (advanced.error) {
    return `<p class="js-debug-system-empty" role="status" data-js-debug-system-advanced-state="error">${esc(advanced.error)}</p>`;
  }
  if (advanced.refusal) {
    const headline = DEBUG_SYSTEM_SNAPSHOT_STATE_TEXT[advanced.refusal.state] || `The advanced diagnostics snapshot is ${advanced.refusal.state}.`;
    return `<p class="js-debug-system-empty" role="status" data-js-debug-system-advanced-state="${esc(advanced.refusal.state)}" data-js-debug-system-advanced-reason-code="${esc(advanced.refusal.reasonCode)}">${esc(`${headline}${advanced.refusal.reason ? ` ${advanced.refusal.reason}` : ''}`)}</p>`;
  }
  return `<p class="js-debug-system-empty" role="status" data-js-debug-system-advanced-state="loading">${esc(t('common.loading'))}</p>`;
}

function debugSystemInnerHtml() {
  const refusal = debugSystemSnapshotRefusal(jsDebugSystemState.payload);
  if (refusal) return debugSystemSnapshotRefusalHtml(refusal);
  const payload = jsDebugSystemState.payload;
  if (!payload) {
    const message = jsDebugSystemState.error || t('common.loading');
    return `<div class="js-debug-system-loading" role="status">${esc(message)}</div>`;
  }
  return DEBUG_SYSTEM_REGIONS
    .map(region => `<div class="js-debug-system-region" data-js-debug-system-region="${region}">${debugSystemRegionHtml(region, payload)}</div>`)
    .join('');
}

// The last HTML written into each region wrapper. A region whose HTML did not change is not
// touched at all, which is what lets an expanded row keep its DOM -- and its focus -- while the
// summary strip's "Updated 2s ago" repaints every five seconds.
const debugSystemRenderedRegions = new WeakMap();

// The ONE seeder for that cache, called by every site that builds the five regions from
// `debugSystemInnerHtml`. A site that writes the regions and does not record what it wrote leaves
// each region's WeakMap value `undefined`, so the very next poll compares `undefined` against the
// freshly generated HTML, finds them different, and assigns `innerHTML` to all five -- a wholesale
// DOM replacement on an UNCHANGED payload, which is precisely what this cache exists to prevent,
// and it discards the focused control and any open disclosure row with it.
//
// `refreshDebugSystemViews` seeded its own build inline; `createDebugPanel` and `renderDebugPanels`
// build the same five regions through `debugPanelHtml` and did not, so the first poll after panel
// creation and after every full rerender rebuilt everything. This is one owner called from all
// three, not a second cache: a second cache would be the divergent copy this panel keeps paying for.
function seedDebugSystemRenderedRegions(root, payload = jsDebugSystemState.payload) {
  if (!root || !payload) return;
  for (const region of root.querySelectorAll('[data-js-debug-system-region]')) {
    debugSystemRenderedRegions.set(region, debugSystemRegionHtml(String(region.dataset.jsDebugSystemRegion || ''), payload));
  }
}

function debugSystemFocusKey() {
  const active = document.activeElement;
  return String(active?.dataset?.jsDebugSystemFocusKey || '');
}

function debugSystemRestoreFocus(view, focusKey) {
  if (!focusKey) return;
  if (debugSystemFocusKey() === focusKey) return;
  // `preventScroll` matters: the caller restores the panel's own scroll position immediately after,
  // and a focus() that scrolls first would fight it.
  view.querySelector(`[data-js-debug-system-focus-key="${cssEscape(focusKey)}"]`)?.focus?.({preventScroll: true});
}

function refreshDebugSystemViews() {
  for (const view of document.querySelectorAll('[data-js-debug-system]')) {
    const scrollTop = view.scrollTop;
    const scrollLeft = view.scrollLeft;
    const focusKey = view.contains(document.activeElement) ? debugSystemFocusKey() : '';
    // A typed refusal is NOT a payload: it has no regions to update, so it takes the shell branch
    // below and `debugSystemInnerHtml` renders the state it was told.
    const payload = debugSystemSnapshotRefusal(jsDebugSystemState.payload) ? null : jsDebugSystemState.payload;
    const regions = view.querySelectorAll('[data-js-debug-system-region]');
    // First render, or a view whose shell is not the current one: build the whole shell. Every
    // later render replaces only the regions whose GENERATED html changed -- the cache holds what
    // was generated, never `region.innerHTML`, because the browser's own serialization of the same
    // markup differs and would make every region look changed on the next poll.
    if (!payload || regions.length !== DEBUG_SYSTEM_REGIONS.length) {
      view.innerHTML = debugSystemInnerHtml();
      seedDebugSystemRenderedRegions(view, payload);
    } else {
      for (const region of regions) {
        const html = debugSystemRegionHtml(String(region.dataset.jsDebugSystemRegion || ''), payload);
        if (debugSystemRenderedRegions.get(region) === html) continue;
        region.innerHTML = html;
        debugSystemRenderedRegions.set(region, html);
      }
    }
    debugSystemRestoreFocus(view, focusKey);
    restoreElementScrollPosition(view, scrollTop, scrollLeft);
    view.setAttribute('aria-busy', jsDebugSystemState.inFlight ? 'true' : 'false');
  }
}

// Disclosure state lives OUTSIDE the DOM so a re-render cannot close a row the reader opened.
function toggleDebugSystemRosterRow(id) {
  const key = String(id || '');
  if (!key) return;
  if (jsDebugSystemRosterState.expanded.has(key)) jsDebugSystemRosterState.expanded.delete(key);
  else jsDebugSystemRosterState.expanded.add(key);
  refreshDebugSystemViews();
}

// The Advanced body, fetched ONLY while the reader has the disclosure open.
//
// LAZINESS IS THE POINT: the whole reason the backend split this half out is that transcript scans
// and top-N folds should not run on the five-second poll of a panel whose Advanced section is
// closed. Reading the route on every poll would move that work back onto the producer and undo the
// split, so the closed-disclosure early return below is load-bearing, not a nicety
// (tests/system_health_panel.test.js pins it).
//
// It is not a second poller: it has no interval of its own. `pollDebugSystemStatus` -- the one
// owner of the `debug-system` timer -- drives it, and `jsDebugSystemAdvancedPollMs` is the minimum
// age at which a re-read can produce different bytes.
async function pollDebugSystemAdvanced({force = false} = {}) {
  if (jsDebugSystemAdvancedState.inFlight || typeof apiFetchJsonQuiet !== 'function') return false;
  if (jsDebugSystemRosterState.advancedOpen !== true) return false;
  const age = Date.now() - jsDebugSystemAdvancedState.updatedAt;
  if (!force && jsDebugSystemAdvancedState.updatedAt > 0 && age < jsDebugSystemAdvancedPollMs) return false;
  jsDebugSystemAdvancedState.inFlight = true;
  jsDebugSystemAdvancedState.error = '';
  try {
    const payload = await apiFetchJsonQuiet('/api/system-status/advanced', {cache: 'no-store'});
    jsDebugSystemAdvancedState.payload = payload;
    // A refusal carries no body, so it does not start a cadence window: the next poll asks again.
    jsDebugSystemAdvancedState.updatedAt = debugSystemSnapshotRefusal(payload) ? 0 : Date.now();
    return true;
  } catch (error) {
    jsDebugSystemAdvancedState.error = userMessageText(error);
    jsDebugSystemAdvancedState.updatedAt = 0;
    return false;
  } finally {
    jsDebugSystemAdvancedState.inFlight = false;
    refreshDebugSystemViews();
  }
}

// How long until the next poll. A typed refusal is answered in half a second, not in five: the
// producer builds on demand, so the body the reader is waiting for normally exists well before the
// next scheduled poll. This is the NORMAL first read of the panel after any quiet period -- the
// slots are demand-gated -- not a rare cold-start edge case, so the panel would otherwise be blank
// for a full poll interval every time somebody opens it.
function debugSystemPollDelayMs() {
  if (debugSystemSnapshotRefusal(jsDebugSystemState.payload)) return jsDebugSystemRefusalPollMs;
  if (jsDebugSystemRosterState.advancedOpen === true && debugSystemSnapshotRefusal(jsDebugSystemAdvancedState.payload)) {
    return jsDebugSystemRefusalPollMs;
  }
  return jsDebugSystemPollMs;
}

async function pollDebugSystemStatus({force = false} = {}) {
  if (jsDebugSystemState.inFlight || typeof apiFetchJsonQuiet !== 'function') return false;
  if (!force && (debugRuntimeState.subTab !== 'system' || !jsDebugStatsPanelVisible())) return false;
  jsDebugSystemState.inFlight = true;
  jsDebugSystemState.error = '';
  refreshDebugSystemViews();
  try {
    jsDebugSystemState.payload = await apiFetchJsonQuiet('/api/system-status', {cache: 'no-store'});
    jsDebugSystemState.updatedAt = Date.now();
    return true;
  } catch (error) {
    jsDebugSystemState.error = userMessageText(error);
    return false;
  } finally {
    jsDebugSystemState.inFlight = false;
    refreshDebugSystemViews();
    // Both reads ride the one poll: the Advanced fetch returns immediately unless its disclosure is
    // open and its retained body is old enough to have been replaced.
    await pollDebugSystemAdvanced();
    retimeDebugSystemPolling();
  }
}

// Re-arm the ONE `debug-system` timer at the delay the current state deserves. `resetRuntimeInterval`
// keeps the existing timer when the delay is unchanged, so calling this after every poll costs
// nothing on the steady path.
function retimeDebugSystemPolling() {
  if (debugRuntimeState.subTab !== 'system' || !jsDebugStatsPanelVisible()) return;
  resetRuntimeInterval('debug-system', () => { void pollDebugSystemStatus(); }, debugSystemPollDelayMs());
}

function syncDebugSystemPolling({pollNow = false} = {}) {
  if (debugRuntimeState.subTab !== 'system' || !jsDebugStatsPanelVisible()) {
    clearRuntimeInterval('debug-system');
    return;
  }
  // ONE arming site for the ONE timer, so the delay rule cannot drift between the two callers.
  retimeDebugSystemPolling();
  if (pollNow || !jsDebugSystemState.payload) void pollDebugSystemStatus({force: true});
}

function refreshDebugLogsViews() {
  for (const view of document.querySelectorAll('[data-js-debug-subview="logs"]')) {
    const list = view.querySelector('[data-js-debug-log-list]');
    const scrollTop = list?.scrollTop || 0;
    const rendered = document.createElement('div');
    rendered.innerHTML = debugLogsInnerHtml();
    const replacement = rendered.querySelector('[data-js-debug-log-list]');
    const currentError = view.querySelector('.js-debug-logs-error');
    const replacementError = rendered.querySelector('.js-debug-logs-error');
    if (replacementError) {
      if (currentError) currentError.replaceWith(replacementError);
      else list?.before(replacementError);
    } else {
      currentError?.remove();
    }
    if (replacement && list) list.replaceWith(replacement);
    else if (replacement) view.appendChild(replacement);
    if (replacement) replacement.scrollTop = scrollTop;
    for (const button of view.querySelectorAll('[data-js-debug-log-level]')) {
      const active = jsDebugLogsState.levels.has(String(button.dataset.jsDebugLogLevel || ''));
      button.classList.toggle('active', active);
      button.setAttribute('aria-pressed', active ? 'true' : 'false');
    }
  }
}

async function pollDebugLogs({force = false} = {}) {
  if (jsDebugLogsState.inFlight || typeof apiFetchJsonQuiet !== 'function') return false;
  if (!force && (debugRuntimeState.subTab !== 'logs' || !jsDebugStatsPanelVisible())) return false;
  jsDebugLogsState.inFlight = true;
  jsDebugLogsState.error = '';
  refreshDebugLogsViews();
  try {
    const payload = await apiFetchJsonQuiet('/api/logs', {cache: 'no-store'});
    const envelope = jsDebugValidateServerLogEnvelope(payload);
    if (!envelope.ok) {
      // A malformed, missing-epoch, or envelope-inconsistent poll fails visibly and keeps the
      // last good snapshot rather than adopting an untrustworthy server response. Duplicate ids
      // are not a rejectable error: they are stored raw and de-duplicated at render time.
      jsDebugLogsState.error = envelope.reason;
      return false;
    }
    jsDebugLogsState.payload = redactDiagnosticValue(envelope.logs.slice(-500));
    jsDebugLogsState.serverEpoch = envelope.epoch;
    jsDebugLogsState.serverSequence = envelope.sequence;
    jsDebugLogsState.updatedAt = Date.now();
    return true;
  } catch (error) {
    jsDebugLogsState.error = userMessageText(error);
    return false;
  } finally {
    jsDebugLogsState.inFlight = false;
    refreshDebugLogsViews();
  }
}

function syncDebugLogsPolling({pollNow = false} = {}) {
  if (debugRuntimeState.subTab !== 'logs' || !jsDebugStatsPanelVisible()) {
    clearRuntimeInterval('debug-logs');
    return;
  }
  resetRuntimeInterval('debug-logs', () => { void pollDebugLogs(); }, jsDebugLogsPollMs);
  if (pollNow || !jsDebugLogsState.updatedAt) void pollDebugLogs({force: true});
}

function debugSubviewNoop() {}

function renderDebugEventsSubview(panel, options = {}) {
  const meta = panel.querySelector(`#meta-${cssEscape(debugPaneItemId)}`);
  if (meta) meta.textContent = debugMetaText();
  const counts = debugEventCounts();
  const values = {
    events: jsDebugEvents.length,
    api: counts.apiCalls,
    sse: counts.sseEvents,
    errors: counts.errors,
  };
  for (const [key, value] of Object.entries(values)) {
    const stat = panel.querySelector(`[data-js-debug-stat="${key}"]`);
    if (stat) stat.textContent = String(value);
  }
  const log = panel.querySelector('[data-js-debug-log]');
  if (!log) return;
  const text = jsDebugTextForClipboard();
  if (log.value === text) return;
  const anchor = debugLogScrollAnchor(log);
  log.value = text;
  restoreDebugLogScrollAnchor(log, anchor, {scrollToBottom: options.scrollLogToBottom === true});
}

function debugSubviewDescriptor({id, html, render = debugSubviewNoop, bind = debugSubviewNoop, activate = debugSubviewNoop, deactivate = debugSubviewNoop, relocalize = debugSubviewNoop}) {
  return Object.freeze({id, html, render, bind, activate, deactivate, relocalize});
}

const DEBUG_SUBVIEWS = Object.freeze([
  debugSubviewDescriptor({
    id: 'logs',
    html: () => `<div class="js-debug-subview js-debug-logs-view" ${debugSubViewAttrs('logs')}>${debugLogsInnerHtml()}</div>`,
    render: () => refreshDebugLogsViews(),
    activate: ({pollNow = false} = {}) => syncDebugLogsPolling({pollNow}),
    deactivate: () => clearRuntimeInterval('debug-logs'),
  }),
  debugSubviewDescriptor({
    id: 'system',
    html: () => `<div class="js-debug-subview js-debug-system-view" data-js-debug-system ${debugSubViewAttrs('system')}>${debugSystemInnerHtml()}</div>`,
    activate: ({pollNow = false} = {}) => syncDebugSystemPolling({pollNow}),
    deactivate: () => clearRuntimeInterval('debug-system'),
  }),
  debugSubviewDescriptor({
    id: 'events',
    html: debugEventsSubviewHtml,
    render: renderDebugEventsSubview,
  }),
  debugSubviewDescriptor({
    id: 'graph',
    html: () => `<div class="js-debug-subview js-debug-graph-view" ${debugSubViewAttrs('graph')}>${debugGraphHtml()}</div>`,
    render: (panel, options = {}) => refreshDebugGraphElement(panel.querySelector('[data-js-debug-graph]'), options),
    bind: panel => {
      bindDebugGraphTouchSelection(panel);
      bindDebugCostSummaryTabButtons(panel.querySelector('[data-js-debug-graph]'));
    },
    activate: () => syncDebugGraphLiveTicker(),
    deactivate: () => syncDebugGraphLiveTicker(),
  }),
  debugSubviewDescriptor({
    id: 'cost',
    html: () => `<div class="js-debug-subview js-debug-cost-view" ${debugSubViewAttrs('cost')}><div class="preferences-scroll js-yocost-scroll"></div></div>`,
    render: renderYoCostPanel,
    bind: bindYoCostPanel,
    activate: () => {
      renderYoCostPanels({force: true});
      if (jsDebugPricingRefreshState.inFlight) scheduleDebugCostPricingStatusRefresh();
      syncDebugGraphLiveTicker();
    },
    deactivate: () => {
      disposeDebugPricingRefreshLifecycle('cost-subtab-deactivated');
      syncDebugGraphLiveTicker();
    },
    relocalize: panel => renderYoCostPanel(panel, {force: true}),
  }),
]);

function debugSubview(id) {
  return DEBUG_SUBVIEWS.find(view => view.id === id);
}

function debugPanelSubviewDescriptors() {
  return DEBUG_SUBVIEWS;
}

function syncDebugSubviewActivation({pollNow = false} = {}) {
  for (const view of debugPanelSubviewDescriptors()) {
    if (view.id === debugRuntimeState.subTab) view.activate({pollNow});
    else view.deactivate();
  }
}

function debugPanelHtml() {
  return `
    ${debugSubTabsHtml()}
    ${['graph', 'cost', 'events', 'system', 'logs'].map(id => debugSubview(id).html()).join('\n    ')}`;
}

function relocalizeDebugPanelChrome(panel = document.getElementById(panelDomId(debugPaneItemId))) {
  const result = relocalizeVirtualPanelChrome(panel, t('tab.debug'));
  for (const view of debugPanelSubviewDescriptors()) view.relocalize(panel);
  return result;
}

function yoCostPanelHtml() {
  const nowMs = Date.now();
  const buckets = debugGraphDisplayBuckets(nowMs);
  const tokenGroups = jsDebugGraphChartGroups.filter(group => group.key === 'agentTokens' || group.key === 'modelTokens');
  const charts = buckets.length
    ? debugGraphSvgHtml(buckets, debugGraphSeriesData(buckets), tokenGroups, nowMs, {includeCostSummary: false, patternScope: 'cost'})
    : `<div class="js-debug-graph-empty">${esc(t('debug.empty'))}</div>`;
  const costBuckets = debugGraphAgentTokenDisplayBuckets(nowMs);
  const refreshedAtMs = Math.max(Number(jsDebugStatsPollState.lastSampleAtMs) || 0, Number(jsDebugPricingRefreshState.lastRequestedAtMs) || 0);
  const ageSeconds = refreshedAtMs > 0 ? Math.max(0, Math.floor((nowMs - refreshedAtMs) / 1000)) : null;
  const age = ageSeconds === null ? t('common.notAvailable') : relativeTimeFormat(ageSeconds);
  const refreshLabel = debugGraphCostText('common.refresh', 'Refresh');
  const refresh = readOnlyMode ? '' : `<button type="button" class="js-debug-cost-refresh control-active-hover" data-js-debug-cost-refresh${jsDebugPricingRefreshState.inFlight ? ' disabled aria-busy="true"' : ''}>${esc(jsDebugPricingRefreshState.inFlight ? `${refreshLabel}…` : refreshLabel)}</button>`;
  const ageLabel = debugGraphCostText('debug.cost.lastRefreshed', `Last refreshed ${age}`, {time: age});
  // The YO!cost chart area carries the ONE shared history loading overlay so a
  // range/resolution change dims it and centers "Loading…" exactly like
  // YO!stats. It deliberately is NOT a [data-js-debug-graph] surface (those get
  // rebuilt with YO!stats content by the graph-refresh loops); the readiness
  // sync toggles this overlay through its own targeted pass.
  const chartArea = `<div class="js-yocost-chart-area" data-js-yocost-chart-area data-js-debug-history-state="${esc(jsDebugHistoryReadinessStateName())}">${charts}${debugGraphHistoryOverlayHtml()}</div>`;
  return `<div class="js-yocost-graphs" data-js-yocost-graphs><div class="js-yocost-controls" data-js-yocost-data-age><span data-js-yocost-data-age-label>${esc(ageLabel)}</span>${debugGraphLayoutControlsHtml()}${refresh}${debugGraphRangeResolutionControlsHtml(nowMs)}</div>${chartArea}</div>${debugGraphCostReportHtml(debugGraphCostSummaryForBuckets(costBuckets), debugGraphDomain(nowMs))}`;
}

function openYoCostTranscriptPreview(event) {
  const link = event.target?.closest?.('[data-js-debug-cost-transcript-path]');
  if (!link) return false;
  event.preventDefault();
  const path = debugGraphCostTranscriptPath({transcript: link.dataset.jsDebugCostTranscriptPath});
  if (!path) return true;
  Promise.resolve(openFileInEditor(path, basenameOf(path), {viewMode: 'preview', userInitiated: true}))
    .catch(() => emitNotification('previewOpen', {item: fileEditorItemFor(path), title: t('preview.openFailed', {path}), className: 'attention-alert toast'}));
  return true;
}

function bindYoCostPanel(panel) {
  if (!panel) return null;
  return bindOnce(panel, 'yo-cost-panel', () => {
    const scope = createLifecycleScope();
    const disposeTouchSelection = bindDebugGraphTouchSelection(panel);
    scope.replace('touch-selection', disposeTouchSelection, dispose => dispose?.());
    scope.ownEvent('scroll', panel, 'scroll', event => {
    if (!event.target?.matches?.('.js-debug-cost-table-wrap')) return;
    panel.dataset.jsDebugCostLastScrollMs = String(Date.now());
  }, {capture: true, passive: true});
    scope.ownEvent('pointerdown', panel, 'pointerdown', event => {
    if (handleDebugGraphControlEvent(event, panel)) return;
    handleDebugGraphPointerDown(event, panel);
  });
    scope.ownEvent('pointermove', panel, 'pointermove', event => { handleDebugGraphPointerMove(event, panel); });
    scope.ownEvent('pointerleave', panel, 'pointerleave', () => { debugGraphClearInteractionLinesUnlessPinned(panel); });
    scope.ownEvent('pointerup', panel, 'pointerup', event => {
    if (handleDebugGraphControlEvent(event, panel)) return;
    handleDebugGraphPointerUp(event, panel);
  });
    scope.ownEvent('pointercancel', panel, 'pointercancel', event => {
    handleDebugGraphControlEvent(event, panel);
    handleDebugGraphPointerCancel(event, panel);
  });
    scope.ownEvent('input', panel, 'input', event => { handleDebugGraphControlEvent(event, panel); });
    scope.ownEvent('change', panel, 'change', event => { handleDebugGraphControlEvent(event, panel); });
    scope.ownEvent('click', panel, 'click', event => {
    if (handleDebugGraphControlEvent(event, panel)) return;
    if (typeof openExternalLinkFromEvent === 'function' && openExternalLinkFromEvent(event, panel)) return;
    openYoCostTranscriptPreview(event);
    });
    return () => scope.dispose('yo-cost-panel-unbound');
  });
}

function debugCostAgeRefreshDelayMs(randomValue = Math.random()) {
  return 3000 + Math.floor(Math.max(0, Math.min(1, Number(randomValue) || 0)) * 7000);
}

function jsDebugCostSubviewVisible() {
  return typeof document !== 'undefined'
    && document.visibilityState !== 'hidden'
    && debugRuntimeState.subTab === 'cost'
    && itemIsActivePaneTab(debugPaneItemId);
}

function renderYoCostPanel(panel, {force = false} = {}) {
  if (dragState.item != null) {
    jsDebugRenderForce ||= force;
    jsDebugRenderDragDeferred = true;
    return false;
  }
  const nowMs = Date.now();
  if (!panel || !jsDebugCostSubviewVisible()) return false;
  if (!force && nowMs < jsDebugCostPanelNextRefreshAtMs) return false;
  const recentlyScrolled = nowMs - Number(panel.dataset.jsDebugCostLastScrollMs || 0) < 1000;
  if (debugGraphInteractionBelongsToPanel(panel) || recentlyScrolled) {
    panel.dataset.jsDebugGraphRefreshPending = 'true';
    return false;
  }
  const body = panel.querySelector('[data-js-debug-subview="cost"]');
  reconcilePanelBody({
    body,
    html: `<div class="preferences-scroll js-yocost-scroll">${yoCostPanelHtml()}</div>`,
    anchors: [
      elementScrollAnchor('.js-yocost-scroll'),
      keyedScrollAnchor('.js-debug-cost-table-wrap [data-js-debug-cost-table]'),
    ],
  });
  delete panel.dataset.jsDebugGraphRefreshPending;
  bindYoCostPanel(panel);
  commitJsDebugCurrentStatsPaint();
  const delayMs = debugCostAgeRefreshDelayMs();
  jsDebugCostPanelNextRefreshAtMs = nowMs + delayMs;
  jsDebugCostAgeNextRefreshAtMs = nowMs + delayMs;
  syncDebugGraphLiveTicker();
  return true;
}

function renderYoCostPanels(options = {}) {
  let rendered = false;
  for (const panel of document.querySelectorAll('.js-debug-panel')) {
    rendered = renderYoCostPanel(panel, options) || rendered;
  }
  return rendered;
}

function refreshDebugGraphSurfaces({force = true, deferFocusedControl = true} = {}) {
  for (const graph of document.querySelectorAll('[data-js-debug-graph]')) {
    refreshDebugGraphElement(graph, {force, deferFocusedControl});
  }
  renderYoCostPanels({force});
}

function createDebugPanel() {
  enableDebugMode();
  const panel = createFramedPanel({
    item: debugPaneItemId,
    className: 'panel js-debug-panel',
    frame: {
      headClass: 'preferences-panel-head',
      controlsHtml: virtualPanelInnerControlsHtml(debugPaneItemId),
      afterHeadHtml: `<div class="pane-info-bar panel-detail-row">
        <div class="pane-info-bar-copy panel-copy">
          <div id="panel-tab-${debugPaneItemId}" class="panel-session-label"><span class="session-button-dir">${esc(t('tab.debug'))}</span></div>
          <div id="meta-${debugPaneItemId}" class="pane-info-bar-meta meta">${esc(debugMetaText())}</div>
        </div>
        ${panelDetailCloseButtonHtml(debugPaneItemId)}
      </div>`,
      bodyClass: 'preferences-body js-debug-body',
      bodyHtml: `<div class="preferences-scroll js-debug-scroll">${debugPanelHtml()}</div>`,
    },
    bind: bindDebugPanel,
  });
  // `debugPanelHtml` above just wrote the five Daemons regions. Record what it wrote, or the first
  // poll after this panel appears replaces every one of them for no change at all.
  seedDebugSystemRenderedRegions(panel);
  return panel;
}

function debugLogScrollAnchor(log) {
  if (!log) return null;
  const scrollTop = Number(log.scrollTop) || 0;
  const maxScroll = Math.max(0, Number(log.scrollHeight) - Number(log.clientHeight));
  return {
    scrollTop,
    scrollLeft: Number(log.scrollLeft) || 0,
    nearBottom: maxScroll - scrollTop <= 20,
    selectionStart: Number(log.selectionStart),
    selectionEnd: Number(log.selectionEnd),
  };
}

function restoreDebugLogScrollAnchor(log, anchor, {scrollToBottom = false} = {}) {
  if (!log || !anchor) return;
  if (Number.isFinite(anchor.selectionStart) && Number.isFinite(anchor.selectionEnd)) {
    try { log.setSelectionRange(anchor.selectionStart, anchor.selectionEnd); } catch (_) {}
  }
  // Restoring a textarea selection can scroll the caret into view, so position
  // restoration must be last to preserve a reader who is above the tail.
  log.scrollTop = scrollToBottom || anchor.nearBottom ? log.scrollHeight : anchor.scrollTop;
  log.scrollLeft = anchor.scrollLeft;
}

function renderDebugPanels(options = {}) {
  if (dragState.item != null) return;
  for (const panel of document.querySelectorAll('.js-debug-panel')) {
    const body = panel.querySelector('.js-debug-body');
    if (body && (options.force === true || !body.querySelector('[data-js-debug-log]'))) {
      reconcilePanelBody({
        body,
        html: `${panelToastStackHtml(debugPaneItemId)}<div class="preferences-scroll js-debug-scroll">${debugPanelHtml()}</div>`,
        anchors: [
          elementScrollAnchor('.js-debug-scroll'),
          keyedScrollAnchor('.js-debug-cost-table-wrap [data-js-debug-cost-table]'),
          {
            capture: root => debugLogScrollAnchor(root.querySelector('[data-js-debug-log]')),
            restore: (root, value) => restoreDebugLogScrollAnchor(root.querySelector('[data-js-debug-log]'), value, {scrollToBottom: options.scrollLogToBottom === true}),
          },
        ],
        // Same contract as `createDebugPanel`: record the five Daemons regions just written.
        afterReplace: seedDebugSystemRenderedRegions,
      });
    }
    refreshDebugPanelFromEvents(panel, options);
    bindDebugPanel(panel);
  }
  if (typeof refreshPanePopouts === 'function') refreshPanePopouts(debugPaneItemId);
}

function refreshDebugPanelsFromEvents(options = {}) {
  if (dragState.item != null) {
    jsDebugRenderForce ||= options.force === true;
    jsDebugRenderDragDeferred = true;
    return;
  }
  for (const panel of document.querySelectorAll('.js-debug-panel')) {
    refreshDebugPanelFromEvents(panel, options);
  }
  if (typeof refreshPanePopouts === 'function') refreshPanePopouts(debugPaneItemId);
}

function refreshDebugPanelFromEvents(panel, options = {}) {
  if (!panel) return;
  applyDebugSubTab(panel);
  for (const view of debugPanelSubviewDescriptors()) view.render(panel, options);
}

function debugGraphFocusedControl(graph) {
  const active = typeof document !== 'undefined' ? document.activeElement : null;
  if (!graph || !active || !graph.contains(active)) return null;
  // These controls live outside the replaceable graph body. Keeping either
  // focused must not defer an accepted history paint until focusout.
  if (active.matches?.('[data-js-debug-range-slider], [data-js-debug-resolution-override]')) return null;
  return active.closest?.('.js-debug-graph-controls') || null;
}

function syncDebugGraphControls(graph, nowMs = Date.now()) {
  if (!graph) return;
  const options = debugGraphAvailableRangeOptions(nowMs);
  const domain = debugGraphDomain(nowMs);
  const zoomed = debugGraphZoomDomainValid();
  const slider = graph.querySelector('[data-js-debug-range-slider]');
  if (slider) {
    slider.max = String(Math.max(0, options.length - 1));
    slider.value = String(jsDebugGraphRangeOptionIndex(activeJsDebugGraphRangeSeconds(nowMs), nowMs));
    slider.disabled = zoomed;
    slider.setAttribute('aria-disabled', zoomed ? 'true' : 'false');
  }
  const rangeLabel = graph.querySelector('[data-js-debug-range-label]');
  if (rangeLabel) {
    rangeLabel.textContent = zoomed ? debugGraphCompactRangeText(domain) : jsDebugGraphRangeLabel(debugRuntimeState.graphRangeSeconds, nowMs);
    rangeLabel.classList.toggle('js-debug-range-label--zoomed', zoomed);
    rangeLabel.title = zoomed ? debugGraphCostRangeText(domain) : '';
  }
  const rangeControl = graph.querySelector('[data-js-debug-range-control]');
  let reset = rangeControl?.querySelector('[data-js-debug-zoom-reset]');
  let prefix = rangeControl?.querySelector('.js-debug-range-prefix');
  if (zoomed && rangeControl && !reset) {
    reset = makeButton({
      className: 'js-debug-zoom-reset',
      dataset: {jsDebugZoomReset: ''},
      label: debugGraphZoomResetLabel(),
    });
    rangeControl.prepend(reset);
  } else if (!zoomed) {
    reset?.remove();
  }
  if (zoomed) {
    prefix?.remove();
  } else if (rangeControl && !prefix) {
    prefix = document.createElement('span');
    prefix.className = 'js-debug-range-prefix';
    prefix.setAttribute('aria-hidden', 'true');
    prefix.textContent = debugGraphRangePrefixText();
    rangeControl.insertBefore(prefix, slider || rangeControl.firstChild);
  }
  graph.querySelectorAll('[data-js-debug-chart-layout]').forEach(button => {
    button.setAttribute('aria-pressed', Number(button.dataset.jsDebugChartLayout) === debugRuntimeState.graphChartLayout ? 'true' : 'false');
  });
  graph.querySelectorAll('[data-js-debug-chart-toggle]').forEach(toggle => {
    toggle.checked = debugGraphChartVisible(toggle.dataset.jsDebugChartToggle);
  });
  const resolution = graph.querySelector('[data-js-debug-resolution]');
  const expectedHost = document.createElement('div');
  expectedHost.innerHTML = debugGraphResolutionLabelHtml(nowMs);
  const expectedResolution = expectedHost.firstElementChild;
  if (resolution && expectedResolution) {
    resolution.dataset.jsDebugResolutionSeconds = expectedResolution.dataset.jsDebugResolutionSeconds;
    const select = resolution.querySelector('[data-js-debug-resolution-override]');
    const expectedSelect = expectedResolution.querySelector('[data-js-debug-resolution-override]');
    if (select && expectedSelect && document.activeElement !== select && select.innerHTML !== expectedSelect.innerHTML) select.innerHTML = expectedSelect.innerHTML;
    if (select && expectedSelect && document.activeElement !== select) select.value = expectedSelect.value;
    const firstText = [...resolution.childNodes].find(node => node.nodeType === 3);
    const expectedText = [...expectedResolution.childNodes].find(node => node.nodeType === 3);
    if (firstText && expectedText) firstText.textContent = expectedText.textContent;
  }
}

function preserveDebugGraphBodyControls(graph, nextBody) {
  const selectors = ['[data-js-debug-chart-close]'];
  for (const selector of selectors) {
    const currentByKey = new Map([...graph.querySelectorAll(selector)].map(control => [
      control.dataset.jsDebugChartClose,
      control,
    ]));
    for (const replacement of nextBody.querySelectorAll(selector)) {
      const key = replacement.dataset.jsDebugChartClose;
      const current = currentByKey.get(key);
      if (!current) continue;
      for (const attribute of [...replacement.attributes]) current.setAttribute(attribute.name, attribute.value);
      for (const attribute of [...current.attributes]) {
        if (!replacement.hasAttribute(attribute.name)) current.removeAttribute(attribute.name);
      }
      replacement.replaceWith(current);
    }
  }
}

function debugCostAgeLabels() {
  if (!jsDebugCostSubviewVisible()) return [];
  return [...document.querySelectorAll('[data-js-yocost-data-age-label]')].filter(label => !label.closest('[hidden]') && label.getClientRects().length > 0);
}

function debugCostAgeLabelText(nowMs = Date.now()) {
  const refreshedAtMs = Math.max(Number(jsDebugStatsPollState.lastSampleAtMs) || 0, Number(jsDebugPricingRefreshState.lastRequestedAtMs) || 0);
  const ageSeconds = refreshedAtMs > 0 ? Math.max(0, Math.floor((nowMs - refreshedAtMs) / 1000)) : null;
  const age = ageSeconds === null ? t('common.notAvailable') : relativeTimeFormat(ageSeconds);
  return debugGraphCostText('debug.cost.lastRefreshed', `Last refreshed ${age}`, {time: age});
}

function refreshDebugCostAgeLabels(nowMs = Date.now()) {
  if (nowMs < jsDebugCostAgeNextRefreshAtMs) return false;
  const labels = debugCostAgeLabels();
  jsDebugCostAgeNextRefreshAtMs = nowMs + debugCostAgeRefreshDelayMs();
  if (!labels.length) return false;
  const text = debugCostAgeLabelText(nowMs);
  labels.forEach(label => { label.textContent = text; });
  return true;
}

function debugGraphSlideIntervalMs(resolutionMs) {
  return Number(resolutionMs) <= 1000 ? 1000 : 5000;
}

function debugGraphSlidingAxisActive() {
  // Live ranges up to 1h advance continuously with the wall clock so the axis
  // slides and content drifts left even between (up to 60s) data ticks. Coarser
  // (>1h) ranges and fixed historical zooms are static by design; a hidden document or
  // hidden panel is not static but simply does not repaint until it is shown again.
  return !debugGraphZoomDomainValid() && debugRuntimeState.graphRangeSeconds <= jsDebugGraphSlideMaxRangeSeconds;
}

function debugGraphLiveTickerNextDueMs(nowMs = Date.now()) {
  const slidingActive = jsDebugStatsPanelVisible() && debugGraphSlidingAxisActive();
  const intervalMs = slidingActive ? debugGraphSlideIntervalMs(debugGraphDisplayResolutionMs(debugGraphDomain(nowMs), 0, nowMs)) : Infinity;
  const nextSlideMs = slidingActive ? Math.ceil((nowMs + 1) / intervalMs) * intervalMs : Infinity;
  const nextAgeMs = jsDebugCostSubviewVisible() ? jsDebugCostAgeNextRefreshAtMs || nowMs : Infinity;
  return Math.min(nextSlideMs, nextAgeMs);
}

function debugGraphLiveTickerNeeded() {
  return (jsDebugStatsPanelVisible() && debugGraphSlidingAxisActive()) || jsDebugCostSubviewVisible();
}

function debugGraphSlideLiveViews(nowMs = Date.now()) {
  // Re-render each visible live graph at most once per slide interval so the
  // plot and x-axis move in the same repaint. One-second data slides every second;
  // coarser data slides every five seconds without inventing extra data ticks.
  const resolutionMs = debugGraphDisplayResolutionMs(debugGraphDomain(nowMs), 0, nowMs);
  const slideIntervalMs = debugGraphSlideIntervalMs(resolutionMs);
  for (const graph of document.querySelectorAll('[data-js-debug-graph]')) {
    if (graph.offsetParent === null) continue;
    const renderedAt = Number(graph.dataset.jsDebugGraphRenderedAt);
    if (Number.isFinite(renderedAt) && nowMs - renderedAt < slideIntervalMs) continue;
    refreshDebugGraphElement(graph, {force: true});
  }
}

function stopDebugGraphLiveTicker() {
  if (jsDebugGraphLiveTimer) debugGraphLifecycleScope().release('live-ticker', jsDebugGraphLiveTimer);
  jsDebugGraphLiveTimer = 0;
}

function debugGraphLiveTimerTick(scope = debugGraphLifecycleScope(), timer = jsDebugGraphLiveTimer) {
  if (!scope.current() || jsDebugGraphLiveTimer !== timer) return;
  scope.relinquish('live-ticker', timer);
  jsDebugGraphLiveTimer = 0;
  if (typeof document === 'undefined' || document.visibilityState === 'hidden') return;
  const nowMs = Date.now();
  refreshDebugCostAgeLabels(nowMs);
  if (debugGraphSlidingAxisActive()) debugGraphSlideLiveViews(nowMs);
  syncDebugGraphLiveTicker();
}

function syncDebugGraphLiveTicker() {
  if (typeof document === 'undefined' || document.visibilityState === 'hidden' || !debugGraphLiveTickerNeeded()) {
    stopDebugGraphLiveTicker();
    return;
  }
  if (jsDebugGraphLiveTimer) return;
  const scope = debugGraphLifecycleScope();
  const timer = setTimeout(() => debugGraphLiveTimerTick(scope, timer), Math.max(0, debugGraphLiveTickerNextDueMs() - Date.now()));
  jsDebugGraphLiveTimer = timer;
  scope.ownTimer('live-ticker', timer);
}

function flushDeferredDebugGraphRefresh(graph) {
  if (!graph || graph.dataset.jsDebugGraphRefreshPending !== 'true' || debugGraphFocusedControl(graph)) return false;
  delete graph.dataset.jsDebugGraphRefreshPending;
  return refreshDebugGraphElement(graph, {force: true});
}

function refreshDebugGraphElement(graph, {force = false, deferFocusedControl = true} = {}) {
  if (!graph) return false;
  if (jsDebugGraphRangeSliderDragging) {
    graph.dataset.jsDebugGraphRefreshPending = 'true';
    return false;
  }
  if (debugGraphInteractionBelongsToPanel(graph.closest('.js-debug-panel'))) {
    graph.dataset.jsDebugGraphRefreshPending = 'true';
    return false;
  }
  if (deferFocusedControl && debugGraphFocusedControl(graph)) {
    graph.dataset.jsDebugGraphRefreshPending = 'true';
    return false;
  }
  const nowMs = Date.now();
  const lastRenderedAt = Number(graph.dataset.jsDebugGraphRenderedAt);
  if (!force && Number.isFinite(lastRenderedAt) && nowMs - lastRenderedAt < jsDebugGraphRefreshMs) return false;
  const perf = clientPerfStart('statsHistoryRender');
  const scrollOwner = graph.closest('.js-debug-graph-view');
  const scrollTop = scrollOwner?.scrollTop || 0;
  const scrollLeft = scrollOwner?.scrollLeft || 0;
  try {
    graph.className = debugGraphClassName(nowMs);
    let body = graph.querySelector('[data-js-debug-graph-body]');
    if (!body) {
      graph.innerHTML = debugGraphInnerHtml(nowMs);
      body = graph.querySelector('[data-js-debug-graph-body]');
    } else {
      const nextBody = document.createElement('div');
      nextBody.innerHTML = debugGraphBodyHtml(nowMs);
      preserveDebugGraphBodyControls(graph, nextBody);
      body.replaceChildren(...nextBody.childNodes);
    }
    syncDebugGraphControls(graph, nowMs);
    restoreElementScrollPosition(scrollOwner, scrollTop, scrollLeft);
    bindDebugCostSummaryTabButtons(graph);
    graph.dataset.jsDebugGraphRenderedAt = String(nowMs);
    graph.dataset.jsDebugHistoryState = jsDebugHistoryReadinessStateName();
    graph.setAttribute('aria-busy', jsDebugHistoryReadinessBusy() ? 'true' : 'false');
    commitJsDebugCurrentStatsPaint();
    graph.dataset.jsDebugStatsGenerationKey = String(jsDebugCurrentStatsClientState.paintedGenerationKey || '');
    delete graph.dataset.jsDebugGraphRefreshPending;
    if (typeof scheduleAgentWindowActivityAnimationSync === 'function') scheduleAgentWindowActivityAnimationSync(graph);
    resolveDebugGraphResolutionChange(jsDebugHistoryReadiness, {painted: true});
    syncDebugGraphLiveTicker();
  } finally {
    clientPerfEnd(perf);
  }
  return true;
}

function bindDebugCostSummaryTabButtons(graph) {
  if (!graph) return;
  graph.querySelectorAll('[data-js-debug-cost-details]').forEach(anchor => {
    bindOnce(anchor, 'debug-cost-details', () => {
      const handleClick = event => {
      event.preventDefault();
      void Promise.resolve(selectSession(debugPaneItemId, {userInitiated: true}))
        .then(() => setDebugSubTab('cost'));
      };
      anchor.addEventListener('click', handleClick);
      return () => anchor.removeEventListener('click', handleClick);
    });
  });
}

function applyDebugSubTab(panel) {
  if (!panel) return;
  panel.querySelectorAll('[data-js-debug-subtab]').forEach(button => {
    const active = normalizedJsDebugSubTab(button.dataset.jsDebugSubtab) === debugRuntimeState.subTab;
    button.classList.toggle(CLS.active, active);
    button.setAttribute('aria-selected', active ? 'true' : 'false');
  });
  panel.querySelectorAll('[data-js-debug-subview]').forEach(view => {
    const active = normalizedJsDebugSubTab(view.dataset.jsDebugSubview) === debugRuntimeState.subTab;
    view.hidden = !active;
  });
}

function setDebugSubTab(tab) {
  loadJsDebugStatsUiPreferences();
  debugRuntimeState.subTab = normalizedJsDebugSubTab(tab);
  saveJsDebugStatsUiPreferences();
  for (const panel of document.querySelectorAll('.js-debug-panel')) applyDebugSubTab(panel);
  syncDebugSubviewActivation({pollNow: true});
}

function requestJsDebugHistoryForCurrentDomain({retry = false, forceGraphRefresh = true} = {}) {
  if (!jsDebugStatsPanelVisible()) return false;
  if (jsDebugGraphExactResolutionEnabled) {
    const client = ensureJsDebugCurrentStatsClient();
    const selection = jsDebugCurrentStatsSelection();
    const currentSelection = client?.controller?.()?.selection?.();
    const cachedSelection = client?.controller?.()?.generation?.()
      && Number(currentSelection?.range_seconds) === Number(selection.rangeSeconds)
      && String(currentSelection?.resolution) === String(selection.resolution);
    if (!retry && cachedSelection) {
      syncJsDebugCurrentStatsClient();
      return false;
    }
    const domain = debugGraphDomain();
    beginJsDebugHistoryReadiness(Math.max(0, Math.floor(domain.startMs / 1000)), {
      targetStartSeconds: Math.max(0, Math.floor(domain.startMs / 1000)),
      targetEndSeconds: Math.max(1, Math.ceil(domain.endMs / 1000)),
      requestedEndSeconds: Math.max(1, Math.ceil(domain.endMs / 1000)),
      requestedResolutionSeconds: jsDebugRequestedHistoryResolutionSeconds(),
      retry,
    });
    return syncJsDebugCurrentStatsClient({select: true});
  }
  const domain = debugGraphDomain();
  const requestedStartSeconds = Math.max(0, Math.floor(domain.startMs / 1000));
  const requestedDomainEndSeconds = Math.max(requestedStartSeconds + 1, Math.ceil(domain.endMs / 1000));
  const requestedResolutionSeconds = jsDebugRequestedHistoryResolutionSeconds();
  const coverageResolutionSeconds = jsDebugHistoryCoverageResolutionSeconds(requestedStartSeconds, requestedResolutionSeconds);
  if (!retry && !jsDebugHistoryCoverageNeedsRefresh(requestedStartSeconds, requestedDomainEndSeconds, coverageResolutionSeconds)) return false;
  const state = jsDebugHistoryReadiness;
  if (!retry && jsDebugHistoryReadinessErrorLike(state) && !jsDebugHistoryAutoRetryDue(state)) return false;
  const currentRequestMatches = jsDebugHistoryReadinessBusy(state)
    && Number(state.requestedRangeSeconds) === Number(debugRuntimeState.graphRangeSeconds)
    && Number(state.targetStartSeconds) === Number(requestedStartSeconds)
    && Number(state.targetEndSeconds) === Number(requestedDomainEndSeconds)
    && Number(state.requestedResolutionSeconds) === Number(coverageResolutionSeconds);
  if (!currentRequestMatches || retry) {
    const requestWindow = jsDebugHistoryRequestWindow(requestedStartSeconds, requestedDomainEndSeconds, coverageResolutionSeconds);
    beginJsDebugHistoryReadiness(requestWindow.startSeconds, {
      targetStartSeconds: requestedStartSeconds,
      targetEndSeconds: requestedDomainEndSeconds,
      requestedEndSeconds: requestWindow.endSeconds,
      requestedResolutionSeconds: coverageResolutionSeconds,
      retry,
    });
  }
  armJsDebugStatsPolling({pollNow: true, forceGraphRefresh});
  return true;
}

function setDebugGraphRange(value, {render = true} = {}) {
  loadJsDebugStatsUiPreferences();
  jsDebugGraphZoomDomain = null;
  debugRuntimeState.graphRangeSeconds = normalizedJsDebugGraphRange(value);
  activeJsDebugGraphRangeSeconds();
  saveJsDebugStatsUiPreferences();
  if (!render) return;
  syncJsDebugStatsDeliveryMode();
  const requestedStartSeconds = Math.max(0, Math.floor(debugGraphDomain().startMs / 1000));
  // An explicit range action is also an explicit retry. Do not let an old
  // automatic-retry backoff make a newly requested domain appear ready while
  // no request is queued.
  const requestedHistory = requestJsDebugHistoryForCurrentDomain({retry: jsDebugHistoryReadiness.phase === 'error'});
  if (!requestedHistory && (jsDebugHistoryReadinessBusy() || jsDebugHistoryReadiness.phase === 'error')) {
    setJsDebugHistoryReadiness('ready', {
      requestedRangeSeconds: debugRuntimeState.graphRangeSeconds,
      requestedStartSeconds,
      attemptCount: 0,
      error: '',
      generation: Number(jsDebugHistoryReadiness.generation || 0) + 1,
    });
  }
  refreshDebugGraphSurfaces();
}

function setDebugGraphResolutionOverride(value) {
  loadJsDebugStatsUiPreferences();
  const previousSeconds = Number(debugRuntimeState.graphResolutionOverrideSeconds) || 0;
  const seconds = Math.max(0, Number(value) || 0);
  const normalized = normalizedDebugGraphResolutionOverrideSeconds(seconds, debugGraphDomain(), Date.now());
  debugRuntimeState.graphResolutionOverrideSeconds = normalized;
  saveJsDebugStatsUiPreferences();
  // Immediate ≤1-frame acknowledgement: the control + Resolution label reflect the target
  // value now, before any fetch resolves.
  refreshDebugGraphSurfaces();
  if (normalized === previousSeconds) {
    clearDebugGraphPendingResolutionChange({hideOverlay: jsDebugHistoryReadiness.phase === 'ready'});
    return;
  }
  // Cached/instant path: when the domain's buckets are already client-side (the common
  // case, since a resolution change is an in-memory re-aggregation) no fetch is needed and
  // the swap is instant with no overlay. Only when the change genuinely needs finer/coarser
  // history do we show the shared dimmed loading overlay over the still-visible old data and
  // arm a generation-guarded revert-on-failure.
  //
  const fetching = requestJsDebugHistoryForCurrentDomain();
  if (!fetching) {
    clearDebugGraphPendingResolutionChange({hideOverlay: jsDebugHistoryReadiness.phase === 'ready'});
    return;
  }
  clearDebugGraphPendingResolutionChange();
  const pending = {
    previousSeconds,
    targetSeconds: normalized,
    rangeSeconds: Number(debugRuntimeState.graphRangeSeconds),
    requestedResolutionSeconds: Number(jsDebugHistoryReadiness.requestedResolutionSeconds),
    targetStartSeconds: Number(jsDebugHistoryReadiness.targetStartSeconds),
    targetEndSeconds: Number(jsDebugHistoryReadiness.targetEndSeconds),
    armedGeneration: Number(jsDebugHistoryReadiness.generation || 0),
    armedAtMs: performanceNow(),
    watchdogTimer: null,
  };
  jsDebugGraphPendingResolutionChange = pending;
  pending.watchdogTimer = setTimeout(() => {
    if (jsDebugGraphPendingResolutionChange !== pending) return;
    resolveDebugGraphResolutionChange(jsDebugHistoryReadiness, {painted: true, watchdog: true});
  }, jsDebugGraphResolutionWatchdogMs);
  // An explicit user action must acknowledge within a frame, so surface the shared overlay
  // immediately rather than after the older-load debounce that avoids flashing on passive
  // tail repairs.
  jsDebugHistoryReadiness.overlayVisible = true;
  clearJsDebugHistoryOverlayTimer();
  syncJsDebugHistoryReadinessSurfaces();
}

function clearDebugGraphPendingResolutionChange({hideOverlay = false} = {}) {
  const pending = jsDebugGraphPendingResolutionChange;
  if (pending && pending.watchdogTimer !== null && typeof clearTimeout === 'function') clearTimeout(pending.watchdogTimer);
  jsDebugGraphPendingResolutionChange = null;
  if (!hideOverlay) return;
  jsDebugHistoryReadiness.overlayVisible = false;
  syncJsDebugHistoryReadinessSurfaces();
}

function debugGraphResolutionChangeDataSatisfied(pending, state) {
  if (!pending || state?.phase !== 'ready') return false;
  if (Number(state.generation) < Number(pending.armedGeneration)) return false;
  if (Number(debugRuntimeState.graphResolutionOverrideSeconds) !== Number(pending.targetSeconds)) return false;
  if (Number(debugRuntimeState.graphRangeSeconds) !== Number(pending.rangeSeconds)) return false;
  if (Number(state.resolutionSeconds) !== Number(pending.requestedResolutionSeconds)) return false;
  const intervals = [...(state.requestCoverageIntervals || [])]
    .filter(interval => Number(interval.resolutionSeconds) === Number(pending.requestedResolutionSeconds))
    .sort((left, right) => Number(left.startSeconds) - Number(right.startSeconds));
  if (jsDebugGraphExactResolutionEnabled) {
    // Exact snapshots are bucket-aligned by the server, while the browser's
    // pre-request domain is based on an arbitrary current millisecond. The
    // accepted range/resolution key plus one contiguous full-range slice is
    // therefore the authority; requiring identical endpoints leaves the
    // resolution-change latch stuck after the correct snapshot has painted.
    let covered = 0;
    let cursor = null;
    for (const interval of intervals) {
      const intervalStart = Number(interval.startSeconds);
      const intervalEnd = Number(interval.endSeconds);
      if (!Number.isFinite(intervalStart) || !Number.isFinite(intervalEnd) || intervalEnd <= intervalStart) continue;
      if (cursor !== null && intervalStart > cursor) return false;
      const start = cursor === null ? intervalStart : Math.max(intervalStart, cursor);
      covered += Math.max(0, intervalEnd - start);
      cursor = Math.max(cursor ?? intervalEnd, intervalEnd);
    }
    return covered >= Number(pending.rangeSeconds);
  }
  const start = Number(pending.targetStartSeconds);
  const end = Number(pending.targetEndSeconds);
  if (!Number.isFinite(start) || !Number.isFinite(end) || end <= start) return false;
  let cursor = start;
  for (const interval of intervals) {
    const intervalStart = Number(interval.startSeconds);
    const intervalEnd = Number(interval.endSeconds);
    if (!Number.isFinite(intervalStart) || !Number.isFinite(intervalEnd) || intervalEnd <= cursor) continue;
    if (intervalStart > cursor) return false;
    cursor = Math.max(cursor, intervalEnd);
    if (cursor >= end) return true;
  }
  return false;
}

// Resolve a pending Resolution-change fetch. Generation-guarded so a stale history response
// can neither clear nor revert a newer request. On success the overlay clears through the
// normal ready path; on failure the control reverts to its previous value with a danger
// toast (never a silent snap-back) and the chart returns to that cached resolution.
function resolveDebugGraphResolutionChange(state, {painted = false, watchdog = false} = {}) {
  const pending = jsDebugGraphPendingResolutionChange;
  if (!pending || Number(state.generation) < Number(pending.armedGeneration)) return;
  if (state.phase === 'ready') {
    if (!painted || !debugGraphResolutionChangeDataSatisfied(pending, state)) return;
    if (watchdog) recordJsDebugStatsDiagnostic('warning', `resolution overlay watchdog cleared a satisfied ${pending.requestedResolutionSeconds}s view after ${Math.round(performanceNow() - pending.armedAtMs)}ms`);
    clearDebugGraphPendingResolutionChange({hideOverlay: true});
    return;
  }
  if (state.phase !== 'error') return;
  if (Number(debugRuntimeState.graphResolutionOverrideSeconds) !== Number(pending.targetSeconds) || Number(debugRuntimeState.graphRangeSeconds) !== Number(pending.rangeSeconds)) return;
  clearDebugGraphPendingResolutionChange();
  const revertedSeconds = normalizedDebugGraphResolutionOverrideSeconds(pending.previousSeconds, debugGraphDomain(), Date.now());
  debugRuntimeState.graphResolutionOverrideSeconds = revertedSeconds;
  saveJsDebugStatsUiPreferences();
  refreshDebugGraphSurfaces();
  const label = revertedSeconds > 0 ? `${revertedSeconds}s` : 'AUTO';
  // A toast is user feedback, never a state-machine dependency: its rendering must not be
  // able to throw back into the readiness transition that invoked this resolver.
  try {
    emitNotification('statsResolution', {
      title: t('debug.graph.resolution.loadFailed', {resolution: label}),
      className: 'danger-alert toast',
      coalesceKey: 'statsResolution',
    });
  } catch (_) {}
}

function setDebugGraphServiceLoadMode(value) {
  loadJsDebugStatsUiPreferences();
  const normalized = normalizedDebugGraphServiceLoadMode(value);
  if (normalized === debugRuntimeState.serviceLoadMode) return false;
  debugRuntimeState.serviceLoadMode = normalized;
  saveJsDebugStatsUiPreferences();
  refreshDebugGraphSurfaces({deferFocusedControl: false});
  return true;
}

function setDebugGraphChartLayout(value) {
  loadJsDebugStatsUiPreferences();
  debugRuntimeState.graphChartLayout = Math.max(0, Math.min(4, Math.round(Number(value) || 0)));
  saveJsDebugStatsUiPreferences();
  refreshDebugGraphSurfaces();
}

function retryJsDebugHistory() {
  if (jsDebugHistoryReadiness.phase !== 'error' || !jsDebugStatsPanelVisible()) return false;
  if (jsDebugGraphExactResolutionEnabled) {
    const client = ensureJsDebugCurrentStatsClient();
    if (!client) return false;
    const domain = debugGraphDomain();
    beginJsDebugHistoryReadiness(Math.max(0, Math.floor(domain.startMs / 1000)), {retry: true});
    void client.retry().catch(error => {
      const selection = jsDebugCurrentStatsSelection();
      setJsDebugHistoryReadiness('error', {
        requestedRangeSeconds: selection.rangeSeconds,
        error: String(error?.reason || error?.message || 'Current stats stream unavailable'),
        nextAutoRetryAtMs: performanceNow() + jsDebugHistoryRetryInitialDelayMs,
      });
    });
    return true;
  }
  return requestJsDebugHistoryForCurrentDomain({retry: true});
}

function debugGraphRangeSliderIndex(slider, options = debugGraphAvailableRangeOptions()) {
  const rawValue = Number(slider?.value);
  const value = Number.isFinite(rawValue) ? rawValue : 0;
  return Math.max(0, Math.min(options.length - 1, Math.round(value)));
}

function debugGraphRangeOptionForSlider(slider) {
  const options = debugGraphAvailableRangeOptions();
  const index = debugGraphRangeSliderIndex(slider, options);
  return options[index] || null;
}

function updateDebugGraphRangeSliderLabel(slider, option) {
  const label = slider?.closest?.('[data-js-debug-range-control]')?.querySelector?.('[data-js-debug-range-label]');
  if (label && option) label.textContent = option.label;
}

function setDebugGraphRangeFromSlider(slider, {render = true, snap = false} = {}) {
  const options = debugGraphAvailableRangeOptions();
  const index = debugGraphRangeSliderIndex(slider, options);
  const option = options[index];
  if (!option) return false;
  if (snap) slider.value = String(index);
  setDebugGraphRange(option.seconds, {render});
  updateDebugGraphRangeSliderLabel(slider, option);
  return true;
}

function debugGraphPointerRatioFromRect(clientX, rect) {
  const left = Number(rect?.left);
  const width = Number(rect?.width);
  if (!Number.isFinite(Number(clientX)) || !Number.isFinite(left) || !Number.isFinite(width) || width <= 0) return null;
  return Math.max(0, Math.min(1, (Number(clientX) - left) / width));
}

function debugGraphPointerRatioForEvent(event) {
  const svg = event?.target?.closest?.('.js-debug-line-chart');
  if (!svg) return null;
  return debugGraphPointerRatioFromRect(event.clientX, svg.getBoundingClientRect());
}

function debugGraphSetInteractionLines(panel, ratio) {
  const graph = panel?.querySelector?.('[data-js-debug-graph], [data-js-yocost-graphs]');
  if (!graph || ratio == null) return;
  const x = (Math.max(0, Math.min(1, Number(ratio))) * jsDebugGraphGeometry.width).toFixed(1);
  graph.classList.add('js-debug-graph--hovering');
  graph.querySelectorAll('[data-js-debug-hover-line]').forEach(line => {
    line.setAttribute('x1', x);
    line.setAttribute('x2', x);
  });
}

function debugGraphSetHoverLegendItems(chart, timestamp) {
  const key = String(chart?.dataset?.jsDebugChart || '');
  const data = jsDebugGraphHoverChartData.get(key);
  const items = chart?.querySelectorAll?.('[data-js-debug-legend]') || [];
  if (!data || (data.group.dynamicAgentTokens !== true && !data.group.dynamicTokenDimension)) {
    items.forEach(item => item.classList.remove('js-debug-legend-item--hovered'));
    return;
  }
  const index = debugGraphHoverBucketIndex(data.buckets, timestamp);
  const activeKeys = new Set(index < 0 ? [] : data.groupSeries
    .filter(series => series.agentTokenSeries === true && (!data.group.dynamicTokenDimension || series.tokenDimension === data.group.dynamicTokenDimension))
    .filter(series => !Array.isArray(series.hasDataValues) || series.hasDataValues[index] === true)
    .map(series => series.key));
  items.forEach(item => item.classList.toggle('js-debug-legend-item--hovered', activeKeys.has(String(item.dataset.jsDebugLegend || ''))));
}

function debugGraphSetHoverTooltip(panel, event, ratio) {
  const svg = event?.target?.closest?.('.js-debug-line-chart');
  const chart = svg?.closest?.('[data-js-debug-chart]');
  const tooltip = chart?.querySelector?.('[data-js-debug-hover-tooltip]');
  if (!svg || !chart || !tooltip || ratio == null) return;
  const domain = debugGraphGridDomain(panel);
  const spanMs = Number(domain.endMs) - Number(domain.startMs);
  if (!Number.isFinite(spanMs) || spanMs <= 0) return;
  const timestamp = Number(domain.startMs) + (Math.max(0, Math.min(1, Number(ratio))) * spanMs);
  const tokenDetail = debugGraphTokenHoverDetailAtTime(chart, timestamp);
  const hoverDetail = tokenDetail ? null : debugGraphHoverDetailAtTime(chart, timestamp, event);
  tooltip.querySelector('[data-js-debug-hover-max]').textContent = tokenDetail?.span || hoverDetail?.text || debugGraphHoverValueAtTime(chart, timestamp);
  tooltip.querySelector('[data-js-debug-hover-time]').textContent = tokenDetail?.detail || debugGraphExactTimeLabel(timestamp);
  tooltip.toggleAttribute('data-js-debug-hover-no-data', tokenDetail?.noData === true);
  const provenance = debugGraphHoverProvenanceAtTime(chart, timestamp, hoverDetail?.seriesKey || '');
  if (provenance.length) tooltip.setAttribute('data-js-debug-hover-provenance', JSON.stringify(provenance));
  else tooltip.removeAttribute('data-js-debug-hover-provenance');
  const sourceText = debugGraphHeldProvenanceText(provenance);
  const source = tooltip.querySelector('[data-js-debug-hover-source]');
  const sourceSeparator = tooltip.querySelector('[data-js-debug-hover-source-separator]');
  if (source) {
    source.textContent = sourceText;
    source.hidden = !sourceText;
  }
  if (sourceSeparator) sourceSeparator.hidden = !sourceText;
  debugGraphSetHoverLegendItems(chart, timestamp);
  for (const item of panel.querySelectorAll('[data-js-debug-hover-tooltip]')) item.hidden = item !== tooltip;
  tooltip.hidden = false;
  tooltip.style.left = '0px';
  tooltip.style.top = '0px';
  const chartRect = chart.getBoundingClientRect();
  const tooltipRect = tooltip.getBoundingClientRect();
  const left = Math.max(4, Math.min(chartRect.width - tooltipRect.width - 4, event.clientX - chartRect.left + 4));
  const top = Math.max(4, Math.min(chartRect.height - tooltipRect.height - 4, event.clientY - chartRect.top - tooltipRect.height - 4));
  tooltip.style.left = `${left}px`;
  tooltip.style.top = `${top}px`;
}

function debugGraphClearInteractionLines(panel) {
  if (jsDebugGraphSelectionState) return;
  const graph = panel?.querySelector?.('[data-js-debug-graph], [data-js-yocost-graphs]');
  if (graph) graph.classList.remove('js-debug-graph--hovering');
  panel?.querySelectorAll?.('[data-js-debug-hover-tooltip]').forEach(tooltip => { tooltip.hidden = true; });
  panel?.querySelectorAll?.('[data-js-debug-legend-item--hovered]').forEach(item => item.classList.remove('js-debug-legend-item--hovered'));
}

function debugGraphClearInteractionLinesUnlessPinned(panel) {
  // A touch tap pins the tooltip: the pointerleave that fires when the finger
  // lifts must not clear it (there is no "move away" gesture on touch). A mouse
  // leaving the chart clears immediately, exactly as before.
  if (jsDebugGraphLastPointerType === 'touch') return;
  debugGraphClearInteractionLines(panel);
}

function handleDebugGraphOutsideTapDismiss(event) {
  const chartMenu = event.target?.closest?.('[data-js-debug-chart-menu]');
  document.querySelectorAll('[data-js-debug-chart-menu][open]').forEach(menu => {
    if (menu !== chartMenu) menu.open = false;
  });
  // Dismiss a pinned touch tooltip when the next tap lands outside the chart that
  // owns it. A tap on a chart updates the tooltip through the normal pointerdown
  // path, so only genuinely-outside taps clear here.
  if (jsDebugGraphLastPointerType !== 'touch') return;
  if (event.target?.closest?.('.js-debug-line-chart')) return;
  for (const panel of document.querySelectorAll('.js-debug-graph-view, [data-js-debug-panel]')) {
    debugGraphClearInteractionLines(panel);
  }
}

if (typeof document !== 'undefined' && document?.addEventListener) {
  document.addEventListener('pointerdown', handleDebugGraphOutsideTapDismiss, true);
}

function debugGraphSetSelectionRects(panel, startRatio, endRatio) {
  const graph = panel?.querySelector?.('[data-js-debug-graph], [data-js-yocost-graphs]');
  if (!graph) return;
  const start = Math.max(0, Math.min(1, Number(startRatio)));
  const end = Math.max(0, Math.min(1, Number(endRatio)));
  const x = Math.min(start, end) * jsDebugGraphGeometry.width;
  const width = Math.abs(end - start) * jsDebugGraphGeometry.width;
  graph.classList.add('js-debug-graph--selecting');
  graph.querySelectorAll('[data-js-debug-selection-rect]').forEach(rect => {
    rect.setAttribute('x', x.toFixed(1));
    rect.setAttribute('width', width.toFixed(1));
  });
}

function debugGraphClearSelectionRects(panel) {
  const graph = panel?.querySelector?.('[data-js-debug-graph], [data-js-yocost-graphs]');
  if (!graph) return;
  graph.classList.remove('js-debug-graph--selecting');
  graph.querySelectorAll('[data-js-debug-selection-rect]').forEach(rect => {
    rect.setAttribute('x', '0');
    rect.setAttribute('width', '0');
  });
}

function debugGraphGridDomain(panel) {
  const grid = panel?.querySelector?.('[data-js-debug-chart-grid]');
  const startMs = Number(grid?.dataset?.jsDebugDomainStart);
  const endMs = Number(grid?.dataset?.jsDebugDomainEnd);
  if (!Number.isFinite(startMs) || !Number.isFinite(endMs) || endMs <= startMs) return debugGraphDomain();
  return {startMs, endMs, rangeSeconds: (endMs - startMs) / 1000};
}

function debugGraphSelectionRatioForEvent(event, selection = jsDebugGraphSelectionState) {
  if (!selection) return null;
  return debugGraphPointerRatioFromRect(event?.clientX, selection.rect);
}

function debugGraphInteractionBelongsToPanel(panel) {
  if (!panel) return false;
  return jsDebugGraphSelectionState?.panel === panel
    || jsDebugGraphTouchCandidateState?.panel === panel;
}

function flushDeferredDebugGraphInteractionRefresh(panel) {
  if (!panel) return false;
  let flushed = false;
  for (const graph of panel.querySelectorAll?.('[data-js-debug-graph]') || []) {
    flushed = flushDeferredDebugGraphRefresh(graph) || flushed;
  }
  if (panel.matches?.('.js-debug-panel') && panel.dataset.jsDebugGraphRefreshPending === 'true') {
    delete panel.dataset.jsDebugGraphRefreshPending;
    flushed = renderYoCostPanels({force: true}) || flushed;
  }
  return flushed;
}

function clearDebugGraphTouchCandidate(candidate = jsDebugGraphTouchCandidateState) {
  if (!candidate || candidate !== jsDebugGraphTouchCandidateState) return;
  if (candidate.armTimer != null) clearTimeout(candidate.armTimer);
  jsDebugGraphTouchCandidateState = null;
}

function debugGraphTouchCandidateDecision(candidate, clientX, clientY, nowMs) {
  if (!candidate) return 'cancel';
  const dx = Math.abs(Number(clientX) - candidate.startClientX);
  const dy = Math.abs(Number(clientY) - candidate.startClientY);
  if (dy >= jsDebugGraphTouchArmDistancePx && dy >= dx) return 'scroll';
  if (dx >= jsDebugGraphTouchArmDistancePx && dx > jsDebugGraphTouchDirectionRatio * dy) {
    return Number(nowMs) - candidate.startedAtMs >= jsDebugGraphTouchHoldMs ? 'arm' : 'scroll';
  }
  if (Number(nowMs) - candidate.startedAtMs >= jsDebugGraphTouchHoldMs && dy < jsDebugGraphTouchArmDistancePx) return 'arm';
  return 'wait';
}

function handleDebugGraphTouchMove(event, panel) {
  if (!event || !panel || event.touches?.length !== 1) return false;
  const touch = event.touches[0];
  const selection = jsDebugGraphSelectionState;
  if (selection?.panel === panel) {
    if (event.cancelable !== false) event.preventDefault();
    return true;
  }
  const candidate = jsDebugGraphTouchCandidateState;
  if (candidate?.panel !== panel) return false;
  candidate.currentClientX = Number(touch.clientX);
  candidate.currentClientY = Number(touch.clientY);
  const ratio = debugGraphPointerRatioFromRect(touch.clientX, candidate.rect);
  if (ratio != null) candidate.currentRatio = ratio;
  const decision = debugGraphTouchCandidateDecision(
    candidate,
    candidate.currentClientX,
    candidate.currentClientY,
    Number.isFinite(Number(event.timeStamp)) ? Number(event.timeStamp) : performanceNow(),
  );
  if (decision === 'arm') {
    if (event.cancelable !== false) event.preventDefault();
    startDebugGraphSelection(candidate);
    return true;
  }
  if (decision === 'scroll') {
    clearDebugGraphTouchCandidate(candidate);
    jsDebugGraphLastPointerType = 'mouse';
    debugGraphClearInteractionLines(panel);
    flushDeferredDebugGraphInteractionRefresh(panel);
  }
  return false;
}

function bindDebugGraphTouchSelection(panel) {
  if (!panel) return null;
  return bindOnce(panel, 'debug-graph-touch-selection', () => {
    const handleTouchMove = event => { handleDebugGraphTouchMove(event, panel); };
    panel.addEventListener('touchmove', handleTouchMove, {passive: false});
    return () => panel.removeEventListener('touchmove', handleTouchMove, {passive: false});
  });
}

function startDebugGraphSelection(candidate, event = null) {
  if (!candidate) return false;
  clearDebugGraphTouchCandidate(candidate);
  if (event?.cancelable !== false) event?.preventDefault?.();
  const {panel, svg, pointerId, rect, domain, startRatio, currentRatio} = candidate;
  if (pointerId != null && typeof svg?.setPointerCapture === 'function') {
    try { svg.setPointerCapture(pointerId); } catch (_) { /* capture is best-effort */ }
  }
  jsDebugGraphSelectionState = {
    panel,
    svg,
    pointerId,
    pointerType: candidate.pointerType,
    rect,
    domain,
    startRatio,
    currentRatio,
    resolutionMs: candidate.resolutionMs,
  };
  debugGraphSetInteractionLines(panel, currentRatio);
  debugGraphSetSelectionRects(panel, startRatio, currentRatio);
  return true;
}

function handleDebugGraphPointerDown(event, panel) {
  const ratio = debugGraphPointerRatioForEvent(event);
  if (ratio == null || event.button > 0) return false;
  const pointerType = event.pointerType || 'mouse';
  if (pointerType !== 'touch') jsDebugGraphLastPointerType = pointerType;
  if (document.activeElement?.closest?.('.js-debug-graph-controls, [data-js-debug-range-control]')) document.activeElement.blur?.();
  const svg = event.target.closest('.js-debug-line-chart');
  const candidate = {
    panel,
    svg,
    pointerId: event.pointerId,
    pointerType,
    rect: svg.getBoundingClientRect(),
    domain: debugGraphGridDomain(panel),
    startRatio: ratio,
    currentRatio: ratio,
    startClientX: Number(event.clientX),
    startClientY: Number(event.clientY),
    currentClientX: Number(event.clientX),
    currentClientY: Number(event.clientY),
    startedAtMs: Number.isFinite(Number(event.timeStamp)) ? Number(event.timeStamp) : performanceNow(),
    resolutionMs: 0,
  };
  candidate.resolutionMs = debugGraphDisplayResolutionMs(candidate.domain, 0, Date.now());
  if (candidate.pointerType === 'touch') {
    clearDebugGraphTouchCandidate();
    jsDebugGraphTouchCandidateState = candidate;
    candidate.armTimer = setTimeout(() => {
      if (candidate !== jsDebugGraphTouchCandidateState) return;
      if (Math.abs(candidate.currentClientY - candidate.startClientY) < jsDebugGraphTouchArmDistancePx) startDebugGraphSelection(candidate);
    }, jsDebugGraphTouchHoldMs);
    debugGraphSetInteractionLines(panel, ratio);
  } else {
    event.preventDefault();
    startDebugGraphSelection(candidate, event);
  }
  // Touch has no hover-before-press, so surface the value at the touched point
  // immediately on contact (a mouse already shows it from hover).
  debugGraphSetHoverTooltip(panel, event, ratio);
  return true;
}

function handleDebugGraphPointerMove(event, panel) {
  const candidate = jsDebugGraphTouchCandidateState;
  if (candidate?.panel === panel && candidate.pointerId === event.pointerId) {
    const ratio = debugGraphPointerRatioFromRect(event.clientX, candidate.rect);
    if (ratio == null) return;
    candidate.currentRatio = ratio;
    candidate.currentClientX = Number(event.clientX);
    candidate.currentClientY = Number(event.clientY);
    const elapsedMs = (Number.isFinite(Number(event.timeStamp)) ? Number(event.timeStamp) : performanceNow()) - candidate.startedAtMs;
    const decision = debugGraphTouchCandidateDecision(candidate, candidate.currentClientX, candidate.currentClientY, candidate.startedAtMs + elapsedMs);
    if (decision === 'arm') {
      startDebugGraphSelection(candidate, event);
    } else if (decision === 'scroll') {
      clearDebugGraphTouchCandidate(candidate);
      jsDebugGraphLastPointerType = 'mouse';
      debugGraphClearInteractionLines(panel);
      return;
    } else {
      debugGraphSetInteractionLines(panel, ratio);
      debugGraphSetHoverTooltip(panel, event, ratio);
      return;
    }
  }
  if (jsDebugGraphSelectionState?.panel === panel) {
    if (jsDebugGraphSelectionState.pointerType === 'touch' && event.cancelable !== false) event.preventDefault();
    const ratio = debugGraphSelectionRatioForEvent(event);
    if (ratio == null) return;
    jsDebugGraphSelectionState.currentRatio = ratio;
    debugGraphSetInteractionLines(panel, ratio);
    debugGraphSetHoverTooltip(panel, event, ratio);
    debugGraphSetSelectionRects(panel, jsDebugGraphSelectionState.startRatio, ratio);
    return;
  }
  const ratio = debugGraphPointerRatioForEvent(event);
  if (ratio == null) return;
  debugGraphSetInteractionLines(panel, ratio);
  debugGraphSetHoverTooltip(panel, event, ratio);
}

function handleDebugGraphPointerUp(event, panel, {useEventRatio = true} = {}) {
  const candidate = jsDebugGraphTouchCandidateState;
  if (candidate?.panel === panel && candidate.pointerId === event.pointerId) {
    clearDebugGraphTouchCandidate(candidate);
    jsDebugGraphLastPointerType = 'touch';
    const ratio = debugGraphPointerRatioFromRect(event.clientX, candidate.rect);
    if (ratio != null) debugGraphSetInteractionLines(panel, ratio);
    flushDeferredDebugGraphInteractionRefresh(panel);
    return;
  }
  const selection = jsDebugGraphSelectionState;
  if (!selection || selection.panel !== panel) return;
  if (selection.pointerId != null && typeof selection.svg?.releasePointerCapture === 'function') {
    try { selection.svg.releasePointerCapture(selection.pointerId); } catch (_) { /* already released */ }
  }
  const ratio = useEventRatio ? debugGraphSelectionRatioForEvent(event) : null;
  if (ratio != null) selection.currentRatio = ratio;
  const start = Math.max(0, Math.min(1, Number(selection.startRatio)));
  const end = Math.max(0, Math.min(1, Number(selection.currentRatio)));
  debugGraphClearSelectionRects(panel);
  jsDebugGraphSelectionState = null;
  const minRatio = Math.min(start, end);
  const maxRatio = Math.max(start, end);
  const domain = selection.domain;
  const spanMs = Math.max(1, Number(domain.endMs) - Number(domain.startMs));
  const selectedMs = (maxRatio - minRatio) * spanMs;
  if (selection.pointerType === 'touch') jsDebugGraphLastPointerType = 'mouse';
  const minimumGestureMs = Math.max(1000, Number(selection.resolutionMs) * jsDebugGraphZoomMinBuckets);
  if (selectedMs >= minimumGestureMs && Math.abs(maxRatio - minRatio) >= jsDebugGraphZoomMinRatio) {
    jsDebugGraphZoomDomain = {
      startMs: Number(domain.startMs) + (minRatio * spanMs),
      endMs: Number(domain.startMs) + (maxRatio * spanMs),
    };
    syncDebugGraphResolutionOverride(Date.now(), {persist: true});
    syncJsDebugStatsDeliveryMode();
    refreshDebugGraphSurfaces();
    requestJsDebugHistoryForCurrentDomain();
    for (const graph of document.querySelectorAll('[data-js-debug-graph]')) syncDebugGraphControls(graph);
  } else {
    debugGraphSetInteractionLines(panel, end);
    flushDeferredDebugGraphInteractionRefresh(panel);
  }
}

function handleDebugGraphPointerCancel(event, panel) {
  const selection = jsDebugGraphSelectionState;
  const matchingPointer = selection?.pointerId == null
    || event?.pointerId == null
    || selection.pointerId === event.pointerId;
  if (selection?.panel === panel && matchingPointer) {
    handleDebugGraphPointerUp(event, panel, {useEventRatio: false});
    return;
  }
  cancelDebugGraphSelection(panel);
}

function cancelDebugGraphSelection(panel) {
  if (jsDebugGraphTouchCandidateState?.panel === panel) {
    clearDebugGraphTouchCandidate();
    jsDebugGraphLastPointerType = 'mouse';
    debugGraphClearInteractionLines(panel);
  }
  const selection = jsDebugGraphSelectionState;
  if (selection?.panel !== panel) return;
  if (selection.pointerId != null && typeof selection.svg?.releasePointerCapture === 'function') {
    try { selection.svg.releasePointerCapture(selection.pointerId); } catch (_) { /* already released */ }
  }
  debugGraphClearSelectionRects(panel);
  jsDebugGraphSelectionState = null;
  flushDeferredDebugGraphInteractionRefresh(panel);
}

function handleDebugGraphControlEvent(event, panel) {
  const costRefresh = event.target.closest('[data-js-debug-cost-refresh]');
  if (event.type === 'click' && costRefresh && panel.contains(costRefresh)) {
    event.preventDefault();
    void refreshDebugCostPricing();
    return true;
  }
  const serviceLoadMode = event.target.closest('[data-js-debug-service-load-mode]');
  if (event.type === 'change' && serviceLoadMode && panel.contains(serviceLoadMode)) {
    setDebugGraphServiceLoadMode(serviceLoadMode.dataset.jsDebugServiceLoadMode);
    return true;
  }
  const chartClose = event.target.closest('[data-js-debug-chart-close]');
  // A chart close reflows the grid. Handling it on pointerdown replaces the target before the
  // corresponding pointerup, so that follow-up event can land on another chart's X. Click is the
  // browser's single completed activation and preserves both mouse and keyboard semantics.
  if (event.type === 'click' && chartClose && panel.contains(chartClose)) {
    event.preventDefault();
    setDebugGraphChartVisible(chartClose.dataset.jsDebugChartClose, false);
    return true;
  }
  const chartToggle = event.target.closest('[data-js-debug-chart-toggle]');
  if (event.type === 'change' && chartToggle && panel.contains(chartToggle)) {
    setDebugGraphChartVisible(chartToggle.dataset.jsDebugChartToggle, chartToggle.checked);
    return true;
  }
  if (event.type === 'click' && chartToggle && panel.contains(chartToggle) && chartToggle.tagName !== 'INPUT') {
    event.preventDefault();
    const chartKey = chartToggle.dataset.jsDebugChartToggle;
    setDebugGraphChartVisible(chartKey, !debugGraphChartVisible(chartKey));
    return true;
  }
  const retry = event.target.closest('[data-js-debug-history-retry]');
  if (retry && panel.contains(retry)) {
    event.preventDefault();
    retryJsDebugHistory();
    return true;
  }
  const reset = event.target.closest('[data-js-debug-zoom-reset]');
  if (event.type === 'click' && reset && panel.contains(reset)) {
    event.preventDefault();
    clearDebugGraphZoom();
    return true;
  }
  const resolutionOverride = event.target.closest('[data-js-debug-resolution-override]');
  if (resolutionOverride && panel.contains(resolutionOverride) && event.type === 'change') {
    setDebugGraphResolutionOverride(resolutionOverride.value);
    return true;
  }
  const chartLayout = event.target.closest('button[data-js-debug-chart-layout]');
  if (chartLayout && panel.contains(chartLayout) && event.type === 'pointerdown') {
    event.preventDefault();
    setDebugGraphChartLayout(chartLayout.dataset.jsDebugChartLayout);
    return true;
  }
  const slider = event.target.closest('[data-js-debug-range-slider]');
  if (slider && panel.contains(slider)) {
    if (event.type === 'pointerdown') {
      jsDebugGraphRangeSliderDragging = true;
      // Claim the event at the graph shell so chart-selection handling cannot
      // inspect or replace the native range input during its drag gesture.
      // Do not preventDefault: the browser owns the range-thumb movement.
      return true;
    }
    if (event.type === 'input') {
      jsDebugGraphRangeSliderDragging = true;
      return setDebugGraphRangeFromSlider(slider, {render: false});
    }
    if (event.type === 'change') {
      jsDebugGraphRangeSliderDragging = false;
      return setDebugGraphRangeFromSlider(slider, {snap: true});
    }
    if (event.type === 'pointerup') return false;
    if (event.type === 'pointercancel') {
      jsDebugGraphRangeSliderDragging = false;
      return false;
    }
    return false;
  }
  const range = event.target.closest('[data-js-debug-range]');
  if (range && panel.contains(range)) {
    event.preventDefault();
    setDebugGraphRange(range.dataset.jsDebugRange);
    return true;
  }
  return false;
}

function bindDebugPanel(panel) {
  if (!panel) return null;
  return bindOnce(panel, 'debug-panel', () => {
    const scope = createLifecycleScope();
    for (const view of debugPanelSubviewDescriptors()) view.bind(panel);
    syncDebugSubviewActivation({pollNow: true});
    const disposeActions = bindActionDispatcher(panel, {
    'debug-subtab': (_event, button) => setDebugSubTab(button.dataset.jsDebugSubtab),
    });
    scope.replace('actions', disposeActions, dispose => dispose?.());
    scope.ownEvent('focusout', panel, 'focusout', event => {
    const graph = event.target?.closest?.('[data-js-debug-graph]');
    if (!graph) return;
    setTimeout(() => { flushDeferredDebugGraphRefresh(graph); }, 0);
  });
    scope.ownEvent('pointerdown', panel, 'pointerdown', event => {
    if (handleDebugGraphControlEvent(event, panel)) return;
    handleDebugGraphPointerDown(event, panel);
  });
    scope.ownEvent('pointermove', panel, 'pointermove', event => {
    handleDebugGraphPointerMove(event, panel);
  });
    scope.ownEvent('pointerleave', panel, 'pointerleave', () => {
    debugGraphClearInteractionLinesUnlessPinned(panel);
  });
    scope.ownEvent('pointerup', panel, 'pointerup', event => {
    if (handleDebugGraphControlEvent(event, panel)) return;
    handleDebugGraphPointerUp(event, panel);
  });
    scope.ownEvent('pointercancel', panel, 'pointercancel', event => {
    handleDebugGraphControlEvent(event, panel);
    handleDebugGraphPointerCancel(event, panel);
  });
    scope.ownEvent('input', panel, 'input', event => {
    handleDebugGraphControlEvent(event, panel);
  });
    scope.ownEvent('change', panel, 'change', event => {
    handleDebugGraphControlEvent(event, panel);
  });
    scope.ownEvent('click', panel, 'click', event => {
    if (handleDebugGraphControlEvent(event, panel)) return;
    const systemRefresh = event.target.closest('[data-js-debug-system-refresh]');
    if (systemRefresh && panel.contains(systemRefresh)) {
      event.preventDefault();
      void pollDebugSystemStatus({force: true});
      return;
    }
    // One disclosure control per roster row. It is a real <button>, so mouse click, Enter and Space
    // all arrive here through the same one path -- there is no key handler duplicating the rule.
    const rosterToggle = event.target.closest('[data-js-debug-roster-toggle]');
    if (rosterToggle && panel.contains(rosterToggle)) {
      event.preventDefault();
      toggleDebugSystemRosterRow(rosterToggle.dataset.jsDebugRosterToggle);
      return;
    }
    const advancedSummary = event.target.closest('[data-js-debug-system-advanced-summary]');
    if (advancedSummary && panel.contains(advancedSummary)) {
      // The <details> would toggle itself, but the next poll re-renders it from state; recording
      // the intent here is what makes it survive.
      event.preventDefault();
      jsDebugSystemRosterState.advancedOpen = !jsDebugSystemRosterState.advancedOpen;
      refreshDebugSystemViews();
      // Opening is the demand signal for the Advanced body: fetch it here rather than waiting up to
      // five seconds for the next poll. Closing fetches nothing.
      if (jsDebugSystemRosterState.advancedOpen) void pollDebugSystemAdvanced({force: true});
      return;
    }
    const logLevel = event.target.closest('[data-js-debug-log-level]');
    if (logLevel && panel.contains(logLevel)) {
      event.preventDefault();
      const level = String(logLevel.dataset.jsDebugLogLevel || '');
      if (!jsDebugLogLevels.includes(level)) return;
      if (jsDebugLogsState.levels.has(level)) jsDebugLogsState.levels.delete(level);
      else jsDebugLogsState.levels.add(level);
      saveJsDebugStatsUiPreferences();
      refreshDebugLogsViews();
      return;
    }
    const logsClear = event.target.closest('[data-js-debug-logs-clear]');
    if (logsClear && panel.contains(logsClear)) {
      event.preventDefault();
      jsDebugLogRecordCleared();
      refreshDebugLogsViews();
      statusEl.textContent = t('debug.logs.cleared');
      return;
    }
    const copy = event.target.closest('[data-copy-feedback-key]');
    if (copy && panel.contains(copy)) {
      const feedbackKey = String(copy.dataset.copyFeedbackKey || '');
      const text = jsDebugCopyTextForFeedbackKey(feedbackKey);
      if (text === null) return;
      event.preventDefault();
      void runDebugCopy(text, {button: copy, feedbackKey});
      return;
    }
    const clear = event.target.closest('[data-js-debug-clear]');
    if (clear && panel.contains(clear)) {
      event.preventDefault();
      clearJsDebugEvents();
      statusEl.textContent = t('debug.cleared');
    }
    });
    return () => scope.dispose('debug-panel-unbound');
  });
}

registerDebugRuntimeFacade('panel', {
  createDebugPanel,
  renderDebugPanels,
  renderYoCostPanels,
});
