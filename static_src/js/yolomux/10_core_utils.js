async function apiFetch(url, options = {}) {
  const requestOptions = {...options};
  if (!requestOptions.credentials) requestOptions.credentials = 'same-origin';
  if (shareToken) {
    if (typeof Headers === 'function') {
      const headers = new Headers(requestOptions.headers || {});
      if (!headers.has('X-Share-Token')) headers.set('X-Share-Token', shareToken);
      requestOptions.headers = headers;
    } else {
      requestOptions.headers = {...(requestOptions.headers || {}), 'X-Share-Token': shareToken};
    }
  }
  const apiDebugEnabled = jsDebugCollectionEnabled;
  const startedAt = apiDebugEnabled ? jsDebugPerformanceNow() : 0;
  const method = apiDebugEnabled ? jsDebugRequestMethod(requestOptions) : '';
  const requestBytes = apiDebugEnabled ? jsDebugRequestBytes(url, requestOptions) : 0;
  let response;
  try {
    response = await fetch(url, requestOptions);
  } catch (error) {
    if (apiDebugEnabled) recordApiDebugEvent(url, method, startedAt, {error, requestBytes});
    throw error;
  }
  if (apiDebugEnabled) {
    const event = recordApiDebugEvent(url, method, startedAt, {status: response.status, ok: response.ok, requestBytes});
    recordApiDebugResponseBytes(event, response);
  }
  if (response.status === 401) {
    await redirectToLogin(response);
    throw new Error('authentication required');
  }
  return response;
}

async function apiJsonResponse(response) {
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    const error = new Error(userMessageText(payload, response.statusText || `HTTP ${response.status}`));
    error.status = response.status;
    error.statusText = response.statusText || '';
    error.payload = payload || {};
    error.response = response;
    throw error;
  }
  return payload;
}

async function apiFetchJson(url, options = {}) {
  return apiJsonResponse(await apiFetch(url, options));
}

async function apiFetchJsonQuiet(url, options = {}) {
  const requestOptions = {...options};
  if (!requestOptions.credentials) requestOptions.credentials = 'same-origin';
  if (shareToken) {
    if (typeof Headers === 'function') {
      const headers = new Headers(requestOptions.headers || {});
      if (!headers.has('X-Share-Token')) headers.set('X-Share-Token', shareToken);
      requestOptions.headers = headers;
    } else {
      requestOptions.headers = {...(requestOptions.headers || {}), 'X-Share-Token': shareToken};
    }
  }
  return apiJsonResponse(await fetch(url, requestOptions));
}

function messageDescriptorText(descriptor, fallback = '') {
  const value = descriptor && typeof descriptor === 'object' ? descriptor : {};
  const key = String(value.key || '').trim();
  if (key) {
    const template = i18nResolve(key);
    if (template !== null) {
      const rawParams = value.params && typeof value.params === 'object' ? value.params : {};
      const params = Object.fromEntries(Object.entries(rawParams).map(([name, param]) => [
        name,
        param && typeof param === 'object' && ('key' in param || 'fallback' in param)
          ? messageDescriptorText(param)
          : param,
      ]));
      return i18nInterpolate(template, params);
    }
  }
  return String(value.fallback || fallback || '');
}

function messageFieldDescriptor(value, field = 'message') {
  const source = value && typeof value === 'object' ? value : {};
  const name = String(field || 'message');
  const params = source[`${name}_params`];
  return {
    key: String(source[`${name}_key`] || ''),
    params: params && typeof params === 'object' ? params : {},
    fallback: String(source[name] || ''),
  };
}

function structuredMessageText(value, field = 'message', fallback = '') {
  return messageDescriptorText(messageFieldDescriptor(value, field), fallback);
}

function structuredMessageSnapshot(value, field = 'message') {
  const descriptor = messageFieldDescriptor(value, field);
  const name = String(field || 'message');
  return {
    [name]: descriptor.fallback,
    [`${name}_key`]: descriptor.key,
    [`${name}_params`]: {...descriptor.params},
  };
}

function userMessageText(value, fallback = '') {
  const source = value && typeof value === 'object' ? value : {};
  const payload = source.payload && typeof source.payload === 'object' ? source.payload : source;
  const descriptor = payload.user_message && typeof payload.user_message === 'object' ? payload.user_message : {};
  return messageDescriptorText(descriptor, payload.error || source.message || fallback || '');
}

function userMessageSnapshot(value, fallback = '') {
  const source = value && typeof value === 'object' ? value : {};
  const payload = source.payload && typeof source.payload === 'object' ? source.payload : source;
  const descriptor = payload.user_message && typeof payload.user_message === 'object' ? payload.user_message : {};
  const fallbackDescriptor = fallback && typeof fallback === 'object' ? fallback : {};
  const fallbackText = typeof fallback === 'object' ? String(fallbackDescriptor.fallback || '') : String(fallback || '');
  const key = String(descriptor.key || fallbackDescriptor.key || '');
  const rawParams = descriptor.key ? descriptor.params : fallbackDescriptor.params;
  const params = rawParams && typeof rawParams === 'object' ? rawParams : {};
  const sourceText = typeof value === 'string' || typeof value === 'number' ? String(value) : '';
  const rawFallback = String(payload.error || source.message || sourceText || fallbackText || '');
  return {
    error: rawFallback,
    user_message: {
      key,
      params: {...params},
      fallback: String(descriptor.fallback || rawFallback),
    },
  };
}

function clientPushCanSupplyData() {
  return Boolean(clientEventsSource && location.protocol !== 'file:');
}

function clientPushConnectedForData() {
  return clientPushCanSupplyData() && clientEventsConnected === true;
}

function loginRedirectUrlForCurrentLocation() {
  const nextPath = `${window.location.pathname}${window.location.search}`;
  return `/login?next=${encodeURIComponent(nextPath || '/')}`;
}

function claimLoginRedirect() {
  if (authRedirectStarted) return;
  authRedirectStarted = true;
  return true;
}

function redirectToLoginUrl(loginUrl = '') {
  if (!claimLoginRedirect()) return false;
  window.location.assign(loginUrl || loginRedirectUrlForCurrentLocation());
  return true;
}

async function redirectToLogin(response) {
  if (!claimLoginRedirect()) return;
  let loginUrl = loginRedirectUrlForCurrentLocation();
  try {
    const payload = await response.clone().json();
    if (payload?.login_url) loginUrl = payload.login_url;
  } catch (_) {}
  window.location.assign(loginUrl);
}

function jsDebugPerformanceNow() {
  if (!jsDebugCollectionEnabled) return 0;
  const value = globalThis.performance?.now?.();
  return Number.isFinite(value) ? value : Date.now();
}

function jsDebugRequestMethod(options = {}) {
  return String(options?.method || 'GET').toUpperCase();
}

function jsDebugByteLength(text) {
  const value = String(text || '');
  if (typeof TextEncoder !== 'undefined') return new TextEncoder().encode(value).length;
  return value.length;
}

function jsDebugRequestBytes(url, options = {}) {
  if (!jsDebugCollectionEnabled) return 0;
  let bytes = jsDebugByteLength(jsDebugUrlText(url));
  const body = options?.body;
  if (typeof body === 'string') bytes += jsDebugByteLength(body);
  else if (body instanceof ArrayBuffer) bytes += body.byteLength;
  else if (body?.byteLength) bytes += Number(body.byteLength) || 0;
  return bytes;
}

function jsDebugDurationMs(startedAt) {
  if (!jsDebugCollectionEnabled || !Number.isFinite(startedAt)) return null;
  const duration = jsDebugPerformanceNow() - startedAt;
  return Number.isFinite(duration) ? Number(duration.toFixed(1)) : null;
}

function jsDebugUrlText(url) {
  const value = String(url || '');
  try {
    const parsed = new URL(value, window.location.origin);
    return `${parsed.pathname}${parsed.search}`;
  } catch (_) {
    return value.slice(0, 240);
  }
}

function jsDebugErrorText(error) {
  return String(error?.message || error || '').slice(0, 500);
}

function recordApiDebugEvent(url, method, startedAt, result = {}) {
  if (!jsDebugCollectionEnabled) return null;
  const payload = {
    method,
    url: jsDebugUrlText(url),
    durationMs: jsDebugDurationMs(startedAt),
  };
  if (Number.isFinite(result.requestBytes)) payload.requestBytes = result.requestBytes;
  if (Number.isFinite(result.status)) payload.status = result.status;
  if (typeof result.ok === 'boolean') payload.ok = result.ok;
  if (result.error) payload.error = jsDebugErrorText(result.error);
  return recordJsDebugEvent('api', payload);
}

function recordApiDebugResponseBytes(event, response) {
  if (!jsDebugCollectionEnabled || !event || !response) return;
  const headerBytes = Number(response.headers?.get?.('Content-Length') || NaN);
  if (Number.isFinite(headerBytes) && headerBytes >= 0) {
    event.responseBytes = headerBytes;
    if (typeof recordApiDebugResponseBytesForGraph === 'function') recordApiDebugResponseBytesForGraph(event, headerBytes);
    scheduleJsDebugPanelRefresh();
    return;
  }
  if (typeof response.clone !== 'function') return;
  response.clone().arrayBuffer().then(buffer => {
    event.responseBytes = buffer.byteLength;
    if (typeof recordApiDebugResponseBytesForGraph === 'function') recordApiDebugResponseBytesForGraph(event, buffer.byteLength);
    scheduleJsDebugPanelRefresh();
  }).catch(() => {});
}

function recordJsDebugEvent(type, payload = {}) {
  if (!jsDebugCollectionEnabled) return null;
  const event = {
    id: ++jsDebugEventSeq,
    ts: new Date().toISOString(),
    type: String(type || 'event'),
    ...payload,
  };
  jsDebugEvents.push(event);
  if (typeof recordJsDebugEventForGraph === 'function') recordJsDebugEventForGraph(event);
  if (jsDebugEvents.length > jsDebugEventLimit) {
    jsDebugEvents.splice(0, jsDebugEvents.length - jsDebugEventLimit);
  }
  scheduleJsDebugPanelRefresh();
  return event;
}

function clientPerfNow() {
  const value = globalThis.performance?.now?.();
  return Number.isFinite(value) ? value : Date.now();
}

function clientPerfMark(name) {
  if (!name || typeof globalThis.performance?.mark !== 'function') return '';
  const mark = `yolomux:${String(name)}`;
  try {
    globalThis.performance.mark(mark);
    return mark;
  } catch (_) {
    return '';
  }
}

function clientPerfMeasureSinceMark(counterName, markName, details = {}) {
  if (!counterName || !markName) return null;
  const endMark = clientPerfMark(`${counterName}:end`);
  let durationMs = null;
  if (typeof globalThis.performance?.measure === 'function' && endMark) {
    try {
      const measure = globalThis.performance.measure(`yolomux:${counterName}`, markName, endMark);
      durationMs = Number(measure?.duration);
      globalThis.performance.clearMeasures?.(`yolomux:${counterName}`);
    } catch (_) {}
  }
  if (!Number.isFinite(durationMs)) {
    const entries = typeof globalThis.performance?.getEntriesByName === 'function'
      ? globalThis.performance.getEntriesByName(markName)
      : [];
    const startedAt = Number(entries?.at?.(-1)?.startTime);
    if (Number.isFinite(startedAt)) durationMs = clientPerfNow() - startedAt;
  }
  globalThis.performance?.clearMarks?.(markName);
  if (endMark) globalThis.performance?.clearMarks?.(endMark);
  return recordClientPerfCounter(counterName, durationMs, details);
}

function clientPerfActiveAnimationCount() {
  const animations = typeof document?.getAnimations === 'function' ? document.getAnimations({subtree: true}) : [];
  return animations.filter(animation => animation?.playState === 'running').length;
}

function recordClientPerfCounter(name, durationMs = null, details = {}) {
  const key = String(name || '').trim();
  if (!key) return null;
  let counter = clientPerfCounters.get(key);
  if (!counter) {
    counter = {name: key, count: 0, totalMs: 0, maxMs: 0, lastMs: null, lastAt: '', rows: 0, nodes: 0, bytes: 0, skipped: 0};
    clientPerfCounters.set(key, counter);
    if (clientPerfCounters.size > clientPerfCounterLimit) {
      clientPerfCounters.delete(clientPerfCounters.keys().next().value);
    }
  }
  counter.count += 1;
  const duration = Number(durationMs);
  if (Number.isFinite(duration) && duration >= 0) {
    const rounded = Number(duration.toFixed(2));
    counter.totalMs = Number((counter.totalMs + rounded).toFixed(2));
    counter.maxMs = Number(Math.max(counter.maxMs, rounded).toFixed(2));
    counter.lastMs = rounded;
  }
  for (const field of ['rows', 'nodes', 'bytes', 'skipped']) {
    const value = Number(details?.[field]);
    if (Number.isFinite(value) && value > 0) counter[field] += value;
  }
  counter.lastAt = new Date().toISOString();
  counter.lastDetails = {...details};
  if (typeof jsDebugStatsPanelVisible === 'function' && jsDebugStatsPanelVisible()) scheduleJsDebugPanelRefresh();
  return counter;
}

function clientPerfStart(name) {
  return {name: String(name || ''), startedAt: clientPerfNow()};
}

function clientPerfEnd(token, details = {}) {
  if (!token?.name) return null;
  return recordClientPerfCounter(token.name, clientPerfNow() - Number(token.startedAt || 0), details);
}

function clientPerfMeasure(name, fn, details = {}) {
  const token = clientPerfStart(name);
  try {
    return fn();
  } finally {
    clientPerfEnd(token, typeof details === 'function' ? details() : details);
  }
}

function clientPerfSummary() {
  return Array.from(clientPerfCounters.values()).map(counter => ({
    ...counter,
    avgMs: counter.count ? Number((counter.totalMs / counter.count).toFixed(2)) : 0,
  }));
}

function clientPerfLongTaskSummary() {
  const samples = clientPerfLongTaskSamples.slice();
  const total = samples.reduce((sum, sample) => sum + Number(sample.durationMs || 0), 0);
  const max = samples.reduce((value, sample) => Math.max(value, Number(sample.durationMs || 0)), 0);
  return {
    count: samples.length,
    averageMs: samples.length ? Number((total / samples.length).toFixed(1)) : 0,
    maxMs: Number(max.toFixed(1)),
    samples,
  };
}

function clearClientPerfCounters() {
  clientPerfCounters.clear();
  clientPerfLongTaskSamples = [];
}

function installClientPerfLongTaskObserver() {
  if (clientPerfLongTaskObserverInstalled || typeof globalThis.PerformanceObserver !== 'function') return;
  clientPerfLongTaskObserverInstalled = true;
  try {
    const observer = new globalThis.PerformanceObserver(list => {
      for (const entry of list.getEntries?.() || []) {
        const durationMs = Number(entry.duration || 0);
        const sample = {ts: new Date().toISOString(), durationMs: Number(durationMs.toFixed(1)), name: String(entry.name || 'longtask')};
        clientPerfLongTaskSamples.push(sample);
        if (clientPerfLongTaskSamples.length > clientPerfLongTaskSampleLimit) {
          clientPerfLongTaskSamples.splice(0, clientPerfLongTaskSamples.length - clientPerfLongTaskSampleLimit);
        }
        recordClientPerfCounter('longTask', durationMs);
      }
    });
    observer.observe({entryTypes: ['longtask']});
  } catch (_) {}
}

function terminalRemovalLatencyNowMs() {
  const value = Date.now();
  return Number.isFinite(value) ? value : 0;
}

function terminalRemovalLatencyKey(targetKind, target) {
  return `${String(targetKind || 'target')}:${String(target || '')}`;
}

function noteTerminalRemovalLatencyStart(targetKind, target, details = {}) {
  if (!jsDebugCollectionEnabled) return;
  const key = terminalRemovalLatencyKey(targetKind, target);
  terminalRemovalLatencyPending.set(key, {
    targetKind: String(targetKind || 'target'),
    target: String(target || ''),
    origin: String(details.origin || 'unknown'),
    startedAtMs: terminalRemovalLatencyNowMs(),
    details: {...details},
  });
}

function clearTerminalRemovalLatency(targetKind, target) {
  terminalRemovalLatencyPending.delete(terminalRemovalLatencyKey(targetKind, target));
}

function completeTerminalRemovalLatency(targetKind, target, details = {}) {
  if (!jsDebugCollectionEnabled) return null;
  const key = terminalRemovalLatencyKey(targetKind, target);
  const pending = terminalRemovalLatencyPending.get(key) || null;
  const explicitStartedAtMs = Number(details.startedAtMs);
  const startedAtMs = Number.isFinite(explicitStartedAtMs)
    ? explicitStartedAtMs
    : Number(pending?.startedAtMs);
  if (!Number.isFinite(startedAtMs) || startedAtMs <= 0) return null;
  terminalRemovalLatencyPending.delete(key);
  const nowMs = terminalRemovalLatencyNowMs();
  const durationMs = Math.max(0, nowMs - startedAtMs);
  const sample = {
    ts: new Date(nowMs).toISOString(),
    targetKind: String(targetKind || pending?.targetKind || 'target'),
    target: String(target || pending?.target || ''),
    origin: String(details.origin || pending?.origin || 'unknown'),
    reason: String(details.reason || ''),
    durationMs: Number(durationMs.toFixed(1)),
    startedAtMs,
    removedAtMs: nowMs,
  };
  if (Number.isFinite(Number(details.eventAtMs))) sample.eventAtMs = Number(details.eventAtMs);
  if (details.eventType) sample.eventType = String(details.eventType);
  if (Number.isFinite(Number(details.closeCode))) sample.closeCode = Number(details.closeCode);
  if (typeof details.wasClean === 'boolean') sample.wasClean = details.wasClean;
  terminalRemovalLatencySamples.push(sample);
  if (terminalRemovalLatencySamples.length > terminalRemovalLatencySampleLimit) {
    terminalRemovalLatencySamples.splice(0, terminalRemovalLatencySamples.length - terminalRemovalLatencySampleLimit);
  }
  recordJsDebugEvent('terminal_removal', {
    message: `${sample.targetKind} ${sample.target} removed after ${sample.durationMs}ms from ${sample.origin}`,
    durationMs: sample.durationMs,
    targetKind: sample.targetKind,
    target: sample.target,
    origin: sample.origin,
    reason: sample.reason,
    eventType: sample.eventType,
    closeCode: sample.closeCode,
    wasClean: sample.wasClean,
  });
  return sample;
}

function completeTerminalRemovalLatencyFromEpochSeconds(targetKind, target, epochSeconds, details = {}) {
  const eventAtMs = Number(epochSeconds) * 1000;
  if (!Number.isFinite(eventAtMs) || eventAtMs <= 0) return null;
  return completeTerminalRemovalLatency(targetKind, target, {
    ...details,
    startedAtMs: eventAtMs,
    eventAtMs,
  });
}

function terminalRemovalLatencySummary() {
  const samples = terminalRemovalLatencySamples.slice();
  const total = samples.reduce((sum, sample) => sum + Number(sample.durationMs || 0), 0);
  const max = samples.reduce((value, sample) => Math.max(value, Number(sample.durationMs || 0)), 0);
  return {
    count: samples.length,
    pending: terminalRemovalLatencyPending.size,
    averageMs: samples.length ? Number((total / samples.length).toFixed(1)) : 0,
    maxMs: Number(max.toFixed(1)),
    last: samples.at(-1) || null,
    samples,
  };
}

function clearJsDebugEvents() {
  jsDebugEvents = [];
  jsDebugEventSeq = 0;
  terminalRemovalLatencyPending.clear();
  terminalRemovalLatencySamples = [];
  clearClientPerfCounters();
  if (typeof clearJsDebugGraphData === 'function') clearJsDebugGraphData();
  if (typeof clearJsDebugServerHistory === 'function') clearJsDebugServerHistory();
  if (jsDebugRenderTimer) {
    clearTimeout(jsDebugRenderTimer);
    jsDebugRenderTimer = null;
  }
  jsDebugRenderForce = false;
  if (typeof renderDebugPanels === 'function') renderDebugPanels({force: true});
}

function scheduleJsDebugPanelRefresh(options = {}) {
  if (!jsDebugCollectionEnabled || typeof refreshDebugPanelsFromEvents !== 'function') return;
  if (options.force === true) jsDebugRenderForce = true;
  if (jsDebugRenderTimer) return;
  jsDebugRenderTimer = setTimeout(() => {
    jsDebugRenderTimer = null;
    const force = jsDebugRenderForce;
    jsDebugRenderForce = false;
    refreshDebugPanelsFromEvents({force});
  }, jsDebugRenderDebounceMs);
}

function installJsDebugEventCapture() {
  if (!jsDebugCollectionEnabled || jsDebugEventCaptureInstalled || !window?.addEventListener) return;
  jsDebugEventCaptureInstalled = true;
  window.addEventListener('error', event => {
    recordJsDebugEvent('error', {
      message: jsDebugErrorText(event.error || event.message),
      source: jsDebugUrlText(event.filename || ''),
      line: Number(event.lineno || 0),
      column: Number(event.colno || 0),
    });
  });
  window.addEventListener('unhandledrejection', event => {
    recordJsDebugEvent('unhandledrejection', {
      message: jsDebugErrorText(event.reason),
    });
  });
}

function enableDebugMode() {
  debugModeEnabled = true;
  installJsDebugEventCapture();
  if (typeof startJsDebugStatsPolling === 'function') startJsDebugStatsPolling();
  scheduleJsDebugPanelRefresh();
}

installJsDebugEventCapture();
installClientPerfLongTaskObserver();

let appViewportOverride = null;
let appMirrorTransform = {scale: 1, tx: 0, ty: 0};

function normalizeAppViewport(value, fallback = null) {
  const source = value && typeof value === 'object' ? value : {};
  const fallbackSource = fallback && typeof fallback === 'object' ? fallback : {};
  const width = Math.max(1, Math.round(Number(source.width ?? source.w ?? fallbackSource.width ?? fallbackSource.w ?? 0) || 0));
  const height = Math.max(1, Math.round(Number(source.height ?? source.h ?? fallbackSource.height ?? fallbackSource.h ?? 0) || 0));
  return {width, height, w: width, h: height};
}

function nativeViewport() {
  const doc = document.documentElement || {};
  const width = Math.max(1, Math.round(Number(window.innerWidth) || Number(doc.clientWidth) || 1)); // static-build-allow-window-viewport
  const height = Math.max(1, Math.round(Number(window.innerHeight) || Number(doc.clientHeight) || 1)); // static-build-allow-window-viewport
  return {width, height, w: width, h: height};
}

function appViewport() {
  return appViewportOverride ? normalizeAppViewport(appViewportOverride, nativeViewport()) : nativeViewport();
}

const MIN_VIEWPORT_WIDTH_PX = 320;
const DEFAULT_VIEWPORT_WIDTH_PX = 1200;
const OFFSCREEN_POSITION_PX = -10000;

function effectiveViewportWidth(viewport = appViewport(), fallback = DEFAULT_VIEWPORT_WIDTH_PX) {
  const width = Number(viewport?.width ?? viewport?.w);
  const fallbackWidth = Number(fallback) || DEFAULT_VIEWPORT_WIDTH_PX;
  return Math.max(MIN_VIEWPORT_WIDTH_PX, width || fallbackWidth);
}

const appViewportBreakpointPx = [1500, 1280, 1100, 1080, 980, 760, 720];

function syncAppViewportBreakpointClasses() {
  const viewport = appViewport();
  const targets = [document.body, appRootElement()].filter(Boolean);
  for (const target of targets) {
    for (const breakpoint of appViewportBreakpointPx) {
      target.classList?.toggle(`app-vw-lte-${breakpoint}`, viewport.width <= breakpoint);
    }
  }
}

function appRootElement() {
  return appRoot || document.getElementById?.('appRoot') || document.body;
}

function appOverlayRootElement() {
  const root = appRootElement();
  if (!root || root === document.body) return document.body;
  let overlay = document.getElementById?.('appOverlayRoot');
  if (!overlay) {
    overlay = document.createElement('div');
    overlay.id = 'appOverlayRoot';
    overlay.className = 'app-overlay-root';
    overlay.dataset.shareReplayOverlayRoot = 'true';
  }
  if (overlay.parentElement !== root) root.appendChild(overlay);
  return overlay;
}

function cleanupDetachedPopoverAnchor(anchor, keep = null) {
  const previous = anchor?.__yolomuxDetachedPopover;
  if (previous && previous !== keep) previous.remove();
  if (previous && previous !== keep) anchor.__yolomuxDetachedPopover = null;
}

function cleanupDetachedPopoversWithin(root) {
  if (!root) return;
  const anchors = [root, ...Array.from(root.querySelectorAll?.('*') || [])];
  for (const anchor of anchors) cleanupDetachedPopoverAnchor(anchor);
}

function applyAppRootViewportSize() {
  const root = appRootElement();
  if (!root?.style) return;
  if (!appViewportOverride) {
    root.style.removeProperty('--app-root-width');
    root.style.removeProperty('--app-root-height');
    return;
  }
  const viewport = appViewport();
  root.style.setProperty('--app-root-width', `${viewport.width}px`);
  root.style.setProperty('--app-root-height', `${viewport.height}px`);
}

function setAppViewportOverride(viewport = null) {
  appViewportOverride = viewport ? normalizeAppViewport(viewport) : null;
  applyAppRootViewportSize();
  syncAppViewportBreakpointClasses();
  return appViewport();
}

function appMirrorTransformState() {
  return {
    scale: Math.max(0.0001, Number(appMirrorTransform.scale) || 1),
    tx: Number(appMirrorTransform.tx) || 0,
    ty: Number(appMirrorTransform.ty) || 0,
  };
}

function setAppMirrorTransform(transform = {}) {
  appMirrorTransform = {
    scale: Math.max(0.0001, Number(transform.scale) || 1),
    tx: Number(transform.tx) || 0,
    ty: Number(transform.ty) || 0,
  };
  return appMirrorTransformState();
}

function appSpaceRect(elementOrRect) {
  const rect = elementOrRect?.getBoundingClientRect ? elementOrRect.getBoundingClientRect() : elementOrRect;
  const transform = appMirrorTransformState();
  const left = (Number(rect?.left) || 0) - transform.tx;
  const top = (Number(rect?.top) || 0) - transform.ty;
  const width = Math.max(0, Number(rect?.width) || Math.max(0, (Number(rect?.right) || 0) - (Number(rect?.left) || 0))) / transform.scale;
  const height = Math.max(0, Number(rect?.height) || Math.max(0, (Number(rect?.bottom) || 0) - (Number(rect?.top) || 0))) / transform.scale;
  const mappedLeft = left / transform.scale;
  const mappedTop = top / transform.scale;
  return {
    left: mappedLeft,
    top: mappedTop,
    width,
    height,
    right: mappedLeft + width,
    bottom: mappedTop + height,
  };
}

function appSpacePoint(x, y) {
  const transform = appMirrorTransformState();
  return {
    x: (Number(x) - transform.tx) / transform.scale,
    y: (Number(y) - transform.ty) / transform.scale,
  };
}

function visualPointFromAppSpace(x, y) {
  const transform = appMirrorTransformState();
  return {
    x: (Number(x) * transform.scale) + transform.tx,
    y: (Number(y) * transform.scale) + transform.ty,
  };
}

function agentLabel(kind) {
  const key = String(kind || '').toLowerCase();
  if (key === 'codex') return 'Codex';
  if (key === 'claude') return 'Claude';
  return String(kind || '');
}

const sessionFileLookbackDefaultHours = 24;
const sessionFileLookbackHourValues = Object.freeze([
  0.5, 1, 2, 4, 8, 12, 24, 48, 72, 96, 120, 144, 168, 192, 216, 240, 264, 288, 312, 336,
]);

function normalizeSessionFileLookbackHours(value, fallback = sessionFileLookbackDefaultHours) {
  const parsed = Number(value);
  const candidate = Number.isFinite(parsed) ? parsed : Number(fallback);
  if (sessionFileLookbackHourValues.includes(candidate)) return candidate;
  if (sessionFileLookbackHourValues.includes(Number(fallback))) return Number(fallback);
  return sessionFileLookbackDefaultHours;
}

function sessionFileLookbackLabel(hours) {
  const value = Number(hours);
  if (value < 1) return t('share.duration.minute', {count: Math.round(value * 60)});
  if (value < 24) return tPlural('duration.hour', value);
  return tPlural('duration.day', value / 24);
}

function sessionFileLookbackOptions() {
  return sessionFileLookbackHourValues.map(hours => ({hours, label: sessionFileLookbackLabel(hours)}));
}

// localStorage can throw (privacy mode, blocked, quota) — these swallow failures so a blocked store
// never breaks the page. storageGet returns the raw string (or `fallback` when absent/blocked);
// storageSet coerces to string and no-ops on failure. Every readStored*/writeStored* builds on these.
function storageGet(key, fallback = null) {
  try {
    const value = window.localStorage?.getItem(key);
    return value == null ? fallback : value;
  } catch (_) {
    return fallback;
  }
}

function storageSet(key, value) {
  try {
    window.localStorage?.setItem(key, String(value));
  } catch (_) {}
}

function readStoredInfoLookbackHours() {
  return normalizeSessionFileLookbackHours(storageGet(infoLookbackHoursStorageKey));
}

function writeStoredInfoLookbackHours(hours) {
  const normalized = normalizeSessionFileLookbackHours(hours);
  storageSet(infoLookbackHoursStorageKey, normalized);
  return normalized;
}

let infoSessionFileLookbackHours = readStoredInfoLookbackHours();

function readStoredTabberLookbackHours() {
  return normalizeSessionFileLookbackHours(storageGet(fileExplorerTabberLookbackHoursStorageKey));
}

function writeStoredTabberLookbackHours(hours) {
  const normalized = normalizeSessionFileLookbackHours(hours);
  storageSet(fileExplorerTabberLookbackHoursStorageKey, normalized);
  return normalized;
}

let tabberSessionFileLookbackHours = readStoredTabberLookbackHours();

function sessionStorageGet(key, fallback = null) {
  try {
    const value = window.sessionStorage?.getItem(key);
    return value == null ? fallback : value;
  } catch (_) {
    return fallback;
  }
}

function sessionStorageSet(key, value) {
  try {
    window.sessionStorage?.setItem(key, String(value));
  } catch (_) {}
}

function fileExplorerClosedByUser() {
  return sessionStorageGet(fileExplorerOpenIntentStorageKey) === '0';
}

function rememberFileExplorerOpenIntent(open) {
  sessionStorageSet(fileExplorerOpenIntentStorageKey, open ? '1' : '0');
}

function safeJsonParse(raw, fallback = null) {
  try {
    return raw ? JSON.parse(raw) : fallback;
  } catch (_) {
    return fallback;
  }
}

function readStoredSet(key) {
  const parsed = safeJsonParse(storageGet(key), []);
  return new Set(Array.isArray(parsed) ? parsed.map(String) : []);
}

function readStoredJson(key, fallback = null) {
  return safeJsonParse(storageGet(key), fallback);
}

function readStoredPinnedTabs() {
  const parsed = readStoredJson(pinnedTabsStorageKey, []);
  if (!Array.isArray(parsed)) return [];
  const result = [];
  for (const raw of parsed) {
    const item = String(raw || '').trim();
    if (item && !result.includes(item)) result.push(item);
  }
  return result;
}

function writeStoredPinnedTabs() {
  storageSet(pinnedTabsStorageKey, JSON.stringify(pinnedTabItems));
}

function normalizeFileStateRecord(state) {
  if (!state || typeof state !== 'object') state = {};
  if (!(state.editorTabItems instanceof Set)) state.editorTabItems = new Set();
  if (!(state.ownerSessions instanceof Set)) state.ownerSessions = new Set();
  if (!(state.viewMode instanceof Map)) state.viewMode = new Map();
  if (!(state.previewZoom instanceof Map)) state.previewZoom = new Map();
  if (!Object.prototype.hasOwnProperty.call(state, 'blame')) state.blame = null;
  if (!Object.prototype.hasOwnProperty.call(state, 'conflictDialogOpen')) state.conflictDialogOpen = false;
  return state;
}

function physicalFileIdentityFromPayload(payload) {
  if (!payload || typeof payload !== 'object') return '';
  const explicit = String(payload.file_identity || payload.fileIdentity || '').trim();
  if (explicit) return explicit;
  const fileId = String(payload.file_id || payload.fileId || '').trim();
  if (fileId) return `id:${fileId}`;
  const realpath = String(payload.realpath || payload.realPath || '').trim();
  return realpath ? `realpath:${realpath}` : '';
}

function applyFileIdentityMetadata(state, payload) {
  if (!state || typeof state !== 'object' || !payload || typeof payload !== 'object') return state;
  const realpath = String(payload.realpath || payload.realPath || '').trim();
  const fileId = String(payload.file_id || payload.fileId || '').trim();
  const identity = physicalFileIdentityFromPayload(payload);
  if (realpath) state.realpath = realpath;
  if (fileId) state.fileId = fileId;
  if (identity) state.fileIdentity = identity;
  return state;
}

function registerFileIdentityForPath(path, payload) {
  const normalized = String(path || '').trim();
  const identity = physicalFileIdentityFromPayload(payload);
  if (!normalized || !identity) return '';
  fileIdentityByPath.set(normalized, identity);
  if (!openFilePathByIdentity.has(identity)) openFilePathByIdentity.set(identity, normalized);
  return identity;
}

function primaryOpenPathForFileIdentity(identity) {
  const text = String(identity || '').trim();
  if (!text) return '';
  const mapped = openFilePathByIdentity.get(text);
  if (mapped && openFiles.has(mapped) && fileStateFor(mapped)?.externalMissing !== true) return mapped;
  for (const [path, state] of openFiles.entries()) {
    if (state?.externalMissing === true) continue;
    if (physicalFileIdentityFromPayload(state) === text) {
      openFilePathByIdentity.set(text, path);
      return path;
    }
  }
  openFilePathByIdentity.delete(text);
  return '';
}

function openPathForPhysicalFile(path, payload = null) {
  const identity = registerFileIdentityForPath(path, payload) || fileIdentityByPath.get(path) || physicalFileIdentityFromPayload(payload);
  return primaryOpenPathForFileIdentity(identity);
}

function reassignOpenFileIdentityPath(oldPath, newPath) {
  const identity = fileIdentityByPath.get(oldPath) || physicalFileIdentityFromPayload(fileStateFor(oldPath));
  if (!identity) return;
  fileIdentityByPath.delete(oldPath);
  fileIdentityByPath.set(newPath, identity);
  if (openFilePathByIdentity.get(identity) === oldPath) openFilePathByIdentity.set(identity, newPath);
}

function clearOpenFileIdentityPath(path) {
  const identity = fileIdentityByPath.get(path) || physicalFileIdentityFromPayload(fileStateFor(path));
  fileIdentityByPath.delete(path);
  if (identity && openFilePathByIdentity.get(identity) === path) openFilePathByIdentity.delete(identity);
}

function normalizedFileGitHistory(value) {
  return Array.isArray(value) ? value.filter(item => item && typeof item === 'object' && item.ref) : [];
}

function applyFileGitMetadata(state, payload) {
  if (!state || typeof state !== 'object' || !payload || typeof payload !== 'object') return state;
  applyFileIdentityMetadata(state, payload);
  const gitHistory = normalizedFileGitHistory(payload.git_history);
  state.gitRoot = payload.git_root ? normalizeDirectoryPath(payload.git_root) : '';
  state.gitTracked = payload.git_tracked === true;
  state.gitHistory = gitHistory;
  state.gitHasHistory = payload.git_has_history === true && gitHistory.length > 1;
  return state;
}

function fileStateHasRepo(path, state) {
  const root = state?.gitRoot ? normalizeDirectoryPath(state.gitRoot) : '';
  const normalized = normalizeDirectoryPath(path || '');
  return Boolean(root && normalized && pathIsInsideDirectory(normalized, root));
}

function fileStateHasUsefulGitHistory(state) {
  return state?.gitTracked === true
    && state?.gitHasHistory === true
    && Array.isArray(state.gitHistory)
    && state.gitHistory.length > 1;
}

function ensureFileState(path, defaults = null) {
  if (!path) return null;
  let state = fileState.get(path);
  if (!state) {
    state = defaults && typeof defaults === 'object' ? defaults : {};
    fileState.set(path, state);
  } else if (defaults && typeof defaults === 'object' && state !== defaults) {
    Object.assign(state, defaults);
  }
  return normalizeFileStateRecord(state);
}

function fileStateFor(path) {
  const state = path ? fileState.get(path) : null;
  return state ? normalizeFileStateRecord(state) : null;
}

function setFileState(path, state) {
  if (!path) return null;
  const previous = fileStateFor(path);
  if (previous && previous !== state && state && typeof state === 'object') {
    if (!(state.editorTabItems instanceof Set)) state.editorTabItems = previous.editorTabItems;
    if (!(state.ownerSessions instanceof Set)) state.ownerSessions = previous.ownerSessions;
    if (!(state.viewMode instanceof Map)) state.viewMode = previous.viewMode;
    if (!(state.previewZoom instanceof Map)) state.previewZoom = previous.previewZoom;
    if (!Object.prototype.hasOwnProperty.call(state, 'diffPinnedFromRef')) state.diffPinnedFromRef = previous.diffPinnedFromRef;
    if (!Object.prototype.hasOwnProperty.call(state, 'diffPinnedToRef')) state.diffPinnedToRef = previous.diffPinnedToRef;
    if (!Object.prototype.hasOwnProperty.call(state, 'imageMode')) state.imageMode = previous.imageMode;
    if (!Object.prototype.hasOwnProperty.call(state, 'blame')) state.blame = previous.blame;
    if (!Object.prototype.hasOwnProperty.call(state, 'conflictDialogOpen')) state.conflictDialogOpen = previous.conflictDialogOpen;
    if (!Object.prototype.hasOwnProperty.call(state, 'realpath')) state.realpath = previous.realpath;
    if (!Object.prototype.hasOwnProperty.call(state, 'fileId')) state.fileId = previous.fileId;
    if (!Object.prototype.hasOwnProperty.call(state, 'fileIdentity')) state.fileIdentity = previous.fileIdentity;
  }
  const normalized = normalizeFileStateRecord(state);
  fileState.set(path, normalized);
  registerFileIdentityForPath(path, normalized);
  return normalized;
}

function deleteFileState(path) {
  if (!path) return false;
  clearOpenFileIdentityPath(path);
  return fileState.delete(path);
}

function fileEditorTabItemsForPath(path) {
  return Array.from(fileStateFor(path)?.editorTabItems || []);
}

function fileHasEditorTab(path) {
  return fileEditorTabItemsForPath(path).length > 0;
}

function addFileEditorTabItem(path, item = fileEditorItemFor(path)) {
  const state = ensureFileState(path);
  if (state && item) state.editorTabItems.add(item);
}

function removeFileEditorTabItem(path, item = fileEditorItemFor(path)) {
  fileStateFor(path)?.editorTabItems.delete(item);
}

function fileEditorViewModesForPath(path, create = false) {
  const state = create ? ensureFileState(path) : fileStateFor(path);
  return state?.viewMode || new Map();
}

function normalizedPreviewZoomKey(key) {
  return String(key || 'default');
}

function normalizePreviewZoomState(value) {
  const mode = value?.mode === 'manual' || value?.mode === 'actual' ? value.mode : 'fit';
  const scale = Number.parseFloat(String(value?.scale || ''));
  return {
    mode,
    scale: Number.isFinite(scale) && scale > 0 ? scale : 1,
  };
}

function fileEditorPreviewZoomStateForPath(path, key = 'default') {
  const state = fileStateFor(path);
  return normalizePreviewZoomState(state?.previewZoom?.get(normalizedPreviewZoomKey(key)));
}

function setFileEditorPreviewZoomStateForPath(path, key, zoomState) {
  const state = ensureFileState(path);
  if (!state) return;
  if (!(state.previewZoom instanceof Map)) state.previewZoom = new Map();
  state.previewZoom.set(normalizedPreviewZoomKey(key), normalizePreviewZoomState(zoomState));
}

function resetFileEditorPreviewZoomStateForPath(path, keyPrefix = '') {
  const state = fileStateFor(path);
  if (!(state?.previewZoom instanceof Map)) return false;
  const prefix = normalizedPreviewZoomKey(keyPrefix);
  let changed = false;
  for (const key of Array.from(state.previewZoom.keys())) {
    if (!prefix || key === prefix || key.startsWith(`${prefix}:`)) {
      state.previewZoom.delete(key);
      changed = true;
    }
  }
  return changed;
}

function editorBlameForPath(path) {
  return fileStateFor(path)?.blame || null;
}

function setEditorBlameForPath(path, blame) {
  const state = ensureFileState(path);
  if (state) state.blame = blame || null;
}

function hasEditorBlameForPath(path) {
  return Boolean(editorBlameForPath(path));
}

function fileConflictDialogOpen(path) {
  return fileStateFor(path)?.conflictDialogOpen === true;
}

function setFileConflictDialogOpen(path, open) {
  const state = ensureFileState(path);
  if (state) state.conflictDialogOpen = open === true;
}

// Normalized view of a session's transcript metadata — prevents each call site from re-implementing
// the same `?.[session]?.project?.git?.root` chain differently.
function sessionTranscriptInfo(session) {
  const info = transcriptMeta.sessions?.[session] || {};
  const git = info.project?.git || {};
  return {
    gitRoot: git.root || '',
    gitCwd: git.cwd || '',
    gitBranch: git.branch || '',
    selectedPath: info.selected_pane?.current_path || '',
    info,
  };
}

function repoRootKey(value) {
  return String(value || '').replace(/\/+$/, '');
}

function sessionRepoSummaries(info) {
  return (Array.isArray(info?.project?.repos) ? info.project.repos : [])
    .filter(repo => repo && repo.root)
    .map(repo => ({...repo, root: repoRootKey(repo.root), cwd: repo.cwd || repo.root}));
}

function selectedSessionRepoIndex(session, info) {
  const repos = sessionRepoSummaries(info);
  if (!repos.length) return -1;
  const selectedRoot = repoRootKey(sessionRepoDisplayRoot.get(session));
  const selectedIndex = selectedRoot ? repos.findIndex(repo => repoRootKey(repo.root) === selectedRoot) : -1;
  return selectedIndex >= 0 ? selectedIndex : 0;
}

function selectedSessionRepo(session, info) {
  const repos = sessionRepoSummaries(info);
  const index = selectedSessionRepoIndex(session, info);
  return index >= 0 ? repos[index] : null;
}

function gitFromRepoSummary(repo) {
  if (!repo) return null;
  return {
    root: repo.root || '',
    cwd: repo.cwd || repo.root || '',
    branch: repo.branch || '',
    ahead: repo.ahead,
    behind: repo.behind,
    dirty_count: repo.dirty_count,
    activity_ts: repo.activity_ts,
    activity_source: repo.activity_source || '',
    github_repo: repo.github_repo || null,
    other_branches: repo.other_branches || null,
    worktree: repo.worktree || null,
  };
}

function displayedSessionGit(session, info) {
  const project = info?.project || {};
  const git = project.git || null;
  const repo = selectedSessionRepo(session, info);
  if (!repo) return git;
  if (git && repoRootKey(git.root) === repoRootKey(repo.root)) return git;
  return gitFromRepoSummary(repo);
}

function cycleSessionRepoDisplay(session, info, direction) {
  const repos = sessionRepoSummaries(info);
  if (repos.length < 2) return null;
  const current = selectedSessionRepoIndex(session, info);
  const delta = Number(direction) < 0 ? -1 : 1;
  const next = (Math.max(0, current) + delta + repos.length) % repos.length;
  sessionRepoDisplayRoot.set(session, repos[next].root);
  return repos[next];
}

// Centralized status-line writers: the err/ok pill markup is defined here, not re-inlined at the ~55
// call sites that report a result. Both take already-built (and esc'd) inner HTML.
function statusErr(html) {
  statusEl.innerHTML = `<span class="err">${html}</span>`;
}

function statusOk(html) {
  statusEl.innerHTML = `<span class="ok">${html}</span>`;
}

function localizedHtml(key, params) {
  return esc(t(key, params));
}

function terminalNotConnectedText(session) {
  return t('terminal.connection.notConnected', {session: sessionLabel(session)});
}

function terminalNotConnectedHtml(session) {
  return esc(terminalNotConnectedText(session));
}

function readStoredTabMetaVisible() {
  return storageGet(tabMetaStorageKey) !== '0';  // absent (null) or anything but '0' => visible
}

function writeStoredTabMetaVisible(value) {
  storageSet(tabMetaStorageKey, value ? '1' : '0');
}

// Legacy share/deeplink marker from the old merged YO!info/YO!agent pane.
function normalizedInfoSubTab(value) {
  return value === 'yoagent' ? 'yoagent' : 'info';
}

function readStoredInfoSubTab() {
  return normalizedInfoSubTab(storageGet(infoSubTabStorageKey));
}

function writeStoredInfoSubTab(value) {
  storageSet(infoSubTabStorageKey, normalizedInfoSubTab(value));
}

function readStoredEditorWrap() {
  return storageGet(fileEditorWrapStorageKey) === '1';
}

function writeStoredEditorWrap(value) {
  storageSet(fileEditorWrapStorageKey, value ? '1' : '0');
}

function readStoredEditorLineNumbers() {
  return storageGet(fileEditorLineNumbersStorageKey) === '1';
}

function writeStoredEditorLineNumbers(value) {
  storageSet(fileEditorLineNumbersStorageKey, value ? '1' : '0');
}

function defaultCollapsedPreferenceSections() {
  return new Set(DEFAULT_COLLAPSED_PREFERENCE_SECTION_IDS);
}

function normalizeCollapsedPreferenceSections(values, sections = []) {
  const validIds = new Set(Object.values(PREFERENCE_SECTION_IDS));
  const legacyTitleIds = new Map(Object.entries(LEGACY_PREFERENCE_SECTION_IDS_BY_ENGLISH_TITLE));
  for (const section of sections) {
    const id = String(section?.id || '');
    const title = String(section?.title || '');
    if (validIds.has(id) && title) legacyTitleIds.set(title, id);
  }
  return new Set(Array.from(values || [], value => {
    const text = String(value || '');
    if (validIds.has(text)) return text;
    return legacyTitleIds.get(text) || '';
  }).filter(Boolean));
}

function readStoredCollapsedPreferenceSections() {
  const raw = storageGet(preferencesCollapsedStorageKey);
  if (!raw) return defaultCollapsedPreferenceSections();
  try {
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed)) return defaultCollapsedPreferenceSections();
    return normalizeCollapsedPreferenceSections(parsed);
  } catch (_) {
    return defaultCollapsedPreferenceSections();
  }
}

function writeStoredCollapsedPreferenceSections() {
  storageSet(preferencesCollapsedStorageKey, JSON.stringify(Array.from(collapsedPreferenceSections)));
}

function setCollapsedPreferenceSections(values, options = {}) {
  const previousIds = Array.from(collapsedPreferenceSections || []);
  const next = normalizeCollapsedPreferenceSections(values, options.sections || []);
  const nextIds = Array.from(next);
  collapsedPreferenceSections = next;
  if (options.persist === true && (previousIds.length !== nextIds.length || previousIds.some((id, index) => id !== nextIds[index]))) {
    writeStoredCollapsedPreferenceSections();
  }
  return collapsedPreferenceSections;
}

function cleanDiffRef(value, fallback = '') {
  const text = String(value || '').trim();
  if (!text) return fallback;
  if (/[\x00\r\n]/.test(text)) return fallback;
  return text;
}

function readStoredDiffRef(key, fallback) {
  return cleanDiffRef(storageGet(key), fallback);
}

function writeStoredDiffRefs() {
  storageSet(diffRefFromStorageKey, diffRefFrom);
  storageSet(diffRefToStorageKey, diffRefTo);
  // C6: persist the per-repo overrides alongside the global default.
  try {
    storageSet(diffRefsByRepoStorageKey, JSON.stringify(diffRefsByRepo || {}));
  } catch (_error) {
    storageSet(diffRefsByRepoStorageKey, '{}');
  }
}

function readStoredDiffRefsByRepo() {
  // C6: restore {repoPath: {from, to}}; tolerate corrupt/absent storage by returning an empty map.
  try {
    const parsed = JSON.parse(storageGet(diffRefsByRepoStorageKey) || '{}');
    if (!parsed || typeof parsed !== 'object') return {};
    const result = {};
    for (const [repo, refs] of Object.entries(parsed)) {
      if (typeof repo !== 'string' || !refs || typeof refs !== 'object') continue;
      const from = cleanDiffRef(refs.from, '');
      const to = cleanDiffRef(refs.to, '');
      if (from || to) result[repo] = {from: from || 'HEAD', to: to || 'current'};
    }
    return result;
  } catch (_error) {
    return {};
  }
}

function normalizeFileExplorerTreeDateMode(value) {
  return fileExplorerTreeDateModes.includes(value) ? value : 'none';
}

function readStoredFileExplorerTreeDateMode() {
  const value = storageGet(fileExplorerTreeDateModeStorageKey);
  if (value !== null) return normalizeFileExplorerTreeDateMode(value);
  return storageGet(fileExplorerTreeShowDatesStorageKey) === '1' ? 'date' : 'none';
}

function writeStoredFileExplorerTreeDateMode(value) {
  storageSet(fileExplorerTreeDateModeStorageKey, normalizeFileExplorerTreeDateMode(value));
}

function normalizeSessionFilesSortMode(value) {
  if (value === 'mtime') return 'newest';
  if (value === 'name') return 'az';
  return ['az', 'za', 'newest', 'oldest'].includes(value) ? value : 'newest';
}

function readStoredFileExplorerTreeSortMode() {
  const value = storageGet(fileExplorerTreeSortStorageKey);
  return ['az', 'za', 'newest', 'oldest'].includes(value) ? value : 'az';
}

function writeStoredFileExplorerTreeSortMode(value) {
  storageSet(fileExplorerTreeSortStorageKey, ['az', 'za', 'newest', 'oldest'].includes(value) ? value : 'az');
}

function normalizeStoredFileExplorerIndexedDir(path) {
  const normalized = normalizeDirectoryPath(expandUserPath(path));
  return normalized.startsWith('/') ? normalized : '';
}

function readStoredFileExplorerIndexedDirs() {
  const paths = readStoredJson(fileExplorerIndexedDirsStorageKey, []);
  return new Set((Array.isArray(paths) ? paths : []).map(normalizeStoredFileExplorerIndexedDir).filter(Boolean));
}

function writeStoredFileExplorerIndexedDirs() {
  const paths = Array.from(fileExplorerIndexedDirs || [])
    .map(normalizeStoredFileExplorerIndexedDir)
    .filter(Boolean)
    .sort((left, right) => left.localeCompare(right));
  storageSet(fileExplorerIndexedDirsStorageKey, JSON.stringify(Array.from(new Set(paths))));
}

function nestedSetting(source, path, fallback) {
  let current = source;
  for (const part of String(path || '').split('.')) {
    if (!part) continue;
    if (!current || typeof current !== 'object' || !(part in current)) return fallback;
    current = current[part];
  }
  return current === undefined || current === null ? fallback : current;
}

function settingFallback(path, fallback) {
  if (arguments.length >= 2) return fallback;
  return Object.prototype.hasOwnProperty.call(SETTING_FALLBACKS, path) ? SETTING_FALLBACKS[path] : undefined;
}

function initialSetting(path, fallback) {
  const defaultValue = arguments.length >= 2 ? fallback : settingFallback(path);
  return nestedSetting(clientSettings, path, nestedSetting(clientSettingsDefaults, path, defaultValue));
}

function themeBodyClass(mode) {
  return THEME_CLASS_BY_MODE[mode] || THEME_CLASS_BY_MODE.dark;
}

function themeResolvedBodyClass(mode) {
  return THEME_RESOLVED_CLASS_BY_MODE[mode] || THEME_RESOLVED_CLASS_BY_MODE.dark;
}

function editorThemeBodyClass(mode) {
  return EDITOR_THEME_CLASS_BY_MODE[mode] || EDITOR_THEME_CLASS_BY_MODE.dark;
}

function mergeSettingObjects(base, patch) {
  const result = Array.isArray(base) ? base.slice() : {...(base || {})};
  if (!patch || typeof patch !== 'object' || Array.isArray(patch)) return result;
  for (const [key, value] of Object.entries(patch)) {
    if (value && typeof value === 'object' && !Array.isArray(value) && result[key] && typeof result[key] === 'object' && !Array.isArray(result[key])) {
      result[key] = mergeSettingObjects(result[key], value);
    } else {
      result[key] = Array.isArray(value) ? value.slice() : value;
    }
  }
  return result;
}

function readStoredFileExplorerRootMode() {
  return storageGet(fileExplorerRootModeStorageKey) === 'fixed' ? 'fixed' : 'sync';
}

function writeStoredFileExplorerRootMode(mode) {
  storageSet(fileExplorerRootModeStorageKey, mode === 'sync' ? 'sync' : 'fixed');
}

function normalizeFileExplorerMode(mode) {
  return mode === 'diff' || mode === 'tabber' ? mode : 'files';
}

function fileExplorerModeFromUrlParam(value) {
  const mode = String(value || '').trim().toLowerCase();
  if (mode === 'finder' || mode === 'files') return 'files';
  if (mode === 'differ' || mode === 'diff') return 'diff';
  if (mode === 'tabber') return 'tabber';
  return '';
}

function readStoredFileExplorerMode() {
  const stored = storageGet(fileExplorerModeStorageKey);
  if (stored === 'diff' || stored === 'files') return stored;
  return storageGet(legacyFileExplorerChangesHiddenStorageKey) === '0' ? 'diff' : 'files';
}

function writeStoredFileExplorerMode(mode) {
  storageSet(fileExplorerModeStorageKey, normalizeFileExplorerMode(mode));
}

function normalizeEditorSchemeId(value) {
  const normalized = String(value || '').trim().toLowerCase();
  if (normalized === 'light' || normalized === 'white') return defaultLightEditorScheme;
  const legacySchemePrefix = ['vs', 'code'].join('');
  const aliases = {
    [`${legacySchemePrefix}-dark-plus`]: 'popular-ide-dark-plus',
    [`${legacySchemePrefix}-light-plus`]: 'popular-ide-light-plus',
  };
  const id = aliases[normalized] || normalized;
  return EDITOR_SCHEMES[id] ? id : defaultEditorScheme;
}

function normalizeGlobalThemeMode(value = globalThemeMode) {
  // Default to the LIVE globalThemeMode (like resolvedGlobalThemeMode) so a no-arg call reflects the
  // current theme — calling it with no argument used to fall through to defaultGlobalTheme ('dark'),
  // which made the View -> Theme menu always mark Dark as active regardless of the real theme.
  const normalized = String(value || '').trim().toLowerCase();
  return ['system', 'dark', 'light'].includes(normalized) ? normalized : defaultGlobalTheme;
}

function globalThemeMediaQuery() {
  try { return window.matchMedia?.('(prefers-color-scheme: dark)') || null; }
  catch (_) { return null; }
}

function systemPrefersDarkTheme() {
  const query = globalThemeMediaQuery();
  return query ? query.matches === true : true;
}

function normalizeResolvedGlobalThemeMode(value = '') {
  const normalized = String(value || '').trim().toLowerCase();
  return normalized === 'dark' || normalized === 'light' ? normalized : '';
}

function resolvedGlobalThemeMode(mode = globalThemeMode) {
  const normalized = normalizeGlobalThemeMode(mode);
  if (shareViewMode && normalized === 'system') {
    const shareResolved = normalizeResolvedGlobalThemeMode(shareResolvedGlobalThemeMode);
    if (shareResolved) return shareResolved;
  }
  if (normalized === 'system') return systemPrefersDarkTheme() ? 'dark' : 'light';
  return normalized;
}

function globalThemeIsDark(mode = globalThemeMode) {
  return resolvedGlobalThemeMode(mode) === 'dark';
}

function globalThemeLabel(mode = globalThemeMode) {
  const normalized = normalizeGlobalThemeMode(mode);
  if (normalized === 'system') return t('pref.appearance.theme.systemResolved', {
    resolved: t(`pref.appearance.theme.${resolvedGlobalThemeMode(mode)}`),
  });
  return t(`pref.appearance.theme.${normalized}`);
}

function nextGlobalThemeMode(mode = globalThemeMode) {
  const normalized = normalizeGlobalThemeMode(mode);
  if (normalized === 'system') return 'dark';
  if (normalized === 'dark') return 'light';
  return 'system';
}

function normalizeTerminalThemeMode(value) {
  const normalized = String(value || '').trim().toLowerCase();
  return ['dark', 'light', 'follow-app'].includes(normalized) ? normalized : defaultTerminalTheme;
}

function resolvedTerminalThemeMode(mode = terminalThemeMode, appMode = globalThemeMode) {
  const normalized = normalizeTerminalThemeMode(mode);
  return normalized === 'follow-app' ? resolvedGlobalThemeMode(appMode) : normalized;
}

function terminalThemeForGlobalTheme(mode = globalThemeMode) {
  const theme = TERMINAL_THEMES[resolvedTerminalThemeMode(terminalThemeMode, mode)] || TERMINAL_THEMES.dark;
  return {...theme};
}

// on a WHITE (light) terminal, agents emit 24-bit truecolor escapes tuned for a dark
// terminal that render faint on white. xterm's minimumContrastRatio auto-darkens ANY text color
// (including app 24-bit colors) against the bg.
// the DARK terminal used to keep 1 (no adjustment), which left low-contrast cells alone — so
// an agent composer that draws light text on an ANSI-white box (Codex's input, ~contrast 1) was
// white-on-white. Use a moderate 3 for dark: enough to force that composer to a readable foreground,
// low enough that intentionally-dim dark-palette text (already at/above 3:1) is mostly untouched. Light
// stays at the stricter WCAG-AA 4.5 (faint colors on white need more help).
function terminalMinimumContrastRatio(mode = globalThemeMode) {
  return resolvedTerminalThemeMode(terminalThemeMode, mode) === 'light' ? 4.5 : 3;
}

function normalizeEditorThemeMode(value) {
  const normalized = String(value || '').trim().toLowerCase();
  if (['inherit', 'system', 'global', 'auto', ''].includes(normalized)) return editorThemeInheritMode;
  return normalizeEditorSchemeId(normalized);
}

function normalizeEditorPreviewDisplayMode(value) {
  return String(value || '').trim().toLowerCase() === 'vanilla' ? 'vanilla' : 'theme';
}

function normalizeEditorSchemeForMode(value, dark) {
  const id = normalizeEditorSchemeId(value);
  const scheme = EDITOR_SCHEMES[id];
  if (scheme && scheme.dark === dark) return id;
  return dark ? defaultEditorScheme : defaultLightEditorScheme;
}

function activeEditorScheme() {
  if (fileEditorThemeMode === editorThemeInheritMode) {
    const inherited = configuredEditorSchemeForMode(globalThemeIsDark());
    return EDITOR_SCHEMES[inherited] || EDITOR_SCHEMES[defaultEditorScheme] || EDITOR_SCHEMES.dark;
  }
  return EDITOR_SCHEMES[normalizeEditorSchemeId(fileEditorThemeMode)] || EDITOR_SCHEMES[defaultEditorScheme] || EDITOR_SCHEMES.dark;
}

function configuredEditorSchemeForMode(dark) {
  const path = dark ? 'appearance.editor_dark_color_scheme' : 'appearance.editor_light_color_scheme';
  const fallback = dark ? defaultEditorScheme : defaultLightEditorScheme;
  return normalizeEditorSchemeForMode(initialSetting(path, fallback), dark);
}

function readStoredEditorThemeMode() {
  return normalizeEditorThemeMode(storageGet(fileEditorThemeModeStorageKey) || editorThemeInheritMode);
}

function writeStoredEditorThemeMode(mode) {
  storageSet(fileEditorThemeModeStorageKey, normalizeEditorThemeMode(mode));
}

function readStoredEditorPreviewDisplayMode() {
  return normalizeEditorPreviewDisplayMode(storageGet(fileEditorPreviewDisplayModeStorageKey) || 'theme');
}

function writeStoredEditorPreviewDisplayMode(mode) {
  storageSet(fileEditorPreviewDisplayModeStorageKey, normalizeEditorPreviewDisplayMode(mode));
}

function readConfiguredEditorScheme() {
  return normalizeEditorThemeMode(readStoredEditorThemeMode());
}

function syncPressedButton(button, active, options = {}) {
  if (!button) return;
  const activeClass = options.activeClass || 'active';
  button.classList.toggle(activeClass, active);
  button.setAttribute('aria-pressed', active ? 'true' : 'false');
  const label = active ? options.labelOn : options.labelOff;
  if (label) {
    button.title = label;
    button.setAttribute('aria-label', label);
  }
}

function syncFileExplorerHiddenButton(button) {
  syncPressedButton(button, fileExplorerShowHidden, {
    labelOn: t('finder.toolbar.hideHidden'),
    labelOff: t('finder.toolbar.hidden'),
  });
}

function fileExplorerTreeDateModeLabel(mode = fileExplorerTreeDateMode) {
  const normalized = normalizeFileExplorerTreeDateMode(mode);
  return t(`finder.dateMode.${normalized}`);
}

function fileExplorerTreeDateModeButtonLabel(mode = fileExplorerTreeDateMode) {
  const normalized = normalizeFileExplorerTreeDateMode(mode);
  return normalized === 'none' ? t('finder.dateMode.date') : fileExplorerTreeDateModeLabel(normalized);
}

function fileExplorerTreeDateModeTitle(mode = fileExplorerTreeDateMode) {
  return t('finder.dateMode.title', {
    mode: fileExplorerTreeDateModeLabel(mode),
    none: fileExplorerTreeDateModeLabel('none'),
    date: fileExplorerTreeDateModeLabel('date'),
    relative: fileExplorerTreeDateModeLabel('relative'),
  });
}

function syncFileExplorerTreeDateButton(button) {
  if (!button) return;
  const mode = normalizeFileExplorerTreeDateMode(fileExplorerTreeDateMode);
  const active = mode !== 'none';
  button.classList.toggle(CLS.active, active);
  button.dataset.dateMode = mode;
  button.setAttribute('aria-pressed', active ? 'true' : 'false');
  button.textContent = fileExplorerTreeDateModeButtonLabel(mode);
  const label = fileExplorerTreeDateModeTitle(mode);
  button.title = label;
  button.setAttribute('aria-label', label);
}

function syncFileExplorerTreeDateButtons(scope = document) {
  for (const button of scope.querySelectorAll?.('[data-file-explorer-tree-dates]') || []) {
    syncFileExplorerTreeDateButton(button);
  }
}

function nextFileExplorerTreeDateMode(mode = fileExplorerTreeDateMode) {
  const normalized = normalizeFileExplorerTreeDateMode(mode);
  const index = fileExplorerTreeDateModes.indexOf(normalized);
  return fileExplorerTreeDateModes[(index + 1) % fileExplorerTreeDateModes.length];
}

function refreshFileExplorerTreeDateModeSurfaces() {
  syncFileExplorerTreeDateButtons();
  if (typeof refreshFileExplorerTrees === 'function') {
    void refreshFileExplorerTrees({preserveExpanded: true, preserveScroll: true});
  }
  if (typeof renderFileExplorerChangesPanels === 'function') renderFileExplorerChangesPanels({force: true});
}

function setFileExplorerTreeDateMode(mode) {
  const next = normalizeFileExplorerTreeDateMode(mode);
  if (next === fileExplorerTreeDateMode) return;
  fileExplorerTreeDateMode = next;
  writeStoredFileExplorerTreeDateMode(fileExplorerTreeDateMode);
  refreshFileExplorerTreeDateModeSurfaces();
  scheduleShareUiStatePublish();
}

function cycleFileExplorerTreeDateMode() {
  setFileExplorerTreeDateMode(nextFileExplorerTreeDateMode());
}

function renderTabMetaToggle() {
  document.body?.classList.toggle('tab-meta-hidden', !tabMetaVisible);
  if (!tabMetaToggle) return;
  syncPressedButton(tabMetaToggle, tabMetaVisible, {
    labelOn: t('menu.view.tabMeta.hide'),
    labelOff: t('menu.view.tabMeta.show'),
  });
}

function toggleTabMetadata() {
  tabMetaVisible = !tabMetaVisible;
  writeStoredTabMetaVisible(tabMetaVisible);
  renderTabMetaToggle();
  renderSessionButtons();
  scheduleTopbarMetricsUpdate();
  scheduleShareUiStatePublish();
}

function recordFocusNavTransition(previousItem, nextItem) {
  if (!nextItem) return;
  if (previousItem && previousItem !== nextItem) recordEditorNav(previousItem);
  recordEditorNav(nextItem);
}

function rememberActivePaneItem(item) {
  if (!item || !itemIsActivePaneTab(item)) return;
  lastActivePaneItem = item;
  if (!isFileExplorerItem(item)) lastActiveNonFileExplorerPaneItem = item;
}

function visualActivePaneItem() {
  if (focusedPanelItem && itemIsActivePaneTab(focusedPanelItem)) return focusedPanelItem;
  if (lastActivePaneItem && itemIsActivePaneTab(lastActivePaneItem)) return lastActivePaneItem;
  return null;
}

function seedVisualActivePaneItem(preferredItems = []) {
  const candidates = [
    ...preferredItems,
    focusedPanelItem,
    focusedTerminal,
    lastFocusedTmuxSession,
    ...activePaneItems(),
  ];
  const item = candidates.find(candidate => candidate && itemIsActivePaneTab(candidate));
  if (item) lastActivePaneItem = item;
  return item || null;
}

function attentionAcknowledgeDelayMsFromOptions(options = {}) {
  return Number.isFinite(Number(options.acknowledgeAgentWindowDelayMs))
    ? Math.max(0, Number(options.acknowledgeAgentWindowDelayMs))
    : (typeof agentWindowActivityAcknowledgeDelayMs === 'number' ? agentWindowActivityAcknowledgeDelayMs : 0);
}

function tmuxWindowUserInteractionIndex(session) {
  const sessionKey = String(session || '').trim();
  if (!sessionKey || typeof document === 'undefined') return null;
  const activeButton = Array.from(document.querySelectorAll('.tmux-window-bar .tmux-window-button.active'))
    .find(button => String(button.dataset.windowSession || '').trim() === sessionKey);
  const activeIndex = tmuxWindowIndexKey(activeButton?.dataset.windowIndex);
  if (activeIndex !== null) return activeIndex;
  const info = transcriptMeta.sessions?.[sessionKey] || null;
  return typeof tmuxWindowCurrentActiveIndex === 'function'
    ? tmuxWindowCurrentActiveIndex(sessionKey, info)
    : null;
}

function acknowledgeTerminalAttentionFromUserAction(session, windowIndex = null, options = {}) {
  const sessionKey = String(session || '').trim();
  if (!sessionKey || !isTmuxSession(sessionKey)) return false;
  const resolvedWindowIndex = windowIndex === null || windowIndex === undefined
    ? tmuxWindowUserInteractionIndex(sessionKey)
    : windowIndex;
  const acknowledgeDelayMs = attentionAcknowledgeDelayMsFromOptions(options);
  let acknowledged = false;
  // The clicked window needs to capture its visual state before the shared prompt acknowledgement
  // makes the model look acknowledged and therefore removes the pause/stop glyph on re-render.
  if (options.acknowledgeAgentWindow !== false && typeof acknowledgeAgentWindowActivity === 'function') {
    acknowledged = acknowledgeAgentWindowActivity(sessionKey, resolvedWindowIndex, {...options, delayMs: acknowledgeDelayMs}) || acknowledged;
  }
  if (options.acknowledgePromptAttention !== false && typeof clearPromptAttentionForSession === 'function') {
    acknowledged = clearPromptAttentionForSession(sessionKey, {...options, delayMs: acknowledgeDelayMs}) || acknowledged;
  }
  return acknowledged;
}

function setFocusedTerminal(session, options = {}) {
  const perf = clientPerfStart('focusSet');
  try {
    return setFocusedTerminalMeasured(session, options);
  } finally {
    clientPerfEnd(perf, {sessions: activeSessions.length, user: options.userInitiated === true ? 1 : 0});
  }
}

function setFocusedTerminalMeasured(session, options = {}) {
  const previousItem = focusedPanelItem;
  const alreadyFocused = focusedTerminal === session && focusedPanelItem === session;
  if (alreadyFocused) {
    rememberActivePaneItem(session);
    if (isTmuxSession(session)) lastFocusedTmuxSession = session;
    if (options.userInitiated === true) {
      dismissAttentionAlertsForSession(session);
      acknowledgeTerminalAttentionFromUserAction(session, null, options);
      if (options.syncFinder !== false) {
        rememberFileExplorerExplicitSyncSession(session);
        scheduleFileExplorerActiveTabSync(session, {explicit: true});
      }
    }
    return;
  }
  if (previousItem !== session) capturePaneViewStateForItemIfPresent(previousItem);
  focusedTerminal = session;
  focusedPanelItem = session;
  rememberActivePaneItem(session);
  clearPendingFileEditorFocusExcept(session);
  if (isTmuxSession(session)) lastFocusedTmuxSession = session;
  dismissAttentionAlertsForSession(session);
  updateSessionButtonStates();
  for (const activeSession of activeSessions) updateTypingIndicator(activeSession);
  updatePanelInactiveOverlays();
  sharePublish('focus', {item: session});
  if (options.userInitiated === true) {
    acknowledgeTerminalAttentionFromUserAction(session, null, options);
    rememberFileExplorerExplicitSyncSession(session);
    scheduleFileExplorerActiveTabSync(session, {explicit: true});
    recordFocusNavTransition(previousItem, session);
  }
  else recordAutoFocusNav(session, previousItem);
}

function clearFocusedTerminal(session) {
  if (focusedTerminal !== session) return;
  focusedTerminal = null;
  focusedPanelItem = null;
  updateSessionButtonStates();
  for (const activeSession of activeSessions) updateTypingIndicator(activeSession);
  updatePanelInactiveOverlays();
}

function setFocusedPanelItem(item, options = {}) {
  const previousItem = focusedPanelItem;
  if (previousItem !== item) capturePaneViewStateForItemIfPresent(previousItem);
  if (focusedTerminal !== item) focusedTerminal = null;
  focusedPanelItem = item;
  rememberActivePaneItem(item);
  clearPendingFileEditorFocusExcept(item);
  if (isTmuxSession(item)) {
    lastFocusedTmuxSession = item;
    dismissAttentionAlertsForSession(item);
  }
  updateSessionButtonStates();
  for (const activeSession of activeSessions) updateTypingIndicator(activeSession);
  updatePanelInactiveOverlays();
  sharePublish('focus', {item});
  if (options.userInitiated === true) {
    if (isTmuxSession(item)) acknowledgeTerminalAttentionFromUserAction(item, null, {...options, preferSummary: true});
    if (isTmuxSession(item)) rememberFileExplorerExplicitSyncSession(item);
    if (isFileEditorItem(item)) {
      activeFile = fileItemPath(item);
      scheduleFileExplorerActiveFileReveal(activeFile);
    }
    const explicitFinderSync = isTmuxSession(item) || isFileEditorItem(item);
    if (!isFileExplorerItem(item)) scheduleFileExplorerActiveTabSync(item, {explicit: explicitFinderSync});
    recordFocusNavTransition(previousItem, item);
  }
  else recordAutoFocusNav(item, previousItem);
}

let autoFocusNavTimer = null;
// an AUTO-FOCUS-driven focus change records back/forward nav "as if clicked", so Back
// returns to where you were. Debounced by a short dwell so rapid auto-focus flapping (focus chasing
// needs-attention) records only the focus that LANDS, not every transient flip. User clicks already
// record immediately (activatePaneTab userInitiated); a back/forward re-activation lands on the item
// already at the stack head, so recordEditorNav's consecutive-dedupe makes this a no-op there.
function recordAutoFocusNav(item, previousItem = null) {
  if (!autoFocusEnabled || !item) return;
  if (autoFocusNavTimer) clearTimeout(autoFocusNavTimer);
  autoFocusNavTimer = setTimeout(() => {
    autoFocusNavTimer = null;
    if (focusedPanelItem === item) recordFocusNavTransition(previousItem, item);
  }, 500);
}

function clearPendingFileEditorFocusExcept(item) {
  for (const pendingItem of Array.from(pendingFileEditorFocus)) {
    if (pendingItem !== item) pendingFileEditorFocus.delete(pendingItem);
  }
}

function focusTerminalWhenAutoFocus(session, delay = 0) {
  if (!autoFocusEnabled) return;
  focusTerminalDom(session, delay);
}

function focusTerminalFromUserAction(session, delay = 0) {
  noteFileExplorerChangesSessionInteraction(session);
  setFocusedTerminal(session, {userInitiated: true});
  focusTerminalDom(session, delay);
}

function focusTerminalDom(session, delay = 0) {
  const run = () => terminals.get(session)?.term?.focus?.();
  if (delay > 0) setTimeout(run, delay);
  else run();
}

function clearFocusForInactiveLayout() {
  if (focusedTerminal && !activeSessions.includes(focusedTerminal)) focusedTerminal = null;
  if (focusedPanelItem && !activeSessions.includes(focusedPanelItem)) focusedPanelItem = null;
  if (lastActivePaneItem && !itemIsActivePaneTab(lastActivePaneItem)) lastActivePaneItem = null;
  if (lastActiveNonFileExplorerPaneItem && !itemIsActivePaneTab(lastActiveNonFileExplorerPaneItem)) lastActiveNonFileExplorerPaneItem = null;
  if (lastFocusedTmuxSession && !activeSessions.includes(lastFocusedTmuxSession)) lastFocusedTmuxSession = null;
}

function terminalPaneIsActive(session) {
  return document.getElementById(`terminal-pane-${session}`)?.classList.contains(CLS.active) === true;
}

function selectPanelOnHover(item) {
  if (!item) return;
  if (!autoFocusEnabled) return;
  if (isTmuxSession(item) && terminalPaneIsActive(item)) {
    setFocusedTerminal(item);
    scheduleFit(item);
    focusTerminalWhenAutoFocus(item, 0);
    return;
  }
  if (focusedPanelItem === item) return;
  setFocusedPanelItem(item);
}

function updatePanelInactiveOverlays() {
  const activeItem = visualActivePaneItem() || seedVisualActivePaneItem();
  for (const [item, panel] of panelNodes.entries()) {
    panel.classList.toggle('focused-pane', item === activeItem);
    panel.classList.toggle('active-pane', item === activeItem);
  }
  // Re-color the active terminal's cursor yellow (and revert the rest) whenever focus moves.
  if (typeof refreshActiveTerminalCursor === 'function') refreshActiveTerminalCursor();
  if (typeof refreshTabberPanelsForFocusChange === 'function') refreshTabberPanelsForFocusChange();
}

function esc(value) {
  return String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}

const disclosureChevronGlyph = '›';
const disclosureTriangleExpandedGlyph = disclosureChevronGlyph;
const disclosureTriangleCollapsedGlyph = disclosureChevronGlyph;

function disclosureTriangleGlyph(expanded) {
  return expanded === true ? disclosureTriangleExpandedGlyph : disclosureTriangleCollapsedGlyph;
}

function disclosureTriangleHtml(expanded, className = '', attrs = '') {
  const classes = ['ui-disclosure-triangle', className].filter(Boolean).join(' ');
  const extraAttrs = attrs ? ` ${attrs}` : '';
  return `<span class="${esc(classes)}" data-disclosure-expanded="${expanded === true ? 'true' : 'false'}" aria-hidden="true"${extraAttrs}>${esc(disclosureTriangleGlyph(expanded))}</span>`;
}

function setDisclosureTriangleElement(element, expanded) {
  if (!element) return;
  element.classList?.add?.('ui-disclosure-triangle');
  element.dataset.disclosureExpanded = expanded === true ? 'true' : 'false';
  element.setAttribute?.('aria-hidden', 'true');
  element.textContent = disclosureTriangleGlyph(expanded);
}

function stripTrailingEllipsisText(value) {
  return String(value ?? '').replace(/\s*(?:\.{3}|…)+\s*$/u, '').trimEnd();
}

function movingEllipsisHtml(className = '') {
  const classes = ['moving-ellipsis', className].filter(Boolean).join(' ');
  return `<span class="${esc(classes)}" aria-hidden="true"><span>.</span><span>.</span><span>.</span></span>`;
}

function textWithMovingEllipsisHtml(value, className = '') {
  return `${esc(stripTrailingEllipsisText(value))}${movingEllipsisHtml(className)}`;
}

const searchRankWeights = Object.freeze({
  perChar: 8,
  contiguous: 10,
  contiguousSubstring: 30000,
  wordStart: 6,
  gapPenalty: 0.2,
  haystackLengthPenalty: 0.01,
  anchorPrimary: 20000,
  anchorSecondary: 12000,
  fieldIndexPenalty: 20,
  domainPrior: {
    files: {file: 6000, pane: 3000, command: 0, setting: 0},
    command: {pane: 6000, command: 3000, setting: 3000, file: 0},
  },
  fileNamePrefix: 3500,
  fileNameContains: 1800,
  fileNameSubsequence: 600,
  finderAlias: 25000,
  finderAliasFilesMode: 2500,
  paneExactIdentifier: 30000,
  recentSelectionBase: 1000,
  recencyCap: 900,
  recencyHalfLifeSeconds: 7 * 24 * 60 * 60,
  repoAffinity: 400,
  mixWindow: 8,
  mixSecondarySlots: 4,
  mixFirstSecondaryIndex: 2,
  mixSecondaryStep: 2,
});

function fuzzySubsequenceMatch(query, text) {
  const needle = String(query || '').toLowerCase().replace(/\s+/g, '');
  const haystack = String(text || '').toLowerCase();
  if (!needle) return {score: 0, indexes: []};
  let position = 0;
  let previousIndex = -1;
  let score = 0;
  const indexes = [];
  for (const char of needle) {
    const index = haystack.indexOf(char, position);
    if (index < 0) return null;
    const previousChar = haystack[index - 1] || '';
    const contiguous = previousIndex >= 0 && index === previousIndex + 1;
    const wordStart = index === 0 || /[\s/_:.-]/.test(previousChar);
    score += searchRankWeights.perChar;
    if (contiguous) score += searchRankWeights.contiguous;
    if (wordStart) score += searchRankWeights.wordStart;
    score -= Math.max(0, index - position) * searchRankWeights.gapPenalty;
    previousIndex = index;
    position = index + 1;
    indexes.push(index);
  }
  if (needle.length >= 3 && haystack.includes(needle)) score += searchRankWeights.contiguousSubstring;
  return {score: score - Math.max(0, haystack.length - needle.length) * searchRankWeights.haystackLengthPenalty, indexes};
}

function fuzzySubsequenceScore(query, text) {
  const match = fuzzySubsequenceMatch(query, text);
  return match ? match.score : Number.NEGATIVE_INFINITY;
}

function fuzzyCanonicalPrefixText(value) {
  return String(value || '').toLowerCase().replace(/[^a-z0-9]+/g, '');
}

function fuzzyFieldStartsWithQuery(query, text) {
  const needle = fuzzyCanonicalPrefixText(query);
  return Boolean(needle) && fuzzyCanonicalPrefixText(text).startsWith(needle);
}

function focusPanelSearchInput(panel, inputSelector, options = {}) {
  const panelSelector = String(options.panelSelector || '');
  const root = panel && panel.isConnected !== false
    ? panel
    : (panelSelector
      ? (Array.from(document.querySelectorAll(panelSelector)).find(candidate => candidate.offsetParent !== null) || document.querySelector(panelSelector))
      : null);
  const search = root?.querySelector?.(inputSelector);
  if (!search) return false;
  search.focus?.({preventScroll: true});
  if (options.select === true) search.select?.();
  else {
    const position = String(search.value || '').length;
    search.setSelectionRange?.(position, position);
  }
  return true;
}

function fuzzySearchScore(query, fields) {
  const tokens = String(query || '').trim().split(/\s+/).filter(Boolean);
  if (!tokens.length) return 0;
  const values = (Array.isArray(fields) ? fields : [fields]).map(value => String(value || '')).filter(Boolean);
  if (!values.length) return Number.NEGATIVE_INFINITY;
  let total = 0;
  for (const token of tokens) {
    let best = Number.NEGATIVE_INFINITY;
    for (const [index, value] of values.entries()) {
      let fieldScore = fuzzySubsequenceScore(token, value);
      if (Number.isFinite(fieldScore) && fuzzyFieldStartsWithQuery(token, value)) {
        fieldScore += index === 0 ? searchRankWeights.anchorPrimary : searchRankWeights.anchorSecondary;
      }
      if (Number.isFinite(fieldScore)) best = Math.max(best, fieldScore - index * searchRankWeights.fieldIndexPenalty);
    }
    if (!Number.isFinite(best)) return Number.NEGATIVE_INFINITY;
    total += best;
  }
  return total;
}

function fuzzyHighlightHtml(query, text) {
  const value = String(text ?? '');
  // Highlight EVERY query token's subsequence match, not just the first — mirrors fuzzySearchScore, which
  // scores all tokens. So "pa exploration" highlights both "PA" and "exploration", not only "pa".
  const tokens = String(query || '').trim().split(/\s+/).filter(Boolean);
  const indexes = new Set();
  for (const token of tokens) {
    const match = fuzzySubsequenceMatch(token, value);
    if (match) for (const matchIndex of match.indexes) indexes.add(matchIndex);
  }
  if (!indexes.size) return esc(value);
  const chars = Array.from(value);
  const parts = [];
  let index = 0;
  while (index < chars.length) {
    if (!indexes.has(index)) {
      parts.push(esc(chars[index]));
      index += 1;
      continue;
    }
    const start = index;
    while (index < chars.length && indexes.has(index)) index += 1;
    parts.push(`<mark class="fuzzy-match">${esc(chars.slice(start, index).join(''))}</mark>`);
  }
  return parts.join('');
}

function restoreElementScrollPosition(element, scrollTop, scrollLeft) {
  if (!element) return;
  element.scrollTop = scrollTop;
  element.scrollLeft = scrollLeft;
  requestAnimationFrame(() => {
    element.scrollTop = scrollTop;
    element.scrollLeft = scrollLeft;
  });
}

function replaceHtmlPreservingScroll(element, html) {
  if (!element) return;
  const scrollTop = element.scrollTop || 0;
  const scrollLeft = element.scrollLeft || 0;
  element.innerHTML = html;
  restoreElementScrollPosition(element, scrollTop, scrollLeft);
}

function wsUrl(session) {
  const scheme = location.protocol === 'https:' ? 'wss:' : 'ws:';
  if (shareViewMode) {
    const params = new URLSearchParams({session, token: shareToken, viewer: shareViewerId});
    return `${scheme}//${location.host}/ws/share-view?${params.toString()}`;
  }
  const params = new URLSearchParams({session, client: shareClientId});
  return `${scheme}//${location.host}/ws?${params.toString()}`;
}

function renderTransportWarning() {
  if (!httpsWarning) return;
  const secure = location.protocol === 'https:';
  httpsWarning.hidden = secure;
  if (secure) return;
  const port = location.port || '9998';
  const selfSigned = `python3 yolomux.py --port ${port} --self-signed`;
  const cert = `python3 yolomux.py --port ${port} --cert /path/fullchain.pem --key /path/privkey.pem`;
  httpsWarning.dataset.tip = t('app.noHttpsDetail', {selfSigned, cert});
  httpsWarning.setAttribute('aria-label', httpsWarning.dataset.tip);
  httpsWarning.tabIndex = 0;
}

function stripTerminalQueryResponses(data) {
  return String(data)
    .replace(/\x1b\[[?>]?[0-9;]*c/g, '')
    .replace(/\x1bP[>|!][^\x1b]*(?:\x1b\\|\x9c)/g, '');
}

const terminalLinkPattern = /(?:https?:\/\/|file:\/\/|www\.)[^\s<>"'`]+/gi;
const terminalFileReferencePattern = /(^|[\s([{<"'`])((?:(?:~\/|\.{1,2}\/|\/)?[A-Za-z0-9._@%+=-]+(?:\/[A-Za-z0-9._@%+=-]+)+)|(?:(?:~\/|\.{1,2}\/|\/)?[A-Za-z0-9._@%+=-]+\.[A-Za-z0-9][A-Za-z0-9+_-]{0,31}))(?::([1-9]\d{0,6}))?/g;
const terminalWrappedUrlMaxRows = 8;
const terminalLinkClosePairs = [
  [')', '('],
  [']', '['],
  ['}', '{'],
];

function countChar(value, char) {
  let count = 0;
  for (const item of value) {
    if (item === char) count += 1;
  }
  return count;
}

function trimTerminalLinkCandidate(value) {
  let text = String(value || '').replace(/^[<("'`]+/, '');
  let changed = true;
  while (changed && text) {
    changed = false;
    const trimmed = text.replace(/[.,;:!?"'`>]+$/, '');
    if (trimmed !== text) {
      text = trimmed;
      changed = true;
    }
    for (const [closeChar, openChar] of terminalLinkClosePairs) {
      if (text.endsWith(closeChar) && countChar(text, closeChar) > countChar(text, openChar)) {
        text = text.slice(0, -1);
        changed = true;
      }
    }
  }
  return text;
}

function normalizeTerminalLink(value) {
  const text = trimTerminalLinkCandidate(value);
  if (!text) return '';
  if (/^www\./i.test(text)) return `https://${text}`;
  return text;
}

function terminalRangesOverlap(leftStart, leftEnd, rightStart, rightEnd) {
  return leftStart < rightEnd && rightStart < leftEnd;
}

function terminalTextUrlReferences(lineText, rangeForOffsets, y = null) {
  const refs = [];
  terminalLinkPattern.lastIndex = 0;
  for (const match of lineText.matchAll(terminalLinkPattern)) {
    const raw = match[0] || '';
    const text = trimTerminalLinkCandidate(raw);
    if (!text) continue;
    const startIndex = (match.index || 0) + raw.indexOf(text);
    const endIndex = startIndex + text.length;
    const range = rangeForOffsets(startIndex, endIndex);
    if (!range) continue;
    if (Number.isFinite(y) && (range.start.y > y || range.end.y < y)) continue;
    refs.push({
      type: 'url',
      text,
      href: normalizeTerminalLink(text),
      range,
      startIndex,
      endIndex,
    });
  }
  return refs;
}

function terminalTextFileReferences(lineText, rangeForOffsets, y = null, excludedRanges = []) {
  const refs = [];
  terminalFileReferencePattern.lastIndex = 0;
  for (const match of lineText.matchAll(terminalFileReferencePattern)) {
    const prefix = match[1] || '';
    const path = match[2] || '';
    if (!path || /^[a-z][a-z0-9+.-]*:/i.test(path)) continue;
    const line = Number(match[3] || 0);
    const startIndex = (match.index || 0) + prefix.length;
    const endIndex = startIndex + path.length + (match[3] ? match[3].length + 1 : 0);
    if (excludedRanges.some(range => terminalRangesOverlap(startIndex, endIndex, range.startIndex, range.endIndex))) continue;
    const range = rangeForOffsets(startIndex, endIndex);
    if (!range) continue;
    if (Number.isFinite(y) && (range.start.y > y || range.end.y < y)) continue;
    refs.push({
      type: 'file',
      text: line ? `${path}:${line}` : path,
      path,
      line: line || null,
      range,
      startIndex,
      endIndex,
    });
  }
  return refs;
}

function terminalTextReferences(lineText, rangeForOffsets, y = null) {
  const urls = terminalTextUrlReferences(lineText, rangeForOffsets, y);
  const files = terminalTextFileReferences(lineText, rangeForOffsets, y, urls);
  return [...urls, ...files].sort((a, b) => a.range.start.y - b.range.start.y || a.range.start.x - b.range.start.x);
}

function terminalTextLinks(lineText, rangeForOffsets, y = null) {
  return terminalTextReferences(lineText, rangeForOffsets, y);
}

function terminalLineLinks(lineText, y) {
  return terminalTextLinks(lineText, (startIndex, endIndex) => ({
    start: {x: startIndex + 1, y},
    end: {x: endIndex, y},
  }));
}

function terminalBufferLineText(line) {
  return line?.translateToString?.(true) || '';
}

// did `line` fill the terminal to its right edge? translateToString(true) trims trailing
// blanks, so a row whose printed text reaches `cols` had a non-blank last cell — evidence the content
// was CLIPPED at the edge and wrapped, not that it merely happened to end at the row. Used to gate the
// hanging-URL stitch: a complete URL ending well short of the edge (e.g. `See https://x.com`) is NOT a
// clipped URL and must not absorb the indented next row. cols<=0 (unknown width) → treat as not clipped.
function terminalRowReachesRightEdge(line, cols) {
  if (!Number.isFinite(cols) || cols <= 0) return false;
  return terminalBufferLineText(line).length >= Math.max(1, cols - 1);
}

// does the joined group text end mid-URL? True when the LAST url token reaches the very end of
// the string (no trailing whitespace/terminator). Used to decide whether to stitch a hanging-indent
// continuation row onto the group — only EXTEND a url token that runs off the row's right edge.
function terminalTailIsUnterminatedUrl(text) {
  if (!text) return false;
  terminalLinkPattern.lastIndex = 0;
  let last = null;
  for (const match of text.matchAll(terminalLinkPattern)) last = match;
  if (!last) return false;
  return last.index + last[0].length === text.length;
}

function terminalRowStartsNewUrlToken(text) {
  return /^(?:https?:\/\/|file:\/\/|www\.)/i.test(String(text || ''));
}

// a row shaped like a hanging-indent continuation — optional leading whitespace, then a URL-valid char
// (not a quote/bracket). Returns {indent, text} with the indent stripped, or null. isWrapped rows are
// not hanging continuations (they are real terminal soft-wraps and are handled by the isWrapped sweep).
function terminalRowHangingShape(buffer, index) {
  const line = buffer.getLine(index);
  if (!line || line.isWrapped === true) return null;
  const raw = terminalBufferLineText(line);
  const match = /^(\s*)([^\s<>"'`])/.exec(raw);
  if (!match) return null;
  const text = raw.slice(match[1].length);
  if (!text || terminalRowStartsNewUrlToken(text)) return null;
  return {indent: match[1].length, text};
}

// row `index` continues the URL printed on row `index - 1` — its own row shape is a hanging
// indent AND the previous row's tail is an unterminated url token. Gates tightly so ordinary indented
// prose under a line that merely happens to end at a URL is not merged.
// ALSO require the previous row to reach the terminal's right edge, proving the URL was
// clipped/hard-wrapped. Without this, a complete URL at end-of-line falsely swallows the next row.
function terminalRowIsHangingUrlContinuation(buffer, index, cols, depth = 0) {
  if (depth >= terminalWrappedUrlMaxRows) return false;
  const shape = terminalRowHangingShape(buffer, index);
  if (!shape) return false;
  const prev = buffer.getLine(index - 1);
  if (!prev) return false;
  if (!terminalRowReachesRightEdge(prev, cols)) return false;
  return terminalTailIsUnterminatedUrl(terminalBufferLineText(prev))
    || terminalRowIsHangingUrlContinuation(buffer, index - 1, cols, depth + 1);
}

function terminalWrappedLineGroup(term, y) {
  const buffer = term.buffer?.active;
  if (!buffer?.getLine) return null;
  // terminal width gates the hanging-URL stitch (a clipped URL fills to the right edge).
  const cols = Number(term.cols) || 0;
  const requested = Math.max(0, y - 1);
  if (!buffer.getLine(requested)) return null;
  // Walk back to the logical line's first row: over terminal soft-wraps (isWrapped) AND over
  // hanging-indent URL continuations (agent-hard-wrapped URLs whose continuation is its own
  // non-wrapped, indented row). So querying ANY row of the wrapped URL yields the same full group.
  let start = requested;
  for (;;) {
    if (start > 0 && buffer.getLine(start)?.isWrapped === true) { start -= 1; continue; }
    if (start > 0 && terminalRowIsHangingUrlContinuation(buffer, start, cols)) { start -= 1; continue; }
    break;
  }
  // Forward pass from start. Include soft-wrap rows (indent 0) and, while the joined text still ends
  // mid-URL, hanging-indent continuation rows (indent stripped for link matching, but recorded so the
  // underline maps back to the row's REAL columns). Stop at the first row that is neither.
  const rows = [];
  let offset = 0;
  let joined = '';
  let index = start;
  for (;;) {
    let text;
    let indent = 0;
    if (index === start) {
      text = terminalBufferLineText(buffer.getLine(index));
    } else if (buffer.getLine(index)?.isWrapped === true) {
      text = terminalBufferLineText(buffer.getLine(index));
    } else if (terminalTailIsUnterminatedUrl(joined) && terminalRowReachesRightEdge(buffer.getLine(index - 1), cols)) {
      const shape = terminalRowHangingShape(buffer, index);
      if (!shape) break;
      indent = shape.indent;
      text = shape.text;
    } else {
      break;
    }
    rows.push({y: index + 1, text, indent, start: offset, end: offset + text.length});
    offset += text.length;
    joined += text;
    index += 1;
    if (rows.length >= terminalWrappedUrlMaxRows) break;
    if (!buffer.getLine(index)) break;
  }
  return {text: joined, rows};
}

function terminalWrappedOffsetPosition(group, offset, endPosition = false) {
  const target = endPosition ? Math.max(0, offset - 1) : offset;
  const row = group.rows.find(candidate => target >= candidate.start && target < candidate.end) || group.rows[group.rows.length - 1];
  if (!row) return null;
  // A stitched continuation row had `indent` leading spaces stripped before joining, so its real
  // terminal column is shifted right by that indent.
  return {x: Math.max(1, target - row.start + 1 + (row.indent || 0)), y: row.y};
}

function terminalWrappedRange(group, startIndex, endIndex) {
  const start = terminalWrappedOffsetPosition(group, startIndex, false);
  const end = terminalWrappedOffsetPosition(group, endIndex, true);
  if (!start || !end) return null;
  return {start, end};
}

function terminalWrappedLineLinks(term, y) {
  const group = terminalWrappedLineGroup(term, y);
  if (!group) return [];
  if (group.rows.length === 1) return terminalLineLinks(group.text, y);
  return terminalTextLinks(group.text, (startIndex, endIndex) => terminalWrappedRange(group, startIndex, endIndex), y);
}

function terminalWrappedLineReferences(term, y) {
  const group = terminalWrappedLineGroup(term, y);
  if (!group) return [];
  if (group.rows.length === 1) return terminalTextReferences(group.text, (startIndex, endIndex) => ({
    start: {x: startIndex + 1, y},
    end: {x: endIndex, y},
  }), y);
  return terminalTextReferences(group.text, (startIndex, endIndex) => terminalWrappedRange(group, startIndex, endIndex), y);
}

function terminalReferenceXtermLink(reference) {
  if (!reference?.range) return null;
  return {
    range: reference.range,
    text: reference.text || reference.href || '',
    activate: () => {},
    decorations: {underline: true, pointerCursor: false},
  };
}

async function terminalReferenceProviderLinks(session, term, y) {
  const refs = terminalWrappedLineReferences(term, y);
  const links = refs.filter(ref => ref.type === 'url').map(terminalReferenceXtermLink).filter(Boolean);
  const fileRefs = refs.filter(ref => ref.type === 'file');
  if (!fileRefs.length) return links;
  const fileTargets = await Promise.all(fileRefs.map(ref => terminalFileReferenceTarget(session, ref, {fresh: false, user: true})));
  fileRefs.forEach((ref, index) => {
    if (fileTargets[index]) {
      const link = terminalReferenceXtermLink(ref);
      if (link) links.push(link);
    }
  });
  return links.sort((a, b) => a.range.start.y - b.range.start.y || a.range.start.x - b.range.start.x);
}

const TERMINAL_FILE_UNDERLINE_REFRESH_MS = 1700;

function terminalFileReferenceViewportSignature(term) {
  return [
    Math.max(0, Math.floor(Number(term?.cols || 0))),
    Math.max(0, Math.floor(Number(term?.rows || 0))),
    Math.max(0, Math.floor(Number(term?.buffer?.active?.viewportY || 0))),
  ].join(':');
}

function terminalFileReferenceKey(reference) {
  const range = reference?.range || {};
  const start = range.start || {};
  const end = range.end || {};
  return [
    reference?.path || '',
    reference?.line || '',
    reference?.text || '',
    start.x || 0,
    start.y || 0,
    end.x || 0,
    end.y || 0,
  ].join('\x1f');
}

function terminalFileReferenceCacheKey(session, reference) {
  return [
    terminalFileReferenceAbsolutePath(session, reference) || reference?.path || '',
    reference?.line || '',
    reference?.path || '',
    reference?.text || '',
  ].join('\x1f');
}

function terminalVisibleFileReferences(term) {
  const rows = Math.max(0, Math.floor(Number(term?.rows || 0)));
  const viewportY = Math.max(0, Math.floor(Number(term?.buffer?.active?.viewportY || 0)));
  const refs = [];
  const seen = new Set();
  for (let screenRow = 1; screenRow <= rows; screenRow += 1) {
    for (const ref of terminalWrappedLineReferences(term, viewportY + screenRow)) {
      if (ref.type !== 'file') continue;
      const key = terminalFileReferenceKey(ref);
      if (seen.has(key)) continue;
      seen.add(key);
      refs.push(ref);
    }
  }
  return refs;
}

function terminalFileReferenceUnderlineSegments(term, reference) {
  const range = reference?.range;
  if (!range?.start || !range?.end) return [];
  const cols = Math.max(0, Math.floor(Number(term?.cols || 0)));
  const rows = Math.max(0, Math.floor(Number(term?.rows || 0)));
  const viewportY = Math.max(0, Math.floor(Number(term?.buffer?.active?.viewportY || 0)));
  if (!rows) return [];
  const firstY = Math.max(range.start.y, viewportY + 1);
  const lastY = Math.min(range.end.y, viewportY + rows);
  const segments = [];
  for (let y = firstY; y <= lastY; y += 1) {
    let startX = y === range.start.y ? range.start.x : 1;
    let endX = y === range.end.y ? range.end.x : cols;
    startX = Math.max(1, Math.floor(Number(startX) || 1));
    endX = Math.floor(Number(endX) || 0);
    if (cols > 0) endX = Math.min(cols, endX);
    if (endX < startX) continue;
    segments.push({x: startX, y, cells: endX - startX + 1});
  }
  return segments;
}

function terminalFileUnderlineLayer(container) {
  if (!container) return null;
  let layer = container.querySelector?.(':scope > .terminal-file-link-underlines') || null;
  if (!layer) {
    layer = document.createElement('div');
    layer.className = 'terminal-file-link-underlines';
    layer.setAttribute('aria-hidden', 'true');
    container.appendChild(layer);
  }
  return layer;
}

function clearTerminalFileReferenceUnderlines(container) {
  const layer = container?.querySelector?.(':scope > .terminal-file-link-underlines') || null;
  layer?.replaceChildren?.();
  return 0;
}

function updateTerminalFileReferenceUnderlineHover(container, hoverKey = '') {
  const layer = container?.querySelector?.(':scope > .terminal-file-link-underlines') || null;
  const nodes = layer?.querySelectorAll?.('.terminal-file-link-underline') || [];
  for (const node of nodes) {
    node.classList?.toggle?.('terminal-file-link-underline--hover', Boolean(hoverKey) && node.dataset.referenceKey === hoverKey);
  }
}

function renderTerminalFileReferenceUnderlines(term, container, references, options = {}) {
  let renderedNodes = 0;
  const perf = clientPerfStart('terminalUnderlineRender');
  try {
    renderedNodes = renderTerminalFileReferenceUnderlinesMeasured(term, container, references, options);
    return renderedNodes;
  } finally {
    clientPerfEnd(perf, {nodes: renderedNodes, rows: Math.max(0, Number(term?.rows || 0))});
  }
}

function renderTerminalFileReferenceUnderlinesMeasured(term, container, references, options = {}) {
  const layer = terminalFileUnderlineLayer(container);
  if (!layer) return 0;
  const cell = terminalCellDimensions(term, container);
  const screen = terminalScreenElement(container);
  const screenRect = screen?.getBoundingClientRect?.();
  const containerRect = container?.getBoundingClientRect?.();
  const cellWidth = Number(cell.width || 0);
  const cellHeight = Number(cell.height || 0);
  if (!screenRect || !containerRect || !(cellWidth > 0) || !(cellHeight > 0)) {
    return clearTerminalFileReferenceUnderlines(container);
  }
  const viewportY = Math.max(0, Math.floor(Number(term?.buffer?.active?.viewportY || 0)));
  const leftOrigin = screenRect.left - containerRect.left;
  const topOrigin = screenRect.top - containerRect.top;
  const nodes = [];
  for (const reference of references || []) {
    const key = terminalFileReferenceKey(reference);
    for (const segment of terminalFileReferenceUnderlineSegments(term, reference)) {
      const screenRow = segment.y - viewportY;
      if (screenRow < 1) continue;
      const line = document.createElement('div');
      line.className = 'terminal-file-link-underline';
      line.dataset.path = reference.targetPath || reference.path || '';
      line.dataset.text = reference.text || '';
      line.dataset.referenceKey = key;
      if (key && key === options.hoverKey) line.classList.add('terminal-file-link-underline--hover');
      line.style.left = `${leftOrigin + ((segment.x - 1) * cellWidth)}px`;
      line.style.top = `${topOrigin + (screenRow * cellHeight) - 2}px`;
      line.style.width = `${segment.cells * cellWidth}px`;
      nodes.push(line);
    }
  }
  layer.replaceChildren(...nodes);
  return nodes.length;
}

function terminalFileReferenceUnderlineIsActive(session, container) {
  return document.visibilityState !== 'hidden'
    && itemIsActivePaneTab(session)
    && terminalIsVisible(session, container);
}

function installTerminalFileReferenceUnderlines(session, term, container, options = {}) {
  if (!session || !term || !container) return null;
  const targetResolver = options.targetResolver || terminalFileReferenceTarget;
  const isActive = typeof options.isActive === 'function' ? options.isActive : terminalFileReferenceUnderlineIsActive;
  const disposables = [];
  let disposed = false;
  let timer = 0;
  let renderFrame = 0;
  let sequence = 0;
  let lastRenderedViewportSignature = '';
  let existingReferenceKeys = new Set();
  const existingReferenceTargets = new Map();
  let hoverKey = '';

  const active = () => !disposed && Boolean(isActive(session, container));

  const clearInactive = () => {
    sequence += 1;
    if (timer) clearTimeout(timer);
    if (renderFrame) cancelAnimationFrame(renderFrame);
    timer = 0;
    renderFrame = 0;
    existingReferenceKeys = new Set();
    hoverKey = '';
    lastRenderedViewportSignature = '';
    return clearTerminalFileReferenceUnderlines(container);
  };

  const setHoverKey = nextKey => {
    const normalizedKey = nextKey && existingReferenceKeys.has(nextKey) ? nextKey : '';
    if (normalizedKey === hoverKey) return;
    hoverKey = normalizedKey;
    updateTerminalFileReferenceUnderlineHover(container, hoverKey);
  };

  const updateHover = event => {
    if (!active()) return;
    const reference = terminalReferenceAtClientPoint(term, container, event?.clientX, event?.clientY);
    setHoverKey(reference?.type === 'file' ? terminalFileReferenceKey(reference) : '');
  };

  const clearHover = () => setHoverKey('');

  const renderCached = () => {
    if (!active()) return clearInactive();
    const existingRefs = [];
    for (const ref of terminalVisibleFileReferences(term)) {
      const key = terminalFileReferenceCacheKey(session, ref);
      const targetPath = existingReferenceTargets.get(key);
      if (targetPath) existingRefs.push({...ref, targetPath});
    }
    existingReferenceKeys = new Set(existingRefs.map(terminalFileReferenceKey));
    if (hoverKey && !existingReferenceKeys.has(hoverKey)) hoverKey = '';
    const count = renderTerminalFileReferenceUnderlines(term, container, existingRefs, {hoverKey});
    lastRenderedViewportSignature = terminalFileReferenceViewportSignature(term);
    return count;
  };

  const refresh = async () => {
    if (disposed) return 0;
    if (!active()) return clearInactive();
    if (timer) {
      clearTimeout(timer);
      timer = 0;
    }
    const currentSequence = ++sequence;
    const refs = terminalVisibleFileReferences(term);
    if (!refs.length) {
      existingReferenceKeys = new Set();
      hoverKey = '';
      const count = renderTerminalFileReferenceUnderlines(term, container, []);
      lastRenderedViewportSignature = terminalFileReferenceViewportSignature(term);
      return count;
    }
    const targets = await Promise.all(refs.map(ref => (
      Promise.resolve(targetResolver(session, ref, {fresh: false, user: true})).catch(() => null)
    )));
    if (disposed || currentSequence !== sequence) return 0;
    if (!active()) return clearInactive();
    const existingRefs = refs
      .map((ref, index) => {
        const cacheKey = terminalFileReferenceCacheKey(session, ref);
        if (!targets[index]) {
          existingReferenceTargets.delete(cacheKey);
          return null;
        }
        const targetPath = targets[index].path || ref.path || '';
        existingReferenceTargets.set(cacheKey, targetPath);
        return {...ref, targetPath};
      })
      .filter(Boolean);
    existingReferenceKeys = new Set(existingRefs.map(terminalFileReferenceKey));
    if (hoverKey && !existingReferenceKeys.has(hoverKey)) hoverKey = '';
    const count = renderTerminalFileReferenceUnderlines(term, container, existingRefs, {hoverKey});
    lastRenderedViewportSignature = terminalFileReferenceViewportSignature(term);
    return count;
  };

  const scheduleCachedRender = () => {
    if (!active()) {
      clearInactive();
      return;
    }
    if (renderFrame) return;
    renderFrame = requestAnimationFrame(() => {
      renderFrame = 0;
      if (active()) renderCached();
      else clearInactive();
    });
  };

  const schedule = (scheduleOptions = {}) => {
    if (!active()) {
      clearInactive();
      return;
    }
    const viewportSignature = terminalFileReferenceViewportSignature(term);
    const viewportChanged = scheduleOptions.viewportChanged === true || viewportSignature !== lastRenderedViewportSignature;
    const contentChanged = scheduleOptions.contentChanged === true || ['output', 'render'].includes(scheduleOptions.reason);
    if (viewportChanged || contentChanged) scheduleCachedRender();
    if (!timer) {
      timer = setTimeout(() => {
        timer = 0;
        if (active()) refresh();
        else clearInactive();
      }, TERMINAL_FILE_UNDERLINE_REFRESH_MS);
    }
  };

  const bindTerminalEvent = (name, callback) => {
    const disposable = typeof term?.[name] === 'function' ? term[name](callback) : null;
    if (disposable?.dispose) disposables.push(disposable);
  };
  bindTerminalEvent('onScroll', () => schedule({reason: 'scroll', viewportChanged: true}));
  bindTerminalEvent('onResize', () => schedule({reason: 'resize', viewportChanged: true}));
  bindTerminalEvent('onRender', () => schedule({reason: 'render', contentChanged: true}));
  container.addEventListener?.('mousemove', updateHover);
  container.addEventListener?.('mouseleave', clearHover);
  disposables.push({
    dispose() {
      container.removeEventListener?.('mousemove', updateHover);
      container.removeEventListener?.('mouseleave', clearHover);
    },
  });
  schedule();

  return {
    schedule,
    refresh,
    dispose() {
      disposed = true;
      sequence += 1;
      if (timer) clearTimeout(timer);
      if (renderFrame) cancelAnimationFrame(renderFrame);
      timer = 0;
      renderFrame = 0;
      disposables.forEach(disposable => {
        try { disposable.dispose(); } catch (_) {}
      });
      clearTerminalFileReferenceUnderlines(container);
      container.querySelector?.(':scope > .terminal-file-link-underlines')?.remove?.();
    },
  };
}

function installTerminalLinkProvider(session, term) {
  if (typeof term.registerLinkProvider !== 'function') return;
  term.registerLinkProvider({
    provideLinks: (y, callback) => {
      terminalReferenceProviderLinks(session, term, y).then(callback).catch(() => callback([]));
    },
  });
}

function terminalCellDimensions(term, container) {
  const cell = term?._core?._renderService?._renderer?.dimensions?.css?.cell
    || term?._core?._renderService?.dimensions?.css?.cell
    || {};
  const width = Number(cell.width || 0);
  const height = Number(cell.height || 0);
  if (width > 0 && height > 0) return {width, height};
  const node = container?.querySelector?.('.xterm-rows') || container?.querySelector?.('.xterm-screen') || container;
  const rect = node?.getBoundingClientRect?.();
  const cols = Number(term?.cols || 0);
  const rows = Number(term?.rows || 0);
  return {
    width: cols > 0 && rect?.width ? rect.width / cols : 0,
    height: rows > 0 && rect?.height ? rect.height / rows : 0,
  };
}

function terminalScreenElement(container) {
  return container?.querySelector?.('.xterm-rows')
    || container?.querySelector?.('.xterm-screen')
    || container?.querySelector?.('.xterm')
    || container;
}

function terminalPositionFromClientPoint(term, container, clientX, clientY) {
  const node = terminalScreenElement(container);
  const rect = node?.getBoundingClientRect?.();
  const cell = terminalCellDimensions(term, container);
  if (!rect || !(cell.width > 0) || !(cell.height > 0)) return null;
  const localX = Number(clientX) - rect.left;
  const localY = Number(clientY) - rect.top;
  if (localX < 0 || localY < 0 || localX > rect.width || localY > rect.height) return null;
  const cols = Math.max(1, Number(term?.cols || 1));
  const rows = Math.max(1, Number(term?.rows || 1));
  const x = Math.max(1, Math.min(cols, Math.floor(localX / cell.width) + 1));
  const screenRow = Math.max(1, Math.min(rows, Math.floor(localY / cell.height) + 1));
  const viewportY = Math.max(0, Number(term?.buffer?.active?.viewportY || 0));
  return {x, y: viewportY + screenRow};
}

function terminalRangeContainsPosition(range, position) {
  if (!range || !position) return false;
  const start = range.start || {};
  const end = range.end || {};
  if (position.y < start.y || position.y > end.y) return false;
  if (start.y === end.y) return position.x >= start.x && position.x <= end.x;
  if (position.y === start.y) return position.x >= start.x;
  if (position.y === end.y) return position.x <= end.x;
  return true;
}

function terminalReferenceAtPosition(term, position) {
  if (!position) return null;
  const refs = terminalWrappedLineReferences(term, position.y);
  return refs.find(ref => terminalRangeContainsPosition(ref.range, position)) || null;
}

function terminalReferenceAtClientPoint(term, container, clientX, clientY) {
  return terminalReferenceAtPosition(term, terminalPositionFromClientPoint(term, container, clientX, clientY));
}

function dedentSelectionText(value) {
  const text = String(value ?? '').replace(/\r\n/g, '\n').replace(/\r/g, '\n');
  const lines = text.split('\n');
  const indents = lines
    .filter(line => line.trim().length > 0 && /^[ \t]+/.test(line))
    .map(line => (line.match(/^[ \t]+/) || [''])[0].length);
  const stripBullet = line => line.replace(/^[ \t]*[●•]\s*/, '');
  if (!indents.length) return lines.map(stripBullet).join('\n');
  const commonIndent = Math.min(...indents);
  return lines
    .map(line => line.trim().length > 0 && /^[ \t]+/.test(line) ? line.slice(commonIndent) : line)
    .map(stripBullet)
    .join('\n');
}

async function copyTextToClipboard(text) {
  const clipboard = globalThis.navigator?.clipboard;
  const value = String(text ?? '');
  if (globalThis.isSecureContext !== false && clipboard?.writeText) {
    try {
      await clipboard.writeText(value);
      return;
    } catch (_) {
      // Fall through to execCommand. Some browsers expose navigator.clipboard
      // but reject writes on self-signed or permission-limited pages.
    }
  }
  if (copyTextToClipboardViaCopyEvent(value)) return;
  const textarea = document.createElement('textarea');
  textarea.value = value;
  textarea.setAttribute('readonly', '');
  textarea.style.position = 'fixed';
  textarea.style.left = `${OFFSCREEN_POSITION_PX}px`;
  textarea.style.top = `${OFFSCREEN_POSITION_PX}px`;
  document.body.appendChild(textarea);
  textarea.select();
  const copied = document.execCommand?.('copy') === true;
  textarea.remove();
  if (!copied) throw new Error(t('common.clipboardUnavailable'));
}

function copyTextToClipboardViaCopyEvent(text) {
  const value = String(text ?? '');
  let copied = false;
  const onCopy = event => {
    if (!event?.clipboardData) return;
    event.clipboardData.setData('text/plain', value);
    event.preventDefault();
    event.stopImmediatePropagation?.();
    event.stopPropagation?.();
    copied = true;
  };
  document.addEventListener?.('copy', onCopy, true);
  try {
    return document.execCommand?.('copy') === true && copied;
  } finally {
    document.removeEventListener?.('copy', onCopy, true);
  }
}

// ONE clipboard-write chain for terminal-initiated copies (shortcut copy AND the OSC 52
// bridge): synchronous copy-event first — it stays inside any live user activation — then the async
// navigator.clipboard path as fallback. Status text reports success/failure either way.
function writeTerminalTextToClipboard(text, options = {}) {
  const config = typeof options === 'string' ? {label: options} : (options || {});
  const action = config.action || TERMINAL_COPY_ACTIONS.selected;
  const label = config.label || terminalCopyStatusText(action, config.params || {});
  const afterCopy = typeof config.afterCopy === 'function' ? config.afterCopy : null;
  let cleanupDone = false;
  const cleanup = () => {
    if (cleanupDone || !afterCopy) return;
    cleanupDone = true;
    afterCopy();
  };
  if (copyTextToClipboardViaCopyEvent(text)) {
    copyDebug('clipboard', {via: 'copy-event', chars: String(text ?? '').length, ok: true});
    statusEl.textContent = label;
    cleanup();
    return;
  }
  copyTextToClipboard(text)
    .then(() => {
      copyDebug('clipboard', {via: 'async', chars: String(text ?? '').length, ok: true});
      statusEl.textContent = label;
    })
    .catch(error => {
      copyDebug('clipboard', {via: 'async', chars: String(text ?? '').length, ok: false, error: String(error)});
      statusErr(localizedHtml('common.copyFailed', {error}));
    });
  cleanup();
}

// opt-in live instrumentation for the copy path. Set storage key 'yolomux.debugCopy' to '1'
// and every copy decision logs ONE compact console line — enough to see which link breaks without
// changing behavior.
function copyDebugEnabled() {
  return storageGet('yolomux.debugCopy') === '1';
}

function copyDebug(stage, fields = {}) {
  if (!copyDebugEnabled()) return;
  const parts = Object.entries(fields).map(([key, value]) => `${key}=${value}`).join(' ');
  console.log(`[copy-debug] ${stage} ${parts}`);
}

function createContextMenuController() {
  let menu = null;
  const close = () => {
    if (!menu) return;
    menu.remove();
    menu = null;
    document.removeEventListener('pointerdown', pointerdown, true);
    document.removeEventListener('keydown', keydown, true);
    window.removeEventListener('blur', close);
    scheduleSharePopupLayerPublish({immediate: true});
  };
  const pointerdown = event => {
    if (menu?.contains(event.target)) return;
    close();
  };
  const keydown = event => {
    if (event.key === 'Escape') close();
  };
  return {
    close,
    isOpen: () => Boolean(menu),
    open(nextMenu, x, y) {
      close();
      menu = nextMenu;
      menu.addEventListener('pointerdown', event => event.stopPropagation());
      appOverlayRootElement().appendChild(menu);
      positionContextMenu(menu, x, y);
      document.addEventListener('pointerdown', pointerdown, true);
      document.addEventListener('keydown', keydown, true);
      window.addEventListener('blur', close);
      scheduleSharePopupLayerPublish();
    },
  };
}

function makeButton(options = {}) {
  const button = document.createElement('button');
  button.type = options.type || 'button';
  if (options.id) button.id = options.id;
  if (options.className) button.className = options.className;
  if (options.role) button.setAttribute('role', options.role);
  if (options.html !== undefined) button.innerHTML = options.html;
  else if (options.label !== undefined) button.textContent = options.label;
  button.disabled = options.disabled === true;
  if (options.title) button.title = options.title;
  if (options.ariaLabel) button.setAttribute('aria-label', options.ariaLabel);
  if (options.pressed !== undefined) button.setAttribute('aria-pressed', options.pressed ? 'true' : 'false');
  if (options.checked !== undefined) {
    button.setAttribute('aria-checked', options.checked ? 'true' : 'false');
    if (options.checked === true) button.dataset.checked = 'true';
  }
  if (options.dataset) {
    for (const [key, value] of Object.entries(options.dataset)) {
      if (value !== undefined && value !== null) button.dataset[key] = String(value);
    }
  }
  if (typeof options.onClick === 'function') button.addEventListener('click', options.onClick);
  return button;
}

function delegate(parent, type, selector, handler, options = {}) {
  if (!parent || typeof handler !== 'function') return null;
  const listener = event => {
    const target = event.target?.closest?.(selector);
    if (!target || (typeof parent.contains === 'function' && !parent.contains(target))) return;
    handler(event, target);
  };
  parent.addEventListener(type, listener, options);
  return listener;
}

function copyPathButtonValue(button) {
  return String(button?.dataset?.copyPath || '');
}

function copyPathButtonStopEvent(event) {
  event.preventDefault();
  event.stopPropagation();
  event.stopImmediatePropagation?.();
}

function activateCopyPathButton(event, button) {
  copyPathButtonStopEvent(event);
  const path = copyPathButtonValue(button);
  if (!path) return;
  copyTextToClipboard(path)
    .then(() => { statusOk(localizedHtml('status.copied')); })
    .catch(error => { statusErr(localizedHtml('common.copyFailed', {error})); });
}

function handleCopyPathPointerUp(event, button) {
  button.__yolomuxCopyPointerHandled = true;
  activateCopyPathButton(event, button);
}

function handleCopyPathClick(event, button) {
  const pointerHandled = button.__yolomuxCopyPointerHandled === true;
  button.__yolomuxCopyPointerHandled = false;
  if (pointerHandled && event.detail !== 0) {
    copyPathButtonStopEvent(event);
    return;
  }
  activateCopyPathButton(event, button);
}

delegate(document, 'pointerup', '[data-copy-path]', handleCopyPathPointerUp, {capture: true});
delegate(document, 'click', '[data-copy-path]', handleCopyPathClick, {capture: true});

// One owner for the per-session/-item DOM id scheme. Both the element that sets the id and every
// getElementById/querySelector that looks it up route through these, so the prefix lives in one place
// (the ids are produced + consumed across 7 partials). Changing a prefix is then a one-line edit.
const panelDomId = item => `panel-${item}`;
const paneTabDomId = session => `panel-tab-${session}`;
const terminalDomId = session => `term-${session}`;
const transcriptDomId = session => `transcript-${session}`;
const summaryDomId = session => `summary-${session}`;

// One inflight-dedup wrapper: run makeRequest() at most once per key while a call is outstanding, so
// concurrent callers for the same key share the single in-flight promise and clean up after it settles.
// When canReuse is false the caller wants an untracked fresh fetch, so it runs without registering. The
// TTL cache-hit check and any resource-specific guards stay at the call site; this owns only the inflight
// Map bookkeeping that was hand-rolled identically per filesystem resource (dir listing, path info, blame).
function dedupeInflight(inflight, key, canReuse, makeRequest) {
  if (canReuse) {
    const existing = inflight.get(key);
    if (existing) return existing;
  }
  const request = makeRequest();
  if (!canReuse) return request;
  inflight.set(key, request);
  return (async () => {
    try {
      return await request;
    } finally {
      if (inflight.get(key) === request) inflight.delete(key);
    }
  })();
}

function appendContextMenuButton(menu, label, handler, closeMenu, options = {}) {
  const iconHtml = options.iconHtml ? stripTitleAttrs(options.iconHtml) : '';
  const shortcutHtml = options.shortcut ? `<span class="context-menu-shortcut">${esc(options.shortcut)}</span>` : '';
  const buttonHtml = iconHtml || shortcutHtml
    ? `<span class="context-menu-line">${iconHtml ? `<span class="context-menu-icon">${iconHtml}</span>` : ''}<span class="context-menu-label">${esc(label)}</span>${shortcutHtml}</span>`
    : undefined;
  const className = ['control-active-hover', options.className || ''].filter(Boolean).join(' ');
  const button = makeButton({
    ...options,
    className,
    html: buttonHtml,
    label: buttonHtml ? undefined : label,
    ariaLabel: options.ariaLabel || label,
    role: options.checked !== undefined ? 'menuitemcheckbox' : 'menuitem',
  });
  button.addEventListener('click', event => {
    event.preventDefault();
    event.stopPropagation();
    if (!button.disabled) handler();
    closeMenu();
  });
  menu.appendChild(button);
  return button;
}

function appendContextMenuSeparator(menu) {
  const separator = document.createElement('div');
  separator.className = 'terminal-context-menu-separator';
  separator.role = 'separator';
  menu.appendChild(separator);
  return separator;
}

function contextMenuIsOpen() {
  return terminalContextMenu.isOpen() || fileContextMenu.isOpen() || sessionContextMenu.isOpen() || linkContextMenu.isOpen();
}

function rootCssLengthPx(name) {
  if (!document.body || typeof window.getComputedStyle !== 'function') return 0;
  const value = window.getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  if (!value) return 0;
  const probe = document.createElement('div');
  probe.style.position = 'fixed';
  probe.style.visibility = 'hidden';
  probe.style.pointerEvents = 'none';
  probe.style.width = value;
  probe.style.height = '0';
  document.body.appendChild(probe);
  const width = probe.getBoundingClientRect().width || 0;
  probe.remove();
  return Math.max(0, width);
}

const MIN_SPLIT_PANE_WIDTH_FALLBACK_PX = 320;
const MIN_SPLIT_PANE_HEIGHT_FALLBACK_PX = 220;

function minSplitPaneWidthPx() {
  return rootCssLengthPx('--min-split-pane-width') || MIN_SPLIT_PANE_WIDTH_FALLBACK_PX;
}

function minSplitPaneHeightPx() {
  return rootCssLengthPx('--min-split-pane-height') || MIN_SPLIT_PANE_HEIGHT_FALLBACK_PX;
}

function popoverEdgeGapPx() {
  return rootCssLengthPx('--popover-edge-gap');
}

function positionContextMenu(menu, x, y) {
  const rect = menu.getBoundingClientRect();
  const edgeGap = popoverEdgeGapPx();
  const viewport = appViewport();
  const left = Math.min(Math.max(edgeGap, x), Math.max(edgeGap, viewport.width - rect.width - edgeGap));
  const top = Math.min(Math.max(edgeGap, y), Math.max(edgeGap, viewport.height - rect.height - edgeGap));
  menu.style.left = `${Math.round(left)}px`;
  menu.style.top = `${Math.round(top)}px`;
}

function closeTerminalContextMenu() {
  terminalContextMenu.close();
}

function closeFileContextMenu() {
  fileContextMenu.close();
}

function closeSessionContextMenu() {
  sessionContextMenu.close();
}

function closeLinkContextMenu() {
  linkContextMenu.close();
}

function closeContextMenus() {
  closeTerminalContextMenu();
  closeFileContextMenu();
  closeSessionContextMenu();
  closeLinkContextMenu();
}

function appendUrlContextMenuItems(menu, href, closeMenu, options = {}) {
  const url = String(href || '');
  if (!url) return false;
  const selectedText = String(options.selectionText || '');
  const action = (reason, handler) => (
    options.term || options.container
      ? consumeTerminalSelection(options.session, options.term, options.container, reason, handler)
      : handler
  );
  appendContextMenuButton(menu, t('contextmenu.openUrl'), action('open-url', () => window.open(url, '_blank', 'noopener,noreferrer')), closeMenu);
  appendContextMenuButton(menu, t('contextmenu.copyUrl'), action('copy-url', () => copyTextToClipboard(url)), closeMenu);
  if (options.includeSelectedText && selectedText && selectedText !== url) {
    appendContextMenuButton(menu, t('contextmenu.copySelectedText'), action('copy-selected-text', () => copyTextToClipboard(selectedText)), closeMenu);
  }
  return true;
}

// right-click menu for links in AI/markdown content — Open URL / Copy URL. Bound on the
// YO!agent body and markdown previews via installLinkContextMenu(container).
function showLinkContextMenu(anchor, x, y) {
  closeTerminalContextMenu();
  closeFileContextMenu();
  closeSessionContextMenu();
  closeOtherSessionPopovers(null);
  const href = anchor?.href || '';
  if (!href) return;
  const menu = document.createElement('div');
  menu.className = 'terminal-context-menu link-context-menu';
  menu.setAttribute('role', 'menu');
  appendUrlContextMenuItems(menu, href, closeLinkContextMenu);
  linkContextMenu.open(menu, x, y);
}

function installLinkContextMenu(container) {
  if (!container || container.dataset.linkContextMenuBound === '1') return;
  container.dataset.linkContextMenuBound = '1';
  container.addEventListener('contextmenu', event => {
    const anchor = event.target?.closest?.('a[href]');
    if (!anchor || !container.contains(anchor)) return;
    event.preventDefault();
    event.stopPropagation();
    showLinkContextMenu(anchor, event.clientX, event.clientY);
  });
}

function nodeInsideElement(element, node) {
  if (!element || !node) return false;
  if (element === node) return true;
  if (element.contains?.(node)) return true;
  let current = node.parentElement || node.parentNode || null;
  while (current) {
    if (current === element) return true;
    current = current.parentElement || current.parentNode || null;
  }
  return false;
}

function browserSelectionTextInside(container) {
  if (!container) return '';
  const selection = globalThis.getSelection?.() || globalThis.window?.getSelection?.();
  const text = String(selection?.toString?.() || '');
  if (!text) return '';
  const anchorNode = selection.anchorNode || null;
  const focusNode = selection.focusNode || null;
  if (!anchorNode && !focusNode) return '';
  return nodeInsideElement(container, anchorNode) || nodeInsideElement(container, focusNode) ? text : '';
}

function terminalSelectedText(term, container = null) {
  return term.getSelection?.() || browserSelectionTextInside(container);
}

function browserSelectionTouchesContainer(container, selection = null) {
  if (!container) return false;
  const current = selection || globalThis.getSelection?.() || globalThis.window?.getSelection?.();
  if (!current) return false;
  const anchorNode = current.anchorNode || null;
  const focusNode = current.focusNode || null;
  if (!anchorNode && !focusNode) return false;
  return nodeInsideElement(container, anchorNode) || nodeInsideElement(container, focusNode);
}

function terminalVisibleSelectionState(session, term, container = null) {
  const xtermText = String(term?.getSelection?.() || '');
  const selection = globalThis.getSelection?.() || globalThis.window?.getSelection?.();
  const browserInside = browserSelectionTouchesContainer(container, selection);
  const browserText = browserInside ? String(selection?.toString?.() || '') : '';
  const appClipboard = recentTerminalAppClipboardText(session);
  let paneMode = '';
  try {
    const panes = typeof tmuxSignalAgentPanesForSession === 'function' ? tmuxSignalAgentPanesForSession(session) : [];
    const labels = typeof tmuxSignalPaneModeLabels === 'function'
      ? panes.flatMap(pane => tmuxSignalPaneModeLabels(pane))
      : [];
    paneMode = labels.join(',');
  } catch (_error) {
    paneMode = '';
  }
  return {
    xtermChars: xtermText.length,
    browserChars: browserText.length,
    browserInside,
    recentOsc52Chars: String(appClipboard || '').length,
    paneMode,
  };
}

function clearTerminalVisibleSelection(session, term, container = null, reason = 'terminal-selection-consumed') {
  const before = terminalVisibleSelectionState(session, term, container);
  const selection = globalThis.getSelection?.() || globalThis.window?.getSelection?.();
  let browserCleared = false;
  if (browserSelectionTouchesContainer(container, selection) && typeof selection?.removeAllRanges === 'function') {
    selection.removeAllRanges();
    browserCleared = true;
  }
  const xtermClearCalled = typeof term?.clearSelection === 'function';
  if (xtermClearCalled) term.clearSelection();
  if (browserCleared || xtermClearCalled || before.xtermChars || before.browserChars || before.recentOsc52Chars || before.paneMode) {
    copyDebug('selection-clear', {session, reason, ...before, browserCleared, xtermClearCalled});
  }
  return {before, browserCleared, xtermClearCalled};
}

function withTerminalVisibleSelectionCleanup(session, term, container, reason, handler) {
  return async () => {
    try {
      return await handler();
    } finally {
      clearTerminalVisibleSelection(session, term, container, reason);
    }
  };
}

const TERMINAL_COPY_ACTIONS = Object.freeze({
  selected: Object.freeze({
    labelKey: 'common.copy',
    statusKey: 'status.copied',
    reason: 'copy-selection',
    dedent: false,
  }),
  selectedDedent: Object.freeze({
    labelKey: 'terminal.copyWithoutIndent',
    statusKey: 'status.copiedWithoutIndent',
    reason: 'copy-without-indent',
    dedent: true,
  }),
  tmux: Object.freeze({
    labelKey: 'common.copyTmuxSelection',
    statusPluralKey: 'status.copiedTmuxSelection',
    reason: 'copy-tmux-selection',
  }),
  osc52: Object.freeze({
    statusPluralKey: 'status.copiedTerminalChars',
    reason: 'copy-osc52-selection',
  }),
});

function terminalCopyActionForOptions(options = {}) {
  if (options.action) return options.action;
  return options.dedent ? TERMINAL_COPY_ACTIONS.selectedDedent : TERMINAL_COPY_ACTIONS.selected;
}

function terminalCopyActionLabel(action) {
  return action?.labelKey ? t(action.labelKey) : '';
}

function terminalCopyStatusText(action, params = {}) {
  if (action?.statusPluralKey) return tPlural(action.statusPluralKey, params.count, params);
  return t(action?.statusKey || 'status.copied', params);
}

function consumeTerminalSelection(session, term, container, action, handler) {
  const reason = typeof action === 'string' ? action : (action?.reason || 'terminal-selection-consumed');
  return withTerminalVisibleSelectionCleanup(session, term, container, reason, handler);
}

const TERMINAL_APP_CLIPBOARD_MAX_AGE_MS = 15000;
const terminalAppClipboardText = new Map();

function rememberTerminalAppClipboardText(session, text, timestamp = Date.now()) {
  const value = String(text ?? '');
  if (!value) return;
  terminalAppClipboardText.set(String(session || ''), {text: value, timestamp});
}

function recentTerminalAppClipboardText(session, timestamp = Date.now()) {
  const key = String(session || '');
  const entry = terminalAppClipboardText.get(key);
  if (!entry) return '';
  if (timestamp - entry.timestamp > TERMINAL_APP_CLIPBOARD_MAX_AGE_MS) {
    terminalAppClipboardText.delete(key);
    return '';
  }
  return entry.text;
}

function terminalContextMenuSelection(session, term, container = null, presetSelection = null) {
  const selected = presetSelection == null ? terminalSelectedText(term, container) : String(presetSelection || '');
  if (selected) return {text: selected, source: 'terminal'};
  const appSelection = recentTerminalAppClipboardText(session);
  return appSelection ? {text: appSelection, source: 'app-clipboard'} : {text: '', source: 'none'};
}

function terminalFileReferenceCandidatePaths(session, reference) {
  const raw = String(reference?.path || '').trim();
  if (!raw || raw.includes('\0') || /[\r\n]/.test(raw)) return [];
  if (/^[a-z][a-z0-9+.-]*:/i.test(raw)) return [];
  if (raw === '~') return homePath ? [normalizeDirectoryPath(homePath)] : [];
  if (raw.startsWith('~/')) return homePath ? [joinAndNormalize(homePath, raw.slice(2))] : [];
  if (raw.startsWith('/')) return [normalizeDirectoryPath(raw)];
  const info = sessionTranscriptInfo(session);
  const selectedRepo = selectedSessionRepo(session, info.info);
  const bases = [
    terminalCurrentPath(session),
    info.selectedPath,
    info.gitCwd,
    selectedRepo?.cwd,
    selectedRepo?.root,
    info.gitRoot,
    homePath,
  ];
  const paths = [];
  for (const base of bases) {
    const normalizedBase = normalizeDirectoryPath(base || '');
    const candidate = normalizedBase ? joinAndNormalize(normalizedBase, raw) : '';
    if (!candidate || paths.includes(candidate)) continue;
    paths.push(candidate);
  }
  return paths;
}

function terminalFileReferenceAbsolutePath(session, reference) {
  return terminalFileReferenceCandidatePaths(session, reference)[0] || '';
}

async function terminalFileReferenceTarget(session, reference, options = {}) {
  if (reference?.type !== 'file') return null;
  const canReuse = options.fresh === false;
  const cacheKey = terminalFileReferenceCacheKey(session, reference);
  if (canReuse && terminalFileReferenceTargetCache.has(cacheKey)) {
    const cached = terminalFileReferenceTargetCache.get(cacheKey);
    setLimitedMapEntry(terminalFileReferenceTargetCache, cacheKey, cached, fileExplorerMemoryCacheLimit);
    return cached;
  }
  const fetchOptions = {
    user: options.user !== false,
    fresh: !canReuse,
  };
  const targetPromise = (async () => {
    for (const path of terminalFileReferenceCandidatePaths(session, reference)) {
      try {
        const info = await fetchFilePathInfo(path, fetchOptions);
        if (info?.kind === 'file') return {path, info, line: reference.line || null, text: reference.text || path};
      } catch (_error) {
        // Try the next context-derived candidate; a missing cwd-relative path can still be repo-relative.
      }
    }
    return null;
  })();
  if (!canReuse) return targetPromise;
  setLimitedMapEntry(terminalFileReferenceTargetCache, cacheKey, targetPromise, fileExplorerMemoryCacheLimit);
  try {
    const target = await targetPromise;
    if (terminalFileReferenceTargetCache.get(cacheKey) === targetPromise) {
      setLimitedMapEntry(terminalFileReferenceTargetCache, cacheKey, target, fileExplorerMemoryCacheLimit);
    }
    return target;
  } catch (error) {
    if (terminalFileReferenceTargetCache.get(cacheKey) === targetPromise) terminalFileReferenceTargetCache.delete(cacheKey);
    throw error;
  }
}

function requestFileEditorLineTarget(item, line) {
  const cleanLine = Math.max(1, Math.floor(Number(line) || 0));
  if (!item || !cleanLine) return false;
  pendingFileEditorLineTargets.set(item, cleanLine);
  const panel = panelNodes.get(item);
  if (panel?._cmView && typeof applyPendingFileEditorLineTarget === 'function') {
    return applyPendingFileEditorLineTarget(item, panel);
  }
  return true;
}

async function openTerminalFileReference(target) {
  if (!target?.path) return;
  const item = await openFileInEditor(target.path, target.info || {name: basenameOf(target.path)}, {viewMode: 'edit', userInitiated: true});
  if (item && target.line) requestFileEditorLineTarget(item, target.line);
}

function appendTerminalReferenceContextMenuItems(menu, reference, fileTarget = null, options = {}) {
  if (!reference) return false;
  if (reference.type === 'url') {
    const href = reference.href || normalizeTerminalLink(reference.text);
    if (!href) return false;
    return appendUrlContextMenuItems(menu, href, closeTerminalContextMenu, {
      includeSelectedText: true,
      selectionText: options.selectionText,
      session: options.session,
      term: options.term,
      container: options.container,
    });
  }
  if (reference.type === 'file' && fileTarget) {
    appendContextMenuButton(menu, t('common.openFile'), () => openTerminalFileReference(fileTarget), closeTerminalContextMenu);
    appendContextMenuButton(menu, t('contextmenu.copyPath'), () => copyTextToClipboard(fileTarget.path), closeTerminalContextMenu);
    return true;
  }
  return false;
}

async function copyTerminalSelection(session, term, options = {}, container = null) {
  // N7: the context menu passes the selection captured at right-click time, because by the time the user
  // clicks the menu the live selection may be gone (focus moved to the menu).
  const selected = options.selectionText != null ? options.selectionText : terminalSelectedText(term, container);
  const action = terminalCopyActionForOptions(options);
  if (!selected) {
    statusEl.textContent = t('status.nothingSelected');
    return;
  }
  const text = action.dedent ? dedentSelectionText(selected) : selected;
  try {
    await copyTextToClipboard(text);
    statusEl.textContent = terminalCopyStatusText(action);
  } catch (error) {
    statusErr(localizedHtml('common.copyFailed', {error}));
  } finally {
    clearTerminalVisibleSelection(session, term, container, action.reason);
  }
}

function copyTerminalSelectionFromShortcut(session, term, options = {}, container = null) {
  const selected = terminalSelectedText(term, container);
  const action = terminalCopyActionForOptions(options);
  if (!selected) {
    statusEl.textContent = t('status.nothingSelected');
    return false;
  }
  const text = action.dedent ? dedentSelectionText(selected) : selected;
  writeTerminalTextToClipboard(text, {
    action,
    afterCopy: () => clearTerminalVisibleSelection(session, term, container, action.reason),
  });
  return true;
}

function copyTerminalSelectionToClipboardEvent(session, term, event, container = null) {
  const selected = terminalSelectedText(term, container);
  if (!selected || !event?.clipboardData) return false;
  event.clipboardData.setData('text/plain', selected);
  event.preventDefault();
  event.stopPropagation();
  statusEl.textContent = terminalCopyStatusText(TERMINAL_COPY_ACTIONS.selected);
  clearTerminalVisibleSelection(session, term, container, TERMINAL_COPY_ACTIONS.selected.reason);
  return true;
}

async function copyTmuxSelectionToClipboard(session, term = null, container = null) {
  const payloadPromise = fetchTmuxSelectionText(session);
  try {
    const {payload, text} = await copyDeferredTextToClipboard(payloadPromise);
    const chars = Number.isFinite(Number(payload.chars)) ? Number(payload.chars) : text.length;
    statusEl.textContent = terminalCopyStatusText(TERMINAL_COPY_ACTIONS.tmux, {count: chars});
    return true;
  } catch (error) {
    if (error?.noClipboardText) {
      statusEl.textContent = error.message || t('status.nothingSelected');
      return false;
    }
    statusErr(esc(userMessageText(error, t('common.copyFailed', {error}))));
    return false;
  } finally {
    clearTerminalVisibleSelection(session, term, container, 'copy-tmux-selection');
  }
}

async function fetchTmuxSelectionText(session) {
  const payload = await apiFetchJson(`/api/tmux-copy-selection?session=${encodeURIComponent(session)}`, {method: 'POST'});
  const text = payload?.copied ? String(payload.text || '') : '';
  if (!text) {
    const error = new Error(userMessageText(payload, t('status.nothingSelected')));
    error.noClipboardText = true;
    throw error;
  }
  return {payload, text};
}

async function copyDeferredTextToClipboard(payloadPromise) {
  const clipboard = globalThis.navigator?.clipboard;
  if (clipboard?.write && typeof globalThis.ClipboardItem === 'function' && typeof globalThis.Blob === 'function') {
    const textBlob = payloadPromise.then(({text}) => new Blob([text], {type: 'text/plain'}));
    try {
      await clipboard.write([new ClipboardItem({'text/plain': textBlob})]);
      return await payloadPromise;
    } catch (error) {
      if (error?.noClipboardText) throw error;
      const result = await payloadPromise;
      await copyTextToClipboard(result.text);
      return result;
    }
  }
  const result = await payloadPromise;
  await copyTextToClipboard(result.text);
  return result;
}

// ROOT CAUSE: while Claude (or any TUI) owns the mouse inside tmux, the visible selection is the
// APP's — a plain drag never creates an xterm selection, and the copied text instead arrives as an
// OSC 52 clipboard escape (app -> tmux `set-clipboard` passthrough -> our PTY -> xterm.js). xterm.js
// DROPS OSC 52 unless a handler is registered, so those copies silently vanished. This bridge decodes
// the escape and writes the browser clipboard. It also catches tmux copy-mode `copy-pipe` buffers.
// Payload format: "Pc;Pd" — Pc selects clipboard(s) (c/p/s/q...), Pd is base64 text or '?' (a READ
// request, which we never answer so apps cannot exfiltrate the browser clipboard).
function osc52ClipboardText(data) {
  const raw = String(data ?? '');
  const semi = raw.indexOf(';');
  if (semi < 0) return null;
  const payload = raw.slice(semi + 1);
  if (!payload || payload === '?') return null;
  try {
    const binary = atob(payload);
    const bytes = new Uint8Array(binary.length);
    for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
    const decoded = new TextDecoder('utf-8', {fatal: false}).decode(bytes);
    return decoded || null;
  } catch (_error) {
    return null; // not valid base64: ignore rather than copy garbage
  }
}

function installTerminalOsc52Bridge(session, term) {
  if (!term?.parser?.registerOscHandler) return false;
  term.parser.registerOscHandler(52, data => {
    const text = osc52ClipboardText(data);
    copyDebug('osc52', {session, payloadChars: String(data ?? '').length, textChars: text ? text.length : 0});
    if (text) {
      rememberTerminalAppClipboardText(session, text);
      writeTerminalTextToClipboard(text, {action: TERMINAL_COPY_ACTIONS.osc52, params: {count: text.length}});
    }
    return true; // consumed either way; '?' queries get no reply
  });
  return true;
}

function handleTerminalCopyShortcutKeydown(session, term, container, event) {
  if (event.type !== 'keydown') return false;
  if (event.code !== 'KeyC' && event.key?.toLowerCase() !== 'c') return false;
  const isTmuxCopyShortcut = event.altKey
    && !event.shiftKey
    && ((isMacPlatform() && event.metaKey && !event.ctrlKey)
      || (!isMacPlatform() && event.ctrlKey && !event.metaKey));
  if (isTmuxCopyShortcut) {
    event.preventDefault();
    copyTmuxSelectionToClipboard(session, term, container);
    return true;
  }
  const isCmdC = event.metaKey && !event.ctrlKey && !event.altKey;
  const isCtrlC = event.ctrlKey && !event.metaKey && !event.altKey;
  if (!isCmdC && !isCtrlC) return false;
  const xtermSelected = term.getSelection?.() || '';
  const browserSelected = browserSelectionTextInside(container);
  const selected = xtermSelected || browserSelected;
  copyDebug('shortcut', {
    session,
    combo: isCmdC ? 'cmd-c' : 'ctrl-c',
    xtermSel: xtermSelected.length,
    browserSel: browserSelected.length,
    branch: selected ? 'copy' : (isCmdC ? 'no-selection' : 'sigint'),
  });
  if (!selected) {
    if (isCmdC) {
      event.preventDefault();
      // in a Claude/tmux pane the APP owns the mouse, so a plain drag never creates an xterm
      // selection — tell the user the working gestures instead of dead-ending.
      statusEl.textContent = isMacPlatform()
        ? t('terminal.copyHintMac')
        : t('terminal.copyHintPc');
      return true;
    }
    return false; // no selection: let Ctrl-C through as SIGINT
  }
  event.preventDefault();
  copyTerminalSelectionFromShortcut(session, term, {}, container);
  return true;
}

function terminalTmuxWindowShortcutDirection(event) {
  if (!event || event.type !== 'keydown' || !event.metaKey || event.ctrlKey || event.altKey || event.shiftKey) return 0;
  if (event.key === 'ArrowLeft' || event.code === 'ArrowLeft') return -1;
  if (event.key === 'ArrowRight' || event.code === 'ArrowRight') return 1;
  return 0;
}

function terminalTmuxWindowShortcutItem(session) {
  const activeItem = visualActivePaneItem();
  return activeItem || session;
}

function handleTerminalTmuxWindowShortcutKeydown(session, event) {
  const direction = terminalTmuxWindowShortcutDirection(event);
  if (!direction) return false;
  event.preventDefault?.();
  if (typeof selectAdjacentPaneTab === 'function') {
    selectAdjacentPaneTab(direction, {item: terminalTmuxWindowShortcutItem(session), userInitiated: true});
  }
  return true;
}

function installTerminalCopyShortcut(session, term, container = null) {
  // Ctrl-C / Cmd-C copy the xterm selection. Plain Ctrl-C with NO selection
  // must still send SIGINT to the PTY, and Cmd-C must stay browser/xterm copy
  // only. Tmux copy-mode text has a separate explicit shortcut/menu action.
  container?.addEventListener?.('keydown', event => {
    if (!handleTerminalTmuxWindowShortcutKeydown(session, event) && !handleTerminalCopyShortcutKeydown(session, term, container, event)) return;
    event.stopImmediatePropagation?.();
    event.stopPropagation?.();
  }, {capture: true});
  term.attachCustomKeyEventHandler?.(event => {
    return (handleTerminalTmuxWindowShortcutKeydown(session, event) || handleTerminalCopyShortcutKeydown(session, term, container, event)) ? false : true;
  });
}

async function showTerminalContextMenu(session, term, x, y, container = null, presetSelection = null, reference = null) {
  closeFileContextMenu();
  closeSessionContextMenu();
  closeFileImagePreview();
  closeOtherSessionPopovers(null);
  const terminalReference = reference || terminalReferenceAtClientPoint(term, container, x, y);
  const fileTarget = terminalReference?.type === 'file' ? await terminalFileReferenceTarget(session, terminalReference) : null;
  const menu = document.createElement('div');
  menu.className = 'terminal-context-menu';
  menu.setAttribute('role', 'menu');
  // N7: prefer the selection captured at right-click time over a live re-read (which can be empty by now).
  // Claude and other TUIs may own the visible selection and only expose it through OSC 52, so fall back
  // to the recent app clipboard payload instead of re-reading a tiny under-cursor browser fragment.
  const selection = terminalContextMenuSelection(session, term, container, presetSelection);
  const selected = selection.text;
  copyDebug('contextmenu', {session, selectionSource: selection.source, chars: selected.length});
  const hasUrlReference = terminalReference?.type === 'url';
  if (hasUrlReference) {
    if (appendTerminalReferenceContextMenuItems(menu, terminalReference, fileTarget, {selectionText: selected, session, term, container})) {
      appendContextMenuSeparator(menu);
    }
  } else {
    const items = [
      [TERMINAL_COPY_ACTIONS.selected, false],
      [TERMINAL_COPY_ACTIONS.selectedDedent, true],
    ];
    for (const [action, dedent] of items) {
      appendContextMenuButton(menu, terminalCopyActionLabel(action), () => copyTerminalSelection(session, term, {action, dedent, selectionText: selected}, container), closeTerminalContextMenu, {disabled: !selected});
    }
    appendContextMenuSeparator(menu);
    if (appendTerminalReferenceContextMenuItems(menu, terminalReference, fileTarget, {selectionText: selected, session, term, container})) appendContextMenuSeparator(menu);
  }
  appendContextMenuButton(menu, terminalCopyActionLabel(TERMINAL_COPY_ACTIONS.tmux), () => copyTmuxSelectionToClipboard(session, term, container), closeTerminalContextMenu);
  if (hasUrlReference) {
    appendContextMenuButton(menu, terminalCopyActionLabel(TERMINAL_COPY_ACTIONS.selectedDedent), () => copyTerminalSelection(session, term, {action: TERMINAL_COPY_ACTIONS.selectedDedent, dedent: true, selectionText: selected}, container), closeTerminalContextMenu, {disabled: !selected});
  }
  terminalContextMenu.open(menu, x, y);
}

function installTerminalContextMenu(session, term, container) {
  // N7: right-click must NOT clear the highlight. xterm clears its selection on mousedown, so capture the
  // selected text on the right-mousedown (capture phase, before xterm's handler) and stopPropagation so
  // xterm never processes that mousedown — the highlight stays visible AND the menu has the text even if
  // focus moves to the menu. No preventDefault, so the contextmenu event still fires normally.
  let rightClickSelection = null;
  container.addEventListener('mousedown', event => {
    if (event.button !== 2) return;
    rightClickSelection = terminalSelectedText(term, container);
    event.stopPropagation();
  }, {capture: true});
  container.addEventListener('contextmenu', event => {
    event.preventDefault();
    event.stopPropagation();
    showTerminalContextMenu(session, term, event.clientX, event.clientY, container, rightClickSelection);
    rightClickSelection = null;
  });
}

function showTabContextMenu(item, x, y, options = {}) {
  if (!isPinnableTab(item) && !isTmuxSession(item)) return;
  closeAppMenus();
  closeTerminalContextMenu();
  closeFileContextMenu();
  closeOtherSessionPopovers(null);
  const menu = document.createElement('div');
  menu.className = 'terminal-context-menu session-context-menu';
  menu.setAttribute('role', 'menu');
  if (isPinnableTab(item)) {
    const pinned = tabIsPinned(item);
    appendContextMenuButton(
      menu,
      pinned ? t('tab.unpin') : t('tab.pin'),
      () => setTabPinned(item, !pinned),
      closeSessionContextMenu,
      {
        checked: pinned,
        iconHtml: appMenuUiIcon('pin', pinned),
        shortcut: `${appShortcutText('K', {shift: true})} Enter`,
      },
    );
    if (typeof paneCanPopout === 'function' && paneCanPopout(item)) {
      appendContextMenuButton(menu, t('tab.popout'), () => openPanePopout(item), closeSessionContextMenu);
    }
  }
  if (isTmuxSession(item)) {
    if (isPinnableTab(item)) appendContextMenuSeparator(menu);
    const renameAction = options.tab ? () => beginPaneTabRename(options.tab, item) : () => renameTmuxSession(item);
    for (const command of tmuxSessionActionCommands(item, {renameAction, includeKill: false})) {
      appendContextMenuButton(menu, command.label, command.action, closeSessionContextMenu, {disabled: command.disabled, checked: command.checked});
    }
    const paneInfoBarLabel = t('menu.tmux.paneDetails');
    const viewItems = tmuxSessionViewCommands(item).filter(command => command.label !== paneInfoBarLabel);
    for (const command of viewItems) {
      appendContextMenuButton(menu, command.label, command.action, closeSessionContextMenu, {
        disabled: command.disabled,
        checked: command.checked,
        title: command.detail || '',
      });
    }
    appendContextMenuSeparator(menu);
    const killItem = tmuxSessionKillCommand(item);
    appendContextMenuButton(menu, killItem.label, killItem.action, closeSessionContextMenu, {disabled: killItem.disabled, className: 'danger'});
  }
  sessionContextMenu.open(menu, x, y);
}

function showSessionContextMenu(session, x, y, options = {}) {
  showTabContextMenu(session, x, y, options);
}
